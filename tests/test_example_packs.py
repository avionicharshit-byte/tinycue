"""The shipped extra and dev packs: they load, they are big enough, and they are apart.

A dev sentence that also sits in the training file measures nothing. This file is the
guard that says so out loud.
"""

from __future__ import annotations

from collections import Counter

import pytest

from conftest import REPO_ROOT
from edgenlu import answers
from edgenlu.parser import load_spec
from edgenlu.schema import NONE_COMMAND

PACKS = [
    ("smart_home", 400, 90),
    ("robot", 400, 90),
]


@pytest.fixture(scope="module")
def packs():
    """Each spec with its extras folded in, plus its dev set read separately."""
    out = {}
    for name, _, _ in PACKS:
        spec = load_spec(REPO_ROOT / f"examples/{name}.yaml")
        extra = answers.load(
            [REPO_ROOT / f"examples/{name}.extra.yaml"], spec, register=True
        )
        answers.apply(spec, extra)
        dev = answers.load([REPO_ROOT / f"examples/{name}.dev.yaml"], spec, register=False)
        out[name] = (spec, extra, dev)
    return out


@pytest.mark.parametrize("name,least_extra,least_dev", PACKS)
def test_the_packs_are_big_enough(packs, name, least_extra, least_dev):
    _, extra, dev = packs[name]
    assert len(extra) >= least_extra
    assert len(dev) >= least_dev


@pytest.mark.parametrize("name,_a,_b", PACKS)
def test_every_command_and_none_is_covered_on_both_sides(packs, name, _a, _b):
    spec, extra, dev = packs[name]
    wanted = set(spec.command_names())
    assert {e.command for e in extra} == wanted
    assert {e.command for e in dev} == wanted
    for counts in (Counter(e.command for e in extra), Counter(e.command for e in dev)):
        for command in wanted:
            assert counts[command] >= 10, (name, command)


@pytest.mark.parametrize("name,_a,_b", PACKS)
def test_no_dev_sentence_appears_in_the_training_material(packs, name, _a, _b):
    spec, _, dev = packs[name]
    trained = {e.text for c in spec.commands for e in c.examples}
    trained |= set(spec.none_examples)
    trained |= {" ".join(e.tokens) for c in spec.commands for e in c.examples}
    shared = [" ".join(e.tokens) for e in dev if " ".join(e.tokens) in trained]
    assert not shared, f"{name}: dev sentences that are also trained on: {shared[:5]}"


@pytest.mark.parametrize("name,_a,_b", PACKS)
def test_the_extra_pack_teaches_words_the_commands_file_never_had(packs, name, _a, _b):
    """The markup is the point: without it an extra file only adds phrasings."""
    plain = load_spec(REPO_ROOT / f"examples/{name}.yaml")
    spec, _, _ = packs[name]
    taught = 0
    for type_name, slot_type in spec.slot_types.items():
        for value, forms in slot_type.values.items():
            before = set(plain.slot_types[type_name].values.get(value, []))
            taught += len(set(forms) - before)
    assert taught >= 20, f"{name}: only {taught} new ways of saying a value"


@pytest.mark.parametrize("name,_a,_b", PACKS)
def test_the_dev_set_is_not_all_one_command(packs, name, _a, _b):
    _, _, dev = packs[name]
    counts = Counter(e.command for e in dev)
    assert counts[NONE_COMMAND] >= 12
    assert max(counts.values()) < len(dev) / 2
