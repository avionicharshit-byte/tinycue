# Development

Python 3.10 or newer. Using [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv/bin/pytest -q          # 357 tests, about 18 seconds
```

The test suite includes a full train on a small dataset and the C parity run, which builds
the runtime with `cc` and skips cleanly when no C compiler is installed. A test also fails
the build if a domain word from either example spec ever appears in `src/edgenlu`, so the
Python package stays free of any knowledge of the examples.

Two more guards worth knowing about. `tests/test_packaging.py` builds a wheel and checks
the language files, the starter templates and the two C runtime sources are inside it; it
uses hatchling in process when it is installed, falls back to `uv build`, and skips when
neither is here. `tests/test_arduino_library.py` fails when the copies in
`arduino/EdgeNLU/src` drift from `runtime/`, which `make arduino-sync` fixes.

## The desktop C command line tool

The same runtime that runs on a board builds for the desktop, which is the quickest way to
try a blob without flashing anything:

```sh
make cli
echo "fan tez karo" | runtime/enlu_cli out/device/model.bin
```

```
{"text":"fan tez karo","command":"set_fan","slots":{"speed":"up"},"missing":[],
 "confidence":0.998809,"intent":0.999447,"slot":0.997450,"unsure":false,"micros":15.0}
```

The Python command line rounds confidence to two decimals; the C runtime prints the full
number.

## Make targets

| target | what it does |
| --- | --- |
| `make cli` | build `runtime/enlu_cli`, the desktop C tool |
| `make model` | train `examples/smart_home.yaml` and export it to `out/device` |
| `make demo-sync` | copy the runtime and the exported model into the ESP32 sketch |
| `make demo-build` | compile the ESP32 sketch with `arduino-cli` |
| `make demo-flash` | compile and upload the ESP32 sketch |
| `make arduino-sync` | copy the runtime into the Arduino library at `arduino/EdgeNLU/src` |
| `make arduino-example-model` | rebuild the model the Arduino example carries |
| `make arduino-build` | compile the Arduino example against the library |
| `make nxp-build` | build the FRDM-MCXN236 demo |
| `make nxp-flash` | build and flash the FRDM-MCXN236 demo |
| `make voice-model` | download and unpack the Vosk model into `.cache/vosk` |
| `make voice-build` | build the microphone firmware for the NXP board |
| `make voice-flash` | build and flash the microphone firmware |
| `make test` | run the Python test suite |
| `make clean` | clean every C build and remove the copies in the sketch folder |

`SPEC`, `MODEL`, `DEVICE`, `FQBN` and `PORT` are all overridable on the make command line,
so `make model SPEC=examples/robot.yaml` trains the other example instead.

The copies `make demo-sync` puts in the sketch folder are build output, not source, and are
not in git.
