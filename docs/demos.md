# The demos

Three demos ship with the repo. All three run the same `runtime/tinycue.c` and a model
built from [examples/smart_home.yaml](../examples/smart_home.yaml).

All of them build with `-DTCUE_FAST_EXP`, which does the two exponentials in the CRF
forward pass in single precision. On the Cortex-M33, which has no hardware double, that
flag is worth 2.6 times for identical answers.

## ESP32 with a round display

[demo/esp32_round](../demo/esp32_round). The smart home model on a classic ESP32 driving a
1.28 inch GC9A01 panel. It reads a sentence from USB serial, answers with one JSON line,
and draws the answer on the screen with a confidence arc around the rim. No Wi-Fi.

Wiring, six lines and no backlight pin:

| panel | ESP32 |
| --- | --- |
| SCL | GPIO 18 |
| SDA | GPIO 23 |
| DC | GPIO 22 |
| CS | GPIO 5 |
| RST | GPIO 4 |
| VCC, GND | 3V3, GND |

The screen is drawn with LVGL 9.6.0. `lv_conf.h` lives in the sketch folder and is found
through `-DLV_CONF_INCLUDE_SIMPLE` in `build_opt.h`.

```sh
make model && make demo-flash && .venv/bin/python demo/send.py "turn on the bedroom light"
```

Measured numbers for this board, including the per-sentence table over the 31 test
sentences, are in [demo/esp32_round/board-results.md](../demo/esp32_round/board-results.md)
and summarised in the "Tested on" cards in the [README](../README.md).

## Cortex-M33, bare metal

[demo/nxp_mcxn236](../demo/nxp_mcxn236). The portability proof: the same runtime on an NXP
FRDM-MCXN236, no RTOS, no heap, flashed over the on-board debug probe. It answers on the
debug serial port and lights the red LED when the answer is unsure.

```sh
make model && make nxp-flash && .venv/bin/python demo/send.py -p /dev/cu.usbmodem<probe>3 "turn on the bedroom light"
```

Per-sentence results are in
[demo/nxp_mcxn236/board-results.md](../demo/nxp_mcxn236/board-results.md).

## Saying it out loud

[demo/voice](../demo/voice). The FRDM-MCXN236 streams its on-board microphone to a Mac at
16 kHz over its debug serial port at 1 Mbaud, the Mac turns speech into text with
[Vosk](https://alphacephei.com/vosk/) offline, and the sentence goes back to both boards.

```sh
make voice-model && make voice-flash && .venv/bin/python demo/voice/listen.py
```

Five commands played out loud into the room were all understood, 795 to 1,099 milliseconds
from the last sound to the answer, of which 44 milliseconds was the two boards and the rest
was Vosk deciding the sentence had ended. The audio link lost no packets in 30 seconds.
Those were measured on the format 1 blob and are not re-measured here; the parse inside
that 44 milliseconds is now 2.5 to 8.5 milliseconds instead of 2.3 to 7.9.

The limit of this demo: Hinglish cannot be spoken to it at all. Vosk's English models have
no entry for 152 of the 481 words in the smart home file, and a word outside a
grammar-constrained recogniser's list can never come out of it. Typed Hinglish works.

`make voice-model` downloads the Vosk model into `.cache/vosk` the first time, so the first
run needs a network connection. Nothing after that does.
