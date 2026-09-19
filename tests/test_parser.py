"""The commands file parser: markup, tags and every validation error."""

from __future__ import annotations

import pytest

from conftest import SMALL_SPEC
from edgenlu.parser import load_spec, strip_markup, tokenize
from edgenlu.schema import NUMBER, SpecError


def test_tokenize_lowercases_and_splits():
    assert tokenize("  Turn ON the  Light ") == ["turn", "on", "the", "light"]


def test_strip_markup():
    assert strip_markup("turn [on](state) the [bedroom](room) light") == (
        "turn on the bedroom light"
    )


def test_single_word_span(small_spec):
    example = small_spec.command("set_light").examples[0]
    assert example.tokens == ["turn", "on", "the", "bedroom", "light"]
    assert example.tags == ["O", "B-state", "O", "B-room", "O"]
    assert example.slots == {"state": "on", "room": "bedroom"}


def test_hinglish_example_starting_with_a_span(small_spec):
    example = small_spec.command("set_light").examples[1]
    assert example.tokens == ["rasoi", "mein", "light", "jala", "do"]
    assert example.tags == ["B-room", "O", "O", "B-state", "I-state"]
    assert example.slots == {"room": "kitchen", "state": "on"}


def test_multi_word_span_gets_b_then_i(write_spec):
    spec = load_spec(
        write_spec(
            SMALL_SPEC.replace(
                '- "turn [on](state) the [bedroom](room) light"',
                '- "put the [bed room](room) light [band kar do](state)"',
            )
        )
    )
    example = spec.command("set_light").examples[0]
    assert example.tokens == ["put", "the", "bed", "room", "light", "band", "kar", "do"]
    assert example.tags == [
        "O",
        "O",
        "B-room",
        "I-room",
        "O",
        "B-state",
        "I-state",
        "I-state",
    ]
    assert example.slots == {"room": "bedroom", "state": "off"}


def test_tokens_and_tags_are_the_same_length(example_spec):
    for command in example_spec.commands:
        for example in command.examples:
            assert len(example.tokens) == len(example.tags)


def test_number_slot_value_is_an_int(small_spec):
    example = small_spec.command("set_timer").examples[0]
    assert example.slots == {"minutes": 10}
    assert small_spec.slot_types["minutes"].kind == NUMBER


def test_command_slot_can_rename_a_slot_type(example_spec):
    fan = example_spec.command("set_fan")
    speed = fan.slot("speed")
    assert speed.type == "direction"
    assert fan.slot("room").required is False
    assert fan.examples[0].tags[-1] == "B-speed"


def test_example_file_loads(example_spec):
    assert [c.name for c in example_spec.commands] == [
        "set_light",
        "set_fan",
        "set_timer",
        "show",
    ]
    assert example_spec.fallback.none_command is True
    assert example_spec.fallback.unsure_below == "auto"
    assert example_spec.languages == ["en", "hinglish"]


def _expect_error(write_spec, text, message):
    with pytest.raises(SpecError) as caught:
        load_spec(write_spec(text))
    assert message in str(caught.value)


def test_unknown_slot(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("[bedroom](room) light", "[bedroom](colour) light"),
        "unknown slot 'colour'",
    )


def test_slot_not_declared_on_the_command(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace(
            '- "set a timer for [ten](minutes) minutes"',
            '- "set a timer in the [bedroom](room) for [ten](minutes) minutes"',
        ),
        "is not declared on command 'set_timer'",
    )


def test_surface_not_in_the_value_list(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("[bedroom](room) light", "[garage](room) light"),
        "'garage' is not listed under slot 'room'",
    )


def test_malformed_brackets(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("[bedroom](room) light", "[bedroom(room) light"),
        "stray bracket",
    )


def test_missing_slot_name_after_a_span(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("[bedroom](room) light", "[bedroom]() light"),
        "no slot name",
    )


def test_empty_span(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("[bedroom](room) light", "[](room) light"),
        "no words in it",
    )


def test_duplicate_command_names(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("  - name: set_timer", "  - name: set_light"),
        "the name is used twice",
    )


def test_empty_examples(write_spec):
    text = SMALL_SPEC.replace(
        '      - "set a timer for [ten](minutes) minutes"',
        "",
    )
    _expect_error(write_spec, text, "needs at least one example sentence")


def test_blank_example_sentence(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace('- "set a timer for [ten](minutes) minutes"', '- "   "'),
        "an example is empty",
    )


def test_unknown_slot_type_on_a_command(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("    slots: [minutes]", "    slots: [seconds]"),
        "unknown slot type 'seconds'",
    )


def test_required_slot_missing_from_an_example(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace('- "[rasoi](room) mein light [jala do](state)"', '- "light [band](state) karo"'),
        "required slot 'room' is not marked",
    )


def test_number_out_of_range(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("[ten](minutes)", "[900](minutes)"),
        "above the maximum 180",
    )


def test_not_a_number_in_a_number_slot(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("[ten](minutes)", "[soon](minutes)"),
        "is not a number",
    )


def test_unquoted_on_becomes_a_boolean(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace('      "on": ["on", chalu, jala do]', "      on: [chalu, jala do]"),
        "read as a true/false value",
    )


def test_missing_commands_section(write_spec):
    text = SMALL_SPEC.split("commands:")[0] + "fallback:\n  none_command: true\n"
    _expect_error(write_spec, text, "no 'commands' section")


def test_missing_slots_section(write_spec):
    _expect_error(
        write_spec,
        "commands:\n  - name: ping\n    examples:\n      - \"ping\"\n",
        "no 'slots' section",
    )


def test_slot_needs_values_or_number(write_spec):
    _expect_error(
        write_spec,
        SMALL_SPEC.replace("    type: number\n    min: 1\n    max: 180", "    min: 1"),
        "needs either a 'values' list or 'type: number'",
    )


def test_bad_yaml(write_spec):
    _expect_error(write_spec, "commands: [\n", "not valid YAML")
