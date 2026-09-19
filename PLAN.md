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
- [x] **M3**: ESP32 demo with the round display and the "did you mean" screen. On a classic ESP32,
  540,632 bytes of flash (41% of the app partition), 40,828 bytes of static RAM, 310,552 bytes of
  heap left, and 2,537 to 6,534 microseconds a sentence, mean 4,376. All 31 board sentences matched
  the desktop C tool and Python. Numbers in `demo/esp32_round/board-results.md`. The screen drawing
  is untested by eye.
- [x] **M3b**: a second chip, to prove the runtime is portable and not quietly written for the
  ESP32. An NXP FRDM-MCXN236, Arm Cortex-M33 at 150 MHz, bare metal, no RTOS: 241,352 bytes of
  flash (23%), 20,976 bytes of static RAM, and 2,318 to 7,873 microseconds a sentence, mean 4,805.
  The same 31 sentences, all 31 matching the desktop C tool with the confidence identical to six
  decimals. Nothing in `runtime/` had to change and the build is warning free. Numbers in
  `demo/nxp_mcxn236/board-results.md`.
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
- [ ] **M4**: README, a Hinglish example pack, and the first release.

## Numbers to aim for

- Model file under 1 MB. **Met**: the device blob is 245 KB for the smart home example and 290 KB
  for the robot one, up from 205 KB and 263 KB before the extra sentences widened the vocabulary.
  The Python bundle, which keeps float32 weights, is 504 KB and 543 KB.
- Under 10 ms per command on a classic ESP32. **Met**: 2.5 to 6.5 ms over 31 sentences, mean
  4.4 ms, timed around the parse alone on the real board. Met on an Arm Cortex-M33 too, at
  2.3 to 7.9 ms, mean 4.8 ms. On a laptop the bigger models cost 7.5 and 5.7 microseconds a
  sentence, against 6.6 before, so the board numbers should barely move.
- Over 95% intent accuracy on held-out phrasing the model has not seen. **Not met, much closer**:
  86.8% on the smart home held-out file and 72.4% on the robot one, from 77% and 43%.
- The unsure path catches most of the answers that would have been wrong. **Not met, and it went
  backwards**: 43.5% on the smart home held-out file and 69.0% on the robot one, from 86% and 95%.
  The cut-off is now fitted on a hand-written dev set rather than generated data, and it lands at
  0.627 and 0.751 rather than 0.903 and 0.852. Far more answers are accepted, 88% and 65% of them
  rather than 51% and 12%, and the accepted ones are right 89.8% and 85.9% of the time against a
  97% target. The dev sets are still easier than the held-out files, so the cut-off fitted on them
  is too generous. See the open question below.

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
  split.~~ Half settled in M3d: `--dev` takes a hand-written set and the cut-off is fitted on that.
  **What is left is the harder half.** A dev set written by the same person, on the same day, as
  the training sentences is still easier than one written by someone else: 93.6% and 95.0% on dev
  against 84.0% and 70.4% on held-out. So the cut-off fitted on dev is too low and only 43.5% and
  69.0% of wrong answers are caught. Two ideas worth trying: fit the cut-off with a margin, so the
  dev target is higher than the target you actually want, or hold out a slice of the dev set from
  the cut-off fit the way the training data is held out from training.
- How to close the gap on unseen wording. M3d moved it a long way with sentences alone, 77% to 87%
  and 43% to 72% on commands, and showed that the lever is vocabulary: `doctor` names words in
  failing sentences that appear nowhere in training, and writing sentences that use them is what
  paid. What is still missing is anything that knows "seize" and "grab" are related without being
  told. Both remaining gaps are the same gap: the `none` class, at 78% on both dev sets, is where
  most of the loss now sits, because an out-of-scope sentence can use any word in the language.
