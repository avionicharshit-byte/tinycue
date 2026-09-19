"""Read an answer-first sentence file.

The answer is written first and the sentences are written for it, so every line is
labelled by construction and nobody has to mark up spans by hand:

    - answer: set_level(place=upstairs, level=high)
      say:
        - push the upstairs level right up
        - upstairs [a notch higher](level) please
    - answer: none
      say:
        - i never touch that thing

For each slot in the answer we look for the words that say it. Markup wins, and the
surface inside it is registered as a new way of saying that value, so training, decoding
and the exported blob all learn it. Otherwise we look for any known way of saying the
value, longest first, or for a number slot any spelling of the number. A slot that is
left out of the answer is simply absent from the sentence.

A file that cannot be labelled is an error naming the answer and the sentence. A
silently mislabelled sentence is worse than no sentence at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .numbers import parse_number
from .parser import BRACKETS, MARKUP, _text, strip_markup, tokenize
from .schema import (
    NONE_COMMAND,
    NUMBER,
    OUTSIDE,
    Example,
    Span,
    Spec,
    SpecError,
)

# command, or command(slot=value, slot=value)
CALL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*(.*?)\s*\))?$", re.DOTALL)

# The longest run of words we will try to read as one number. "one hundred and twenty"
# is four, so five leaves a little room.
MAX_NUMBER_TOKENS = 5

# Keys a file may hang its list of blocks under, on top of a plain top level list.
LIST_KEYS = ("sentences", "say", "extra", "dev")


def _blocks(path) -> list:
    """The list of answer blocks in a file."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(
            f"{path}: the file is not valid YAML: {exc}\n"
            "A sentence that starts with '[' has to be in quotes, or YAML reads it as a list."
        ) from exc
    if isinstance(raw, dict):
        for key in LIST_KEYS:
            if key in raw:
                raw = raw[key]
                break
    if not isinstance(raw, list) or not raw:
        raise SpecError(
            f"{path}: expected a list of blocks, each with an 'answer' and a 'say' list"
        )
    return raw


def parse_answer(text: str, spec: Spec, where: str) -> tuple[str, dict]:
    """Read 'command(slot=value, ...)' into a command name and its canonical values."""
    match = CALL.match(text.strip())
    if match is None:
        raise SpecError(
            f"{where}: '{text}' is not written as command(slot=value, ...) or just command"
        )
    name = match.group(1)
    body = (match.group(2) or "").strip()

    if name == NONE_COMMAND:
        if body:
            raise SpecError(f"{where}: the '{NONE_COMMAND}' answer takes no slots")
        return NONE_COMMAND, {}

    command = spec.command(name)
    if command is None:
        known = ", ".join(c.name for c in spec.commands)
        raise SpecError(f"{where}: '{name}' is not a command. The file has: {known}")

    slots: dict[str, str | int] = {}
    for piece in body.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise SpecError(f"{where}: '{piece}' has to be written as slot=value")
        slot_name, _, raw_value = piece.partition("=")
        slot_name = slot_name.strip()
        raw_value = raw_value.strip()
        if slot_name in slots:
            raise SpecError(f"{where}: slot '{slot_name}' is given twice")

        slot = command.slot(slot_name)
        if slot is None:
            listed = ", ".join(s.name for s in command.slots) or "no slots at all"
            raise SpecError(
                f"{where}: command '{name}' has no slot '{slot_name}'. It has: {listed}"
            )
        slot_type = spec.slot_types[slot.type]

        if slot_type.kind == NUMBER:
            value = parse_number(raw_value.split())
            if value is None:
                raise SpecError(f"{where}: slot '{slot_name}' takes a number, not '{raw_value}'")
            if slot_type.min is not None and value < slot_type.min:
                raise SpecError(f"{where}: {value} is below the minimum {slot_type.min}")
            if slot_type.max is not None and value > slot_type.max:
                raise SpecError(f"{where}: {value} is above the maximum {slot_type.max}")
            slots[slot_name] = value
            continue

        if raw_value not in slot_type.values:
            known = ", ".join(sorted(slot_type.values))
            raise SpecError(
                f"{where}: '{raw_value}' is not a value of slot '{slot_name}'. "
                f"Slot type '{slot_type.name}' has: {known}"
            )
        slots[slot_name] = raw_value
    return name, slots


