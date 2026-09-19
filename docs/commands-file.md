# The commands file

One YAML file is the whole input to `edgenlu`. It names the languages, the kinds of value
your commands carry, the commands themselves with example sentences, and what happens when
the model is not sure.

Two worked examples ship, from unrelated domains and commented line by line:
[examples/smart_home.yaml](../examples/smart_home.yaml) and
[examples/robot.yaml](../examples/robot.yaml). Nothing in the Python package knows about
either one, and a test fails the build if a domain word ever appears in `src/edgenlu`.

## language

A list, such as `[en, hinglish]`. It picks the language files in `src/edgenlu/langs/`,
which supply the filler words, the carrier words the generator may drop, and a base list of
out-of-scope sentences. `hinglish` means Hindi typed in Latin letters, usually mixed with
English in the same sentence.

## slots

A slot is a kind of value a command carries. There are two sorts.

A value list slot gives each canonical value, which is what your code receives, and the
surface forms people actually say for it. Put the most common form first.

```yaml
slots:
  room:
    values:
      bedroom: [bedroom, bed room, sone ka kamra, kamre]
      kitchen: [kitchen, rasoi]
```

A number slot has no value list. Digits, English number words and Hindi number words are
all understood, and an optional `min` and `max` mark anything outside the range as not a
match.

```yaml
  minutes:
    type: number
    min: 1
    max: 180
```

Slot tags carry the slot *type*, not the slot name, so two commands that both take a
`direction` share every training example of it.

## commands

Each command has a `name`, the slots it takes, and example sentences with the slot spans
marked `[surface text](slot_name)`.

```yaml
commands:
  - name: set_light
    slots: [room, state]
    examples:
      - "turn [on](state) the [bedroom](room) light"
      - "[rasoi](room) mein light [jala do](state)"
```

Wrap every example in quotes. YAML reads a line starting with `[` as a list, so an example
that opens with a slot span breaks without them.

The long form of `slots` marks a slot optional or gives it a type under a different name:

```yaml
    slots:
      - name: room
        required: false
      - name: speed
        type: direction
```

A required slot that a sentence does not fill comes back in the result's missing list
rather than being invented.

## equivalents, fillers and none_examples

All three are optional and all three feed the training sentence generator.

- `equivalents` is a list of word groups that mean the same thing in this domain, such as
  `[turn, switch]`. The generator swaps them around so the model sees wording you did not
  type by hand. It never touches a token inside a slot span.
- `fillers` adds domain filler words on top of the ones the language files hold.
- `none_examples` adds out-of-scope sentences. Near misses belong here rather than in the
  language files: they use this domain's own words but ask for nothing the device can do,
  like "i am a big fan of cricket".

## fallback

```yaml
fallback:
  unsure_below: auto
  none_command: true
  on_unsure: ask
```

`unsure_below` is the confidence below which an answer becomes "unsure" instead of an
action. `auto` lets the trainer fit it on held-out data and print what it chose; a number
between 0 and 1 pins it yourself. `none_command` maps sentences that are none of your
commands to "no command" rather than the closest one, and is best left on. `on_unsure`
records what the device should do: `ask` for a "did you mean ...?" prompt, or `cloud` to
hand the sentence to a bigger model when there is a network.

## Checking it

```sh
.venv/bin/edgenlu check examples/smart_home.yaml
```

`check` reads the file, reports anything malformed and prints what it found, before you
spend the time on a training run.
