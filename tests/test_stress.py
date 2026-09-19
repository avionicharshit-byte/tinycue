"""Roughed up copies: the answer must survive, the slot words must not be touched."""

from __future__ import annotations

import random

from edgenlu import stress
from edgenlu.schema import Example

KNOWN = {"turn", "the", "attic", "lamp", "please", "on"}


def _example() -> Example:
    return Example(
        tokens=["turn", "on", "the", "attic", "lamp"],
        tags=["O", "B-state", "O", "B-place", "O"],
        command="set_light",
        slots={"state": "on", "place": "attic"},
        text="turn on the attic lamp",
        frame="set_light#x0",
    )


def test_a_pseudo_word_is_never_one_the_model_knows():
    rng = random.Random(0)
    for _ in range(200):
        assert stress.pseudo_word(rng, KNOWN) not in KNOWN


def test_a_typo_changes_the_word():
    rng = random.Random(1)
    changed = sum(1 for _ in range(50) if stress.typo(rng, "thermostat") != "thermostat")
    assert changed == 50


def test_words_inside_a_slot_span_are_never_touched():
    rng = random.Random(3)
    source = _example()
    for _ in range(200):
        made = stress.stress_example(source, KNOWN, rng)
        if made is None:
            continue
        for token, tag in zip(made.tokens, made.tags):
            if tag != "O":
                assert token in {"on", "attic"}


def test_the_answer_is_carried_over_unchanged():
    source = _example()
    made = stress.stress(([source]), KNOWN, seed=5, copies=6)
    assert made
    for item in made:
        assert item.command == source.command
        assert item.slots == source.slots
        assert item.frame == source.frame
        assert item.labelled is False


def test_the_tags_still_line_up_with_the_tokens():
    made = stress.stress([_example()], KNOWN, seed=7, copies=10)
    for item in made:
        assert len(item.tokens) == len(item.tags)
        assert item.tokens


def test_something_actually_changed():
    source = _example()
    made = stress.stress([source], KNOWN, seed=9, copies=10)
    assert made
    assert all(item.tokens != source.tokens for item in made)


def test_the_same_seed_gives_the_same_copies():
    a = stress.stress([_example()], KNOWN, seed=11, copies=5)
    b = stress.stress([_example()], KNOWN, seed=11, copies=5)
    assert [x.tokens for x in a] == [y.tokens for y in b]


def test_a_sentence_with_no_carrier_words_is_left_alone():
    only_slot = Example(
        tokens=["attic"],
        tags=["B-place"],
        command="show",
        slots={"place": "attic"},
        text="attic",
        frame="show#x0",
    )
    assert stress.stress([only_slot], KNOWN, seed=0, copies=4) == []


def test_a_none_sentence_is_all_carrier_and_keeps_its_answer():
    chatter = Example(
        tokens=["i", "am", "reading", "a", "book"],
        tags=["O"] * 5,
        command="none",
        slots={},
        text="i am reading a book",
        frame="none#x0",
    )
    made = stress.stress([chatter], KNOWN, seed=2, copies=4)
    assert made
    for item in made:
        assert item.command == "none"
        assert item.slots == {}
        assert len(item.tokens) >= 1
