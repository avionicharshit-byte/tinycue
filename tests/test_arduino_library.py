"""The Arduino library is a copy of the runtime, so the copy must not drift.

Somebody who downloads a ZIP of this repo gets whatever is committed, so the two C
sources are committed rather than synced at build time. That makes drift possible, and
this is what stops it: change `runtime/tinycue.c` without running `make arduino-sync`
and the suite fails here.
"""

from __future__ import annotations

from conftest import REPO_ROOT
from tinycue import __version__

LIB_DIR = REPO_ROOT / "arduino" / "TinyCue"
LIB_SRC = LIB_DIR / "src"
RUNTIME_DIR = REPO_ROOT / "runtime"
EXAMPLE_DIR = LIB_DIR / "examples" / "SerialCommands"

SOURCES = ("tinycue.h", "tinycue.c")


def _properties() -> dict[str, str]:
    out = {}
    for line in (LIB_DIR / "library.properties").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def test_the_library_copies_match_the_runtime():
    drifted = []
    for name in SOURCES:
        copy = (LIB_SRC / name).read_bytes()
        original = (RUNTIME_DIR / name).read_bytes()
        if copy != original:
            drifted.append(name)
    assert not drifted, (
        "the Arduino copies no longer match runtime/: "
        + ", ".join(drifted)
        + ". Run 'make arduino-sync'."
    )


def test_library_properties_has_what_the_ide_needs():
    props = _properties()
    for key in ("name", "version", "author", "sentence", "paragraph", "category",
                "url", "architectures", "includes"):
        assert props.get(key), f"library.properties is missing '{key}'"
    assert props["category"] == "Data Processing"
    assert props["architectures"] == "*"
    assert props["includes"] == "tinycue.h"
    assert "github.com" in props["url"]


def test_the_library_version_matches_the_python_package():
    assert _properties()["version"] == __version__


def test_keywords_are_tab_separated():
    lines = (LIB_DIR / "keywords.txt").read_text(encoding="utf-8").splitlines()
    entries = [line for line in lines if line and not line.startswith("#")]
    assert len(entries) > 10
    for line in entries:
        assert "\t" in line, f"keywords.txt needs a tab, not spaces: {line!r}"
        assert line.split("\t")[1] in {"KEYWORD1", "KEYWORD2", "LITERAL1"}


def test_the_example_carries_a_model_it_can_build():
    sketch = (EXAMPLE_DIR / "SerialCommands.ino").read_text(encoding="utf-8")
    assert "#include <tinycue.h>" in sketch
    assert '#include "model_data.h"' in sketch
    assert (EXAMPLE_DIR / "model_data.h").is_file()
    data = (EXAMPLE_DIR / "model_data.c").read_text(encoding="utf-8")
    assert "tcue_model_data" in data
    assert "aligned(8)" in data
