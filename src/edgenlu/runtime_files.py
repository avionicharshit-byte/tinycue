"""Where the two C runtime sources live, installed or in a checkout.

The wheel carries copies at `edgenlu/runtime/`. A checkout has the originals in
`runtime/` at the repo root and no copies at all, so there is one file under version
control and never two that can drift.
"""

from __future__ import annotations

import shutil
from pathlib import Path

SOURCES = ("edgenlu.h", "edgenlu.c")

_HERE = Path(__file__).resolve().parent
_CANDIDATES = (
    _HERE / "runtime",          # installed: copied in by the wheel build
    _HERE.parents[1] / "runtime",  # a checkout installed with pip install -e .
)


class RuntimeMissing(RuntimeError):
    """The C sources are not next to the package. The message says where it looked."""


def runtime_dir() -> Path:
    """The folder holding edgenlu.h and edgenlu.c."""
    for candidate in _CANDIDATES:
        if all((candidate / name).is_file() for name in SOURCES):
            return candidate
    looked = ", ".join(str(c) for c in _CANDIDATES)
    raise RuntimeMissing(
        "the C runtime sources are not installed next to the package. Looked in: " + looked
    )


def copy_runtime(out_dir) -> list[Path]:
    """Copy edgenlu.h and edgenlu.c into a folder. Returns what was written."""
    source = runtime_dir()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name in SOURCES:
        target = out / name
        shutil.copyfile(source / name, target)
        written.append(target)
    return written
