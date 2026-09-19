# Development

Python 3.10 or newer. Using [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv/bin/pytest -q          # 357 tests, about 18 seconds
```

The test suite includes a full train on a small dataset and the C parity run, which builds
the runtime with `cc` and skips cleanly when no C compiler is installed. A test also fails
the build if a domain word from either example spec ever appears in `src/tinycue`, so the
Python package stays free of any knowledge of the examples.

Two more guards worth knowing about. `tests/test_packaging.py` builds a wheel and checks
the language files, the starter templates and the two C runtime sources are inside it; it
uses hatchling in process when it is installed, falls back to `uv build`, and skips when
neither is here. `tests/test_arduino_library.py` fails when the copies in
`arduino/TinyCue/src` drift from `runtime/`, which `make arduino-sync` fixes.

## The desktop C command line tool

The same runtime that runs on a board builds for the desktop, which is the quickest way to
try a blob without flashing anything:

```sh
make cli
echo "fan tez karo" | runtime/tinycue_cli out/device/model.bin
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
| `make cli` | build `runtime/tinycue_cli`, the desktop C tool |
| `make model` | train `examples/smart_home.yaml` and export it to `out/device` |
| `make demo-sync` | copy the runtime and the exported model into the ESP32 sketch |
| `make demo-build` | compile the ESP32 sketch with `arduino-cli` |
| `make demo-flash` | compile and upload the ESP32 sketch |
| `make arduino-sync` | copy the runtime into the Arduino library at `arduino/TinyCue/src` |
| `make arduino-example-model` | rebuild the model the Arduino example carries |
| `make arduino-build` | compile the Arduino example against the library |
| `make nxp-build` | build the FRDM-MCXN236 demo |
| `make nxp-flash` | build and flash the FRDM-MCXN236 demo |
| `make voice-model` | download and unpack the Vosk model into `.cache/vosk` |
| `make voice-build` | build the microphone firmware for the NXP board |
| `make voice-flash` | build and flash the microphone firmware |
| `make demo-svg` | re-record the terminal demo in `docs/assets` and rebuild `demo.svg` |
| `make test` | run the Python test suite |
| `make clean` | clean every C build and remove the copies in the sketch folder |

`SPEC`, `MODEL`, `DEVICE`, `FQBN` and `PORT` are all overridable on the make command line,
so `make model SPEC=examples/robot.yaml` trains the other example instead.

The copies `make demo-sync` puts in the sketch folder are build output, not source, and are
not in git.

## The README pictures

`docs/assets` holds what the README shows.

### The four drawn figures

`hero`, `flow`, `unsure` and `boards` each ship as a **light and dark pair**, so the drawing
has no slab of its own and sits on the GitHub page whichever theme the reader is using. The
backgrounds are transparent, the hairlines follow GitHub's own border greys, and the README
hands the browser both files:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/hero-light.svg">
  <img alt="..." src="docs/assets/hero-light.svg" width="880">
</picture>
```

Both halves of a pair are written by `build_assets.py` from one drawing, so they cannot drift
apart. Each file still opens with a palette block, which is the only place a colour is named.

```sh
uv pip install fonttools brotli playwright     # not part of the dev extra
make readme-assets                             # rewrites the eight SVGs and their PNGs
python3 docs/assets/build_assets.py            # SVGs only, no browser needed
```

`make readme-assets` also rebuilds the PNG copy beside every SVG, at twice the drawn size,
for anywhere that will not render SVG. They are screenshots taken by headless Chrome through
Playwright, which is the only renderer here that honours the embedded fonts, so a PNG rebuild
needs Chrome installed. Without Playwright the SVGs are still written and the PNG step says
it was skipped.

### Fonts

The pictures are set in Schibsted Grotesk with IBM Plex Mono for anything code shaped. An SVG
shown through an `<img>` tag is not allowed to fetch a webfont, so a subset of each face
travels inside every file as a base64 woff2, about 30 KB of the roughly 45 KB each file
weighs. Both faces are SIL Open Font License 1.1, which allows this; the notice is in
`FONT-LICENSE.txt` and the subsets are in `fonts/`. Rebuild them with
`python3 docs/assets/fonts/make_subsets.py`, which fetches both faces from the Google Fonts
repository and keeps only the characters the pictures use. Only needed when a picture gains a
character the current subsets do not carry, which shows up as a missing glyph.

### Motion

The hero pulses one pin on the chip mark and draws its two connectors; the flow sends a short
accent dash along each arrow in turn. Both are CSS keyframes inside the SVG, and both sit
inside a `prefers-reduced-motion: reduce` block that turns them off, leaving the finished
state. `unsure` and `boards` do not move at all.

### The terminal demo

`demo.svg` is different: it is a real terminal session, recorded from `demo.sh` with asciinema
into `demo.cast` and turned into an animated SVG by `svg-term-cli`, coloured by
`demo-theme.xresources` so the slab and the violet prompt match the drawn figures. `make
demo-svg` redoes both steps, so the numbers on screen are always the numbers the code produces
that day. `label_svg.py` puts the title and description back on the generated file, opens the
window corner to the same 12 pixel radius the other pictures use, and quiets the three window
dots down to one grey.

### The social preview

`social-preview.png` is a 1280 by 640 card for the repository's **Settings, Social preview**
field. It is built alongside the rest and is not referenced from any page.
