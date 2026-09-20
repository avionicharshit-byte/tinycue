<p align="center">
  <img alt="tinycue: a sentence goes in, a command with its slot values comes out, on a microcontroller. Around 250 KB, about 5 ms on an ESP32, fully offline, and it says unsure instead of guessing." src="https://raw.githubusercontent.com/avionicharshit-byte/tinycue/main/docs/assets/hero-light.png" width="880">
</p>

tinycue turns a short list of example commands into a model small enough to live in flash and a
C99 runtime small enough to live in your firmware. It does not listen: it reads text, typed or
from a recogniser. Early preview.

<p align="center">
  <img alt="A terminal on the left sends two sentences to a classic ESP32 with a round screen on the right, and the board answers both. The first lights the screen green at confidence 0.997. The second comes back at 0.668 with unsure true, and the screen turns amber and asks instead of acting." src="https://raw.githubusercontent.com/avionicharshit-byte/tinycue/main/docs/assets/board-demo.gif" width="880">
</p>

A classic ESP32 with a 1.28 inch round screen, filmed off the bench. The JSON on the left is what
the chip sent back over the serial port, not a mock-up. Nothing in the loop is online.

## Quickstart

```sh
pip install tinycue
tinycue init coffee
tinycue train coffee.yaml --extra coffee.extra.yaml --dev coffee.dev.yaml -o out/model
tinycue parse out/model "make me two lattes"
tinycue export out/model -o out/device --with-runtime
```

Now delete the coffee machine and write your own. `tinycue doctor` names what to write next.

## Use it on a device

Export leaves four files in `out/device`: `tinycue.c`, `tinycue.h`, and your model as
`model_data.c` and `model_data.h`. Add both `.c` files to the firmware build, and link libm.

```c
#include "tinycue.h"
#include "model_data.h"

static tcue_model model;
static tcue_result answer;
static uint8_t scratch[12288] __attribute__((aligned(8)));

void cue_begin(void) { tcue_init(&model, tcue_model_data, tcue_model_data_len); }
void cue_line(const char *sentence)
{
    if (tcue_parse(&model, sentence, &answer, scratch, sizeof scratch) != TCUE_OK) return;
    if (answer.unsure || answer.is_none) ask(sentence); else run(&answer);
}
```

The budget is a 32 bit part with about 300 KB of free flash and 12 KB of RAM.

## Tested on

<p align="center">
  <img alt="Two cards. A classic ESP32, Xtensa LX6 at 240 MHz: 345 KB of flash for the whole image, 34 KB of static RAM, 5.1 ms per sentence. An NXP FRDM-MCXN236, Arm Cortex-M33 at 150 MHz: 289 KB of flash, 21 KB of static RAM, 5.1 ms per sentence. Both answered all 31 test sentences the same way as the desktop build." src="https://raw.githubusercontent.com/avionicharshit-byte/tinycue/main/docs/assets/boards-light.png" width="880">
</p>

## Unsure instead of wrong

The confidence is calibrated and the cut-off is fitted on held-out data, not guessed. A device
that acts on a shaky answer is worse than one that asks, so `unsure` is a first-class result
rather than an error.

| | smart home | robot |
| --- | --- | --- |
| right answer, everyday phrasing | 96.4% | 93.5% |
| right answer, deliberately awkward phrasing | 84.0% | 70.4% |
| right when it says it is sure | 98.1% | 98.2% |

Three limits: it only knows the words you have shown it, it is not speech to text, and typed
Hinglish works while spoken Hinglish depends on your recogniser having those words at all.

## Docs

Everything else, including the commands file reference, the model format, the two board demos and
the agent skill, is in the
[repository](https://github.com/avionicharshit-byte/tinycue).

## Licence

Apache-2.0.
