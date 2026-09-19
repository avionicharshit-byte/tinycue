"""The C runtime has to answer exactly what Python answers.

Every sentence of both hand-written held-out files, plus 300 generated sentences per
spec, goes through both sides. The command, the slot values, the unsure flag and the
gate evidence must match, and the confidence must agree to 1e-3.

Python is run twice. Once with the float32 weights it trained, and once with the same
weights rounded to the float16 the blob carries, which is what the C side actually reads.
The float16 run is the apples to apples one and has to match perfectly; the float32 run
is reported as well, because that difference is the price of shrinking the weights.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from conftest import EXAMPLE_FILE, REPO_ROOT, ROBOT_FILE
from edgenlu import model as bundle
from edgenlu.export import (
    BLOB_VERSION,
    build_blob,
    crf_tables,
    dequantised_weights,
    export,
    fnv1a64,
)
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
def cli_fast(tmp_path_factory):
    """The same runtime built with the single precision exponentials the device uses."""
    found = compiler()
    if found is None:
        pytest.skip("no C compiler on this machine")
    build = tmp_path_factory.mktemp("runtime_fast")
    result = subprocess.run(
        [
            found,
            "-O2",
            "-Wall",
            "-Wextra",
            "-std=c99",
            "-pedantic",
            "-DENLU_FAST_EXP",
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
        pytest.fail("the fast build did not build:\n" + result.stderr)
    assert result.stderr.strip() == "", result.stderr
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
    reading = model.read(tokenize(text))
    return {
        "command": reading.command,
        "slots": dict(reading.decoded.slots),
        "missing": list(reading.decoded.missing),
        "confidence": reading.confidence,
        "unsure": reading.unsure,
        "unknown": reading.signals["unknown_count"],
        "unknown_share": reading.signals["unknown_share"],
        "all_carrier_unknown": bool(reading.signals["all_carrier_unknown"]),
        "margin": reading.signals["margin"],
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
    if "unknown" in got:
        if got["unknown"] != want["unknown"]:
            return f"unknown words {got['unknown']} against {want['unknown']}"
        if bool(got["all_carrier_unknown"]) != bool(want["all_carrier_unknown"]):
            return (
                f"all carrier words unknown {got['all_carrier_unknown']} against "
                f"{want['all_carrier_unknown']}"
            )
        if abs(got["unknown_share"] - want["unknown_share"]) > 1e-6:
            return f"unknown share {got['unknown_share']} against {want['unknown_share']}"
        if abs(got["margin"] - want["margin"]) > TOLERANCE:
            return f"margin {got['margin']:.6f} against {want['margin']:.6f}"
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


@pytest.mark.parametrize("name", [name for name, _, _ in SPECS])
def test_the_device_build_answers_the_same(cli, cli_fast, models, name):
    """ENLU_FAST_EXP is what the ESP32 runs. It must not change a single answer."""
    entry = models[name]
    texts = sentences(entry)
    slow = run_c(cli, entry["blob"], texts)
    quick = run_c(cli_fast, entry["blob"], texts)
    for a, b in zip(slow, quick):
        assert a["command"] == b["command"]
        assert a["slots"] == b["slots"]
        assert a["missing"] == b["missing"]
        assert a["unsure"] == b["unsure"]
        assert abs(a["confidence"] - b["confidence"]) < 1e-5


def test_the_blob_starts_with_its_magic(models):
    blob = models["smart_home"]["blob"].read_bytes()
    assert blob[:4] == b"ENLU"
    assert int.from_bytes(blob[4:8], "little") == BLOB_VERSION
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


EXTRA_SURFACE = """
- answer: set_fan(room=bedroom, speed=up)
  say:
    - "bedroom fan [a notch higher](speed) please"
    - "crank the bedroom fan [a notch higher](speed)"
    - "[sone ka kamra](room) ka pankha [a notch higher](speed) kar do"
"""


def test_a_surface_taught_by_an_extra_file_reaches_the_c_blob(cli, tmp_path):
    """A word the markup taught has to survive training, export and the device."""
    from edgenlu import answers

    spec = load_spec(EXAMPLE_FILE)
    extra = tmp_path / "extra.yaml"
    extra.write_text(EXTRA_SURFACE, encoding="utf-8")
    spec.extra_files.append(str(extra))
    answers.apply(spec, answers.load(spec.extra_files, spec, register=True))

    model_dir = tmp_path / "model"
    train(spec, model_dir, n_per_command=TRAIN_N, seed=0, log=lambda *a: None)
    device = tmp_path / "device"
    export(model_dir, device)

    blob = (device / "model.bin").read_bytes()
    assert b"a notch higher\x00" in blob, "the new surface never reached the blob"

    loaded = bundle.load(model_dir)
    text = "bedroom fan a notch higher please"
    reply = run_c(cli, device / "model.bin", [text])[0]
    want = run_python(half_model(loaded), text)
    assert compare(reply, want) is None
    assert reply["command"] == "set_fan"
    assert reply["slots"] == {"room": "bedroom", "speed": "up"}
