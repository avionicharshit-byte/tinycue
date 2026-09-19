# edge-nlu

Turn a short list of example commands into a 249 KB model and two C files, so a
microcontroller understands typed or transcribed English and Hinglish commands with no
network, no LLM and no per-request cost.

What you get from one YAML file:

- A model trained on your laptop in about 6 seconds, exported as one flat blob plus C99
  source. No malloc, no file IO, and the weights are read in place, in flash.
- An answer in single digit milliseconds on the boards below, 6.4 microseconds on a desktop.
- A calibrated confidence on every answer, and a tuned cut-off below which the device says
  "unsure" instead of guessing. Ask again, or hand the sentence to something bigger. The
  device counts the words of your sentence the model has never seen, and says so.
- English and Hinglish, Hindi typed in Latin letters, including number words. "10", "ten"
  and "das" all arrive as `10`.

edge-nlu is not speech to text. Any recogniser can feed it text; see
[the voice demo](demo/voice) for one that does. **Status: early, private preview.** It is
measured and it works, but the APIs and the model format may still change.

![How edge-nlu works](diagrams/edge-nlu-flow.png)

## Thirty seconds with it

```sh
pip install "git+https://github.com/avionicharshit-byte/edge-nlu"   # not on PyPI yet
edgenlu init coffee
```

`init` writes three commented starter files for a toy coffee machine, so there is
something that trains and answers before you have invented anything. The whole path,
install to a compiled device folder, is in [docs/install.md](docs/install.md).

You write the commands and the words people use for them, with slot spans marked
`[surface text](slot_name)`. This is the smart home example that ships:

```yaml
language: [en, hinglish]
slots:
  room:
    values:
      bedroom: [bedroom, bed room, sone ka kamra, kamre]
      kitchen: [kitchen, rasoi]
  state:
    values:
      "on": ["on", chalu, jala do, jalao]
      "off": ["off", band, bujha do]
commands:
  - name: set_light
    slots: [room, state]
    examples:
      - "turn [on](state) the [bedroom](room) light"
      - "[rasoi](room) mein light [jala do](state)"
```

Then three commands, and you can read sentences:

```sh
edgenlu check examples/smart_home.yaml
edgenlu train examples/smart_home.yaml --extra examples/smart_home.extra.yaml \
    --dev examples/smart_home.dev.yaml -n 1500 --seed 0 -o out/model
edgenlu export out/model -o out/device --with-runtime
```

```
$ edgenlu parse out/model "turn on the bedroom light"
set_light(state=on, room=bedroom) confidence=1.00
$ edgenlu parse out/model "rasoi mein light jala do"
set_light(room=kitchen, state=on) confidence=0.99
$ edgenlu parse out/model "i am a big fan of cricket"
unsure (best guess: none confidence=0.84)
$ edgenlu parse out/model "flick the bedroom lamp on"
set_light(room=bedroom, state=on) confidence=0.95
```

The off-topic sentence was named as off-topic, not forced into the nearest command, and
still went to unsure, because `none` is the class the model is least sure of. "flick" and
"lamp" are in the extra sentences now, so the last one is read and accepted.

The rest of the format, `equivalents`, `fillers`, `none_examples` and `fallback`, is in
[docs/commands-file.md](docs/commands-file.md). Two worked examples ship, commented line
by line: [smart home](examples/smart_home.yaml) and [robot](examples/robot.yaml).

## How it works

The **intent** is which command was meant, `none` for anything off-topic. The **slots** are
the values it carries: which room, how many minutes.

On your laptop a generator grows your handful of examples into thousands, swapping slot
values, synonyms and equivalent words and inserting fillers, never touching a token inside
a slot span. A linear classifier over word and character n-gram features picks the intent,
a linear-chain CRF labels each word so slot values can be pulled out, and calibration makes
the confidence honest and fits the cut-off. Tags carry the slot *type*, not the name, so
two commands that both take a `direction` share its training examples. Export writes one
blob plus the same bytes as C source ([docs/model-format.md](docs/model-format.md)).

On the device a command, its slot values, any required slot left out and a confidence come
back, `unsure` set under the cut-off with the best guess still filled in. A value the
tagger finds but nobody listed comes through as text with `known` set to 0, which is how an
invented name survives.

