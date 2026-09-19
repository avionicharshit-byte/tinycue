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
- A classic ESP32 and a Raspberry Pi as the test boards.
- Our own hand-written example sentences, in English and Hinglish.
- No GPU and no money.

## Milestones

- [x] **M0**: commands file format, parser and example generator.
- [x] **M1**: Python train and eval command line tool, with a calibration report: reliability
  buckets, the chosen cut-off, what share of inputs go to the fallback and what share of wrong
  answers that catches. Measured numbers are in [README.md](README.md).
- [ ] **M2**: the C99 runtime, proved bit-exact against the Python model on a desktop.
  The feature definition it has to reproduce is written out in `src/edgenlu/features.py`.
- [ ] **M3**: ESP32 demo with the round display and the "did you mean" screen, with measured flash,
  RAM and latency.
- [ ] **M4**: README, a Hinglish example pack, and the first release.

## Numbers to aim for

- Model file under 1 MB. **Met**: 305 KB for the smart home example, 376 KB for the robot one.
- Under 10 ms per command on a classic ESP32. Not measured yet, that is M3.
- Over 95% intent accuracy on held-out phrasing the model has not seen. **Not met**: 77% on the
  smart home held-out file and 43% on the robot one.
- The unsure path catches most of the answers that would have been wrong. **Met**: 97% on the
  smart home held-out file and 95% on the robot one.

## Open questions

- What to call the project. `edge-nlu` is a working name only.
- Licence. Apache-2.0 or MIT; Apache-2.0 is the safer default because of the patent grant.
- ~~How to handle numbers.~~ Settled in M0 and widened in M1: a table of English and Hindi number
  words covers 0 to 180 in both directions, and composed forms like "ek sau bees" work.
- How open-vocabulary slots behave. A free-text name the user invented is not in any value list,
  so the CRF has to tag it from context alone. Decoding passes the words straight through when the
  value is not listed, but how often the tagger finds them, and what confidence to report, is
  still unmeasured.
- How to close the gap on unseen wording. M1 measured it: on hand-written held-out phrasings the
  smart home model gets 77% of intents right and the robot model 43%, because hashed n-grams carry
  no idea that "seize" and "grab" are related. More example sentences is the only lever we have
  today.
