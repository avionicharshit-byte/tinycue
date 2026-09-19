"""Read a commands file: the YAML structure and the [surface text](slot) markup in examples."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import lang as lang_resources
from .numbers import parse_number
from .schema import (
    NONE_COMMAND,
    NUMBER,
    OUTSIDE,
    VALUES,
    Command,
    CommandSlot,
    Example,
    Fallback,
    Spec,
    SlotType,
    Span,
    SpecError,
)

MARKUP = re.compile(r"\[([^\[\]]*)\]\(([^()]*)\)")
BRACKETS = set("[]()")


def tokenize(text: str) -> list[str]:
    """Lowercase and split on whitespace. The device runtime does the same."""
    return text.lower().split()


def strip_markup(text: str) -> str:
    """The sentence with the markup removed, so it reads as plain text."""
    return MARKUP.sub(lambda m: m.group(1), text)


def load_spec(path) -> Spec:
    """Load and validate a commands file."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: the file is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError(f"{path}: the file must be a mapping with a 'commands' section")

    spec = Spec()
    spec.source = str(path)
    spec.languages = _languages(raw.get("language"))
    spec.slot_types = _slot_types(raw.get("slots"))
    spec.fallback = _fallback(raw.get("fallback"))
    spec.commands = _commands(raw.get("commands"), spec)

    resources = lang_resources.merged(spec.languages)
    spec.fillers = _merge_words(resources["fillers"], raw.get("fillers"), "fillers")
    spec.droppable = _merge_words(resources["droppable"], raw.get("droppable"), "droppable")
    spec.none_examples = _merge_words(
        resources["none_examples"], raw.get("none_examples"), "none_examples"
    )
    spec.equivalents = _equivalents(raw.get("equivalents"))
    spec.extra_files = _paths(raw.get("extra"), path, "extra")
    return spec


def _paths(raw, base: Path, where: str) -> list[str]:
    """File paths listed in a commands file, read relative to that file."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise SpecError(f"'{where}' must be a file path or a list of file paths")
    out: list[str] = []
    for item in raw:
        text = _text(item, where).strip()
        if not text:
            raise SpecError(f"'{where}': a path is empty")
        found = Path(text)
        if not found.is_absolute():
            found = Path(base).parent / found
        out.append(str(found))
    return out


def _merge_words(built_in: list[str], extra, where: str) -> list[str]:
    """The language file's list plus whatever the commands file adds, duplicates removed."""
    if extra is None:
        extra = []
    if isinstance(extra, str):
        extra = [extra]
    if not isinstance(extra, list):
        raise SpecError(f"'{where}' must be a list of sentences or words")
    out: list[str] = []
    for item in list(built_in) + list(extra):
        text = " ".join(_text(item, where).lower().split())
        if text and text not in out:
            out.append(text)
    return out


