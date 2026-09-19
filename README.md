# edge-nlu

Edge-nlu turns a file of example commands into a small model that understands typed or spoken
orders fully offline. You describe the commands and slots you want, and the tool expands your
examples into training data for a tiny intent model and a word tagger. The goal is plain C99 plus
a model file under 1 MB, small enough for an ESP32 or a Raspberry Pi, with English and Hinglish
support.

Status: milestone M3. The commands file parser, the example generator, training, calibration, the
command line, the C99 device runtime and three board demos all work. The C runtime is proved
against Python on 842 sentences, and it runs offline on a classic ESP32 and on an Arm Cortex-M33,
in under 5 milliseconds a sentence on both, with the same source file and no chip specific code.
There is also a [voice demo](demo/voice) where you speak to the boards, with the speech to text
running on the laptop. See [PLAN.md](PLAN.md) for what is left.

![How edge-nlu works](diagrams/edge-nlu-flow.png)

## The commands file

One YAML file describes everything. Slots list the words people use, and each example marks the
slot spans with `[surface text](slot_name)`.

```yaml
slots:
  room:
    values:
      bedroom: [bedroom, bed room, sone ka kamra]
      kitchen: [kitchen, rasoi]
  state:
    values:
      "on": ["on", chalu, jala do]
      "off": ["off", band, band kar do]

commands:
  - name: set_light
    slots: [room, state]
    examples:
      - "turn [on](state) the [bedroom](room) light"
      - "[rasoi](room) mein light [jala do](state)"
```

Two full examples ship with the tool, from two unrelated domains:
[examples/smart_home.yaml](examples/smart_home.yaml) (lights, fan, timer, sensor readings) and
[examples/robot.yaml](examples/robot.yaml) (drive, turn, stop, gripper, speed). Nothing in the
Python package knows about either one; a test fails the build if a domain word ever appears in
`src/edgenlu`.

A commands file can also carry three optional blocks: `equivalents` (word groups the generator
swaps around), `fillers` (extra filler words) and `none_examples` (out-of-scope sentences, which
is where near misses like "i am a big fan of cricket" belong). Filler words, droppable carrier
words and a base list of out-of-scope sentences come from the language files in
`src/edgenlu/langs/`, picked by the `language:` list.

## Install and run

