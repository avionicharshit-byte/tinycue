# edge-nlu

Edge-nlu turns a file of example commands into a small model that understands typed or spoken
orders fully offline. You describe the commands and slots you want, and the tool expands your
examples into training data for a tiny intent model and a word tagger. The goal is plain C99 plus
a model file under 1 MB, small enough for an ESP32 or a Raspberry Pi, with English and Hinglish
support.

Status: milestone M1. The commands file parser, the example generator, training, calibration and
the command line all work. The C export is not written yet. See [PLAN.md](PLAN.md) for the plan.

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
| training time | 4.2 s | 4.6 s |
| **generated test split** | 1018 sentences | 1627 sentences |
| intent accuracy | 92.8% | 81.7% |
| slot F1 | 98.2% | 97.6% |
| full command accuracy | 91.5% | 80.9% |
| ECE | 0.069 | 0.100 |
| sent to unsure | 46.0% | 64.4% |
| wrong answers caught | 98.9% | 98.4% |
| accepted answers right | 99.8% | 99.1% |
| **hand-written held-out set** | 144 sentences | 98 sentences |
| intent accuracy | 77.1% | 42.9% |
| slot F1 | 88.8% | 78.9% |
| full command accuracy | 74.3% | 39.8% |
| ECE | 0.089 | 0.166 |
| sent to unsure | 63.9% | 87.8% |
| wrong answers caught | 97.3% | 94.9% |
| accepted answers right | 98.1% | 75.0% |

The chosen cut-off was 0.920 for the smart home model and 0.852 for the robot model.

Read those two blocks together. On phrasings that are variations of what you wrote, the tool is
good. On wording it has never seen, it is not, and the honest part is that it knows: on the smart
home held-out set it throws away 64% of answers and catches 97% of the ones that would have been
wrong.

The robot numbers show where the approach runs out. Its `stop`, `grab` and `release` commands carry
no slots, so nothing but the literal words identifies them, and the held-out file deliberately uses
synonyms ("seize", "unclamp", "terminate motion") that appear nowhere in the commands file. A model
with no word embeddings cannot reach those. The fix is to write more example sentences, which is
the same fix as for every other gap.

## Tests

```sh
.venv/bin/pytest -q
```

221 tests, about 4 seconds, including a full train on a small dataset.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
