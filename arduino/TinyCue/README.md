# TinyCue Arduino library

The tinycue C99 runtime, packaged the way the Arduino IDE expects. Text in, a command
and its slot values out, offline, with no allocation and no network.

## Install

Not in the Arduino library registry yet. Two ways in:

- **From a ZIP.** Download this repository as a ZIP, unzip it, and copy the
  `arduino/TinyCue` folder into your sketchbook's `libraries` folder. On macOS that is
  `~/Documents/Arduino/libraries/TinyCue`.
- **From a checkout.** Point the compiler at it:
  `arduino-cli compile --fqbn esp32:esp32:esp32 --library arduino/TinyCue <sketch>`.

Then open **File > Examples > TinyCue > SerialCommands**, upload, and type a sentence
into the Serial Monitor at 115200.

## Your own model

The example carries a toy coffee machine. To teach it your own device, install the Python
tool and write a commands file:

```sh
pip install "git+https://github.com/avionicharshit-byte/tinycue"
tinycue init mydevice
tinycue train mydevice.yaml --extra mydevice.extra.yaml --dev mydevice.dev.yaml -o out/model
tinycue export out/model -o out/device
```

Copy `out/device/model_data.c` and `out/device/model_data.h` into your sketch folder. The
full loop, including `tinycue doctor`, is in
[docs/install.md](https://github.com/avionicharshit-byte/tinycue/blob/main/docs/install.md).

## What it needs

A 32 bit board with roughly 300 KB of free flash and 12 KB of free RAM. Measured on the
example sketch: 353 KB of flash and 35 KB of RAM on a classic ESP32, 171 KB and 57 KB on
an Arduino Nano 33 BLE. An 8 bit AVR will not build it: the model array is larger than
AVR can address.

`src/tinycue.c` and `src/tinycue.h` are copies of `runtime/` at the repository root.
`make arduino-sync` refreshes them and a test fails if they drift.

Apache-2.0.
