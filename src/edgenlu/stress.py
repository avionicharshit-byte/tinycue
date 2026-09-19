"""Make the data the gate is fitted on harder, without touching a test set.

A dev set written by the same person, on the same day, as the training sentences holds
almost no words the model has never seen. A calibrator fitted on it therefore never
learns what an unknown word costs, which is the one thing it most needs to know.

So each dev sentence is copied a few times with its carrier words roughed up: one swapped
for a word nobody has ever written, one misspelt, one taken out. Words inside a slot span
are never touched, so the answer stays exactly what it was. When the carrier word that
went was the only thing naming the command, the model now gets the sentence wrong, and
that is the useful half: the gate learns that a sentence full of words it does not know
is a sentence to be unsure about.

Nothing here reads a test set and nothing here changes a gold answer.
"""

from __future__ import annotations

import random

from .schema import OUTSIDE, Example

VOWELS = "aeiou"
CONSONANTS = "bcdfghjklmnprstvwz"

# How many words one copy has roughed up, and how likely each count is.
EDIT_COUNTS = (1, 1, 2, 2, 3)

# What one edit does.
SWAP_PSEUDO = "pseudo"
SWAP_TYPO = "typo"
DROP = "drop"
EDITS = (SWAP_PSEUDO, SWAP_PSEUDO, SWAP_PSEUDO, SWAP_TYPO, SWAP_TYPO, DROP)

# A sentence never goes below this many words, or there is nothing left to read.
MIN_TOKENS = 2


def pseudo_word(rng: random.Random, known: set[str]) -> str:
    """A pronounceable word that is in nobody's vocabulary."""
    for _ in range(50):
        syllables = rng.randint(2, 3)
        word = "".join(
            rng.choice(CONSONANTS) + rng.choice(VOWELS) + (rng.choice(CONSONANTS) if rng.random() < 0.4 else "")
            for _ in range(syllables)
        )
        if word not in known:
            return word
    return "zqx" + str(rng.randint(100, 999))


def typo(rng: random.Random, word: str) -> str:
    """One letter dropped, doubled, swapped with its neighbour or replaced."""
    if len(word) < 3:
        return word + rng.choice(CONSONANTS)
    kind = rng.randint(0, 3)
    index = rng.randrange(len(word) - 1)
    if kind == 0:
        return word[:index] + word[index + 1 :]
    if kind == 1:
        return word[:index] + word[index] + word[index:]
    if kind == 2:
        return word[:index] + word[index + 1] + word[index] + word[index + 2 :]
    return word[:index] + rng.choice(CONSONANTS) + word[index + 1 :]


def _carriers(tags) -> list[int]:
    """Where the words that are not inside any slot span sit."""
    return [i for i, tag in enumerate(tags) if tag == OUTSIDE]


def stress_example(example: Example, known: set[str], rng: random.Random) -> Example | None:
    """One roughed up copy of a sentence, or None when there is nothing safe to touch."""
    tokens = list(example.tokens)
    tags = list(example.tags)
    carriers = _carriers(tags)
    if not carriers:
        return None

    edits = rng.choice(EDIT_COUNTS)
    changed = False
    for _ in range(edits):
        carriers = _carriers(tags)
        if not carriers:
            break
        action = rng.choice(EDITS)
        index = rng.choice(carriers)
        if action == DROP and len(tokens) > MIN_TOKENS:
            del tokens[index]
            del tags[index]
            changed = True
            continue
        if action == SWAP_TYPO:
            replacement = typo(rng, tokens[index])
        else:
            replacement = pseudo_word(rng, known)
        if replacement and replacement != tokens[index]:
            tokens[index] = replacement
            changed = True

    if not changed or len(tokens) < 1:
        return None
    return Example(
        tokens=tokens,
        tags=tags,
        command=example.command,
        slots=dict(example.slots),
        spans=[],
        text=" ".join(tokens),
        frame=example.frame,
        labelled=False,
    )


def stress(examples, known: set[str], seed: int = 0, copies: int = 3) -> list[Example]:
    """`copies` roughed up versions of every sentence handed in."""
    rng = random.Random(seed)
    out: list[Example] = []
    seen: set[tuple[str, str]] = set()
    for example in examples:
        for _ in range(copies):
            made = stress_example(example, known, rng)
            if made is None:
                continue
            key = (made.command, " ".join(made.tokens))
            if key in seen:
                continue
            seen.add(key)
            out.append(made)
    return out
