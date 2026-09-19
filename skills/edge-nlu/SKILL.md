---
name: edge-nlu
license: Apache-2.0
description: >
  Build an offline command parser that fits on a microcontroller. The user describes
  what their device can do, you write a commands file and hundreds of example
  sentences, and edge-nlu trains a tiny intent model plus a slot tagger and exports
  plain C99 and a model blob of a few hundred kilobytes. No network, no LLM, no
  per-request cost. Use when someone wants a device to understand typed or spoken
  orders offline, when a template matcher is too rigid, when a cloud assistant is too
  slow or too expensive, or whenever edgenlu, edge-nlu or an on-device intent parser
  is mentioned. Covers the file formats, the improvement loop driven by
  `edgenlu doctor`, and dropping the runtime into firmware.
---

# Build an offline command parser with edge-nlu

The device model is small on purpose: hashed word and character n-grams into a linear
classifier, plus a linear-chain CRF that tags slot words. It has no embeddings, so it
knows only words it was shown. **Accuracy comes from writing more and better example
sentences, not from a bigger model.** That is the job.

## Install

```bash
git clone https://github.com/avionicharshit-byte/edge-nlu && cd edge-nlu
python3 -m venv .venv && .venv/bin/pip install -e .
```

## The workflow

### 1. Interview, briefly

Ask only what you cannot guess, in one message:

- What can the device do? One line per action.
- Which of those carry a value, and what are the values? Free numbers or a fixed list?
- What words do people really use for each value, including slang and misspellings?
- Which languages? Hinglish, meaning Hindi typed in Latin letters, is supported.
- What should happen when the device is not sure: ask the user, or call something bigger?

### 2. Write the commands file

```yaml
language: [en, hinglish]
slots:
  lens:                       # a value list. The canonical value is what your code gets.
    values:
      wide: [wide, wide angle, chauda]
      tele: [tele, zoom lens, telephoto]
  seconds:
    type: number
    min: 1
    max: 600
commands:
  - name: record
    slots: [seconds]
    examples:
      - "record for [ten](seconds) seconds"
      - "[das](seconds) second record karo"
  - name: pick_lens
    slots:
      - name: lens
        required: true
    examples:
      - "switch to the [wide](lens) lens"
equivalents: [[record, capture], [karo, kar do]]
none_examples: ["what is the weather", "record label of that band"]
fallback: { unsure_below: auto, none_command: true, on_unsure: ask }
extra: [camera.extra.yaml]          # optional, same as --extra
```

Rules: quote every example, or YAML reads `[wide](lens) lens lagao` as a list. One
command may use each slot type only once. `[the words](slot_name)` marks a slot span.

### 3. Write the extra sentences, answer first

This is where the accuracy comes from. Write the answer, then write sentences for it, so
every line is labelled by construction.

```yaml
- answer: pick_lens(lens=tele)
  say:
    - switch to the zoom lens
    - "give me the [long lens](lens)"     # teaches a NEW way of saying tele
    - tele lens lagao
- answer: none
  say:
    - the record label signed them
```

Markup registers the surface as a new way of saying that value: it reaches the
gazetteer, decoding **and** the exported blob, and the generator then reuses it in every
other sentence. A slot left out of the answer is simply absent from the sentence.

Aim for **300 or more sentences**, every command and `none` covered. Vary hard:

- different verbs for the same action: grab, pick up, seize, hold, lift, take
- different word orders: "zoom in", "in with the zoom", "zoom it in", "zoom in more"
- polite and blunt: "could you please ..." and one bare word
- short and long, with trailing chat: "stop i changed my mind"
- typos and speech-to-text run-ons: "recrod", "zoomin", "stopp"
- the user's languages, mixed in one sentence when that is how they talk
- **plenty of `none` near misses that reuse the command words**: "i record everything in
  a notebook", "the release date is next month", "a full stop goes at the end". This is
  the single most common gap.

### 4. Write a separate dev set

Same format, 80 or more sentences, every command and `none`. **Different phrasings, never
copied from the extra file.** It is never trained on, and its markup teaches the model
nothing, so a dev sentence full of unknown words is meant to fail and be reported.

The dev set is what the temperature, the tagger power, the gate weights and the unsure
cut-off are fitted on. A dev set that is easier than real speech gives a cut-off that
lies, so the trainer also fits on roughed up copies of it, with carrier words swapped for
words nobody has ever written. That is what teaches the gate what an unfamiliar word
costs.

### 5. Run the doctor and act on it

```bash
edgenlu doctor camera.yaml --extra camera.extra.yaml --dev camera.dev.yaml --json
```

Read, in this order:

1. `unknown_words`: words in failing sentences that appear in no training sentence. They
   can only hurt. **Write new sentences using those words. Do not paste the dev sentence
   into the training file**, or you are measuring your own homework.
