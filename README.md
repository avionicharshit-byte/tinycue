# edge-nlu

Edge-nlu turns a file of example commands into a small model that understands typed or spoken
orders fully offline. You describe the commands and slots you want, and the tool expands your
examples into training data for a tiny intent model and a word tagger. The goal is plain C99 plus
a model file under 1 MB, small enough for an ESP32 or a Raspberry Pi, with English and Hinglish
support.

Status: early work in progress, milestone M0. Only the commands file parser and the example
generator exist today. Training, calibration and the C export are not written yet. See
[PLAN.md](PLAN.md) for the full plan.

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

A full example with number slots, optional slots and Hinglish phrasing is in
[commands.example.yaml](commands.example.yaml).

## Install and run

Needs Python 3.10 or newer. Using [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Check a commands file, then generate training examples:

```sh
.venv/bin/edgenlu check commands.example.yaml
.venv/bin/edgenlu generate commands.example.yaml -n 500 --seed 0 -o out/train.jsonl
```

Each output line is one JSON object with `tokens`, `tags`, `command` and `slots`.

## Tests

```sh
.venv/bin/pytest -q
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).
