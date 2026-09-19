# edge-nlu (working name)

## What it is

An open-source tool that lets a tiny device understand typed or spoken commands fully offline.
You write a file of example sentences, it trains two small models on your laptop in seconds, and it
exports plain C99 code plus a model file under 1 MB that runs on an ESP32, a Raspberry Pi or an
Android phone.
No LLM, no network, no per-request cost.

## Who it is for

- Smart home switches, lights, fans and plugs that should keep working when the internet is down.
- Robots and toys that take spoken orders from a child or a hobbyist.
- Offline gadgets in farms and factories, where there is no reliable network and no cloud budget.
- Apps that want instant command parsing and do not want to pay an LLM for every request.

## Prior art and the gap

- **Snips NLU**: same recipe (logistic regression plus CRF), written in Python and Rust. Abandoned
  since around 2020 and never aimed at microcontrollers.
- **Rhasspy / hassil**: open source and offline, but it matches templates. It needs a Pi and it
  does not handle phrasing it has never seen.
- **Picovoice Rhino**: runs on microcontrollers, but it is closed source and paid.
- **Espressif ESP-SR MultiNet**: runs on the ESP32, but the phrase list is fixed, there are no
  slots with free values, and it is English and Chinese only.
- **cactus-compute/needle**: a 45M parameter tool-calling LLM. It takes seconds on a small board
  and is far too big for a microcontroller.
- **Jev (TypeSafe) and its open rebuilds**: cloud or GPU scale, not embedded.

**The gap**: nothing is open source *and* microcontroller sized *and* able to generalise from a few
examples *and* honest about its own confidence with a clear "unsure" path *and* good at Hinglish.

## How it works

1. You write a commands file: the commands you want, the slots they take, and a handful of example
   sentences for each.
2. A generator expands those examples into many more by swapping in every slot value and synonym.
3. Training runs on a laptop in seconds: an intent model (a linear model over word and character
   n-gram features) picks which command was meant, and a linear-chain CRF tags each word so slot
   values can be pulled out.
4. Calibration makes the confidence number honest, then picks the cut-off below which the answer
   is "unsure".
5. Export writes plain C99 source plus a model blob, together under 1 MB.
6. On the device: text goes in, and either
   `set_light(room=bedroom, state=on, confidence=0.94)` comes out, or the answer is `unsure`.

## What we need

- Python 3, python-crfsuite and scikit-learn for the training side.
- CRFsuite and liblbfgs C sources from upstream, vendored so the device runtime has no
  dependencies.
- A classic ESP32, an Arm Cortex-M33 board and a Raspberry Pi as the test boards.
- Our own hand-written example sentences, in English and Hinglish.
- No GPU and no money.

## Milestones

- [x] **M0**: commands file format, parser and example generator.
- [x] **M1**: Python train and eval command line tool, with a calibration report: reliability
  buckets, the chosen cut-off, what share of inputs go to the fallback and what share of wrong
  answers that catches. Measured numbers are in [README.md](README.md).
- [x] **M2**: the C99 runtime, proved against the Python model on a desktop. 842 sentences, every
  one of both held-out files plus 300 generated per spec, gave 0 mismatches on command, slot
  values, missing slots and unsure flag, with the worst confidence gap 7.8e-07 against Python
  reading the same float16 weights and 2.3e-04 against Python's own float32. Mean 7.5 microseconds
  a sentence on an M1 MacBook Air. Blob 205 KB for the smart home model, 263 KB for the robot one.
  The format is in `docs/model-format.md`.
- [x] **M3**: ESP32 demo with the round display and the "did you mean" screen. Re-measured on the
  format 2 blob: 588,492 bytes of flash (44% of the app partition), 40,884 bytes of static RAM,
  310,496 bytes of heap left, and 2,995 to 7,866 microseconds a sentence, mean 5,106. All 31 board
  sentences matched the desktop C tool and Python. On the format 1 blob it was 540,632 bytes of
  flash, 40,828 of static RAM and a mean of 4,376. Numbers in
  `demo/esp32_round/board-results.md`. The screen drawing is untested by eye.
