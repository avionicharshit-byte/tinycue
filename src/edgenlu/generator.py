"""Grow the hand-written examples into a training set.

Three things happen to every sentence:
  1. slot swapping: every marked span is replaced by another value of the same slot type
  2. augmentation: filler words go in, carrier words come out, equivalent words swap
  3. balancing: every command, including "none", is grown to the same size

Augmentation never touches a token inside a slot span, so the tags stay correct. The
filler, droppable and equivalent word lists come from the language files and the commands
file, never from this code, so nothing here is tied to a domain.
"""

from __future__ import annotations

import random
import sys

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

# How many tries per wanted example before we accept that the phrasings have run out.
_ATTEMPTS_PER_EXAMPLE = 30
# Give up on a class after this many tries in a row that produced nothing new.
_DEAD_END = 4000
# How many augmentation steps one sentence can get.
_MAX_OPS = 3


class _Words:
    """The word lists a spec makes available to the augmenter, ready to use."""

    def __init__(self, spec: Spec):
        self.fillers = [f.split() for f in spec.fillers if f.split()]
        self.droppable = [d.split() for d in spec.droppable if d.split()]
        self.equivalents = []
        for group in spec.equivalents:
            self.equivalents.append([g.split() for g in group if g.split()])

    def any(self) -> bool:
        return bool(self.fillers or self.droppable or self.equivalents)


def generate(
    spec: Spec,
    n_per_command: int = 200,
    seed: int = 0,
    warn=None,
) -> list[Example]:
    """Build training examples. Same spec and same seed give exactly the same list."""
    rng = random.Random(seed)
    words = _Words(spec)
    if warn is None:
        def warn(message):
            print(f"warning: {message}", file=sys.stderr)

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
        bases = command.examples
        if not bases:
            continue
        kept = _fill(
            bases,
            n_per_command,
            keep,
            lambda base: _variant(base, command, spec, words, rng),
        )
        if kept < n_per_command:
            warn(
                f"command '{command.name}' reached {kept} of {n_per_command} examples. "
                "Add more example sentences, slot values or equivalents."
            )

    if spec.fallback.none_command:
        bases = _none_bases(spec)
        if bases:
            kept = _fill(
                bases,
                n_per_command,
                keep,
                lambda base: _augmented(base, words, rng),
            )
            if kept < n_per_command:
                warn(
                    f"command '{NONE_COMMAND}' reached {kept} of {n_per_command} examples. "
                    "Add more sentences under 'none_examples'."
                )

    return out


def _fill(bases, target: int, keep, make) -> int:
    """Keep the hand-written sentences, then grow variants until the target is reached."""
    kept = 0
    for base in bases:
        if kept >= target:
            break
        if keep(base):
            kept += 1

    attempts = max(0, target - kept) * _ATTEMPTS_PER_EXAMPLE
    misses = 0
    index = 0
    while kept < target and attempts > 0 and misses < _DEAD_END:
        base = bases[index % len(bases)]
        index += 1
        attempts -= 1
        variant = make(base)
        if variant is not None and keep(variant):
            kept += 1
            misses = 0
        else:
            misses += 1
    return kept


def _none_bases(spec: Spec) -> list[Example]:
    """The out-of-scope sentences as examples, all tagged outside any slot."""
    bases: list[Example] = []
    for index, text in enumerate(spec.none_examples):
        tokens = text.split()
        if not tokens:
            continue
        bases.append(
            Example(
                tokens=tokens,
                tags=[OUTSIDE] * len(tokens),
                command=NONE_COMMAND,
                slots={},
                spans=[],
                text=text,
                frame=f"{NONE_COMMAND}#{index}",
            )
        )
    return bases


def _variant(
    base: Example, command: Command, spec: Spec, words: _Words, rng: random.Random
) -> Example | None:
    """One copy of an example with the slot spans swapped and the wording changed."""
    tokens: list[str] = []
    tags: list[str] = []
    order: list[tuple[str, str | int]] = []
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

        for index, token in enumerate(surface.split()):
            tokens.append(token)
            tags.append(("B-" if index == 0 else "I-") + slot.type)
        order.append((span.slot, value))
        position = span.end

    for token in base.tokens[position:]:
        tokens.append(token)
        tags.append(OUTSIDE)

    if not tokens:
        return None
    tokens, tags = _augment(tokens, tags, words, rng)
    return _rebuild(tokens, tags, order, base, command.name)


