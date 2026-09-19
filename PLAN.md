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
- [ ] **M4**: README, a Hinglish example pack, and the first release.

## Numbers to aim for

- Model file under 1 MB. **Met**: the device blob is 205 KB for the smart home example and 263 KB
  for the robot one. The Python bundle, which keeps float32 weights, is 305 KB and 376 KB.
- Under 10 ms per command on a classic ESP32. **Met**: 2.5 to 6.5 ms over 31 sentences, mean
  4.4 ms, timed around the parse alone on the real board. Met on an Arm Cortex-M33 too, at
  2.3 to 7.9 ms, mean 4.8 ms.
- Over 95% intent accuracy on held-out phrasing the model has not seen. **Not met**: 77% on the
  smart home held-out file and 43% on the robot one.
- The unsure path catches most of the answers that would have been wrong. **Half met**: 95% on the
  robot held-out file, but only 86% on the smart home one since the cut-off refitted to 0.903. The
  cut-off is chosen on generated dev data, and the held-out file is harder than that data.

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
- How to pick the cut-off on data that looks like the held-out file rather than the generated dev
  split. Today the cut-off is fitted on generated sentences, and it is too generous for wording the
  model has never seen.
- How to close the gap on unseen wording. M1 measured it: on hand-written held-out phrasings the
  smart home model gets 77% of intents right and the robot model 43%, because hashed n-grams carry
  no idea that "seize" and "grab" are related. More example sentences is the only lever we have
  today.
