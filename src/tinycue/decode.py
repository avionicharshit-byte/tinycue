"""Turn BIO tags into slot values: spans, canonical values, slot names, missing slots.

Tags carry the slot TYPE. The command says which of its slot names uses that type, so the
name only comes back here. A value that is not in the word list is kept as it was
said, so a name nobody listed still arrives.

Trimming
--------
A tagger often pulls one carrier word into a span, so "up karo" comes back where "up" was
meant. When the whole span matches nothing, the span is retried shorter: first with
trailing words dropped one at a time, then with leading words dropped one at a time. The
first shorter span that matches wins. The C runtime does the same, in the same order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .numbers import parse_number
from .schema import NONE_COMMAND, NUMBER, OUTSIDE, Spec


@dataclass
class Slot:
    """One filled slot: where it was, what was said, and the value the caller gets."""

    name: str
    type: str
    surface: str
    value: str | int
    start: int
    end: int
    known: bool = True


@dataclass
class Decoded:
    """The result of reading one sentence."""

    command: str
    slots: dict[str, str | int] = field(default_factory=dict)
    found: list[Slot] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    stray: list[str] = field(default_factory=list)

    def call(self) -> str:
        """The command as a call, for example move(heading=left, steps=3)."""
        if self.command == NONE_COMMAND:
            return NONE_COMMAND
        inner = ", ".join(f"{name}={value}" for name, value in self.slots.items())
        return f"{self.command}({inner})"


def spans_from_tags(tokens, tags) -> list[tuple[str, int, int]]:
    """Every (slot type, start, end) run in a BIO tag sequence. End is exclusive."""
    spans: list[tuple[str, int, int]] = []
    current: str | None = None
    start = 0
    for index, tag in enumerate(tags):
        if tag.startswith("B-"):
            if current is not None:
                spans.append((current, start, index))
            current = tag[2:]
            start = index
        elif tag.startswith("I-"):
            # A stray I- with no B- in front of it still starts a span, so a tagger
            # slip costs one word rather than the whole sentence.
            if current is None:
                current = tag[2:]
                start = index
            elif current != tag[2:]:
                spans.append((current, start, index))
                current = tag[2:]
                start = index
        else:
            if current is not None:
                spans.append((current, start, index))
            current = None
    if current is not None:
        spans.append((current, start, len(tags)))
    return spans


def span_value(tokens, start: int, end: int, slot_type):
    """What a span is worth to a slot type, or None when the type does not know it."""
    if slot_type.kind == NUMBER:
        value = parse_number(tokens[start:end])
        if value is None:
            return None
        if slot_type.min is not None and value < slot_type.min:
            return None
        if slot_type.max is not None and value > slot_type.max:
            return None
        return value
    return slot_type.find_canonical(" ".join(tokens[start:end]))


def trim_span(tokens, start: int, end: int, slot_type):
    """The first matching span, dropping trailing words first, then leading ones."""
    for stop in range(end, start, -1):
        value = span_value(tokens, start, stop, slot_type)
        if value is not None:
            return start, stop, value
    for begin in range(start + 1, end):
        value = span_value(tokens, begin, end, slot_type)
        if value is not None:
            return begin, end, value
    return None


def decode(tokens, tags, command_name: str, spec: Spec) -> Decoded:
    """Read one tagged sentence into a command call."""
    result = Decoded(command=command_name)
    if command_name == NONE_COMMAND:
        return result

    command = spec.command(command_name)
    if command is None:
        result.stray = [t[2:] for t in tags if t != OUTSIDE and t.startswith("B-")]
        return result

    for type_name, start, end in spans_from_tags(tokens, tags):
        slot = command.slot_for_type(type_name)
        surface = " ".join(tokens[start:end])
        if slot is None:
            result.stray.append(type_name)
            continue
        if slot.name in result.slots:
            # Two spans of the same type. The first one wins.
            continue
        slot_type = spec.slot_types.get(slot.type)
        if slot_type is None:
            result.stray.append(type_name)
            continue

        match = trim_span(tokens, start, end, slot_type)
        if match is not None:
            start, end, value = match
            surface = " ".join(tokens[start:end])
            known = True
        elif slot_type.kind == NUMBER:
            # A number slot has no open vocabulary: words that read as no number at all
            # are a tagger slip, not a value.
            result.stray.append(type_name)
            continue
        else:
            value = surface
            known = False

        result.found.append(
            Slot(
                name=slot.name,
                type=slot.type,
                surface=surface,
                value=value,
                start=start,
                end=end,
                known=known,
            )
        )
        result.slots[slot.name] = value

    result.missing = [s.name for s in command.slots if s.required and s.name not in result.slots]
    return result
