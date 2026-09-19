"""Data types for a commands file: slot types, commands, examples and the fallback block."""

from __future__ import annotations

from dataclasses import dataclass, field

VALUES = "values"
NUMBER = "number"

NONE_COMMAND = "none"
OUTSIDE = "O"


class SpecError(ValueError):
    """A commands file that cannot be used. The message says which command and example."""


@dataclass
class SlotType:
    """A kind of value a command can carry, either a value list or a number range."""

    name: str
    kind: str = VALUES
    # canonical value -> the surface forms people say for it
    values: dict[str, list[str]] = field(default_factory=dict)
    min: int | None = None
    max: int | None = None

    def surfaces(self) -> list[tuple[str, str]]:
        """Every (canonical value, surface form) pair, in file order."""
        pairs = []
        for canonical, forms in self.values.items():
            for form in forms:
                pairs.append((canonical, form))
        return pairs

    def find_canonical(self, surface: str) -> str | None:
        """The canonical value for a surface form, or None if it is not listed."""
        wanted = " ".join(surface.lower().split())
        for canonical, forms in self.values.items():
            for form in forms:
                if " ".join(str(form).lower().split()) == wanted:
                    return canonical
        return None


@dataclass
class CommandSlot:
    """A slot as used by one command. The markup name may differ from the slot type."""

    name: str
    type: str
    required: bool = True


@dataclass
class Span:
    """A slot span inside an example: token range, slot name and canonical value."""

    start: int
    end: int  # exclusive
    slot: str
    value: str | int


@dataclass
class Example:
    """One tokenised sentence with BIO tags, its command and its canonical slot values.

    Tags carry the slot TYPE, not the slot name, so commands that use the same type share
    evidence. The name comes back at decode time.
    """

    tokens: list[str]
    tags: list[str]
    command: str
    slots: dict[str, str | int] = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)
    text: str = ""
    # Which hand-written sentence this grew from. Splits are made by frame.
    frame: str = ""

    def as_dict(self) -> dict:
        return {
            "tokens": self.tokens,
            "tags": self.tags,
            "command": self.command,
            "slots": self.slots,
        }


@dataclass
class Command:
    """One thing the device can do, with its slots and hand-written examples."""

    name: str
    slots: list[CommandSlot] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)

    def slot(self, name: str) -> CommandSlot | None:
        for s in self.slots:
            if s.name == name:
                return s
        return None

    def slot_for_type(self, type_name: str) -> CommandSlot | None:
        """The slot that uses a type. One type is used at most once per command."""
        for s in self.slots:
            if s.type == type_name:
                return s
        return None


@dataclass
class Fallback:
    """What happens when the model is not sure."""

    unsure_below: float | str = "auto"
    none_command: bool = True
    on_unsure: str = "ask"


@dataclass
class Spec:
    """A whole commands file after parsing, plus the language resources it pulled in."""

    languages: list[str] = field(default_factory=lambda: ["en"])
    slot_types: dict[str, SlotType] = field(default_factory=dict)
    commands: list[Command] = field(default_factory=list)
    fallback: Fallback = field(default_factory=Fallback)
    # Groups of words that mean the same thing, from the file plus the language files.
    equivalents: list[list[str]] = field(default_factory=list)
    # Words that can be dropped into a sentence without changing its meaning.
    fillers: list[str] = field(default_factory=list)
    # Carrier words that can be removed without changing the meaning.
    droppable: list[str] = field(default_factory=list)
    # Out-of-scope sentences for the "no command" class.
    none_examples: list[str] = field(default_factory=list)
    # Where the file was read from, used for the model digest.
    source: str = ""

    def command(self, name: str) -> Command | None:
        for c in self.commands:
            if c.name == name:
                return c
        return None

    def command_names(self) -> list[str]:
        """Every command name, plus 'none' when the fallback asks for it."""
        names = [c.name for c in self.commands]
        if self.fallback.none_command:
            names.append(NONE_COMMAND)
        return names

    def tag_set(self) -> list[str]:
        """Every BIO tag the tagger can produce, in a stable order."""
        tags = [OUTSIDE]
        for name in sorted(self.slot_types):
            tags.append("B-" + name)
            tags.append("I-" + name)
        return tags