def _rows(path, spec: Spec) -> list[tuple[str, dict, str, str]]:
    """Every (command, slot values, sentence, where) in one file."""
    out: list[tuple[str, dict, str, str]] = []
    for index, block in enumerate(_blocks(path), start=1):
        if not isinstance(block, dict):
            raise SpecError(
                f"{path}: block {index} must be a mapping with an 'answer' and a 'say' list"
            )
        raw_answer = block.get("answer")
        if raw_answer is None:
            raise SpecError(f"{path}: block {index} has no 'answer'")
        answer = _text(raw_answer, f"{path}, block {index}")
        where = f"{path}, answer '{answer}'"
        command, slots = parse_answer(answer, spec, where)

        say = block.get("say")
        if isinstance(say, str):
            say = [say]
        if not isinstance(say, list) or not say:
            raise SpecError(f"{where}: needs a 'say' list with at least one sentence")
        for item in say:
            if isinstance(item, list):
                raise SpecError(
                    f"{where}: a sentence that starts with '[' has to be in quotes, "
                    "or YAML reads it as a list"
                )
            sentence = _text(item, where)
            if not sentence.strip():
                raise SpecError(f"{where}: a sentence is empty")
            out.append((command, slots, sentence, where))
    return out


def _register(spec: Spec, command_name: str, slots: dict, text: str, where: str) -> None:
    """Teach the spec every surface form the markup in one sentence spells out."""
    if command_name == NONE_COMMAND:
        return
    command = spec.command(command_name)
    for match in MARKUP.finditer(text):
        surface = " ".join(match.group(1).lower().split())
        slot_name = match.group(2).strip()
        if not surface:
            raise SpecError(f"{where}, sentence '{text}': a marked span has no words in it")
        if slot_name not in slots:
            raise SpecError(
                f"{where}, sentence '{text}': slot '{slot_name}' is marked in the sentence "
                "but is not in the answer. Put it in the answer, or drop the markup."
            )
        slot = command.slot(slot_name)
        slot_type = spec.slot_types[slot.type]
        value = slots[slot_name]

        if slot_type.kind == NUMBER:
            if parse_number(surface.split()) != value:
                raise SpecError(
                    f"{where}, sentence '{text}': '{surface}' does not read as {value}. "
                    "A number slot cannot be taught a new spelling; mark the number itself."
                )
            continue

        known = slot_type.find_canonical(surface)
        if known == value:
            continue
        if known is not None:
            raise SpecError(
                f"{where}, sentence '{text}': '{surface}' already means '{known}' under "
                f"slot type '{slot_type.name}', so it cannot also mean '{value}'"
            )
        slot_type.values[value].append(surface)


def _find(tokens: list[str], taken: set[int], slot_type, value) -> tuple[int, int] | None:
    """Where a value is said in a sentence, longest match first, or None."""
    if slot_type.kind == NUMBER:
        longest = min(MAX_NUMBER_TOKENS, len(tokens))
        for size in range(longest, 0, -1):
            for start in range(0, len(tokens) - size + 1):
                if any(i in taken for i in range(start, start + size)):
                    continue
                if parse_number(tokens[start : start + size]) == value:
                    return start, start + size
        return None

    forms = sorted(slot_type.values.get(value, []), key=lambda f: (-len(f.split()), f))
    for form in forms:
        want = form.split()
        size = len(want)
        for start in range(0, len(tokens) - size + 1):
            if tokens[start : start + size] != want:
                continue
            if any(i in taken for i in range(start, start + size)):
                continue
            return start, start + size
    return None


