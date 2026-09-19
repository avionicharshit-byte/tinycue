# How tinycue is measured

Four test sets, because they answer different questions, and the gaps between them are
the honest picture of where the tool stands.

- **The generated test split.** `tinycue train` holds out whole phrasings before it
  generates, so no sentence in the test split grew from a phrasing the model trained on.
  This measures how well the model covers variations of wording you did write. When a
  hand-written dev file is given, every generated sentence is trained on instead and
  there is no generated split.
- **The hand-written dev sets**, `examples/*.dev.yaml`. Written by the same person, on
  the same day, as the training sentences. The temperature, the tagger power, the gate
  weights and the cut-off are all fitted on these, so they are a tuning set and their
  numbers flatter themselves.
- **The stranger sets**, `eval/stranger_*.yaml`. Written blind by somebody who had never
  seen the training sentences or the commands files, in plain everyday wording. Nothing
  is fitted on these. They were used to choose between the methods below, by aggregate
  numbers only.
- **The hand-written held-out files**, `eval/heldout_*.yaml`. These share no wording with
  the commands files and were written to be awkward on purpose: typos, missing slots,
  out-of-range numbers and deliberate synonyms ("seize", "unclamp", "terminate motion")
  that appear in no example. This is the final exam. It is run once, at the end of a
  piece of work, and never during it.

Both models were trained with

```sh
tinycue train examples/<spec>.yaml --extra examples/<spec>.extra.yaml \
    --dev examples/<spec>.dev.yaml -n 1500 --seed 0 -o out/model
```

on an M1 MacBook Air. Nothing was trained on `eval/`.

## Where the numbers stand

| | smart home | robot |
| --- | --- | --- |
| commands | 4 plus `none` | 6 plus `none` |
| hand-written examples | 56 | 72 |
| extra sentences | 476 | 503 |
| dev sentences | 94 | 99 |
| training words the models saw | 1,017 | 1,016 |
| device blob size | 249.0 KB | 294.0 KB |
| training time | 6.7 s | 7.1 s |
| chosen unsure cut-off | 0.849 | 0.887 |
| **dev set** | 94 sentences | 99 sentences |
| full command accuracy | 93.6% | 94.9% |
| slot f1, value level | 99.1% | 98.5% |
| ECE | 0.044 | 0.049 |
| sent to unsure | 17.0% | 24.2% |
| wrong answers caught | 100.0% | 80.0% |
| accepted answers right | 100.0% | 98.7% |
| **stranger set, written blind** | 139 sentences | 138 sentences |
| full command accuracy | 96.4% | 93.5% |
| slot f1, value level | 98.4% | 98.8% |
| ECE | 0.056 | 0.039 |
| sent to unsure | 22.3% | 19.6% |
| wrong answers caught | 60.0% | 77.8% |
| accepted answers right | 98.1% | 98.2% |
| **hand-written held-out set** | 144 sentences | 98 sentences |
| full command accuracy | 84.0% | 70.4% |
| slot f1, span level | 88.3% | 82.5% |
| ECE | 0.063 | 0.076 |
| sent to unsure | 40.3% | 56.1% |
| wrong answers caught | 82.6% | 96.6% |
| accepted answers right | 95.3% | 97.7% |

Reproduce a column with:

```sh
tinycue eval out/model --data examples/smart_home.dev.yaml --summary
tinycue eval out/model --data eval/stranger_smart_home.yaml --summary
```

`--summary` prints the aggregate numbers and never a sentence from the set, which is how
a set you must not read can still be measured.

## Before the sentence packs

The first numbers on the held-out files, measured before the extra sentence packs of M3d
were written and before the gate fix of M3e. They are here so the three steps can be read
end to end; [PLAN.md](../PLAN.md) M3d has the rest of that measurement.

| held-out file | smart home | robot |
| --- | --- | --- |
| full command accuracy | 75.7% | 39.8% |
| sent to unsure | 48.6% | not recorded |
| wrong answers caught | 85.7% | 94.9% |
| accepted answers right | 93.2% | 75.0% |

More sentences bought accuracy and cost honesty: accuracy rose to 84.0% and 70.4% while
wrong answers caught fell to 43.5% and 69.0%. The gate fix below put the honesty back.

## What the unsure gate fix changed

Gating never changes the answer, only whether the device shows it, so full command
accuracy is untouched. Everything else moved.

| held-out file | smart home before | after | robot before | after |
| --- | --- | --- | --- | --- |
| full command accuracy | 84.0% | 84.0% | 70.4% | 70.4% |
| sent to unsure | 11.8% | 40.3% | 34.7% | 56.1% |
| wrong answers caught | 43.5% | 82.6% | 69.0% | 96.6% |
| accepted answers right | 89.8% | 95.3% | 85.9% | 97.7% |
| ECE | 0.066 | 0.063 | 0.160 | 0.076 |
| cut-off | 0.627 | 0.849 | 0.752 | 0.887 |

The price is coverage. On the awkward held-out file the smart home model now asks again
on 4 sentences in 10 and the robot model on more than half. On the stranger sets, which
are ordinary wording, it asks again on 1 in 5.