## Boards tested

The same `runtime/edgenlu.c` and the same smart home model on both, over the same 31
sentences. Nothing in `runtime/` was changed to port it to the second chip.

| | classic ESP32 | NXP FRDM-MCXN236 |
| --- | --- | --- |
| core | Xtensa LX6, 240 MHz | Arm Cortex-M33, 150 MHz |
| flash image | 588,492 bytes, 44% of the app partition | 289,064 bytes, 28% of 1 MB |
| static RAM | 40,884 bytes | 21,032 bytes |
| model blob, in flash | 255,012 bytes | 255,012 bytes |
| scratch buffer | 10,744 bytes | 10,744 bytes |
| parse: fastest, mean, slowest | 2,995 / 5,106 / 7,866 us | 2,524 / 5,129 / 8,455 us |
| sentences matching the desktop | 31 of 31 | 31 of 31 |
| free heap after 31 sentences | unchanged from boot | no heap at all |

The flash images are not the same measurement: the ESP32 one carries the Arduino core and
the display library, the MCXN236 one four NXP drivers. Both are timed around `enlu_parse`
alone, on the board, with `-DENLU_FAST_EXP`. Per-sentence tables:
[ESP32](demo/esp32_round/board-results.md),
[FRDM-MCXN236](demo/nxp_mcxn236/board-results.md).

Three demos run on these boards: the ESP32 round display, the NXP board bare metal, and
offline speech to text on a Mac feeding both. How to run each, what that build flag is
worth, and what they measured: [docs/demos.md](docs/demos.md).

## Use it in your firmware

`edgenlu export --with-runtime` leaves four files in one folder: `edgenlu.h`, `edgenlu.c`
and the `model_data.c` and `model_data.h` it wrote. Add the `.c` files to your build.
Nothing else to link, and nothing else to clone.

```c
#include <stdio.h>
#include "edgenlu.h"      /* the runtime, two files, nothing else */
#include "model_data.h"   /* enlu_model_data[] and its length, written by edgenlu export */
void ask_again(void);     /* your own "did you mean ...?" prompt */

static enlu_model model;
static enlu_result out;
static uint8_t scratch[12288] __attribute__((aligned(8)));

int nlu_begin(void)                     /* once, at boot */
{
    if (enlu_init(&model, enlu_model_data, enlu_model_data_len) != ENLU_OK) return -1;
    return enlu_scratch_size(&model) <= sizeof scratch ? 0 : -1;
}

void nlu_handle(const char *sentence)   /* once per sentence */
{
    int i;
    if (enlu_parse(&model, sentence, &out, scratch, sizeof scratch) != ENLU_OK) return;
    if (out.unsure || out.is_none) { ask_again(); return; }
    printf("%s at %.2f\n", out.command, out.confidence);
    for (i = 0; i < out.slot_count; i++)
        if (out.slots[i].is_number) printf("  %s = %d\n", out.slots[i].name, (int)out.slots[i].number);
        else                        printf("  %s = %s\n", out.slots[i].name, out.slots[i].text);
    for (i = 0; i < out.missing_count; i++) printf("  %s missing\n", out.missing[i]);
}
```

`enlu_scratch_size` reports what the model needs, so the buffer above has room to spare.
Both calls return a negative code on refusal, which `enlu_error` turns into text. Nothing
after `enlu_init` allocates or opens a file, and `make cli` builds the same runtime for the
desktop.

For Arduino there is a library at [arduino/EdgeNLU](arduino/EdgeNLU), not in the registry
yet, so copy the folder into your sketchbook. Its **SerialCommands** example needs no
display and carries the starter model: 353,252 bytes of flash and 35,004 of RAM on a
classic ESP32, 171,104 and 57,448 on an Arduino Nano 33 BLE. An 8 bit AVR cannot build it.

## Accuracy and limits

Two sets nothing is fitted on. The **held-out files** in `eval/heldout_*.yaml` were written
to be awkward: typos, missing slots, out-of-range numbers and deliberate synonyms. The
**stranger sets** in `eval/stranger_*.yaml` were written blind by somebody who had never
seen the training sentences, in plain everyday wording. Both are run with `--summary`, which
prints the numbers and never a sentence.

