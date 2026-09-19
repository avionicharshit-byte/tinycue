"""Shared test helpers: a small commands file and a way to write throwaway ones."""

from __future__ import annotations

from pathlib import Path

import pytest

from tinycue.parser import load_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_FILE = REPO_ROOT / "examples/smart_home.yaml"
ROBOT_FILE = REPO_ROOT / "examples/robot.yaml"
HELDOUT_FILE = REPO_ROOT / "eval/heldout_smart_home.yaml"
PACKAGE_DIR = REPO_ROOT / "src/tinycue"

SMALL_SPEC = """
language: [en, hinglish]

slots:
  room:
    values:
      bedroom: [bedroom, bed room, sone ka kamra]
      kitchen: [kitchen, rasoi]
  state:
    values:
      "on": ["on", chalu, jala do]
      "off": ["off", band, band kar do]
  minutes:
    type: number
    min: 1
    max: 180

commands:
  - name: set_light
    slots: [room, state]
    examples:
      - "turn [on](state) the [bedroom](room) light"
      - "[rasoi](room) mein light [jala do](state)"
  - name: set_timer
    slots: [minutes]
    examples:
      - "set a timer for [ten](minutes) minutes"

fallback:
  unsure_below: auto
  none_command: true
  on_unsure: ask
"""


@pytest.fixture
def write_spec(tmp_path):
    """Write a commands file and return its path."""

    def _write(text: str, name: str = "commands.yaml") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def small_spec(write_spec):
    return load_spec(write_spec(SMALL_SPEC))


@pytest.fixture
def example_spec():
    return load_spec(EXAMPLE_FILE)


@pytest.fixture
def robot_spec():
    return load_spec(ROBOT_FILE)


def spans_from_tags(tokens, tags):
    """Rebuild (slot type, surface text) pairs from BIO tags."""
    spans = []
    current_slot = None
    current_tokens: list[str] = []
    for token, tag in zip(tokens, tags):
        if tag.startswith("B-"):
            if current_slot is not None:
                spans.append((current_slot, " ".join(current_tokens)))
            current_slot = tag[2:]
            current_tokens = [token]
        elif tag.startswith("I-"):
            assert current_slot == tag[2:], "an I- tag must follow its own B- tag"
            current_tokens.append(token)
        else:
            if current_slot is not None:
                spans.append((current_slot, " ".join(current_tokens)))
            current_slot = None
            current_tokens = []
    if current_slot is not None:
        spans.append((current_slot, " ".join(current_tokens)))
    return spans
