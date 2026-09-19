"""Smoke tests for the edgenlu command line against the example commands file."""

from __future__ import annotations

import json

from conftest import EXAMPLE_FILE
from edgenlu.cli import main


def test_check(capsys):
    assert main(["check", str(EXAMPLE_FILE)]) == 0
    out = capsys.readouterr().out
    assert "ok" in out
    assert "commands: 4" in out
    assert "set_light: 6 examples" in out
    assert "examples: 24" in out


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
