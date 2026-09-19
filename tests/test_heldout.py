"""The held-out files: they parse, they are big enough, and they are not the training set."""

from __future__ import annotations

from collections import Counter

import pytest

from conftest import EXAMPLE_FILE, HELDOUT_FILE, REPO_ROOT, ROBOT_FILE
from edgenlu.parser import load_examples_file, load_spec
from edgenlu.schema import NONE_COMMAND

ROBOT_HELDOUT = REPO_ROOT / "eval/heldout_robot.yaml"


def test_smart_home_held_out_set_is_big_enough(example_spec):
    examples = load_examples_file(HELDOUT_FILE, example_spec)
    counts = Counter(e.command for e in examples)
    assert counts[NONE_COMMAND] >= 40
    for command in example_spec.commands:
        assert counts[command.name] >= 25, command.name


def test_robot_held_out_set_loads(robot_spec):
    examples = load_examples_file(ROBOT_HELDOUT, robot_spec)
    counts = Counter(e.command for e in examples)
    assert counts[NONE_COMMAND] >= 20
    for command in robot_spec.commands:
        assert counts[command.name] >= 12, command.name


def test_held_out_sentences_are_not_in_the_commands_file(example_spec):
    """If a sentence appears in both, the test set is not held out at all."""
    training = {e.text for c in example_spec.commands for e in c.examples}
    training |= set(example_spec.none_examples)
    for example in load_examples_file(HELDOUT_FILE, example_spec):
        assert " ".join(example.tokens) not in training


def test_robot_held_out_sentences_are_not_in_the_commands_file(robot_spec):
    training = {e.text for c in robot_spec.commands for e in c.examples}
    training |= set(robot_spec.none_examples)
    for example in load_examples_file(ROBOT_HELDOUT, robot_spec):
        assert " ".join(example.tokens) not in training


def test_held_out_examples_may_leave_a_required_slot_out(robot_spec):
    """'mvoe forward' has no step count. Decoding it must say so, not invent one."""
    examples = load_examples_file(ROBOT_HELDOUT, robot_spec)
    missing = [e for e in examples if e.command == "move" and "steps" not in e.slots]
    assert missing


def test_none_block_is_tagged_outside(example_spec):
    for example in load_examples_file(HELDOUT_FILE, example_spec):
        if example.command == NONE_COMMAND:
            assert set(example.tags) == {"O"}
            assert example.slots == {}


def test_an_unknown_command_in_a_test_file_is_an_error(tmp_path, example_spec):
    from edgenlu.schema import SpecError

    path = tmp_path / "bad.yaml"
    path.write_text('commands:\n  - name: fly\n    examples: ["fly away"]\n', encoding="utf-8")
    with pytest.raises(SpecError, match="not a command"):
        load_examples_file(path, example_spec)


def test_the_two_example_specs_share_no_commands():
    """Proof the second domain really is a different domain."""
    a = {c.name for c in load_spec(EXAMPLE_FILE).commands}
    b = {c.name for c in load_spec(ROBOT_FILE).commands}
    assert not (a & b)