- [x] **M3b**: a second chip, to prove the runtime is portable and not quietly written for the
  ESP32. An NXP FRDM-MCXN236, Arm Cortex-M33 at 150 MHz, bare metal, no RTOS. Re-measured on the
  format 2 blob: 289,064 bytes of flash (28%), 21,032 bytes of static RAM, and 2,524 to 8,455
  microseconds a sentence, mean 5,129, against 241,352 bytes of flash and a mean of 4,805 on the
  format 1 blob. The same 31 sentences, all 31 matching the desktop C tool with the confidence
  identical to six decimals. Nothing in `runtime/` had to change and the build is warning free.
  Numbers in `demo/nxp_mcxn236/board-results.md`.
- [x] **M3c**: a voice demo across both boards at once. The FRDM-MCXN236 streams its microphone
  at 16 kHz over the MCU-Link serial port at 1 Mbaud, framed, with 0 lost packets in a 30 second
  counter test and 3 times the headroom the audio needs; the Mac runs Vosk against a word list
  built from the commands file; both boards parse the sentence. Five commands played out loud were
  all right, 795 to 1,099 ms from the end of speech to the answer, of which 44 ms was the boards.
  Speech to text runs on the Mac, not on a chip. Numbers in `demo/voice/README.md`.
- [x] **M3d**: sentences written at build time, and the tooling that says which ones to write.
  Three new inputs and one new command:
  - `--extra FILE`, an answer-first file. The answer is written first and the sentences are
    written for it, so every line is labelled by construction. `[the words](slot)` markup teaches
    a new way of saying a value and it reaches the gazetteer, decoding and the exported blob.
  - `--dev FILE`, the same format. The temperature, the tagger power and the cut-off are fitted on
    it instead of a slice of generated data, and it is never trained on. The cut-off rule is now
    the lowest one whose accepted answers are right at least `--cutoff-target` of the time,
    default 0.97, printed with the whole trade-off table.
  - `edgenlu doctor SPEC --extra F --dev F [--json]`: per command accuracy, confused pairs, slot
    value errors, **words in failing sentences that no training sentence has**, confidently wrong
    sentences, thin commands, and a short list of what to write next. It refuses a file under
    `eval/` named `heldout*` unless told twice.
  - A Claude Code plugin and skill, `.claude-plugin/` and `skills/edge-nlu/SKILL.md`, that teaches
    an agent the whole loop.

  Measured. 476 extra and 94 dev sentences for the smart home example, 503 and 99 for the robot
  one, written from scratch, English and Hinglish. Four doctor rounds each. On the **held-out**
  files, measured once at the start and once at the end:

  | | smart home | robot |
  |---|---|---|
  | full command accuracy | 75.7% to **84.0%** | 39.8% to **70.4%** |
  | command accuracy | 77.1% to **86.8%** | 42.9% to **72.4%** |
  | slot f1 | 88.8% to 88.3% | 78.9% to 82.5% |
  | ece | 0.090 to 0.066 | 0.166 to 0.160 |
  | device blob | 205 KB to 245 KB | 263 KB to 290 KB |

  On the hand-written dev sets the same models get 93.6% and 95.0% full command accuracy. The gap
  between dev and held-out is the honest measure of how much a dev set flatters itself.
