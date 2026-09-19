"""The starter files `edgenlu init` writes.

Three files, commented line by line, holding a toy device that trains in seconds. They
are meant to be deleted and replaced, not kept: the point is that a newcomer has
something that runs before they have to invent anything.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# template file -> the suffix the written file gets
TEMPLATES = {
    "commands.yaml": ".yaml",
    "extra.yaml": ".extra.yaml",
    "dev.yaml": ".dev.yaml",
}

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class InitError(RuntimeError):
    """The starter files cannot be written. The message says why."""


def targets(name: str, out_dir) -> list[Path]:
    """Where `edgenlu init NAME` would write, in the order it writes them."""
    out = Path(out_dir)
    return [out / f"{name}{suffix}" for suffix in TEMPLATES.values()]


def write(name: str, out_dir) -> list[Path]:
    """Write the three starter files. Refuses rather than overwriting anything."""
    if not NAME_PATTERN.match(name):
        raise InitError(
            f"'{name}' is not a usable name. Use letters, digits, '-' and '_', "
            "starting with a letter or a digit."
        )
    out = Path(out_dir)
    existing = [p for p in targets(name, out) if p.exists()]
    if existing:
        raise InitError(
            "these files are already here, so nothing was written: "
            + ", ".join(str(p) for p in existing)
        )

    out.mkdir(parents=True, exist_ok=True)
    written = []
    for template, suffix in TEMPLATES.items():
        text = (TEMPLATE_DIR / template).read_text(encoding="utf-8")
        target = out / f"{name}{suffix}"
        target.write_text(text.replace("{name}", name), encoding="utf-8")
        written.append(target)
    return written
