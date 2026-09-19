# The model blob

`edgenlu export <bundle> -o <dir>` writes three files:

- `model.bin`, the whole model as one flat byte string,
- `model_data.c` and `model_data.h`, the same bytes as a `const unsigned char[]` so they
  can be linked into a firmware image and stay in flash.

The runtime reads the blob where it lies. Nothing is decompressed, nothing is copied and
no memory is allocated, so on a microcontroller the weights never reach RAM.

`src/edgenlu/export.py` writes this format and `runtime/edgenlu.c` reads it. Change one
and you change all three.

## Rules that hold everywhere

- Every number is little-endian.
- `u8`, `u16`, `u32`, `u64` are unsigned; `i32` is a two's complement signed 32 bit
  integer; `f32` and `f16` are IEEE 754 binary32 and binary16.
- Offsets inside a section are counted from the first byte of that section. Offsets in
  the directory are counted from the first byte of the blob.
- A **string offset** is a byte offset into the strings section. The string there runs to
  the next zero byte. Offset 0 is always the empty string.
- Every section starts on an 8 byte boundary, and the blob itself must be placed on an
  8 byte boundary. `u64` arrays are 8 byte aligned, `f32` and `u32` arrays 4 byte aligned.
  A device that faults on an unaligned read is the reason.
- `0xFFFFFFFF` in an index field means "nothing".

## Header, 32 bytes

| at | type | what |
| --- | --- | --- |
| 0 | u8[4] | `E` `N` `L` `U` |
| 4 | u32 | format version, 2 today |
| 8 | u32 | the length of the whole blob |
| 12 | u32 | how many sections follow |
| 16 | u32[4] | reserved, all zero |

Then one 16 byte directory entry per section: `u32 id`, `u32 offset`, `u32 length`,
`u32 reserved`. Entries may be in any order; a reader looks up the id it wants.

Section ids: 1 strings, 2 intent, 3 tagger, 4 commands, 5 numbers, 6 gate.

Version 2 added the gate section and nothing else. A version 1 blob has no
training vocabulary in it, so a version 2 runtime refuses it rather than answering
with a confidence it cannot build.

## 1. Strings

Zero terminated UTF-8, one after another, starting with an empty one. Everything else
in the blob points in here.

## 2. Intent

| at | type | what |
| --- | --- | --- |
| 0 | u32 | hash table size, always a power of two |
| 4 | u32 | number of classes |
| 8 | f32 | temperature |
| 12 | u32 | where the class name offsets are |
| 16 | u32 | where the bias is |
| 20 | u32 | where the weights are |
| 24 | u32 | weight format, 1 means f16 |
| 28 | u32 | reserved |

- class names: `u32` string offset per class,
- bias: `f32` per class,
- weights: `f16`, one row of `table size` per class, classes in order.

The scoring recipe is in `src/edgenlu/features.py`: hash each feature string with
FNV-1a 32, add 1.0 to that bucket, divide the vector by its L2 norm, multiply by the
weight row, add the bias, divide by the temperature and take the softmax.

Weights are stored at half size because measured on both example bundles that moves the
reported confidence by at most 2.3e-4 and changes no answer. int8 with a per class scale
would halve it again but moves confidence by up to 8.1e-3.

## 3. Tagger

| at | type | what |
| --- | --- | --- |
| 0 | u32 | number of labels |
| 4 | u32 | number of attributes |
| 8 | u32 | number of (attribute, label) weights |
| 12 | u32 | where the label name offsets are |
| 16 | u32 | where the transition matrix is |
| 20 | u32 | where the attribute hashes are |
| 24 | u32 | where the attribute start indices are |
| 28 | u32 | where the feature labels are |
| 32 | u32 | where the feature weights are |
| 36 | u32 | reserved |

- label names: `u32` string offset per label,
- transitions: `f32[labels * labels]`, read as `from * labels + to`. A pair the tagger
  never learned scores 0.
- attribute hashes: `u64[attributes]`, FNV-1a 64 of the feature string, **sorted
  ascending**, so a reader binary searches them. The exporter checks that no two
  attribute strings share a hash and refuses to write the blob if any two do.
- attribute starts: `u32[attributes + 1]`. Attribute *i* owns the weights from
  `start[i]` up to `start[i + 1]`.
- feature labels: `u16` per weight, feature weights: `f32` per weight.

A token's score for each label is the sum of the weights of every attribute the token
carries. The path score adds one transition per step. The best path is Viterbi; the
probability of that path is `exp(path score - log Z)`, with `log Z` from the forward
algorithm in log space.

## 4. Commands