- [x] **M3e**: an unsure cut-off that holds up on wording nobody wrote.

  The old confidence was the intent probability times the tagger's own probability. Both
  come from the same hashed n-gram features, so a word the model has never seen
  contributes nothing and the words it does know decide alone, confidently. The blob now
  carries the training vocabulary as a sorted array of 32 bit FNV-1a hashes, about 4 KB,
  and the device counts the words of a sentence it has never seen. A logistic regression
  over five signals, the intent logit, the tagger's log probability, the unknown word
  share, a flag for "every word outside a slot span is new" and the gap between the top
  two commands, gives the chance the whole answer is right, and that is the confidence.
  Five weights and a bias, in the blob, computed the same way in C.

  The gate is fitted on the hand-written dev set plus roughed up copies of it, where
  carrier words are swapped for words nobody has ever written, misspelt or dropped and
  the gold answer is unchanged. The cut-off is then chosen on out-of-fold scores, five
  folds grouped by source sentence, for a target of 97% accepted accuracy. Blob format
  bumped to 2.

  Three new pieces of tooling came with it: `edgenlu eval` reads an answer-first file
  without needing the spans marked, so a set whose wording the commands file has never
  listed can still be scored on the command and the slot values; `edgenlu parse --json`
  and the C tool both report `unknown_share`; and `edgenlu doctor` says in plain words
  how honest the confidence is. The held-out guard now also refuses `eval/stranger*`.

  Measured on the **held-out** files, once:

  | | smart home | robot |
  |---|---|---|
  | full command accuracy | 84.0%, unchanged | 70.4%, unchanged |
  | sent to unsure | 11.8% to **40.3%** | 34.7% to **56.1%** |
  | wrong answers caught | 43.5% to **82.6%** | 69.0% to **96.6%** |
  | accepted answers right | 89.8% to **95.3%** | 85.9% to **97.7%** |
  | ece | 0.066 to **0.063** | 0.160 to **0.076** |
  | device blob | 245 KB to 249 KB | 290 KB to 294 KB |

  On the **stranger** sets, 139 and 138 sentences written blind by somebody who had never
  seen the training sentences and which nothing was fitted on, the accepted answers are
  right 98.1% and 98.2% of the time with 22.3% and 19.6% going to unsure. Every variant
  that was scored is in `docs/accuracy.md`.
- [x] **M3f**: packaging, so somebody who is not us can install it and get to a working
  device without cloning anything.

  The Python side. `pip install "git+https://github.com/avionicharshit-byte/edge-nlu"`
  gives a working `edgenlu` command. The wheel carries the language word lists, the
  starter templates and both C runtime sources, the last force-included from `runtime/`
  at build time so there is one copy in git and never two that can drift.
  `edgenlu export --with-runtime` copies `edgenlu.h` and `edgenlu.c` out of the installed
  package next to the model, which makes the exported folder the whole device side.
  `edgenlu init NAME` writes three commented starter files for a toy coffee machine, a
  domain that is neither example, so the domain-free guard still holds. `edgenlu --version`
  and full metadata in `pyproject.toml`.

  Proved, not assumed: a wheel built with `uv build`, installed into a throwaway venv
  outside the repo, then `init`, `check`, `train` (3.3 s), `parse` on four sentences and
  `export --with-runtime` run from an empty folder. The exported folder plus a 15 line
  main compiled with `cc -std=c99 -Wall -Wextra -pedantic` with no warnings and answered
  `brew confidence 0.99 unsure 0`. `tests/test_packaging.py` builds a wheel and checks the
  language files, the templates and the runtime sources are in it.

  The Arduino side. `arduino/EdgeNLU/` is a standard library: `library.properties`,
  `keywords.txt`, the runtime under `src/` and a board-agnostic `SerialCommands` example
  with the starter model as `model_data.c`. The runtime copies are committed rather than
  synced, because a ZIP download of the repo has to work, so
  `tests/test_arduino_library.py` fails when they drift from `runtime/` and
  `make arduino-sync` fixes it. `make arduino-example-model` rebuilds the example model.

  Measured with `arduino-cli 1.5.1`: the example builds for `esp32:esp32:esp32` at
  353,252 bytes of flash (26%) and 35,004 bytes of RAM (10%), and for
  `arduino:mbed_nano:nano33ble` at 171,104 bytes (17%) and 57,448 bytes (21%). It does
  **not** build for `arduino:avr:mega`: `size of array is too large`, because the model
  array is past what 8 bit AVR can address, and its 8 KB of SRAM could not hold the
  scratch buffer either. The realistic minimum is a 32 bit MCU with about 300 KB of free
  flash and 12 KB of RAM.

  No wrapper header named `EdgeNLU.h` ships. The runtime header is `edgenlu.h` and macOS
  filesystems are case insensitive, so the two cannot sit in the same folder. Sketches
  include `<edgenlu.h>`, which already carries its own `extern "C"` guards.

  Nothing was published: not PyPI, not the Arduino library registry, and the repository
  is still private.