def _augmented(base: Example, words: _Words, rng: random.Random) -> Example | None:
    """One copy of an example with only the wording changed, used for the none class."""
    tokens, tags = _augment(list(base.tokens), list(base.tags), words, rng)
    if tokens == base.tokens:
        return None
    order = [(span.slot, span.value) for span in base.spans]
    return _rebuild(tokens, tags, order, base, base.command)


def _rebuild(
    tokens: list[str],
    tags: list[str],
    order: list[tuple[str, str | int]],
    base: Example,
    command_name: str,
) -> Example | None:
    """Put the spans and slot values back after the tokens moved around."""
    spans: list[Span] = []
    slots: dict[str, str | int] = {}
    index = 0
    start = None

    def close(end: int) -> bool:
        nonlocal index, start
        if index >= len(order):
            return False
        name, value = order[index]
        spans.append(Span(start=start, end=end, slot=name, value=value))
        slots[name] = value
        index += 1
        start = None
        return True

    for position, tag in enumerate(tags):
        if tag.startswith("B-"):
            if start is not None and not close(position):
                return None
            start = position
        elif tag == OUTSIDE and start is not None:
            if not close(position):
                return None
    if start is not None and not close(len(tags)):
        return None
    if index != len(order):
        return None

    return Example(
        tokens=tokens,
        tags=tags,
        command=command_name,
        slots=slots,
        spans=spans,
        text=" ".join(tokens),
        frame=base.frame,
    )


def _augment(
    tokens: list[str], tags: list[str], words: _Words, rng: random.Random
) -> tuple[list[str], list[str]]:
    """Change the wording around the slots. Tokens inside a slot span are never touched."""
    if not words.any():
        return tokens, tags
    for _ in range(rng.randint(0, _MAX_OPS)):
        choices = [_insert_filler, _drop_word, _swap_equivalent]
        rng.shuffle(choices)
        for op in choices:
            changed = op(tokens, tags, words, rng)
            if changed is not None:
                tokens, tags = changed
                break
    return tokens, tags


def _insert_filler(tokens, tags, words, rng):
    """Put a filler word at the start, at the end or between two non-slot tokens."""
    if not words.fillers:
        return None
    spots = [i for i in range(len(tokens) + 1) if i == len(tokens) or not tags[i].startswith("I-")]
    if not spots:
        return None
    filler = rng.choice(words.fillers)
    spot = rng.choice(spots)
    if spot > 0 and tokens[spot - 1 : spot + len(filler) - 1] == filler:
        return None
    if tokens[spot : spot + len(filler)] == filler:
        return None
    new_tokens = tokens[:spot] + list(filler) + tokens[spot:]
    new_tags = tags[:spot] + [OUTSIDE] * len(filler) + tags[spot:]
    return new_tokens, new_tags


def _drop_word(tokens, tags, words, rng):
    """Take out a carrier word, but never leave fewer than two tokens."""
    if not words.droppable:
        return None
    spots = []
    for phrase in words.droppable:
        size = len(phrase)
        if len(tokens) - size < 2:
            continue
        for i in range(len(tokens) - size + 1):
            if tokens[i : i + size] != phrase:
                continue
            if any(tag != OUTSIDE for tag in tags[i : i + size]):
                continue
            spots.append((i, size))
    if not spots:
        return None
    start, size = rng.choice(spots)
    return tokens[:start] + tokens[start + size :], tags[:start] + tags[start + size :]


def _swap_equivalent(tokens, tags, words, rng):
    """Swap a word for another that means the same thing, outside every slot span."""
    if not words.equivalents:
        return None
    spots = []
    for group in words.equivalents:
        for phrase in group:
            size = len(phrase)
            for i in range(len(tokens) - size + 1):
                if tokens[i : i + size] != phrase:
                    continue
                if any(tag != OUTSIDE for tag in tags[i : i + size]):
                    continue
                others = [p for p in group if p != phrase]
                if others:
                    spots.append((i, size, others))
    if not spots:
        return None
    start, size, others = rng.choice(spots)
    replacement = rng.choice(others)
    new_tokens = tokens[:start] + list(replacement) + tokens[start + size :]
    new_tags = tags[:start] + [OUTSIDE] * len(replacement) + tags[start + size :]
    return new_tokens, new_tags


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