<!-- ACCURACY-UPDATE -->

| smart home model, held-out file | before the sentence packs | after the packs | after the gate fix |
| --- | --- | --- | --- |
| full command accuracy | 75.7% | 84.0% | 84.0% |
| sent to unsure | 48.6% | 11.8% | 40.3% |
| wrong answers caught by the cut-off | 85.7% | 43.5% | 82.6% |
| accepted answers right | 93.2% | 89.8% | 95.3% |

The robot model, on its own held-out file, went the same way: 39.8% to 70.4% full command
accuracy over the same three steps, with wrong answers caught at 94.9%, then 69.0%, then
96.6%, and accepted answers right at 75.0%, then 85.9%, then 97.7%. More sentences bought
accuracy and quietly cost honesty; the gate fix bought the honesty back.

Gating never changes the answer, only whether the device shows it. On the stranger sets,
ordinary wording written by somebody else, the smart home model gets 96.4% of commands fully
right, asks again on 22.3% and the answers it accepts are right 98.1% of the time; the robot
model gets 93.5%, asks again on 19.6% and accepts answers that are right 98.2% of the time.
The awkward held-out files cost much more coverage: 40.3% and 56.1% go to unsure there.

Three limits. Input is capped at 32 tokens and 256 bytes, and longer input is refused
rather than truncated. Hinglish is typed only until there is a recogniser with a code-mixed
lexicon. And the blob is dominated by the hashed feature table, so fewer commands is not
much smaller, `--table-size` the only knob. Full tables, every variant that was tried, what
still fails and the C versus Python parity: [docs/accuracy.md](docs/accuracy.md).

## How it compares

| | open source | fits an MCU | free slot values | calibrated "unsure" | Hinglish |
| --- | --- | --- | --- | --- | --- |
| Snips NLU, abandoned around 2020 | yes | no | yes | no | no |
| Rhasspy / hassil | yes | needs a Pi | template matching | no | no |
| Picovoice Rhino | no, paid | yes | yes | no | no |
| Espressif ESP-SR MultiNet | yes | yes | no, fixed phrases | no | no |
| cactus-compute/needle, 45M parameters | yes | seconds per command | yes | no | partly |
| edge-nlu | yes | yes | yes | yes | yes |

Nothing else is open source, microcontroller sized, able to generalise past exact templates
and honest enough about its own confidence to refuse.

## Roadmap

1. ~~**Accuracy on unseen phrasing.** A training-sentence generator, and an `edgenlu doctor`
   that catches a thin commands file before you flash it.~~ Done. Both ship, and the doctor
   now says in plain words how honest the confidence is.
2. ~~**An unsure cut-off that holds up on wording nobody wrote.**~~ Done. The blob carries
   the training vocabulary, the device counts the words it has never seen, and a handful of
   fitted weights turn that into the confidence.
3. ~~**Packaging.** A pip install, and an Arduino library the runtime drops straight
   into.~~ Done. The wheel carries the language files and the C runtime, `edgenlu init`
   writes a starter device, `export --with-runtime` leaves a folder that compiles alone,
   and [arduino/EdgeNLU](arduino/EdgeNLU) is the library.
4. **Public release.** A name that is not a working name, a demo video, a first tagged
   version, and then PyPI and the Arduino library registry.

## Development

Python 3.10 or newer, [uv](https://docs.astral.sh/uv/) for the virtual environment, `make`
for the C side. Installing it to use it is [docs/install.md](docs/install.md); setup, the
tests and the make targets are in [docs/development.md](docs/development.md);
[PLAN.md](PLAN.md) has the milestone history and the open questions.

## Credits and licence

The recipe, a linear intent classifier plus a CRF slot tagger, is the one
[Snips NLU](https://github.com/snipsco/snips-nlu) used before it was abandoned. Framing the
output as a typed decision with a calibrated confidence, never free text, is borrowed from
TypeSafe's Jev model. New here is making that fit a microcontroller: the flat blob read out
of flash, the C99 runtime with no allocation, the tuned unsure path, and Hinglish.

Apache-2.0. See [LICENSE](LICENSE).
