# Install and first run

Two halves. The Python tool trains a model on your laptop. The C runtime reads sentences
on the device. You need the first to get the second.

## The Python tool

Python 3.10 or newer. It is not on PyPI yet, so install it from git:

```sh
pip install "git+https://github.com/avionicharshit-byte/tinycue"
```

Or from a checkout, which is what you want if you are going to change it:

```sh
git clone https://github.com/avionicharshit-byte/tinycue && cd tinycue
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Either way you get a `tinycue` command:

```sh
$ tinycue --version
tinycue 0.1.0
```

The wheel carries the language word lists, the starter templates and the two C runtime
sources, so nothing else has to be downloaded or cloned. The optional extras are
`demo` (pyserial, for the serial demos), `voice` (Vosk and sounddevice, for the speech
demo) and `dev` (pytest and hatchling, for the test suite).

## Five minutes, start to finish

Run this in an empty folder. It works with nothing installed but the pip package.

### 1. Write the starter files

```sh
$ tinycue init coffee
wrote:
  coffee.yaml
  coffee.extra.yaml
  coffee.dev.yaml
```

`coffee.yaml` is a toy coffee machine: three commands, three slots, commented line by
line. `coffee.extra.yaml` holds extra training sentences written answer first, and
`coffee.dev.yaml` is the set the confidence and the unsure cut-off are fitted on. All
three are meant to be replaced with your own device.

### 2. Check what you wrote

```sh
$ tinycue check coffee.yaml --extra coffee.extra.yaml
coffee.yaml: ok
extra sentences: coffee.extra.yaml
languages: en, hinglish
slots: 3
  drink: 4 values, 16 words
  cups: number, range 1 to 6
  strength: 2 values, 13 words
commands: 3
  brew: 16 examples, slots: drink, cups (optional)
  set_strength: 10 examples, slots: strength
  stop_machine: 6 examples, slots: none
examples: 32
word lists: 13 fillers, 11 droppable, 4 equivalent groups, 192 none sentences
unsure below: auto, on unsure: ask
none command: on
```

`check` refuses a file it cannot read and names the line, which is quicker than waiting
for a train to fail.

### 3. Train

```sh
$ tinycue train coffee.yaml --extra coffee.extra.yaml --dev coffee.dev.yaml -o out/model
...
  chosen cut-off 0.888 sits between two rows of this table
bundle:
  slots.crfsuite: 108.9 KB
  intent.npz: 107.2 KB
  meta.json: 10.2 KB
  total: 226.3 KB
trained in 3.3 s
```

It prints the whole cut-off trade-off table before it picks one: how much goes to unsure
at each cut-off, how often an accepted answer is right, and how many wrong answers are
caught. The default target is 97% of accepted answers right.

### 4. Ask the doctor what to write next

```sh
$ tinycue doctor coffee.yaml --extra coffee.extra.yaml --dev coffee.dev.yaml
25 dev sentences against 6000 training sentences made of 451 different words

full command accuracy 76.0%, command right 84.0%, slot f1 87.2%, ece 0.235
...
per command, worst first
  command        dev   accuracy   command right   phrasings
  brew              9      55.6%           77.8%          16
  stop_machine      4      75.0%           75.0%           6
  set_strength      6      83.3%           83.3%          10
  none              6     100.0%          100.0%         192
```

It trains, measures on the dev file and says where the model is weak: which commands get
confused, which words in failing sentences appear in no training sentence, which commands
are running on too few phrasings. 76% is what a dozen starter sentences buys. Write
sentences for what it names, and run it again. That loop, not a bigger model, is where
accuracy comes from.

### 5. Read a sentence

```sh
$ tinycue parse out/model "make me two lattes"
brew(cups=2, drink=latte) confidence=0.99
$ tinycue parse out/model "teen cup chai bana do"
brew(cups=3, drink=tea) confidence=0.98
$ tinycue parse out/model "i want it milder"
set_strength(strength=mild) confidence=0.99
$ tinycue parse out/model "who won the match last night"
none confidence=0.98
```

### 6. Export for the device

```sh
$ tinycue export out/model -o out/device --with-runtime
wrote out/device/model.bin
  162.0 KB, 4 classes, 7 tags, 16384 buckets
  451 training words, 5 gate weights
  plus model_data.h and model_data.c in out/device
  plus tinycue.h
  plus tinycue.c
  out/device now builds on its own: cc -std=c99 *.c your_main.c -lm
