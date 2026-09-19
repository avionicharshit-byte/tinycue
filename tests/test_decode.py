"""Turning tags back into values: spans, canonical values, slot names, missing slots."""

from __future__ import annotations

from edgenlu.decode import decode, spans_from_tags
from edgenlu.parser import tokenize
from edgenlu.schema import NONE_COMMAND


def test_spans_from_tags():
    tags = ["O", "B-state", "O", "B-room", "I-room", "O"]
    assert spans_from_tags(list(range(6)), tags) == [("state", 1, 2), ("room", 3, 5)]


def test_two_spans_back_to_back():
    tags = ["B-room", "B-state"]
    assert spans_from_tags([0, 1], tags) == [("room", 0, 1), ("state", 1, 2)]


def test_a_stray_i_tag_still_makes_a_span():
    """A tagger slip should cost one word, not the whole sentence."""
    tags = ["O", "I-room", "O"]
    assert spans_from_tags([0, 1, 2], tags) == [("room", 1, 2)]


def test_a_span_that_runs_to_the_end():
    tags = ["O", "B-state", "I-state"]
    assert spans_from_tags([0, 1, 2], tags) == [("state", 1, 3)]


def test_decode_a_full_command(example_spec):
    tokens = tokenize("turn on the bedroom light")
    tags = ["O", "B-state", "O", "B-room", "O"]
    result = decode(tokens, tags, "set_light", example_spec)
    assert result.slots == {"state": "on", "room": "bedroom"}
    assert result.missing == []
    assert result.call() == "set_light(state=on, room=bedroom)"


def test_decode_maps_the_type_back_to_the_slot_name(example_spec):
    """set_fan calls its direction slot 'speed'. The tags say 'direction'."""
    tokens = tokenize("make the bedroom fan faster")
    tags = ["O", "O", "B-room", "O", "B-direction"]
    result = decode(tokens, tags, "set_fan", example_spec)
    assert result.slots == {"room": "bedroom", "speed": "up"}
    assert result.missing == []


def test_decode_reports_a_missing_required_slot(example_spec):
    tokens = tokenize("light band karo")
    tags = ["O", "B-state", "O"]
    result = decode(tokens, tags, "set_light", example_spec)
    assert result.slots == {"state": "off"}
    assert result.missing == ["room"]


def test_an_optional_slot_is_never_missing(example_spec):
    tokens = tokenize("fan speed down")
    tags = ["O", "O", "B-direction"]
    result = decode(tokens, tags, "set_fan", example_spec)
    assert result.slots == {"speed": "down"}
    assert result.missing == []


def test_a_multi_word_surface_becomes_one_value(example_spec):
    tokens = tokenize("rasoi mein light jala do")
    tags = ["B-room", "O", "O", "B-state", "I-state"]
    result = decode(tokens, tags, "set_light", example_spec)
    assert result.slots == {"room": "kitchen", "state": "on"}


def test_numbers_come_back_as_ints(example_spec):
    for text, tags, value in [
        ("set a timer for ten minutes", ["O", "O", "O", "O", "B-minutes", "O"], 10),
        ("das minute baad alarm", ["B-minutes", "O", "O", "O"], 10),
        ("timer 45 minutes", ["O", "B-minutes", "O"], 45),
        ("ek sau bees minute ka alarm", ["B-minutes", "I-minutes", "I-minutes", "O", "O", "O"], 120),
    ]:
        result = decode(tokenize(text), tags, "set_timer", example_spec)
        assert result.slots == {"minutes": value}


def test_a_number_outside_the_range_is_dropped(example_spec):
    tokens = tokenize("timer 900 minutes")
    tags = ["O", "B-minutes", "O"]
    result = decode(tokens, tags, "set_timer", example_spec)
    assert result.slots == {}
    assert result.missing == ["minutes"]
    assert result.stray == ["minutes"]


def test_a_trailing_carrier_word_is_trimmed_off_the_span(example_spec):
    """The tagger often pulls one word too many in. 'up karo' still means up."""
    tokens = tokenize("fan up karo")
    tags = ["O", "B-direction", "I-direction"]
    result = decode(tokens, tags, "set_fan", example_spec)
    assert result.slots == {"speed": "up"}
    assert [s.surface for s in result.found] == ["up"]
    assert [s.known for s in result.found] == [True]


def test_a_leading_word_is_trimmed_when_trailing_words_do_not_help(example_spec):
    tokens = tokenize("the kitchen light on")
    tags = ["B-room", "I-room", "O", "B-state"]
    result = decode(tokens, tags, "set_light", example_spec)
    assert result.slots == {"room": "kitchen", "state": "on"}


def test_trimming_works_on_a_number_slot_too(example_spec):
    tokens = tokenize("timer 45 minutes")
    tags = ["O", "B-minutes", "I-minutes"]
    result = decode(tokens, tags, "set_timer", example_spec)
    assert result.slots == {"minutes": 45}


def test_trimming_never_hands_back_an_empty_span(example_spec):
    """Nothing in the span matches, so the whole span is passed on as it was said."""
    tokens = tokenize("turn on the garage door")
    tags = ["O", "B-state", "O", "B-room", "I-room"]
    result = decode(tokens, tags, "set_light", example_spec)
    assert result.slots == {"state": "on", "room": "garage door"}
    assert [s.known for s in result.found] == [True, False]


def test_a_word_nobody_listed_arrives_as_it_was_said(example_spec):
    """An open vocabulary value: the room is not in the list, so we pass the words on."""
    tokens = tokenize("turn on the garage light")
    tags = ["O", "B-state", "O", "B-room", "O"]
    result = decode(tokens, tags, "set_light", example_spec)
    assert result.slots == {"state": "on", "room": "garage"}
    assert [s.known for s in result.found] == [True, False]


def test_a_tag_the_command_does_not_use_is_stray(example_spec):
    tokens = tokenize("what is the temperature")
    tags = ["O", "O", "O", "B-room"]
    result = decode(tokens, tags, "show", example_spec)
    assert result.slots == {}
    assert result.stray == ["room"]
    assert result.missing == ["what"]


def test_none_decodes_to_nothing(example_spec):
    result = decode(tokenize("tell me a joke"), ["O"] * 4, NONE_COMMAND, example_spec)
    assert result.call() == NONE_COMMAND
    assert result.slots == {}
    assert result.missing == []


def test_the_first_span_of_a_type_wins(example_spec):
    tokens = tokenize("bedroom kitchen light on")
    tags = ["B-room", "B-room", "O", "B-state"]
    result = decode(tokens, tags, "set_light", example_spec)
    assert result.slots == {"room": "bedroom", "state": "on"}


def test_decode_on_the_robot_spec(robot_spec):
    tokens = tokenize("move forward ten steps")
    tags = ["O", "B-heading", "B-steps", "O"]
    result = decode(tokens, tags, "move", robot_spec)
    assert result.slots == {"heading": "forward", "steps": 10}
    assert result.call() == "move(heading=forward, steps=10)"


def test_robot_missing_step_count(robot_spec):
    result = decode(tokenize("move left"), ["O", "B-heading"], "move", robot_spec)
    assert result.slots == {"heading": "left"}
    assert result.missing == ["steps"]
