"""The package must not know about any one domain.

Domain words belong in a commands file. If one of them ever appears in src/edgenlu, the
tool has stopped being general and this test says so.
"""

from __future__ import annotations

import re

from conftest import PACKAGE_DIR

# Words from the two example domains. Neither set may appear in the package source.
DOMAIN_WORDS = [
    # smart home
    "light",
    "lights",
    "batti",
    "fan",
    "pankha",
    "bedroom",
    "kitchen",
    "rasoi",
    "bathroom",
    "thermostat",
    # robot
    "gripper",
    "claw",
    "robot",
    "kadam",
]

def source_files():
    return sorted(PACKAGE_DIR.rglob("*.py")) + sorted(PACKAGE_DIR.rglob("*.yaml"))


def test_there_are_source_files_to_check():
    assert len(source_files()) > 8


def test_no_domain_words_in_the_package():
    found = []
    for path in source_files():
        text = path.read_text(encoding="utf-8").lower()
        for word in DOMAIN_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", text):
                found.append(f"{path.relative_to(PACKAGE_DIR)}: {word}")
    assert not found, "domain words in the package source: " + ", ".join(found)


def test_the_language_files_carry_no_domain_words():
    """The language files are per language, not per domain. Same rule, said twice."""
    for path in sorted((PACKAGE_DIR / "langs").glob("*.yaml")):
        text = path.read_text(encoding="utf-8").lower()
        for word in DOMAIN_WORDS:
            assert not re.search(rf"\b{re.escape(word)}\b", text), f"{path.name}: {word}"


def test_the_guard_would_actually_catch_something(tmp_path):
    """A test that never fires is not a test. Prove the pattern matches."""
    assert re.search(r"\bfan\b", "turn the fan on")
    assert not re.search(r"\bfan\b", "fantastic")