Needs Python 3.10 or newer. Using [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Check a commands file, then train, evaluate and read a sentence:

```sh
.venv/bin/edgenlu check examples/smart_home.yaml
.venv/bin/edgenlu train examples/smart_home.yaml -n 1500 --seed 0 -o out/model
.venv/bin/edgenlu eval out/model --data out/splits/test.jsonl
.venv/bin/edgenlu eval out/model --data eval/heldout_smart_home.yaml
.venv/bin/edgenlu parse out/model "turn on the bedroom light"
```

```
set_light(state=on, room=bedroom) confidence=0.98
```

`edgenlu generate` writes the training examples on their own, one JSON object per line with
`tokens`, `tags`, `command` and `slots`.

## On a device

Export the bundle as one flat blob plus C source, then build the runtime:

```sh
.venv/bin/edgenlu export out/model -o out/device
make -C runtime
echo "fan tez karo" | runtime/enlu_cli out/device/model.bin
```

```
{"text":"fan tez karo","command":"set_fan","slots":{"speed":"up"},"missing":[],
 "confidence":0.995,"intent":0.999,"slot":0.986,"unsure":false,"micros":4.2}
```

`out/device/` holds `model.bin`, and `model_data.c` and `model_data.h`, which are the same bytes
as a `const unsigned char[]` for flashing. The format is written out in
[docs/model-format.md](docs/model-format.md).

The runtime is two files, `runtime/edgenlu.h` and `runtime/edgenlu.c`: portable C99, no malloc
after init, no file reading, nothing beyond libc and libm. The caller hands it a scratch buffer and
it reads the model where it lies, so on a microcontroller the weights never leave flash. The API is
two calls:

```c
int enlu_init(enlu_model *model, const uint8_t *blob, size_t len);
int enlu_parse(const enlu_model *model, const char *text, enlu_result *out,
               void *scratch, size_t scratch_len);
```

`enlu_result` carries the command name, the slots with their canonical values or numbers, any
required slots that were left out, the confidence and its two halves, and an `unsure` flag. The
best guess is filled in even when the answer is unsure.

## Boards tested

Both boards ran the same 31 sentences, the same smart home model and the same `runtime/edgenlu.c`.
Every answer matched the desktop C tool: the same command, slot values, missing slots and unsure
flag, and on the Cortex-M33 the confidence was identical to all six printed decimals.

| | classic ESP32 | NXP FRDM-MCXN236 |
| --- | --- | --- |
| core | Xtensa LX6, 240 MHz | Arm Cortex-M33, 150 MHz |
| build | Arduino sketch, round display | bare metal, no RTOS |
| flash image | 540,632 bytes | 241,352 bytes |
| static RAM | 40,828 bytes | 20,976 bytes |
| model blob, in flash | 209,640 bytes | 209,640 bytes |
| scratch buffer | 10,712 bytes | 10,712 bytes |
| parse, fastest | 2,537 us | 2,318 us |
| parse, mean | 4,376 us | 4,805 us |
| parse, slowest | 6,534 us | 7,873 us |
| sentences matching the desktop | 31 of 31 | 31 of 31 |

The two flash numbers are not the same measurement: the ESP32 image carries the Arduino core and
the display library, the MCXN236 one carries four NXP drivers. Both boards are timed around
`enlu_parse` alone, on the board, with `-DENLU_FAST_EXP`.

### The ESP32 demo

[demo/esp32_round](demo/esp32_round) runs the smart home model on a classic ESP32 with a 1.28 inch
round GC9A01 display. It reads a sentence from USB serial, answers with one JSON line, and draws
the command on the screen with a confidence ring around the rim. No Wi-Fi.

```sh
make model          # train and export
make demo-flash     # copy the runtime in, build and upload
.venv/bin/python demo/send.py "turn on the bedroom light"
```

Full numbers in [demo/esp32_round/board-results.md](demo/esp32_round/board-results.md). The screen
drawing has not been checked by eye.

### The Cortex-M33 demo

[demo/nxp_mcxn236](demo/nxp_mcxn236) is the portability proof: the same runtime on an NXP
FRDM-MCXN236, bare metal, flashed with pyOCD over the on-board debug probe. No line of
`runtime/edgenlu.c` was changed for it, and the build is warning free. It answers on the debug
serial port and lights the red LED when the answer is unsure.

```sh
make model                        # train and export
make nxp-flash                    # copy the runtime in, build and flash
.venv/bin/python demo/send.py -p /dev/cu.usbmodem<probe>3 "turn on the bedroom light"
```

Full numbers in [demo/nxp_mcxn236/board-results.md](demo/nxp_mcxn236/board-results.md). The M33
has a single precision FPU only, so `-DENLU_FAST_EXP`, which does the two exponentials in the CRF
forward pass in single precision, is worth 2.7 times there: 4,805 microseconds a sentence with it
and 12,943 without, for the same answers to six decimals.

### Saying it out loud

[demo/voice](demo/voice) puts the two boards together. The FRDM-MCXN236 streams its on-board
microphone to the Mac at 16 kHz over its debug serial port, the Mac turns the speech into text with
[Vosk](https://alphacephei.com/vosk/) offline, and the sentence goes back to both boards, which
parse it and show the answer on the round display and the red LED.

```sh
make voice-model                          # fetch the Vosk model, 54 MB, once
make voice-flash                          # build and flash the microphone firmware
.venv/bin/python demo/voice/listen.py     # then speak
```

**The speech to text runs on the Mac, not on a chip.** edge-nlu turns text into a command; it is
not a speech recogniser. What the chips do here is what they do everywhere else in this repository.

Five commands played out loud into the room were all understood correctly, in 795 to 1,099
milliseconds from the last sound of the sentence to the answer, of which 44 milliseconds was the
two boards and the rest was Vosk deciding the sentence had ended. The audio link runs at 1 Mbaud
and lost no packets in 30 seconds of testing. The Vosk word list is built from the commands file,
so it follows `--spec` and knows no domain of its own. The honest limit is that Vosk's English
models have no entry for 152 of the 481 words in the smart home file, so every Hinglish word is
inaudible through this path. Numbers and the full list are in
[demo/voice/README.md](demo/voice/README.md).

## What it does today

- **Tags carry the slot type, not the slot name.** Two commands that both take a `direction` share
  every example of it. The command maps the type back to its own slot name when decoding.
- **The generator grows your examples.** It swaps in every slot value and synonym, inserts filler
  words, drops carrier words and swaps equivalent words, never touching a token inside a slot span.
  Every command, including `none`, is grown to the same size.
- **Numbers.** Digits, English words and Hindi words in Latin letters, 0 to 180, both directions.
  "10", "ten", "das" and "ek sau bees" all work.
- **Confidence is measured, not guessed.** The intent model gets temperature scaling on a held-out
  split, the tagger's sequence probability gets a fitted exponent, and the cut-off below which the
  answer becomes "unsure" is chosen automatically as the lowest value whose accepted answers are
  right at least 99% of the time on dev.
- **Two test sets.** The generated test split holds out whole phrasings, so no sentence in it grew
  from a phrasing the model trained on. The files in `eval/` are hand-written sentences that share
  no wording with the commands files at all, with typos and missing words. That is the honest test.

## Measured results

Both numbers below come from `edgenlu train <spec> -n 1500 --seed 0`, on an M1 MacBook Air.
Training takes about 5 seconds per model. Nothing here is trained on the `eval/` files.

| | smart home | robot |
| --- | --- | --- |
| commands | 4 plus `none` | 6 plus `none` |
| hand-written examples | 56 | 72 |
| bundle size | 305 KB | 376 KB |
| device blob size | 205 KB | 263 KB |
| training time | 4.7 s | 4.6 s |
| desktop parse time, mean | 7.5 us | 4.4 us |
| **generated test split** | 1018 sentences | 1627 sentences |
| intent accuracy | 92.8% | 81.7% |
| slot F1 | 98.2% | 97.6% |
| full command accuracy | 92.7% | 80.9% |
| ECE | 0.033 | 0.100 |
| sent to unsure | 31.9% | 64.4% |
| wrong answers caught | 100.0% | 98.4% |
| accepted answers right | 100.0% | 99.1% |
| **hand-written held-out set** | 144 sentences | 98 sentences |
| intent accuracy | 77.1% | 42.9% |
| slot F1 | 88.8% | 78.9% |
| full command accuracy | 75.7% | 39.8% |
| ECE | 0.090 | 0.166 |
| sent to unsure | 48.6% | 87.8% |
| wrong answers caught | 85.7% | 94.9% |
| accepted answers right | 93.2% | 75.0% |

The chosen cut-off was 0.903 for the smart home model and 0.852 for the robot model. The desktop
parse time is the C runtime over 842 sentences on an M1 MacBook Air, timed around `enlu_parse`
alone.

The smart home numbers moved when span trimming went in. A tagged span that matches no listed
value is now retried a word shorter, so "fan up karo" gives `speed=up` instead of `speed=up karo`.
Full command accuracy on the generated split went from 91.5% to 92.7% and on the held-out set from
74.3% to 75.7%. The cut-off then refitted lower, from 0.920 to 0.903, because on dev it now reaches
100% accepted accuracy sooner. That is worse on the held-out set, where the fallback used to catch
97.3% of the wrong answers and now catches 85.7%. The cut-off is chosen on generated dev data and
the held-out file is harder than that data, which is the gap to close.

Read those two blocks together. On phrasings that are variations of what you wrote, the tool is
good. On wording it has never seen, it is not, and the honest part is that it knows: on the smart
home held-out set it throws away 64% of answers and catches 97% of the ones that would have been
wrong.

The robot numbers show where the approach runs out. Its `stop`, `grab` and `release` commands carry
no slots, so nothing but the literal words identifies them, and the held-out file deliberately uses
synonyms ("seize", "unclamp", "terminate motion") that appear nowhere in the commands file. A model
with no word embeddings cannot reach those. The fix is to write more example sentences, which is
the same fix as for every other gap.

## Proving the C runtime

`tests/test_c_parity.py` trains both example specs, exports both blobs, builds the C command line
tool, and runs every sentence of both held-out files plus 300 generated sentences per spec through
Python and through C: 842 sentences. It asserts the same command, the same slot values, the same
missing slots, the same unsure flag and the confidence to within 1e-3.

It runs Python twice. Once with the float32 weights it trained, and once with those weights
rounded to the float16 the blob carries, which is what the C side actually reads. Against the
float16 run there were **0 mismatches**, with the worst confidence gap 7.8e-07. Against the
float32 run **no decision changed** on any of the 842 sentences, and the worst confidence gap was
2.3e-04. int8 weights with a per class scale were measured too: no decision changed there either,
but the confidence moved by up to 8.1e-03, over the 1e-3 bar, so float16 it is.

## Tests

```sh
.venv/bin/pytest -q
```

237 tests, about 8 seconds, including a full train on a small dataset and the C parity run. The
parity tests build the runtime with `cc` and skip cleanly if no C compiler is installed.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
