# edge-nlu

Turn a short list of example commands into a 205 KB model and two C files, so a
microcontroller understands typed or transcribed English and Hinglish commands with no
network, no LLM and no per-request cost.

What you get from one YAML file:

- A model trained on your laptop in about 4 seconds, exported as one flat blob plus C99
  source. No malloc, no file IO, and the weights are read in place, in flash.
- An answer in single digit milliseconds on the boards below, 7.5 microseconds on a desktop.
- A calibrated confidence on every answer, and a tuned cut-off below which the device says
  "unsure" instead of guessing. Ask again, or hand the sentence to something bigger.
- English and Hinglish, Hindi typed in Latin letters, including number words. "10", "ten"
  and "das" all arrive as `10`.

edge-nlu is not speech to text. Any recogniser can feed it text; see
[the voice demo](demo/voice) for one that does. **Status: early, private preview.** It is
measured and it works, but the APIs and the model format may still change.

![How edge-nlu works](diagrams/edge-nlu-flow.png)

## Thirty seconds with it

You write the commands and the words people use for them, with slot spans marked
`[surface text](slot_name)`.

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
.venv/bin/edgenlu check examples/smart_home.yaml
.venv/bin/edgenlu train examples/smart_home.yaml -n 1500 --seed 0 -o out/model
.venv/bin/edgenlu export out/model -o out/device
```

```
$ .venv/bin/edgenlu parse out/model "turn on the bedroom light"
set_light(state=on, room=bedroom) confidence=1.00
$ .venv/bin/edgenlu parse out/model "rasoi mein light jala do"
set_light(room=kitchen, state=on) confidence=1.00
$ .venv/bin/edgenlu parse out/model "i am a big fan of cricket"
none confidence=0.97
$ .venv/bin/edgenlu parse out/model "flick the bedroom lamp on"
unsure (best guess: none confidence=0.65)
```

The off-topic sentence was named as off-topic, not forced into the nearest command, and
"flick" and "lamp", in no example sentence, dropped the confidence under the cut-off.

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
| flash image | 540,632 bytes, 41% of the app partition | 241,352 bytes, 23% of 1 MB |
| static RAM | 40,828 bytes | 20,976 bytes |
| model blob, in flash | 209,640 bytes | 209,640 bytes |
| scratch buffer | 10,712 bytes | 10,712 bytes |
| parse: fastest, mean, slowest | 2,537 / 4,376 / 6,534 us | 2,318 / 4,805 / 7,873 us |
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

Copy `runtime/edgenlu.h` and `runtime/edgenlu.c` into your project along with the
`model_data.c` and `model_data.h` that `edgenlu export` wrote. Nothing else to link.

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

## Accuracy and limits

Two test sets. The **generated split** holds out whole phrasings, so it measures variations
of wording you did write. The **hand-written held-out files** in `eval/` share no wording
with the commands file and carry typos, missing slots and synonyms. That is the honest test.

<!-- ACCURACY-UPDATE -->

| smart home model | generated split | hand-written held-out |
| --- | --- | --- |
| sentences | 1018 | 144 |
| full command accuracy | 92.7% | 75.7% |
| sent to unsure | 31.9% | 48.6% |
| wrong answers caught by the cut-off | 100.0% | 85.7% |
| accepted answers right | 100.0% | 93.2% |

On wording it has never seen the model is not accurate, and the useful part is that it
knows. The robot example is harder still, at 39.8% full command accuracy on its held-out
file: its `stop`, `grab` and `release` commands carry no slots, so nothing but the literal
words identifies them, and that file uses synonyms ("seize", "unclamp") from nowhere in the
commands file.

Three limits. Input is capped at 32 tokens and 256 bytes, and longer input is refused
rather than truncated. Hinglish is typed only until there is a recogniser with a code-mixed
lexicon. And the blob is dominated by the hashed feature table, so fewer commands is not
much smaller, `--table-size` the only knob. Full tables, the other limits and the C versus
Python parity: [docs/accuracy.md](docs/accuracy.md).

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

1. **Accuracy on unseen phrasing.** A training-sentence generator, and an `edgenlu doctor`
   that catches a thin commands file before you flash it.
2. **Packaging.** A pip install, and an Arduino library the runtime drops straight into.
3. **Public release.** A name that is not a working name, and a first tagged version.

## Development

Python 3.10 or newer, [uv](https://docs.astral.sh/uv/) for the virtual environment, `make`
for the C side. Setup, the tests and the make targets are in
[docs/development.md](docs/development.md); [PLAN.md](PLAN.md) has the milestone history
and the open questions.

## Credits and licence

The recipe, a linear intent classifier plus a CRF slot tagger, is the one
[Snips NLU](https://github.com/snipsco/snips-nlu) used before it was abandoned. Framing the
output as a typed decision with a calibrated confidence, never free text, is borrowed from
TypeSafe's Jev model. New here is making that fit a microcontroller: the flat blob read out
of flash, the C99 runtime with no allocation, the tuned unsure path, and Hinglish.

Apache-2.0. See [LICENSE](LICENSE).
