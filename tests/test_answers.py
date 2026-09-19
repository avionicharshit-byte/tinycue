"""The answer-first files: labelling by construction, and the errors when it cannot be done."""

from __future__ import annotations

import pytest

from conftest import EXAMPLE_FILE, spans_from_tags
from edgenlu import answers
from edgenlu.parser import load_spec
from edgenlu.schema import NONE_COMMAND, SpecError


@pytest.fixture
def write_extra(tmp_path):
    def _write(text: str, name: str = "extra.yaml"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write


def load(spec, path, register: bool = True):
    return answers.load([path], spec, register=register)


def test_a_value_is_found_without_any_markup(example_spec, write_extra):
    path = write_extra(
        "- answer: set_light(room=bedroom, state=off)\n"
        "  say:\n"
        "    - kill the bedroom light off\n"
    )
    example = load(example_spec, path)[0]
    assert example.command == "set_light"
    assert example.slots == {"room": "bedroom", "state": "off"}
    assert spans_from_tags(example.tokens, example.tags) == [
        ("room", "bedroom"),
        ("state", "off"),
    ]


def test_markup_teaches_a_new_way_of_saying_a_value(example_spec, write_extra):
    path = write_extra(
        "- answer: set_fan(room=bedroom, speed=up)\n"
        "  say:\n"
        '    - "bedroom fan [a notch higher](speed) please"\n'
    )
    example = load(example_spec, path)[0]
    assert example.slots == {"room": "bedroom", "speed": "up"}
    assert ("direction", "a notch higher") in spans_from_tags(example.tokens, example.tags)
    # The spec now knows it, so decoding and the blob know it too.
    assert "a notch higher" in example_spec.slot_types["direction"].values["up"]
    assert example_spec.slot_types["direction"].find_canonical("a notch higher") == "up"


def test_a_surface_taught_in_one_block_is_found_in_another(example_spec, write_extra):
    """Registration happens for the whole file before a single sentence is labelled."""
    path = write_extra(
        "- answer: set_fan(speed=up)\n"
        "  say:\n"
        '    - "wind it [a notch higher](speed)"\n'
        "- answer: set_fan(speed=up)\n"
        "  say:\n"
        "    - a notch higher please\n"
    )
    second = load(example_spec, path)[1]
    assert ("direction", "a notch higher") in spans_from_tags(second.tokens, second.tags)


def test_numbers_are_found_as_digits_english_or_hindi(example_spec, write_extra):
    path = write_extra(
        "- answer: set_timer(minutes=120)\n"
        "  say:\n"
        "    - 120 minute ka timer\n"
        "    - wake me in one hundred twenty minutes\n"
        "    - ek sau bees minute baad\n"
    )
    examples = load(example_spec, path)
    assert [e.slots for e in examples] == [{"minutes": 120}] * 3
    surfaces = [spans_from_tags(e.tokens, e.tags)[0][1] for e in examples]
    assert surfaces == ["120", "one hundred twenty", "ek sau bees"]


def test_the_longest_way_of_saying_a_value_wins(example_spec, write_extra):
    path = write_extra(
        "- answer: set_fan(speed=up)\n  say:\n    - fan tez karo abhi\n"
    )
    example = load(example_spec, path)[0]
    assert spans_from_tags(example.tokens, example.tags) == [("direction", "tez karo")]


def test_a_slot_left_out_of_the_answer_is_simply_absent(example_spec, write_extra):
    path = write_extra("- answer: set_fan(speed=down)\n  say:\n    - slow the fan down\n")
    example = load(example_spec, path)[0]
    assert example.slots == {"speed": "down"}
    assert "room" not in example.slots


def test_a_none_answer_needs_no_slots(example_spec, write_extra):
    path = write_extra(
        f"- answer: {NONE_COMMAND}\n  say:\n    - i am a big fan of cricket\n"
    )
    example = load(example_spec, path)[0]
    assert example.command == NONE_COMMAND
    assert set(example.tags) == {"O"}
    assert example.slots == {}


def test_a_value_that_is_nowhere_in_the_sentence_is_an_error(example_spec, write_extra):
    path = write_extra(
        "- answer: set_light(room=bedroom, state=on)\n  say:\n    - illuminate the bedroom\n"
    )
    with pytest.raises(SpecError, match="no words in the sentence say it"):
        load(example_spec, path)


def test_an_unknown_command_is_an_error(example_spec, write_extra):
    path = write_extra("- answer: fly(room=bedroom)\n  say:\n    - fly away\n")
    with pytest.raises(SpecError, match="is not a command"):
        load(example_spec, path)


def test_an_unknown_slot_is_an_error(example_spec, write_extra):
    path = write_extra("- answer: set_light(colour=red)\n  say:\n    - make it red\n")
    with pytest.raises(SpecError, match="has no slot 'colour'"):
        load(example_spec, path)


def test_an_unknown_value_is_an_error(example_spec, write_extra):
    path = write_extra("- answer: set_light(room=garage, state=on)\n  say:\n    - on in garage\n")
    with pytest.raises(SpecError, match="is not a value of slot"):
        load(example_spec, path)


def test_a_number_outside_its_range_is_an_error(example_spec, write_extra):
    path = write_extra("- answer: set_timer(minutes=900)\n  say:\n    - 900 minute timer\n")
    with pytest.raises(SpecError, match="above the maximum"):
        load(example_spec, path)


def test_marking_a_slot_that_is_not_in_the_answer_is_an_error(example_spec, write_extra):
    path = write_extra(
        "- answer: set_fan(speed=up)\n  say:\n    - \"[bedroom](room) fan up\"\n"
    )
    with pytest.raises(SpecError, match="is not in the answer"):
        load(example_spec, path)


def test_a_surface_cannot_mean_two_values(example_spec, write_extra):
    path = write_extra(
        "- answer: set_fan(speed=down)\n  say:\n    - \"fan [faster](speed) karo\"\n"
    )
    with pytest.raises(SpecError, match="already means"):
        load(example_spec, path)


def test_a_number_slot_cannot_be_taught_a_new_spelling(example_spec, write_extra):
    path = write_extra(
        "- answer: set_timer(minutes=30)\n  say:\n    - \"[half an hour](minutes) timer\"\n"
    )
    with pytest.raises(SpecError, match="does not read as 30"):
        load(example_spec, path)


def test_markup_picks_which_number_is_the_slot(example_spec, write_extra):
    path = write_extra(
        "- answer: set_timer(minutes=15)\n"
        "  say:\n"
        '    - "at 8 set a [15](minutes) minute timer"\n'
    )
    example = load(example_spec, path)[0]
    assert example.slots == {"minutes": 15}
    assert spans_from_tags(example.tokens, example.tags) == [("minutes", "15")]


def test_a_sentence_that_yaml_reads_as_a_list_says_so(example_spec, write_extra):
    """An unquoted sentence starting with '[' is the mistake everyone makes first."""
    path = write_extra("- answer: set_fan(speed=up)\n  say:\n    - [up](speed) karo\n")
    with pytest.raises(SpecError, match="has to be in quotes"):
        load(example_spec, path)

    clean = write_extra(
        "- answer: set_fan(speed=up)\n  say:\n    - [up, faster]\n", name="list.yaml"
    )
    with pytest.raises(SpecError, match="has to be in quotes"):
        load(example_spec, clean)


def test_a_block_without_an_answer_is_an_error(example_spec, write_extra):
    path = write_extra("- say:\n    - turn it on\n")
    with pytest.raises(SpecError, match="no 'answer'"):
        load(example_spec, path)


def test_a_block_without_sentences_is_an_error(example_spec, write_extra):
    path = write_extra("- answer: set_fan(speed=up)\n")
    with pytest.raises(SpecError, match="needs a 'say' list"):
        load(example_spec, path)


def test_reading_without_registering_leaves_the_spec_alone(example_spec, write_extra):
    """A dev set must never teach the model one of its own words."""
    path = write_extra(
        "- answer: set_fan(speed=up)\n  say:\n    - \"fan [right up](speed)\"\n"
    )
    before = list(example_spec.slot_types["direction"].values["up"])
    example = load(example_spec, path, register=False)[0]
    assert ("direction", "right up") in spans_from_tags(example.tokens, example.tags)
    assert example_spec.slot_types["direction"].values["up"] == before


def test_every_sentence_becomes_its_own_frame(example_spec, write_extra):
    path = write_extra(
        "- answer: set_fan(speed=up)\n  say:\n    - fan up\n    - fan faster\n"
    )
    frames = [e.frame for e in load(example_spec, path)]
    assert len(set(frames)) == 2
    assert all(f.startswith("set_fan#x") for f in frames)


def test_applying_extras_puts_them_where_the_generator_looks(example_spec, write_extra):
    path = write_extra(
        "- answer: set_fan(speed=up)\n  say:\n    - crank it up\n"
        f"- answer: {NONE_COMMAND}\n  say:\n    - cricket is on today\n"
    )
    before = len(example_spec.command("set_fan").examples)
    none_before = len(example_spec.none_examples)
    added = answers.apply(example_spec, load(example_spec, path))
    assert added == 2
    assert len(example_spec.command("set_fan").examples) == before + 1
    assert len(example_spec.none_examples) == none_before + 1


def test_a_commands_file_can_list_its_own_extra_files(tmp_path):
    extra = tmp_path / "more.yaml"
    extra.write_text("- answer: set_fan(speed=up)\n  say:\n    - crank it up\n", encoding="utf-8")
    text = EXAMPLE_FILE.read_text(encoding="utf-8") + "\nextra:\n  - more.yaml\n"
    spec_file = tmp_path / "commands.yaml"
    spec_file.write_text(text, encoding="utf-8")
    spec = load_spec(spec_file)
    assert spec.extra_files == [str(tmp_path / "more.yaml")]
    answers.apply(spec, answers.load(spec.extra_files, spec))
    assert any(e.text == "crank it up" for e in spec.command("set_fan").examples)
