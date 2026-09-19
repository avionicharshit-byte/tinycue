"""tinycue: offline command understanding for tiny devices."""

from .decode import decode
from .generator import generate
from .numbers import parse_number
from .parser import load_examples_file, load_spec
from .schema import Command, Example, Fallback, SlotType, Spec, SpecError

__version__ = "0.1.0"

__all__ = [
    "Command",
    "Example",
    "Fallback",
    "SlotType",
    "Spec",
    "SpecError",
    "decode",
    "generate",
    "load_examples_file",
    "load_spec",
    "parse_number",
]
