"""The example generator: determinism, valid tags and valid slot surfaces."""

from __future__ import annotations

from collections import Counter

from conftest import spans_from_tags
from edgenlu.generator import generate
from edgenlu.numbers import parse_number
from edgenlu.schema import NONE_COMMAND, NUMBER, OUTSIDE


def test_is_deterministic(example_spec):
    first = generate(example_spec, n_per_command=50, seed=0)
    second = generate(example_spec, n_per_command=50, seed=0)
    assert [e.as_dict() for e in first] == [e.as_dict() for e in second]


def test_a_different_seed_gives_different_examples(example_spec):
    first = generate(example_spec, n_per_command=50, seed=0)
    second = generate(example_spec, n_per_command=50, seed=1)
    assert [e.as_dict() for e in first] != [e.as_dict() for e in second]


def test_tokens_and_tags_line_up(example_spec):
    for example in generate(example_spec, n_per_command=100, seed=0):
        assert len(example.tokens) == len(example.tags)
        assert example.tokens
        assert all(t == t.lower() for t in example.tokens)


def test_tags_are_well_formed(example_spec):
    for example in generate(example_spec, n_per_command=100, seed=0):
        previous = OUTSIDE
        for tag in example.tags:
            if tag.startswith("I-"):
                assert previous in (tag, "B-" + tag[2:]), example.tokens
            previous = tag


def test_every_slot_surface_belongs_to_its_slot_type(example_spec):
    for example in generate(example_spec, n_per_command=200, seed=0):
        if example.command == NONE_COMMAND:
            continue
        command = example_spec.command(example.command)
        for type_name, surface in spans_from_tags(example.tokens, example.tags):
            slot = command.slot_for_type(type_name)
            assert slot is not None, type_name
            slot_name = slot.name
            slot_type = example_spec.slot_types[slot.type]
            if slot_type.kind == NUMBER:
                value = parse_number(surface.split())
                assert value is not None, surface
                assert slot_type.min <= value <= slot_type.max
                assert example.slots[slot_name] == value
            else:
                canonical = slot_type.find_canonical(surface)
                assert canonical is not None, f"{surface} is not a {slot_type.name}"
                assert example.slots[slot_name] == canonical


def test_hand_written_examples_are_kept(example_spec):
    generated = {tuple(e.tokens) for e in generate(example_spec, n_per_command=50, seed=0)}
    for command in example_spec.commands:
        for example in command.examples:
            assert tuple(example.tokens) in generated


def test_none_examples_are_present(example_spec):
    examples = generate(example_spec, n_per_command=100, seed=0)
    none_examples = [e for e in examples if e.command == NONE_COMMAND]
    assert len(none_examples) >= 20
    for example in none_examples:
        assert example.slots == {}
        assert set(example.tags) == {OUTSIDE}


def test_none_examples_are_off_when_the_file_says_so(example_spec):
    example_spec.fallback.none_command = False
    examples = generate(example_spec, n_per_command=20, seed=0)
    assert all(e.command != NONE_COMMAND for e in examples)


def test_no_duplicate_sentences(example_spec):
    examples = generate(example_spec, n_per_command=200, seed=0)
    keys = [(e.command, tuple(e.tokens)) for e in examples]
    assert len(keys) == len(set(keys))


def test_counts_per_command(example_spec):
    examples = generate(example_spec, n_per_command=200, seed=0)
    counts = Counter(e.command for e in examples)
    for command in example_spec.commands:
        assert counts[command.name] > 0
        assert counts[command.name] <= 200


def test_number_styles_all_show_up(example_spec):
    examples = generate(example_spec, n_per_command=400, seed=0)
    digits = words = 0
    for example in examples:
        if example.command != "set_timer":
            continue
        for _, surface in spans_from_tags(example.tokens, example.tags):
            if surface.isdigit():
                digits += 1
            else:
                words += 1
    assert digits > 0
    assert words > 0


def test_every_command_reaches_the_target_including_none(example_spec):
    examples = generate(example_spec, n_per_command=400, seed=0)
    counts = Counter(e.command for e in examples)
    assert counts[NONE_COMMAND] == 400
    for command in example_spec.commands:
        assert counts[command.name] == 400


def test_the_robot_spec_balances_too(robot_spec):
    examples = generate(robot_spec, n_per_command=300, seed=0)
    counts = Counter(e.command for e in examples)
    assert set(counts) == set(robot_spec.command_names())
    assert all(count == 300 for count in counts.values())


def test_a_class_that_cannot_reach_the_target_warns(example_spec):
    """No word lists to draw on, so the phrasings run out and the generator says so."""
    example_spec.fillers = []
    example_spec.droppable = []
    example_spec.equivalents = []
    example_spec.none_examples = ["one stray sentence", "another stray sentence"]
    warnings = []
    generate(example_spec, n_per_command=500, seed=0, warn=warnings.append)
    assert any(NONE_COMMAND in w for w in warnings)
    assert any("500" in w for w in warnings)


def test_augmentation_never_touches_a_slot_span(example_spec):
    """Every tagged surface must still be a real value of its slot type."""
    for example in generate(example_spec, n_per_command=600, seed=1):
        if example.command == NONE_COMMAND:
            continue
        command = example_spec.command(example.command)
        for type_name, surface in spans_from_tags(example.tokens, example.tags):
            slot = command.slot_for_type(type_name)
            slot_type = example_spec.slot_types[slot.type]
            if slot_type.kind == NUMBER:
                assert parse_number(surface.split()) is not None, surface
            else:
                assert slot_type.find_canonical(surface) is not None, surface


def test_filler_words_do_show_up(example_spec):
    tokens = {t for e in generate(example_spec, n_per_command=400, seed=0) for t in e.tokens}
    assert "please" in tokens
    assert "zara" in tokens or "thoda" in tokens


def test_equivalent_words_do_show_up(example_spec):
    """'switch' is only in one hand-written sentence, but 'turn' swaps to it everywhere."""
    sentences = [
        " ".join(e.tokens)
        for e in generate(example_spec, n_per_command=400, seed=0)
        if e.command == "set_light"
    ]
    hand_written = {e.text for e in example_spec.command("set_light").examples}
    grown = [s for s in sentences if s not in hand_written and "switch" in s.split()]
    assert grown


def test_dropping_never_leaves_fewer_than_two_tokens(example_spec):
    for example in generate(example_spec, n_per_command=600, seed=2):
        assert len(example.tokens) >= 2


def test_none_examples_are_augmented_too(example_spec):
    built_in = set(example_spec.none_examples)
    grown = [
        " ".join(e.tokens)
        for e in generate(example_spec, n_per_command=400, seed=0)
        if e.command == NONE_COMMAND and " ".join(e.tokens) not in built_in
    ]
    assert len(grown) > 100


def test_there_are_plenty_of_built_in_none_sentences(example_spec):
    assert len(example_spec.none_examples) >= 150


def test_slot_values_match_the_tagged_surface(example_spec):
    """The slots dict and the tags must never drift apart after augmentation."""
    for example in generate(example_spec, n_per_command=400, seed=3):
        assert len(example.spans) == sum(1 for t in example.tags if t.startswith("B-"))
        for span in example.spans:
            assert example.slots[span.slot] == span.value
            assert example.tags[span.start].startswith("B-")