def _label(spec: Spec, command_name: str, slots: dict, text: str, frame: str, where: str) -> Example:
    """Turn one sentence plus its answer into tokens, BIO tags and canonical values."""
    tokens: list[str] = []
    tags: list[str] = []
    claimed: dict[str, tuple[int, int]] = {}
    plain_parts: list[str] = []
    position = 0

    for match in MARKUP.finditer(text):
        plain = text[position : match.start()]
        plain_parts.append(plain)
        for token in tokenize(plain):
            tokens.append(token)
            tags.append(OUTSIDE)
        surface_tokens = tokenize(match.group(1))
        slot_name = match.group(2).strip()
        if not surface_tokens:
            raise SpecError(f"{where}, sentence '{text}': a marked span has no words in it")
        if command_name == NONE_COMMAND:
            raise SpecError(
                f"{where}, sentence '{text}': a '{NONE_COMMAND}' sentence has no slots to mark"
            )
        if slot_name in claimed:
            raise SpecError(f"{where}, sentence '{text}': slot '{slot_name}' is marked twice")
        start = len(tokens)
        for token in surface_tokens:
            tokens.append(token)
            tags.append(OUTSIDE)
        claimed[slot_name] = (start, len(tokens))
        position = match.end()

    tail = text[position:]
    plain_parts.append(tail)
    for token in tokenize(tail):
        tokens.append(token)
        tags.append(OUTSIDE)

    stray = BRACKETS.intersection("".join(plain_parts))
    if stray:
        raise SpecError(
            f"{where}, sentence '{text}': stray bracket {sorted(stray)[0]!r}, "
            "the markup is written as [the words](slot_name)"
        )
    if not tokens:
        raise SpecError(f"{where}: a sentence is empty")

    if command_name != NONE_COMMAND:
        command = spec.command(command_name)
        taken = {i for start, end in claimed.values() for i in range(start, end)}
        for slot_name, value in slots.items():
            if slot_name in claimed:
                continue
            slot = command.slot(slot_name)
            slot_type = spec.slot_types[slot.type]
            found = _find(tokens, taken, slot_type, value)
            if found is None:
                raise SpecError(
                    f"{where}, sentence '{text}': the answer says {slot_name}={value} but no "
                    "words in the sentence say it. Write it as [the words]"
                    f"({slot_name}), or add those words to slot type '{slot_type.name}'."
                )
            claimed[slot_name] = found
            taken.update(range(found[0], found[1]))

        for slot_name, (start, end) in claimed.items():
            slot_type_name = command.slot(slot_name).type
            for index in range(start, end):
                tags[index] = ("B-" if index == start else "I-") + slot_type_name

    spans = [
        Span(start=start, end=end, slot=name, value=slots[name])
        for name, (start, end) in sorted(claimed.items(), key=lambda pair: pair[1][0])
    ]
    return Example(
        tokens=tokens,
        tags=tags,
        command=command_name,
        slots=dict(slots),
        spans=spans,
        text=strip_markup(text),
        frame=frame,
    )


def load(paths, spec: Spec, register: bool = True) -> list[Example]:
    """Read one or more answer-first files against a spec.

    With `register` on, every marked surface is added to its slot type first, so a
    later sentence can use it without markup. A dev set is read with it off: teaching
    the model a word from the set you are measuring on makes the measurement a lie.
    """
    rows: list[tuple[str, dict, str, str]] = []
    for path in paths:
        rows.extend(_rows(path, spec))

    if register:
        for command_name, slots, text, where in rows:
            _register(spec, command_name, slots, text, where)

    out: list[Example] = []
    counters: dict[str, int] = {}
    for command_name, slots, text, where in rows:
        index = counters.get(command_name, 0)
        counters[command_name] = index + 1
        out.append(_label(spec, command_name, slots, text, f"{command_name}#x{index}", where))
    return out


def load_answers(paths, spec: Spec) -> list[Example]:
    """Read answer-first files without working out where each slot value is said.

    `load` has to find the words behind every slot so it can write BIO tags, and it
    refuses a sentence whose wording it cannot place. That is right for training, and
    wrong for measuring: a test set written by somebody who has never seen the commands
    file is full of wordings the file does not list, and throwing those sentences away
    would quietly drop the hardest ones.

    What comes back here carries the command and the canonical slot values only, with
    every tag O and `labelled` off. Scoring it compares the answer against the answer,
    which is what the caller of the device actually gets.
    """
    out: list[Example] = []
    counters: dict[str, int] = {}
    for path in paths:
        for command_name, slots, text, _where in _rows(path, spec):
            index = counters.get(command_name, 0)
            counters[command_name] = index + 1
            plain = strip_markup(text)
            tokens = tokenize(plain)
            if not tokens:
                raise SpecError(f"{path}: a sentence is empty")
            out.append(
                Example(
                    tokens=tokens,
                    tags=[OUTSIDE] * len(tokens),
                    command=command_name,
                    slots=dict(slots),
                    text=plain,
                    frame=f"{command_name}#a{index}",
                    labelled=False,
                )
            )
    return out


def apply(spec: Spec, examples) -> int:
    """Fold answer-first examples into a spec so they train like hand-written ones."""
    added = 0
    for example in examples:
        if example.command == NONE_COMMAND:
            text = " ".join(example.tokens)
            if text not in spec.none_examples:
                spec.none_examples.append(text)
                added += 1
            continue
        command = spec.command(example.command)
        if command is None:
            raise SpecError(f"'{example.command}' is not a command in {spec.source}")
        command.examples.append(example)
        added += 1
    return added