## How the confidence is built

The intent model's probability is temperature scaled on the dev set, and the tagger
reports its own probability for the tag sequence it chose. Those two were the whole
confidence before: `intent * tagger ** power`. Both come out of the same hashed n-gram
features, and a word the model has never seen contributes nothing to those features, so
the words it does know decide alone and the answer stays confident. That is exactly the
case a gate has to catch, and the old one could not see it.

So the blob now carries the training vocabulary as a sorted array of 32 bit FNV-1a
hashes, about 4 KB, and the runtime works out five signals per sentence:

| signal | what it says |
| --- | --- |
| `log(p / (1 - p)) / 5` | how sure the intent model is, on a scale that does not saturate |
| `log(q) / 5` | how sure the tagger is about the tags it chose |
| unknown share | how many words appear in no training sentence, as a fraction |
| all carrier words unknown | 1 when every word outside a slot span is new |
| margin | the top command's probability minus the second one's |

A word counts as known when the model trained on it, when it reads as a number, or when
it appears in some slot type's value list. Three more signals are defined in the format
and computed by the runtime, a required slot missing, a slot value nobody listed, and an
answer of "no command", but the shipped models do not weigh them; see the variants below.

A logistic regression over the five gives the chance the whole answer is right, and that
is the confidence. The weights live in the blob and the C runtime computes the same
sigmoid. The two probabilities are still reported on their own, because they say
different things and a caller may want them.

The fitted weights, and the cut-off chosen from them:

| | intent | tagger | unknown share | all carriers new | margin | bias | cut-off |
| --- | --- | --- | --- | --- | --- | --- | --- |
| smart home | 2.746 | 2.093 | -1.060 | -0.432 | 0.244 | -0.058 | 0.849 |
| robot | 3.055 | 2.121 | -1.822 | -0.093 | 0.374 | 0.256 | 0.887 |

Both models learned the same shape: more evidence, more confidence; more words from
nowhere, less.

## Making the fitting data harder, honestly

A dev set written by the same person as the training sentences has almost no unfamiliar
words in it. Only 4% of the words in the smart home dev set appear in no training
sentence, so a gate fitted on it alone never learns what an unknown word costs.

`tinycue train` therefore fits the gate on the dev set plus roughed up copies of it:
three copies of every dev sentence, and one copy of each of 300 sampled generated
training sentences, with carrier words swapped for pronounceable words nobody has ever
written, misspelt, or dropped. Words inside a slot span are never touched, so the gold
answer is unchanged. When the carrier word that went was the only thing naming the
command, the model now gets the sentence wrong, and that is the useful half.

That gives roughly 680 rows per spec. The cut-off is then chosen on **out of fold**
scores: five folds, grouped by the sentence a row came from, so a copy is never scored
by a calibrator that saw its original. Without the grouping the cut-off would be fitted
on its own training data wearing a hat.

The whole thing costs about a second on top of a six second training run, and the
whole training run is still under eight seconds.

## Every variant that was scored

Seven methods, all measured on the two stranger sets, which nothing was fitted on. The
dev columns are a tuning set and flatter themselves; they are here for completeness.

**Smart home**, stranger set, 139 sentences, 5 of them answered wrongly:

| method | cut-off | dev accepted right | to unsure | wrong caught | accepted right |
| --- | --- | --- | --- | --- | --- |
| product, cut-off on dev at 0.97 (what shipped before) | 0.627 | 97.8% | 3.6% | 20.0% | 97.0% |
| product, cut-off on dev at 0.99 | 0.811 | 100.0% | 12.2% | 40.0% | 97.5% |
| gate, five signals, fitted on dev only | 0.865 | 100.0% | 15.1% | 40.0% | 97.5% |
| **gate, five signals, dev plus roughed up copies** | **0.849** | **100.0%** | **22.3%** | **60.0%** | **98.1%** |
| gate, all eight signals, dev plus copies | 0.639 | 100.0% | 14.4% | 60.0% | 98.3% |
| gate, five signals, twice the copies | 0.865 | 100.0% | 23.0% | 60.0% | 98.1% |
| gate, five signals, cut-off target 0.99 | 0.923 | 100.0% | 33.8% | 80.0% | 98.9% |
| chosen method plus "unsure when `none` and most words are new" | 0.849 | 100.0% | 23.7% | 60.0% | 98.1% |

**Robot**, stranger set, 138 sentences, 9 of them answered wrongly:

| method | cut-off | dev accepted right | to unsure | wrong caught | accepted right |
| --- | --- | --- | --- | --- | --- |
| product, cut-off on dev at 0.97 (what shipped before) | 0.752 | 97.7% | 8.7% | 55.6% | 96.8% |
| product, cut-off on dev at 0.99 | 0.884 | 100.0% | 17.4% | 77.8% | 98.2% |
| gate, five signals, fitted on dev only | 0.921 | 98.7% | 15.9% | 77.8% | 98.3% |
| **gate, five signals, dev plus roughed up copies** | **0.887** | **98.7%** | **19.6%** | **77.8%** | **98.2%** |
| gate, all eight signals, dev plus copies | 0.930 | 97.3% | 27.5% | 66.7% | 97.0% |
| gate, five signals, twice the copies | 0.886 | 98.7% | 18.1% | 66.7% | 97.3% |
| gate, five signals, cut-off target 0.99 | 0.929 | 100.0% | 26.1% | 88.9% | 99.0% |
| chosen method plus "unsure when `none` and most words are new" | 0.887 | 98.7% | 21.0% | 77.8% | 98.2% |