def _equivalents(raw) -> list[list[str]]:
    """Groups of words that mean the same thing, so the generator can swap them."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecError("'equivalents' must be a list of word groups")
    groups: list[list[str]] = []
    for group in raw:
        if isinstance(group, str):
            group = group.split(",")
        if not isinstance(group, list) or len(group) < 2:
            raise SpecError("each group under 'equivalents' needs at least two words")
        words = []
        for word in group:
            text = " ".join(_text(word, "equivalents").lower().split())
            if not text:
                raise SpecError("a word under 'equivalents' is empty")
            if text not in words:
                words.append(text)
        if len(words) < 2:
            raise SpecError("each group under 'equivalents' needs at least two different words")
        groups.append(words)
    return groups


def _languages(raw) -> list[str]:
    if raw is None:
        return ["en"]
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return list(raw)
    raise SpecError("language must be a string or a list of strings, such as [en, hinglish]")


def _text(value, where: str) -> str:
    """Keep YAML from turning words like on, off, yes and no into booleans."""
    if isinstance(value, bool):
        raise SpecError(
            f"{where}: '{value}' was read as a true/false value. "
            'Put it in quotes, for example "on".'
        )
    if value is None:
        raise SpecError(f"{where}: the value is empty")
    return str(value)


def _slot_types(raw) -> dict[str, SlotType]:
    if not raw:
        raise SpecError("the file has no 'slots' section")
    if not isinstance(raw, dict):
        raise SpecError("'slots' must be a mapping of slot name to its definition")

    types: dict[str, SlotType] = {}
    for name, body in raw.items():
        name = _text(name, "slot name")
        if not isinstance(body, dict):
            raise SpecError(f"slot '{name}': expected a mapping with 'values' or 'type: number'")

        kind = body.get("type", VALUES if "values" in body else None)
        if kind is None:
            raise SpecError(f"slot '{name}': needs either a 'values' list or 'type: number'")
        if kind not in (VALUES, NUMBER):
            raise SpecError(f"slot '{name}': unknown type '{kind}', expected 'number'")

        if kind == NUMBER:
            if "values" in body:
                raise SpecError(f"slot '{name}': a number slot cannot also have 'values'")
            types[name] = SlotType(
                name=name,
                kind=NUMBER,
                min=_bound(body.get("min"), name, "min"),
                max=_bound(body.get("max"), name, "max"),
            )
            continue

        values = body.get("values")
        if not isinstance(values, dict) or not values:
            raise SpecError(f"slot '{name}': 'values' must map each canonical value to its words")
        table: dict[str, list[str]] = {}
        for canonical, forms in values.items():
            canonical = _text(canonical, f"slot '{name}' value")
            if isinstance(forms, str):
                forms = [forms]
            if not isinstance(forms, list) or not forms:
                raise SpecError(
                    f"slot '{name}', value '{canonical}': needs a list of the words people say"
                )
            table[canonical] = [
                " ".join(_text(f, f"slot '{name}' value '{canonical}'").lower().split())
                for f in forms
            ]
        types[name] = SlotType(name=name, kind=VALUES, values=table)
    return types


def _bound(value, slot: str, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpecError(f"slot '{slot}': '{key}' must be a whole number")
    return value


def _fallback(raw) -> Fallback:
    if raw is None:
        return Fallback()
    if not isinstance(raw, dict):
        raise SpecError("'fallback' must be a mapping")
    unsure = raw.get("unsure_below", "auto")
    if isinstance(unsure, str):
        if unsure != "auto":
            raise SpecError("fallback.unsure_below must be 'auto' or a number between 0 and 1")
    elif isinstance(unsure, (int, float)) and not isinstance(unsure, bool):
        if not 0.0 <= float(unsure) <= 1.0:
            raise SpecError("fallback.unsure_below must be between 0 and 1")
        unsure = float(unsure)
    else:
        raise SpecError("fallback.unsure_below must be 'auto' or a number between 0 and 1")

    none_command = raw.get("none_command", True)
    if not isinstance(none_command, bool):
        raise SpecError("fallback.none_command must be true or false")
    on_unsure = raw.get("on_unsure", "ask")
    if on_unsure not in ("ask", "cloud"):
        raise SpecError("fallback.on_unsure must be 'ask' or 'cloud'")
    return Fallback(unsure_below=unsure, none_command=none_command, on_unsure=on_unsure)


def _commands(raw, spec: Spec) -> list[Command]:
    if not raw:
        raise SpecError("the file has no 'commands' section")
    if not isinstance(raw, list):
        raise SpecError("'commands' must be a list")

    commands: list[Command] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise SpecError("each command must be a mapping with a 'name' and 'examples'")
        name = entry.get("name")
        if not name or not isinstance(name, str):
            raise SpecError("a command is missing its 'name'")
        if name in seen:
            raise SpecError(f"command '{name}': the name is used twice")
        seen.add(name)

        command = Command(name=name, slots=_command_slots(entry.get("slots"), name, spec))
        examples = entry.get("examples")
        if not examples:
            raise SpecError(f"command '{name}': needs at least one example sentence")
        if not isinstance(examples, list):
            raise SpecError(f"command '{name}': 'examples' must be a list of sentences")
        for index, text in enumerate(examples):
            example = parse_example(_text(text, f"command '{name}'"), command, spec)
            example.frame = f"{name}#{index}"
            command.examples.append(example)
        commands.append(command)
    return commands


def _command_slots(raw, command: str, spec: Spec) -> list[CommandSlot]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecError(f"command '{command}': 'slots' must be a list")

    slots: list[CommandSlot] = []
    for entry in raw:
        if isinstance(entry, str):
            slot = CommandSlot(name=entry, type=entry, required=True)
        elif isinstance(entry, dict):
            name = entry.get("name")
            if not name or not isinstance(name, str):
                raise SpecError(f"command '{command}': a slot is missing its 'name'")
            required = entry.get("required", True)
            if not isinstance(required, bool):
                raise SpecError(f"command '{command}', slot '{name}': 'required' must be true or false")
            slot = CommandSlot(name=name, type=entry.get("type", name), required=required)
        else:
            raise SpecError(f"command '{command}': a slot must be a name or a mapping")

        if slot.type not in spec.slot_types:
            raise SpecError(
                f"command '{command}', slot '{slot.name}': "
                f"unknown slot type '{slot.type}', it is not in the 'slots' section"
            )
        if any(s.name == slot.name for s in slots):
            raise SpecError(f"command '{command}': slot '{slot.name}' is listed twice")
        clash = next((s for s in slots if s.type == slot.type), None)
        if clash is not None:
            raise SpecError(
                f"command '{command}': slots '{clash.name}' and '{slot.name}' both use type "
                f"'{slot.type}'. Tags are written from the slot type, so one command can use "
                f"each type only once. Give one of them its own type under 'slots'."
            )
        slots.append(slot)
    return slots


def parse_example(text: str, command: Command, spec: Spec) -> Example:
    """Turn one marked-up sentence into tokens, BIO tags and canonical slot values."""
    where = f"command '{command.name}', example '{text}'"
    if not text.strip():
        raise SpecError(f"command '{command.name}': an example is empty")

    tokens: list[str] = []
    tags: list[str] = []
    spans: list[Span] = []
    slots: dict[str, object] = {}
    plain_parts: list[str] = []
    position = 0

    for match in MARKUP.finditer(text):
        plain = text[position : match.start()]
        plain_parts.append(plain)
        for token in tokenize(plain):
            tokens.append(token)
            tags.append(OUTSIDE)

        surface = match.group(1).strip()
        slot_name = match.group(2).strip()
        slot_tokens = tokenize(surface)
        if not slot_tokens:
            raise SpecError(f"{where}: a slot span has no words in it")
        if not slot_name:
            raise SpecError(f"{where}: a slot span has no slot name after it")

        slot = command.slot(slot_name)
        if slot is None:
            if slot_name in spec.slot_types:
                raise SpecError(
                    f"{where}: slot '{slot_name}' is not declared on command '{command.name}'"
                )
            raise SpecError(f"{where}: unknown slot '{slot_name}'")
        if slot_name in slots:
            raise SpecError(f"{where}: slot '{slot_name}' is marked twice")

        slot_type = spec.slot_types[slot.type]
        value = _slot_value(slot_type, surface, slot_name, where)

        start = len(tokens)
        for index, token in enumerate(slot_tokens):
            tokens.append(token)
            tags.append(("B-" if index == 0 else "I-") + slot.type)
        spans.append(Span(start=start, end=len(tokens), slot=slot_name, value=value))
        slots[slot_name] = value
        position = match.end()

    tail = text[position:]
    plain_parts.append(tail)
    for token in tokenize(tail):
        tokens.append(token)
        tags.append(OUTSIDE)

    stray = BRACKETS.intersection("".join(plain_parts))
    if stray:
        raise SpecError(
            f"{where}: stray bracket {sorted(stray)[0]!r}, "
            "the markup is written as [surface text](slot_name)"
        )
    if not tokens:
        raise SpecError(f"command '{command.name}': an example is empty")

    # 'required' is a runtime rule, not a file rule. An example may leave a required slot
    # out on purpose, and decoding that sentence then reports the slot as missing.
    return Example(
        tokens=tokens,
        tags=tags,
        command=command.name,
        slots=dict(slots),
        spans=spans,
        text=strip_markup(text),
    )


def _slot_value(slot_type: SlotType, surface: str, slot_name: str, where: str):
    """The canonical value behind a surface form, or a clear error saying why not."""
    if slot_type.kind == NUMBER:
        value = parse_number(tokenize(surface))
        if value is None:
            raise SpecError(
                f"{where}: '{surface}' is not a number that slot '{slot_name}' understands"
            )
        if slot_type.min is not None and value < slot_type.min:
            raise SpecError(f"{where}: {value} is below the minimum {slot_type.min}")
        if slot_type.max is not None and value > slot_type.max:
            raise SpecError(f"{where}: {value} is above the maximum {slot_type.max}")
        return value

    canonical = slot_type.find_canonical(surface)
    if canonical is None:
        raise SpecError(
            f"{where}: '{surface}' is not listed under slot '{slot_name}'. "
            f"Add it to the '{slot_type.name}' values."
        )
    return canonical


def load_examples_file(path, spec: Spec) -> list[Example]:
    """Read a hand-written test set: the same markup, checked against an existing spec.

    The file lists commands and their sentences, plus an optional 'none' block for
    sentences that are not any command. Never train on this.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: the file is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), list):
        raise SpecError(f"{path}: expected a 'commands' list")

    out: list[Example] = []
    for entry in raw["commands"]:
        if not isinstance(entry, dict):
            raise SpecError(f"{path}: each command must be a mapping with 'name' and 'examples'")
        name = entry.get("name")
        sentences = entry.get("examples")
        if not name or not isinstance(sentences, list) or not sentences:
            raise SpecError(f"{path}: command '{name}' needs a list of examples")

        if name == NONE_COMMAND:
            for index, text in enumerate(sentences):
                tokens = tokenize(_text(text, f"{path}, none example"))
                if not tokens:
                    raise SpecError(f"{path}: a 'none' example is empty")
                out.append(
                    Example(
                        tokens=tokens,
                        tags=[OUTSIDE] * len(tokens),
                        command=NONE_COMMAND,
                        text=" ".join(tokens),
                        frame=f"{NONE_COMMAND}#{index}",
                    )
                )
            continue

        command = spec.command(name)
        if command is None:
            raise SpecError(f"{path}: '{name}' is not a command in {spec.source}")
        for index, text in enumerate(sentences):
            example = parse_example(_text(text, f"{path}, command '{name}'"), command, spec)
            example.frame = f"{name}#{index}"
            out.append(example)
    return out