2. `confusions`: pairs like `stop` read as `release`. Write sentences for both that share
   wording, so the difference is visible.
3. `slot_errors`: a value lost or read wrong. Mark the new wording as
   `[the words](slot)` in an extra sentence.
4. `thin_commands`: fewer than 12 phrasings is running on luck.
5. `confident_mistakes`: wrong and sure of it, so the user is never asked. Worst kind.

Repeat until the numbers stop moving. **If a round makes the numbers worse, throw that
round away** rather than piling more on top. Three or four rounds is normal.

### 6. Train, export, ship

```bash
edgenlu train camera.yaml --extra camera.extra.yaml --dev camera.dev.yaml -o out/model
edgenlu export out/model -o out/device
```

`out/device` holds `model.bin` plus `model_data.c` and `model_data.h`, the same bytes as
a C array. Copy `runtime/edgenlu.c`, `runtime/edgenlu.h` and those two files into the
firmware. No dependencies beyond libm.

```c
#include "edgenlu.h"
#include "model_data.h"
static enlu_model model;                       /* pointers into flash, no copy */
enlu_init(&model, enlu_model_data, enlu_model_data_len);
static uint8_t scratch[6000];                  /* >= enlu_scratch_size(&model) */
enlu_result r;
if (enlu_parse(&model, line, &r, scratch, sizeof scratch) == ENLU_OK && !r.unsure) {
    /* r.command, r.slots[i].name / .text / .number / .known, r.missing[i] */
}
```

Handle `r.unsure` and `r.missing_count` every time. Unsure means ask the user ("did you
mean ...?") or hand the sentence to something bigger if there is a network. A device that
acts on an unsure answer is worse than one that asks.

## Command reference

| Command | What it does |
|---|---|
| `edgenlu check FILE [--extra F]` | validate and summarise, before training |
| `edgenlu train FILE [--extra F] [--dev F] [-n 1500] [--cutoff-target 0.97] -o DIR` | train, calibrate, save |
| `edgenlu doctor FILE [--extra F] --dev F [--json]` | train and say where it is weak |
| `edgenlu eval DIR --data FILE [--summary]` | measure on a held-out set |
| `edgenlu export DIR -o DIR` | write `model.bin` and the C arrays |
| `edgenlu parse DIR "sentence"` | try one sentence on the desktop |
| `edgenlu generate FILE -n 200` | dump the training sentences as JSON lines |

`--cutoff-target` is the share of accepted answers that must be right; the trainer picks
the lowest cut-off that reaches it on out-of-fold scores and prints the whole trade-off
table. The default is 0.97; 0.99 catches more wrong answers and asks again more often.

`edgenlu eval` reads a set written answer first as well as the older marked-up layout.
An answer-first set is scored on the command and the slot values, not on where the spans
fell, so a wording your commands file has never listed still counts.

`edgenlu parse DIR --json "sentence"` prints the answer with the evidence behind the
confidence, including `unknown_share`, the share of words in no training sentence. The C
tool prints the same fields.

## What to expect

Measured on the two example devices, on a classic ESP32 and an NXP Cortex-M33:

- model blob 200 to 400 KB, growing with vocabulary; the budget is 1 MB
- 2.5 to 8.5 ms per sentence on a 150 MHz Cortex-M33, mean about 5.1 ms
- 282 KB of flash and 21 KB of static RAM for the runtime and its tables
- 6 microseconds per sentence on a laptop
- training takes seconds, on a CPU, with no GPU

With a few hundred varied sentences, expect roughly 85% of held-out commands fully right,
an unsure path that catches 80% or more of the rest, and accepted answers right about 95%
of the time. With only the dozen examples in a commands file, expect half that. The
awkwarder the test set, the more goes to unsure: on a set written to be difficult that can
be half of it.

## Common mistakes

- **Unquoted sentences.** A line starting with `[` is a YAML list. Quote everything.
- **Copying dev sentences into the training files.** The numbers go up and the device
  gets no better.
- **Too few `none` sentences.** Without near misses that reuse the command words, the
  device will confidently act on small talk.
- **Testing on the final set while iterating.** Keep one set you touch twice: once for a
  baseline, once at the end. `doctor` and `train` refuse files under `eval/` named
  `heldout*` or `stranger*`.
- **Teaching a carrier word as a slot value.** Marking `[speed up](rate)` puts "speed"
  into the slot word list and the tagger then fires on every sentence that says "speed".
  Mark the value word, and let the carrier stay a carrier.
- **A slot value that no word in the sentence says.** The loader refuses the line and
  names it. Either mark the words, or leave that slot out of the answer and let the
  device report it missing.
- **Chasing one dev sentence.** Fix the pattern, not the line.
