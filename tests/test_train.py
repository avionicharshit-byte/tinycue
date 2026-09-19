"""A small end to end train, then the bundle it wrote, read back and used."""

from __future__ import annotations

import json

import pytest

from conftest import EXAMPLE_FILE
from edgenlu import model as bundle
from edgenlu.decode import decode
from edgenlu.generator import generate
from edgenlu.parser import load_spec, tokenize
from edgenlu.train import read_jsonl, split_by_frame, train, write_jsonl

SMALL_N = 120


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """One fast bundle, shared by every test in this file."""
    spec = load_spec(EXAMPLE_FILE)
    out = tmp_path_factory.mktemp("bundle") / "model"
    result = train(spec, out, n_per_command=SMALL_N, seed=0, log=lambda *a: None)
    return result, bundle.load(out)


def test_split_holds_out_whole_frames(example_spec):
    examples = generate(example_spec, n_per_command=SMALL_N, seed=0)
    split = split_by_frame(examples, seed=0)
    train_frames = {e.frame for e in split.train}
    for part in (split.dev, split.test):
        assert part
        assert not ({e.frame for e in part} & train_frames)
    assert len(split.train) + len(split.dev) + len(split.test) == len(examples)


def test_split_is_the_same_every_time(example_spec):
    examples = generate(example_spec, n_per_command=SMALL_N, seed=0)
    first = split_by_frame(examples, seed=0)
    second = split_by_frame(examples, seed=0)
    assert [e.text for e in first.test] == [e.text for e in second.test]


def test_every_command_is_in_every_part(example_spec):
    examples = generate(example_spec, n_per_command=SMALL_N, seed=0)
    split = split_by_frame(examples, seed=0)
    wanted = set(example_spec.command_names())
    for part in (split.train, split.dev, split.test):
        assert {e.command for e in part} == wanted


def test_the_bundle_has_three_files(trained):
    result, _ = trained
    names = [name for name, _ in bundle.sizes(result.directory)]
    assert sorted(names) == ["intent.npz", "meta.json", "slots.crfsuite"]


def test_the_bundle_is_well_under_a_megabyte(trained):
    result, _ = trained
    total = sum(size for _, size in bundle.sizes(result.directory))
    assert total < 1024 * 1024


def test_meta_records_how_to_rebuild_the_features(trained):
    result, _ = trained
    meta = json.loads((result.directory / "meta.json").read_text())
    assert meta["features"]["hash"] == "fnv1a-32"
    assert meta["features"]["table_size"] == 1 << 14
    assert meta["features"]["crf_offsets"] == [-2, -1, 0, 1, 2]
    assert len(meta["spec_digest"]) == 16
    assert "set_light" in [c["name"] for c in meta["spec"]["commands"]]


def test_the_loaded_model_knows_its_classes(trained):
    _, loaded = trained
    assert set(loaded.classes) == {"set_light", "set_fan", "set_timer", "show", "none"}
    assert loaded.weights.shape == (len(loaded.classes), loaded.table_size)
    assert loaded.temperature > 0.0
    assert 0.0 <= loaded.unsure_below <= 1.0


def test_probabilities_add_up_to_one(trained):
    _, loaded = trained
    probabilities = loaded.intent_probabilities(tokenize("turn on the bedroom light"))
    assert probabilities.sum() == pytest.approx(1.0)
    assert (probabilities >= 0).all()


def test_it_reads_the_commands_it_was_trained_on(trained):
    """Loose on purpose: a 120 per command model should still get the plain ones right."""
    _, loaded = trained
    wanted = {
        "turn on the bedroom light": "set_light",
        "switch the kitchen light off": "set_light",
        "rasoi mein light jala do": "set_light",
        "set a timer for ten minutes": "set_timer",
        "das minute baad alarm": "set_timer",
        "tell me the humidity in here": "show",
        "tell me a joke": "none",
        "aaj mausam kaisa hai": "none",
    }
    hits = 0
    for text, command in wanted.items():
        tokens = tokenize(text)
        best = int(loaded.intent_probabilities(tokens).argmax())
        hits += loaded.classes[best] == command
    assert hits >= len(wanted) - 2


def test_slot_values_come_back(trained):
    _, loaded = trained
    tokens = tokenize("turn on the bedroom light")
    tags, _ = loaded.tag(tokens)
    result = decode(tokens, tags, "set_light", loaded.spec)
    assert result.slots.get("room") == "bedroom"
    assert result.slots.get("state") == "on"


def test_confidence_is_between_zero_and_one(trained):
    _, loaded = trained
    for text in ["turn on the bedroom light", "wibble wobble", "das minute baad alarm"]:
        tokens = tokenize(text)
        probabilities = loaded.intent_probabilities(tokens)
        _, slot_probability = loaded.tag(tokens)
        value = loaded.confidence(float(probabilities.max()), slot_probability)
        assert 0.0 <= value <= 1.0


def test_jsonl_round_trip(tmp_path, example_spec):
    examples = generate(example_spec, n_per_command=20, seed=0)[:50]
    path = tmp_path / "part.jsonl"
    assert write_jsonl(examples, path) == len(examples)
    back = read_jsonl(path)
    assert [e.tokens for e in back] == [e.tokens for e in examples]
    assert [e.tags for e in back] == [e.tags for e in examples]
    assert [e.command for e in back] == [e.command for e in examples]