- [ ] **M4**: README, a Hinglish example pack, and the first release.

## Numbers to aim for

- Model file under 1 MB. **Met**: the device blob is 249 KB for the smart home example and 294 KB
  for the robot one, up from 205 KB and 263 KB before the extra sentences widened the vocabulary
  and the gate added about 4 KB of training word hashes.
  The Python bundle, which keeps float32 weights, is 504 KB and 543 KB.
- Under 10 ms per command on a classic ESP32. **Met**: 3.0 to 7.9 ms over 31 sentences, mean
  5.1 ms, timed around the parse alone on the real board with the format 2 blob. Met on an Arm
  Cortex-M33 too, at 2.5 to 8.5 ms, mean 5.1 ms. The wider vocabulary and the unknown word lookup
  cost both boards about 0.5 ms of the mean, and the slowest sentence on the M33 is now 8.5 ms,
  which is the least room this target has had.
- Over 95% intent accuracy on held-out phrasing the model has not seen. **Not met, much closer**:
  86.8% on the smart home held-out file and 72.4% on the robot one, from 77% and 43%.
- The unsure path catches most of the answers that would have been wrong. **Met on the robot,
  close on the smart home**: 82.6% on the smart home held-out file and 96.6% on the robot one,
  up from 43.5% and 69.0%. The accepted answers are right 95.3% and 97.7% of the time against a
  97% target. What it costs is coverage: 40.3% and 56.1% of that file goes to unsure, against
  22.3% and 19.6% on the stranger sets, which are ordinary wording. See M3e.

## Open questions

- **Hinglish cannot be spoken to this demo.** Vosk's English models do not carry the Hinglish
  words, and a word outside a grammar-constrained recogniser's word list can never be produced,
  however clearly it is said. So the tool's best feature is invisible through a microphone today.
  It needs a recogniser with a Hindi or code-mixed lexicon, or proxy spellings that an English
  lexicon can reach. Typed Hinglish still works.
- What to call the project. `edge-nlu` is a working name only.
- Licence. Apache-2.0 or MIT; Apache-2.0 is the safer default because of the patent grant.
- ~~How to handle numbers.~~ Settled in M0 and widened in M1: a table of English and Hindi number
  words covers 0 to 180 in both directions, and composed forms like "ek sau bees" work.
- How open-vocabulary slots behave. A free-text name the user invented is not in any value list,
  so the CRF has to tag it from context alone. Decoding passes the words straight through when the
  value is not listed, but how often the tagger finds them, and what confidence to report, is
  still unmeasured.
- ~~How to pick the cut-off on data that looks like the held-out file rather than the generated dev
  split.~~ Settled in M3e. Neither idea written here was the answer on its own. What worked was
  giving the gate something the two probabilities could not see, whether the sentence uses words
  the model has never met, and then making the fitting data harder on purpose by roughing up the
  dev set, so the gate could learn what those words cost. The cut-off is chosen on out-of-fold
  scores over that harder set, which is where the margin comes from. Raising the target from 0.97
  to 0.99 buys more caught wrong answers for 6 to 11 points of coverage and is one flag away.
- **Coverage on awkward wording.** The gate is honest now and the price is that the robot model
  asks again on 56% of its held-out file. The lever is vocabulary, not the cut-off: the doctor
  names the words, and writing sentences that use them is what moves it.
- How to close the gap on unseen wording. M3d moved it a long way with sentences alone, 77% to 87%
  and 43% to 72% on commands, and showed that the lever is vocabulary: `doctor` names words in
  failing sentences that appear nowhere in training, and writing sentences that use them is what
  paid. What is still missing is anything that knows "seize" and "grab" are related without being
  told. Both remaining gaps are the same gap: the `none` class, at 78% on both dev sets, is where
  most of the loss now sits, because an out-of-scope sentence can use any word in the language.
