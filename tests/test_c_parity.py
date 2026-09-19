"""The C runtime has to answer exactly what Python answers.

Every sentence of both hand-written held-out files, plus 300 generated sentences per
spec, goes through both sides. The command, the slot values and the unsure flag must
match, and the confidence must agree to 1e-3.

Python is run twice. Once with the float32 weights it trained, and once with the same
weights rounded to the float16 the blob carries, which is what the C side actually reads.
The float16 run is the apples to apples one and has to match perfectly; the float32 run
is reported as well, because that difference is the price of shrinking the weights.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import numpy as np
import pytest

from conftest import EXAMPLE_FILE, REPO_ROOT, ROBOT_FILE
from edgenlu import model as bundle
from edgenlu.decode import decode
from edgenlu.export import build_blob, crf_tables, dequantised_weights, export, fnv1a64
from edgenlu.generator import generate
from edgenlu.parser import load_examples_file, load_spec, tokenize
from edgenlu.train import train

RUNTIME = REPO_ROOT / "runtime"
TOLERANCE = 1e-3
GENERATED = 300
TRAIN_N = 400

SPECS = [
    ("smart_home", EXAMPLE_FILE, REPO_ROOT / "eval/heldout_smart_home.yaml"),
    ("robot", ROBOT_FILE, REPO_ROOT / "eval/heldout_robot.yaml"),
]


def compiler() -> str | None:
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    """Build enlu_cli, or skip the whole file when there is no C compiler."""
    found = compiler()
    if found is None:
        pytest.skip("no C compiler on this machine")
    build = tmp_path_factory.mktemp("runtime")
    result = subprocess.run(
        [
            found,
            "-O2",
            "-Wall",
            "-Wextra",
            "-std=c99",
            "-pedantic",
            "-o",
            str(build / "enlu_cli"),
            str(RUNTIME / "cli.c"),
            str(RUNTIME / "edgenlu.c"),
            "-lm",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail("the C runtime did not build:\n" + result.stderr)
    assert result.stderr.strip() == "", "the C runtime built with warnings:\n" + result.stderr
    return build / "enlu_cli"


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    """One trained and exported bundle per example spec."""
    out = {}
    root = tmp_path_factory.mktemp("parity")
    for name, spec_file, held in SPECS:
        spec = load_spec(spec_file)
        directory = root / name
        train(spec, directory, n_per_command=TRAIN_N, seed=0, log=lambda *a: None)
        device = root / (name + "_device")
        export(directory, device)
        out[name] = {
            "model": bundle.load(directory),
            "blob": device / "model.bin",
            "held": held,
            "spec": spec,
        }
    return out


def sentences(entry) -> list[str]:
    """Every held-out sentence plus a slice of freshly generated ones."""
    model = entry["model"]
    out = [" ".join(e.tokens) for e in load_examples_file(entry["held"], model.spec)]
    grown = generate(entry["spec"], n_per_command=GENERATED, seed=11)
    picked = []
    for example in grown:
        text = " ".join(example.tokens)
        if len(example.tokens) > 32 or len(text.encode("utf-8")) > 240:
            continue
        if text in picked:
            continue
        picked.append(text)
        if len(picked) >= GENERATED:
            break
    return out + picked


def run_c(cli_path, blob, texts) -> list[dict]:
    result = subprocess.run(
        [str(cli_path), str(blob)],
        input="\n".join(texts) + "\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == len(texts), f"{len(lines)} replies for {len(texts)} sentences"
    return [json.loads(line) for line in lines]


def run_python(model, text) -> dict:
    tokens = tokenize(text)
    probabilities = model.intent_probabilities(tokens)
    best = int(np.argmax(probabilities))
    command = model.classes[best]
    tags, slot_probability = model.tag(tokens)
    result = decode(tokens, tags, command, model.spec)
    confidence = model.confidence(float(probabilities[best]), slot_probability)
    return {
        "command": command,
        "slots": dict(result.slots),
        "missing": list(result.missing),
        "confidence": confidence,
        "unsure": confidence < model.unsure_below,
    }


def half_model(model):
    """The same model with the weights the device will actually read."""
    import copy

    twin = copy.copy(model)
    twin.weights = dequantised_weights(model)
    twin._tagger = model.tagger
    return twin


def compare(got: dict, want: dict) -> str | None:
    if got["command"] != want["command"]:
        return f"command {got['command']} against {want['command']}"
    if got["slots"] != want["slots"]:
        return f"slots {got['slots']} against {want['slots']}"
    if got["missing"] != want["missing"]:
        return f"missing {got['missing']} against {want['missing']}"
    if bool(got["unsure"]) != bool(want["unsure"]):
        return f"unsure {got['unsure']} against {want['unsure']}"
    if abs(got["confidence"] - want["confidence"]) > TOLERANCE:
        return f"confidence {got['confidence']:.6f} against {want['confidence']:.6f}"
    return None


@pytest.mark.parametrize("name", [name for name, _, _ in SPECS])
def test_c_matches_python(cli, models, name, capsys):
    entry = models[name]
    texts = sentences(entry)
    assert len(texts) > 300

    replies = run_c(cli, entry["blob"], texts)
    exact = half_model(entry["model"])

    mismatches = []
    float32_differences = 0
    worst_float32 = 0.0
    for text, reply in zip(texts, replies):
        assert "error" not in reply, f"{text}: {reply.get('error')}"
        want = run_python(exact, text)
        problem = compare(reply, want)
        if problem is not None:
            mismatches.append(f"{text}: {problem}")

        plain = run_python(entry["model"], text)
        if plain["command"] != reply["command"] or plain["slots"] != reply["slots"]:
            float32_differences += 1
        worst_float32 = max(worst_float32, abs(plain["confidence"] - reply["confidence"]))

    with capsys.disabled():
        print(
            f"\n{name}: {len(texts)} sentences, {len(mismatches)} mismatches against the "
            f"float16 weights, {float32_differences} decisions moved by rounding the "
            f"weights, worst confidence gap against float32 {worst_float32:.2e}"
        )
    assert not mismatches, "\n".join(mismatches[:20])
    assert float32_differences == 0
    assert worst_float32 < TOLERANCE


def test_the_blob_starts_with_its_magic(models):
    blob = models["smart_home"]["blob"].read_bytes()
    assert blob[:4] == b"ENLU"
    assert int.from_bytes(blob[4:8], "little") == 1
    assert int.from_bytes(blob[8:12], "little") == len(blob)


def test_the_blob_is_well_under_a_megabyte(models):
    for name in models:
        size = models[name]["blob"].stat().st_size
        assert size < 1024 * 1024, f"{name}: {size} bytes"


def test_every_tagger_feature_hashes_to_its_own_value(models):
    """The blob stores hashes, not strings, so a collision would silently change answers."""
    for name in models:
        tables = crf_tables(models[name]["model"])
        assert len(set(tables["hashes"])) == len(tables["hashes"])
        assert tables["hashes"] == sorted(tables["hashes"])


def test_a_hash_collision_stops_the_export(monkeypatch, models):
    """The check has to actually fire, so force two features onto one hash."""
    import edgenlu.export as export_module

    monkeypatch.setattr(export_module, "fnv1a64", lambda text: 1)
    with pytest.raises(export_module.ExportError, match="same 64 bit value"):
        build_blob(models["smart_home"]["model"])


def test_fnv1a64_matches_the_known_value():
    assert fnv1a64("") == 0xCBF29CE484222325
    assert fnv1a64("a") == 0xAF63DC4C8601EC8C
    assert fnv1a64("foobar") == 0x85944171F73967E8


def test_the_c_side_refuses_a_blob_it_does_not_know(cli, tmp_path):
    broken = tmp_path / "broken.bin"
    broken.write_bytes(b"NOPE" + bytes(200))
    result = subprocess.run([str(cli), str(broken)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "blob" in result.stderr


def test_a_sentence_that_is_too_long_is_refused_not_cut_short(cli, models):
    texts = [" ".join(["word"] * 60)]
    reply = run_c(cli, models["smart_home"]["blob"], texts)[0]
    assert "error" in reply
