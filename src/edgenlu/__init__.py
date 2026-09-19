"""edge-nlu: offline command understanding for tiny devices."""

from .generator import generate
from .numbers import parse_number
from .parser import load_spec
from .schema import Command, Example, Fallback, SlotType, Spec, SpecError

__version__ = "0.0.1"

__all__ = [
    "Command",
    "Example",
    "Fallback",
    "SlotType",
    "Spec",
    "SpecError",
    "generate",
    "load_spec",
    "parse_number",
]