| at | type | what |
| --- | --- | --- |
| 0 | u32 | number of commands |
| 4 | u32 | number of slot types |
| 8 | f32 | the cut-off below which an answer is unsure |
| 12 | f32 | the exponent on the tagger's probability, used only when section 6 carries no gate weights |
| 16 | u32 | where the command records are |
| 20 | u32 | where the command slot records are |
| 24 | u32 | where the slot type records are |
| 28 | u32 | where the value records are |
| 32 | u32 | where the surface form offsets are |
| 36 | u32 | where the word list offsets are |
| 40 | u32 | where the class to command map is |
| 44 | u32 | where the label to slot type map is |
| 48 | u32 | where the label begins flags are |
| 52 | u32 | reserved |

**Command record, 16 bytes**: `u32` name, `u32` how many slots, `u32` index of its first
slot record, `u32` reserved.

**Command slot record, 12 bytes**: `u32` name, `u32` slot type index, `u32` 1 when the
slot is required.

**Slot type record, 40 bytes**: `u32` name, `u32` kind (0 a value list, 1 a number),
`i32` minimum, `i32` maximum, `u32` bounds (bit 0 set when the minimum counts, bit 1 when
the maximum does), `u32` how many values, `u32` index of its first value record, `u32` how
many word list entries, `u32` index of its first word list entry, `u32` reserved.

**Value record, 12 bytes**: `u32` the canonical value, `u32` how many surface forms,
`u32` index of its first surface form offset.

**Surface forms**: `u32` string offsets. Each one is already lowercased with its spaces
squeezed, so matching is a plain byte comparison against the spoken words joined by one
space.

**Word list**: `u32` string offsets, the single words that appear anywhere in a slot
type's values, sorted, used for the tagger's `g=` features.

**Class to command**: `u32` per intent class, the index of the command it names, or
`0xFFFFFFFF` for the "no command" class.

**Label to slot type**: `u32` per tagger label, the slot type a `B-` or `I-` tag carries,
or `0xFFFFFFFF` for `O`. **Label begins**: `u8` per label, 1 for a `B-` tag.

## 5. Numbers

| at | type | what |
| --- | --- | --- |
| 0 | u32 | how many number words |
| 4 | u32 | how many filler words |
| 8 | u32 | where the number word records are |
| 12 | u32 | where the filler word offsets are |

**Number word record, 8 bytes**: `u32` the word, `i32` its value. Sorted by the word as
bytes, so a reader binary searches them. English and Hindi words are in the same table,
which is how `src/edgenlu/numbers.py` holds them.

Filler words are `u32` string offsets. They carry no value and are skipped while reading
a number, which is how "ek sau bees" and "one hundred and twenty" both work.

## 6. Gate

Everything the "should I trust this" number needs beyond the two models.

| at | type | what |
| --- | --- | --- |
| 0 | u32 | how many training words |
| 4 | u32 | where the training word hashes are |
| 8 | u32 | how many gate weights |
| 12 | u32 | where the gate feature ids are |
| 16 | u32 | where the gate weights are |
| 20 | f32 | the gate bias |
| 24 | u32 | how many signals this format defines, 8 today |
| 28 | u32 | reserved |

- **training word hashes**: `u32[count]`, FNV-1a 32 of every word the two models were
  trained on, **sorted ascending** and deduplicated, so a reader binary searches them. A
  word that is not here, does not read as a number and is in no slot type's word list is
  a word the model has never seen. Two words sharing a hash would make one of them look
  familiar; at a few thousand words in a 32 bit space that is a one in four thousand
  chance and the cost is one word counted wrong, so no check is made.
- **gate feature ids**: `u32[weight count]`, which signal each weight multiplies.
- **gate weights**: `f32[weight count]`.

The eight signals, in the order this format fixes, with `p` the intent probability and
`q` the tagger's sequence probability:

| id | signal |
| --- | --- |
| 0 | `log(p / (1 - p)) / 5`, with `p` pinned into 1e-6 to 1 - 1e-6 first |
| 1 | `log(q) / 5`, with `q` pinned into 1e-9 to 1 first |
| 2 | unknown words divided by all words |
| 3 | 1 when every word carrying no slot tag is unknown, else 0 |
| 4 | the top command's probability minus the second one's |
| 5 | 1 when a required slot came back missing, else 0 |
| 6 | 1 when a slot value matched nothing anybody listed, else 0 |
| 7 | 1 when the answer is "no command", else 0 |

The confidence is `1 / (1 + exp(-(bias + sum of weight times signal)))`. A blob with no
gate weights falls back to the old `p * q ** power` from section 4. The scoring recipe,
the divisor of 5 and the two floors are all in `src/edgenlu/gate.py`.

## What the format does not carry

The example sentences, the generator's word lists and the training settings. None of them
are needed to answer a question, and all of them are in the commands file.

## A note on bytes and characters

`features.py` defines the character n-grams and the prefix and suffix features over
**bytes**, not characters, so the C side is a plain byte loop. The Python trainer uses
Python string slicing, which counts characters. The two agree on ASCII input, which is
what the example specs and the held-out files are. A non-ASCII token would train and
decode slightly differently on the two sides.
