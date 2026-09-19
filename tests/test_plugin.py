"""The Claude Code plugin: the manifests parse and the skill says what it is for."""

from __future__ import annotations

import json

from conftest import REPO_ROOT

MARKETPLACE = REPO_ROOT / ".claude-plugin/marketplace.json"
PLUGIN = REPO_ROOT / ".claude-plugin/plugin.json"
SKILL = REPO_ROOT / "skills/tinycue/SKILL.md"

# The skill is read into a context window on every use, so it has to stay short.
MAX_LINES = 250


def test_the_marketplace_lists_the_plugin():
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert data["name"] == "tinycue"
    assert data["description"]
    assert [p["name"] for p in data["plugins"]] == ["tinycue"]
    assert data["plugins"][0]["source"] == "./"


def test_the_plugin_manifest_matches_the_marketplace():
    plugin = json.loads(PLUGIN.read_text(encoding="utf-8"))
    listed = json.loads(MARKETPLACE.read_text(encoding="utf-8"))["plugins"][0]
    assert plugin["name"] == listed["name"]
    for key in ("version", "description", "homepage", "repository", "license"):
        assert plugin[key], key


def test_the_skill_has_front_matter_with_a_name_and_a_description():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front = text.split("---", 2)[1]
    assert "name: tinycue" in front
    assert "description:" in front
    # The description is what decides whether the skill is ever picked up.
    assert "offline" in front and "microcontroller" in front


def test_the_skill_stays_short():
    assert len(SKILL.read_text(encoding="utf-8").splitlines()) <= MAX_LINES


def test_the_skill_teaches_the_whole_loop():
    text = SKILL.read_text(encoding="utf-8")
    for command in ("tinycue check", "tinycue train", "tinycue doctor", "tinycue export"):
        assert command in text, command
    for topic in ("answer: ", "unknown_words", "unsure", "tcue_parse", "--dev"):
        assert topic in text, topic


def test_the_skill_names_no_domain_of_its_own():
    """The instructions have to fit any device, so the two example domains stay out."""
    import re

    from test_domain_free import DOMAIN_WORDS

    text = SKILL.read_text(encoding="utf-8").lower()
    found = [w for w in DOMAIN_WORDS if re.search(rf"\b{re.escape(w)}\b", text)]
    # A toy domain is fine in an example, but not the domains we ship examples for.
    assert found == [], found