What the table says, read across both domains:

- The gate beats the product on both, and most of the gain is the unknown word evidence.
- Roughing up the fitting data is worth about 20 points of wrong answers caught on the
  smart home set and nothing measurable on the robot one. Doubling the copies gives
  nothing back, so three is enough.
- All eight signals is the only variant that goes backwards on a set: on the robot it
  catches fewer wrong answers than five signals while sending more to unsure. With 99
  dev sentences behind them, three more weights is three more things to overfit.
- Forcing `none` to unsure when most words are new changes nothing either way. The
  unknown-share weight already does that job, so the rule is not shipped.
- Raising the cut-off target from 0.97 to 0.99 catches more on both sets and costs 6 to
  11 points of coverage. It is not the default because the brief for the default is 0.97;
  `--cutoff-target 0.99` is one flag away and the numbers above are what it buys.

The differences between the last few rows are one or two sentences on sets with five and
nine wrong answers, so they are not worth reading finely. Three seeds of the chosen
method gave 50% to 67% caught on the smart home set and 78% to 80% on the robot one,
which is the width of the noise.

## What still fails

- **The robot model asks again on more than half of the awkward held-out sentences.**
  It is right to: its `stop`, `grab` and `release` commands carry no slots, so nothing
  but the literal words identifies them, and that file uses synonyms from nowhere in the
  commands file. But 56% unsure is a bad experience, and the fix is vocabulary, not the
  gate.
- **The gate cannot tell a hard sentence from an unlucky one.** It sees that a word is
  new, not what the word means. "seize the block" and "quorbek the block" look the same
  to it. Anything that knows "seize" and "grab" are related would need embeddings, which
  do not fit in this budget.
- **A stressed sentence is not a stranger's sentence.** Roughing up carrier words
  teaches the gate what unknown words cost, but a real person's unfamiliar phrasing also
  reorders the sentence and drops the words the model leans on. The stress copies are a
  cheap stand-in, not the real thing.
- **`none` is still where most of the loss sits**, at 78% on both dev sets, because an
  out-of-scope sentence can use any word in the language.
- **Unit phrases are out of scope.** "half an hour" and "quarter of an hour" are not
  read as 30 and 15. The number reader takes digits and number words, not units.

## Proving the C runtime against Python

`tests/test_c_parity.py` trains both example specs, exports both blobs, builds the C
command line tool, and runs every sentence of both held-out files plus 300 generated
sentences per spec through Python and through C: 842 sentences. It asserts the same
command, the same slot values, the same missing slots, the same unsure flag, the same
unknown word count, the same "all carrier words unknown" flag and the same margin, with
the confidence to within 1e-3.

It runs Python twice: once with the float32 weights it trained, and once with those
weights rounded to the float16 the blob carries, which is what the C side actually reads.

| Python reference | decision mismatches | worst confidence gap |
| --- | --- | --- |
| float16, the weights the blob carries | 0 of 842 | 5.3e-07 |
| float32, Python's own weights | 0 of 842 | 2.1e-04 |

int8 weights with a per class scale were measured too. No decision changed there either,
but the confidence moved by up to 8.1e-3, over the 1e-3 bar, so the blob ships float16.

The gate cost almost nothing. The blob grew by 4,156 bytes for the smart home model and
4,152 for the robot one, about 1.5% in both cases, and over three timed runs of 31
sentences the mean parse time on an M1 MacBook Air went from 6.49 to 6.36 microseconds
for the smart home model and 5.14 to 4.99 for the robot one, which is inside the noise.

## On the boards

Both boards were reflashed with the format 2 blob on 2026-09-19 and ran the same 31
sentences from `demo/esp32_round/sentences.txt` again. All 31 matched the desktop C tool
on both boards, with every confidence, margin, unknown word count and carrier flag
identical to all six printed decimals on both. Per-sentence tables are in
[demo/esp32_round/board-results.md](../demo/esp32_round/board-results.md) and
[demo/nxp_mcxn236/board-results.md](../demo/nxp_mcxn236/board-results.md).

The gate is not free on a small chip. The blob grew by 45,372 bytes, and the mean parse
went from 4,376 to 5,106 microseconds on the ESP32 and from 4,805 to 5,129 on the
Cortex-M33, which is the wider vocabulary and the unknown word lookup. Seven of the 31
sentences now go to unsure where five did, and the one confident wrong answer in the old
tables is caught.

The NXP board is left running the voice firmware, which carries the same runtime and blob.
Its text path was checked without audio over the same 31 sentences: 31 of 31 agreed with
the desktop, while the microphone kept streaming.
