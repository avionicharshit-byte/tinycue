"""Grow the hand-written examples into a training set by swapping slot values around."""

from __future__ import annotations

import random

from .numbers import STYLES, render_number
from .schema import (
    NONE_COMMAND,
    NUMBER,
    OUTSIDE,
    Command,
    Example,
    Spec,
    SlotType,
    Span,
)

# Sentences that are not any command. They teach the model to say "no command" instead
# of forcing a stray sentence into the closest one.
NONE_SENTENCES = [
    "what is the weather like today",
    "tell me a joke",
    "who won the match last night",
    "good morning",
    "hello there",
    "how are you doing",
    "what is your name",
    "thank you very much",
    "never mind",
    "sing me a song",
    "how far is the moon",
    "spell the word banana",
    "call my friend",
    "read me the news",
    "how old are you",
    "stop talking",
    "are you a robot",
    "i am going out for a walk",
    "aaj mausam kaisa hai",
    "koi joke sunao",
    "namaste kaise ho",
    "tum kaun ho",
    "kuch gaana bajao",
    "mujhe bhookh lagi hai",
    "kal chhutti hai kya",
    "thoda ruko",
    "kya kar rahe ho",
    "mera naam batao",
    "achha theek hai",
    "chalo baat karte hain",
    "bahar kaafi shor hai",
    "kal milte hain",
]

# How many tries per wanted example before we accept that the phrasings have run out.
_ATTEMPTS_PER_EXAMPLE = 25


def generate(spec: Spec, n_per_command: int = 200, seed: int = 0) -> list[Example]:
    """Build training examples. Same spec and same seed give exactly the same list."""
    rng = random.Random(seed)
    out: list[Example] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    def keep(example: Example) -> bool:
        key = (example.command, tuple(example.tokens))
        if key in seen:
            return False
        seen.add(key)
        out.append(example)
        return True

    for command in spec.commands:
        kept = 0
        for example in command.examples:
            if kept >= n_per_command:
                break
            if keep(example):
                kept += 1
        if not command.examples:
            continue
        attempts = max(0, n_per_command - kept) * _ATTEMPTS_PER_EXAMPLE
        index = 0
        while kept < n_per_command and attempts > 0:
            base = command.examples[index % len(command.examples)]
            index += 1
            attempts -= 1
            variant = _variant(base, command, spec, rng)
            if variant is not None and keep(variant):
                kept += 1

    if spec.fallback.none_command:
        for text in NONE_SENTENCES[:n_per_command]:
            tokens = text.split()
            keep(
                Example(
                    tokens=tokens,
                    tags=[OUTSIDE] * len(tokens),
                    command=NONE_COMMAND,
                    slots={},
                    spans=[],
                    text=text,
                )
            )

    return out


def _variant(base: Example, command: Command, spec: Spec, rng: random.Random) -> Example | None:
    """One copy of an example with every slot span swapped for another value."""
    tokens: list[str] = []
    tags: list[str] = []
    spans: list[Span] = []
    slots: dict[str, str | int] = {}
    position = 0

    for span in base.spans:
        for token in base.tokens[position : span.start]:
            tokens.append(token)
            tags.append(OUTSIDE)

        slot = command.slot(span.slot)
        if slot is None:
            return None
        picked = _pick(spec.slot_types[slot.type], rng)
        if picked is None:
            return None
        value, surface = picked

        start = len(tokens)
        for index, token in enumerate(surface.split()):
            tokens.append(token)
            tags.append(("B-" if index == 0 else "I-") + span.slot)
        spans.append(Span(start=start, end=len(tokens), slot=span.slot, value=value))
        slots[span.slot] = value
        position = span.end

    for token in base.tokens[position:]:
        tokens.append(token)
        tags.append(OUTSIDE)

    if not tokens:
        return None
    return Example(
        tokens=tokens,
        tags=tags,
        command=command.name,
        slots=slots,
        spans=spans,
        text=" ".join(tokens),
    )


def _pick(slot_type: SlotType, rng: random.Random) -> tuple[str | int, str] | None:
    """A random (canonical value, surface form) pair for a slot type."""
    if slot_type.kind == NUMBER:
        return _pick_number(slot_type, rng)
    pairs = slot_type.surfaces()
    if not pairs:
        return None
    canonical, surface = rng.choice(pairs)
    return canonical, surface


def _pick_number(slot_type: SlotType, rng: random.Random) -> tuple[int, str] | None:
    """A random in-range number written as digits, English words or Hindi words."""
    low = slot_type.min if slot_type.min is not None else 0
    high = slot_type.max if slot_type.max is not None else 100
    if high < low:
        return None
    style = rng.choice(STYLES)
    choices = [n for n in range(low, high + 1) if render_number(n, style)]
    if not choices:
        value = rng.randint(low, high)
        return value, str(value)
    value = rng.choice(choices)
    return value, render_number(value, style)