```

`--with-runtime` copies the two C sources out of the installed package, so `out/device`
is the whole device side in one folder: nothing to clone, nothing to link.

## The C runtime, in any build system

Four files and no dependencies beyond libm:

| file | what it is |
| --- | --- |
| `tinycue.h` | the whole API: `tcue_init`, `tcue_scratch_size`, `tcue_parse` |
| `tinycue.c` | the runtime, portable C99 |
| `model_data.h` | declares `tcue_model_data[]` and its length |
| `model_data.c` | the blob as a C array, 8 byte aligned, `const` so it stays in flash |

Add all the `.c` files to your build and include `tinycue.h`. It already carries
`extern "C"` guards, so C++ can include it straight. A 15 line program is enough to prove
the folder is self contained:

```c
#include <stdio.h>
#include "tinycue.h"
#include "model_data.h"

static tcue_model model;
static tcue_result out;
static uint8_t scratch[16384] __attribute__((aligned(8)));

int main(void) {
    if (tcue_init(&model, tcue_model_data, tcue_model_data_len) != TCUE_OK) return 1;
    if (tcue_parse(&model, "make me two lattes", &out, scratch, sizeof scratch) != TCUE_OK) return 2;
    printf("%s confidence %.2f unsure %d\n", out.command, out.confidence, out.unsure);
    return 0;
}
```

```sh
$ cc -std=c99 -Wall -Wextra -pedantic -O2 tinycue.c model_data.c main.c -lm -o demo
$ ./demo
brew confidence 0.99 unsure 0
```

Size the scratch buffer with `tcue_scratch_size(&model)` rather than guessing; it reports
what this model needs and the call is free. Every entry point returns a negative code on
refusal and `tcue_error` turns that into a line of text. Nothing after `tcue_init`
allocates or opens a file.

Each filled slot carries a `known` flag. It is 0 when the tagger found a value nobody
listed, and the words come through as text anyway, which is how a name your user invented
survives. `make cli` builds the same runtime as a desktop tool, which is the quickest way
to try a blob without flashing anything.

## The Arduino library

`arduino/TinyCue/` in this repository is a standard Arduino library. It is not in the
Arduino library registry yet, so install it by hand:

- **From a ZIP**: download the repository as a ZIP, unzip it, and copy the
  `arduino/TinyCue` folder into your sketchbook's `libraries` folder, which on macOS is
  `~/Documents/Arduino/libraries/`. Restart the IDE.
- **From a checkout**, with `arduino-cli`:

  ```sh
  arduino-cli compile --fqbn esp32:esp32:esp32 \
      --library arduino/TinyCue arduino/TinyCue/examples/SerialCommands
  ```

The **SerialCommands** example is board agnostic and needs no display. It reads a line
from the serial port at 115200 and prints the command, the slot values, the confidence,
the unsure flag and how many words the model had never seen. It carries the same toy
coffee machine `tinycue init` writes, so you can flash it before you have written
anything. Swap in your own `model_data.c` and `model_data.h` when you have them.

Measured with `arduino-cli 1.5.1` on the example sketch:

| board | flash | RAM |
| --- | --- | --- |
| classic ESP32, `esp32:esp32:esp32` | 353,252 bytes, 26% | 35,004 bytes, 10% |
| Arduino Nano 33 BLE, `arduino:mbed_nano:nano33ble` | 171,104 bytes, 17% | 57,448 bytes, 21% |

## Minimum hardware

A 32 bit microcontroller, roughly **300 KB of free flash** and about **12 KB of RAM** for
the scratch buffer, on top of whatever your own code uses. Most of the flash is the model
blob, which grows with your vocabulary, not with your number of commands.

8 bit AVR boards are out. An Arduino Mega 2560 fails to build the example with
`size of array is too large`: the model is bigger than AVR can address, and its 8 KB of
SRAM could not hold the scratch buffer either. There is no version of this that fits an
Uno.

## The agent skill

If you use Claude Code, the repository is also a plugin. It teaches an agent the whole
loop: interview you about the device, write the commands file and the sentences, run
`tinycue doctor` until the numbers stop moving, and export.

```
/plugin marketplace add avionicharshit-byte/tinycue
/plugin install tinycue@tinycue
```

The skill on its own is `skills/tinycue/SKILL.md`; copy that folder into
`~/.claude/skills/` if you would rather not add the marketplace.
