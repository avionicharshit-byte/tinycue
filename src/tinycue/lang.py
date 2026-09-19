"""Language resources: filler words, droppable carrier words and out-of-scope sentences.

These are per language, not per domain. A commands file picks them with its 'language' list
and can add its own on top.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import SpecError

LANG_DIR = Path(__file__).resolve().parent / "langs"

KEYS = ("fillers", "droppable", "none_examples")


def available() -> list[str]:
    """Every language we ship a resource file for."""
    return sorted(p.stem for p in LANG_DIR.glob("*.yaml"))


def load(language: str) -> dict[str, list[str]]:
    """One language file. Unknown languages come back empty rather than failing."""
    path = LANG_DIR / f"{language}.yaml"
    if not path.is_file():
        return {key: [] for key in KEYS}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, list[str]] = {}
    for key in KEYS:
        value = raw.get(key) or []
        if not isinstance(value, list):
            raise SpecError(f"language file {path}: '{key}' must be a list")
        out[key] = [str(v) for v in value]
    return out


def merged(languages) -> dict[str, list[str]]:
    """The resources for a list of languages, in order, with duplicates removed."""
    out: dict[str, list[str]] = {key: [] for key in KEYS}
    for language in languages:
        part = load(language)
        for key in KEYS:
            for item in part[key]:
                if item not in out[key]:
                    out[key].append(item)
    return out
