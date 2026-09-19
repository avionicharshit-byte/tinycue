"""Smoke tests for the edgenlu command line against the example commands file."""

from __future__ import annotations

import json

import pytest

from conftest import EXAMPLE_FILE
from edgenlu.cli import main


def test_check(capsys):
    assert main(["check", str(EXAMPLE_FILE)]) == 0
    out = capsys.readouterr().out
    assert "ok" in out
    assert "commands: 4" in out
    assert "set_light: 14 examples" in out
    assert "examples: 56" in out


def test_check_reports_a_bad_file(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("commands: []\n", encoding="utf-8")
    assert main(["check", str(bad)]) == 1
    assert "error:" in capsys.readouterr().err


def test_check_reports_a_missing_file(tmp_path, capsys):
    assert main(["check", str(tmp_path / "nope.yaml")]) == 1
    assert "error:" in capsys.readouterr().err


def test_generate_to_a_file(tmp_path, capsys):
    out = tmp_path / "out" / "train.jsonl"
    assert main(["generate", str(EXAMPLE_FILE), "-n", "50", "--seed", "0", "-o", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) > 100
    for line in lines:
        record = json.loads(line)
        assert set(record) == {"tokens", "tags", "command", "slots"}
        assert len(record["tokens"]) == len(record["tags"])
    assert "wrote" in capsys.readouterr().err


def test_generate_to_standard_output(capsys):
    assert main(["generate", str(EXAMPLE_FILE), "-n", "5", "--seed", "0"]) == 0
    lines = capsys.readouterr().out.strip().split("\n")
    assert lines
    assert json.loads(lines[0])["command"] == "set_light"


def test_check_reports_the_word_lists(capsys):
    assert main(["check", str(EXAMPLE_FILE)]) == 0
    out = capsys.readouterr().out
    assert "fillers" in out
    assert "equivalent groups" in out


def test_check_the_robot_file(capsys):
    from conftest import ROBOT_FILE

    assert main(["check", str(ROBOT_FILE)]) == 0
    out = capsys.readouterr().out
    assert "commands: 6" in out
    assert "stop: 12 examples, slots: none" in out


@pytest.fixture(scope="module")
def cli_model(tmp_path_factory):
    """One small bundle for the train, eval and parse smoke tests."""
    out = tmp_path_factory.mktemp("cli") / "model"
    code = main(["train", str(EXAMPLE_FILE), "-n", "120", "--seed", "0", "-o", str(out)])
    assert code == 0
    return out


def test_train_writes_a_bundle_and_splits(cli_model):
    for name in ("intent.npz", "slots.crfsuite", "meta.json"):
        assert (cli_model / name).is_file()
    splits = cli_model.parent / "splits"
    assert (splits / "dev.jsonl").is_file()
    assert (splits / "test.jsonl").is_file()


def test_eval_on_the_generated_test_split(cli_model, capsys):
    data = cli_model.parent / "splits" / "test.jsonl"
    assert main(["eval", str(cli_model), "--data", str(data)]) == 0
    out = capsys.readouterr().out
    for line in ("intent accuracy", "slot f1", "full command accuracy", "ece", "reliability"):
        assert line in out
    assert "cut-off" in out
    assert "sent to unsure" in out


def test_eval_on_the_held_out_file(cli_model, capsys):
    from conftest import HELDOUT_FILE

    assert main(["eval", str(cli_model), "--data", str(HELDOUT_FILE)]) == 0
    out = capsys.readouterr().out
    assert "144 sentences" in out
    assert "worst" in out


def test_eval_with_a_cutoff_of_your_own(cli_model, capsys):
    data = cli_model.parent / "splits" / "test.jsonl"
    assert main(["eval", str(cli_model), "--data", str(data), "--cutoff", "0.0"]) == 0
    out = capsys.readouterr().out
    assert "sent to unsure:        0.0%" in out


def test_parse_prints_a_call(cli_model, capsys):
    assert main(["parse", str(cli_model), "turn on the bedroom light"]) == 0
    out = capsys.readouterr().out.strip()
    assert "confidence=" in out
    assert "set_light(" in out or "unsure" in out


def test_parse_takes_a_sentence_as_several_words(cli_model, capsys):
    assert main(["parse", str(cli_model), "das", "minute", "baad", "alarm"]) == 0
    assert "confidence=" in capsys.readouterr().out


def test_parse_an_out_of_scope_sentence(cli_model, capsys):
    assert main(["parse", str(cli_model), "tell me a joke"]) == 0
    out = capsys.readouterr().out
    assert "none" in out or "unsure" in out


def test_parse_rejects_an_empty_sentence(cli_model, capsys):
    assert main(["parse", str(cli_model), "   "]) == 1
    assert "error:" in capsys.readouterr().err


def test_parse_as_json_carries_the_gate_evidence(cli_model, capsys):
    assert main(["parse", str(cli_model), "--json", "turn on the bedroom light"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["command"]
    for key in ("confidence", "intent", "slot", "margin", "unknown", "unknown_share"):
        assert key in reply
    assert reply["unknown"] == 0
    assert reply["unknown_share"] == 0.0


def test_a_sentence_of_words_from_nowhere_is_all_unknown(cli_model, capsys):
    assert main(["parse", str(cli_model), "--json", "quorbek fimnaz durvel"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["unknown"] == 3
    assert reply["unknown_share"] == 1.0
    assert reply["all_carrier_unknown"] is True
    assert reply["unsure"] is True


def test_eval_reads_an_answer_first_file(cli_model, tmp_path, capsys):
    """The value-level path: no spans, so a wording nobody listed still counts."""
    data = tmp_path / "answers.yaml"
    data.write_text(
        "- answer: set_light(room=bedroom, state=on)\n"
        "  say:\n"
        "    - illuminate the boudoir\n"
        "    - switch the bedroom light on\n"
        "- answer: none\n"
        "  say:\n"
        "    - i am reading a book\n",
        encoding="utf-8",
    )
    assert main(["eval", str(cli_model), "--data", str(data), "--summary"]) == 0
    out = capsys.readouterr().out
    assert "3 sentences" in out
    assert "slot f1 (value)" in out


def test_eval_on_a_marked_up_file_still_scores_spans(cli_model, capsys):
    from conftest import HELDOUT_FILE

    assert main(["eval", str(cli_model), "--data", str(HELDOUT_FILE), "--summary"]) == 0
    assert "slot f1 (span)" in capsys.readouterr().out
