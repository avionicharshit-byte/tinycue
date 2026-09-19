"""The model bundle: save it, load it, run it.

A bundle is a directory with three files:

    intent.npz      the linear intent model: weights, bias, temperature, class names
    slots.crfsuite  the CRFsuite tagger, as CRFsuite writes it
    meta.json       the feature settings, the cut-off and enough of the commands file
                    to turn tags back into values

meta.json carries the commands file rather than pointing at it, so a bundle works on its
own. The C runtime reads the same three files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import features as F
from .schema import (
    NONE_COMMAND,
    NUMBER,
    Command,
    CommandSlot,
    Fallback,
    SlotType,
    Spec,
)

FORMAT_VERSION = 1
INTENT_FILE = "intent.npz"
SLOTS_FILE = "slots.crfsuite"
META_FILE = "meta.json"


def spec_digest(path) -> str:
    """A short fingerprint of the commands file the bundle was trained from."""
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()[:16]


def spec_to_json(spec: Spec) -> dict:
    """The parts of a commands file a decoder needs, without the example sentences."""
    slot_types = {}
    for name, slot_type in spec.slot_types.items():
        if slot_type.kind == NUMBER:
            slot_types[name] = {"kind": NUMBER, "min": slot_type.min, "max": slot_type.max}
        else:
            slot_types[name] = {"kind": slot_type.kind, "values": slot_type.values}
    return {
        "languages": spec.languages,
        "slot_types": slot_types,
        "commands": [
            {
                "name": c.name,
                "slots": [
                    {"name": s.name, "type": s.type, "required": s.required} for s in c.slots
                ],
            }
            for c in spec.commands
        ],
        "fallback": {
            "unsure_below": spec.fallback.unsure_below,
            "none_command": spec.fallback.none_command,
            "on_unsure": spec.fallback.on_unsure,
        },
    }


def spec_from_json(raw: dict) -> Spec:
    """Rebuild a spec from meta.json. It has no examples, which decoding does not need."""
    spec = Spec()
    spec.languages = list(raw.get("languages", ["en"]))
    for name, body in raw["slot_types"].items():
        if body["kind"] == NUMBER:
            spec.slot_types[name] = SlotType(
                name=name, kind=NUMBER, min=body.get("min"), max=body.get("max")
            )
        else:
            spec.slot_types[name] = SlotType(
                name=name, kind=body["kind"], values=dict(body.get("values", {}))
            )
    for body in raw["commands"]:
        command = Command(name=body["name"])
        for s in body["slots"]:
            command.slots.append(
                CommandSlot(name=s["name"], type=s["type"], required=s["required"])
            )
        spec.commands.append(command)
    fallback = raw.get("fallback", {})
    spec.fallback = Fallback(
        unsure_below=fallback.get("unsure_below", "auto"),
        none_command=fallback.get("none_command", True),
        on_unsure=fallback.get("on_unsure", "ask"),
    )
    return spec


@dataclass
class Model:
    """A trained bundle, ready to answer."""

    spec: Spec
    classes: list[str]
    weights: np.ndarray  # one row per class, one column per hash bucket
    bias: np.ndarray
    temperature: float
    slot_power: float
    table_size: int
    unsure_below: float
    meta: dict
    tagger_path: str

    _tagger = None

    @property
    def tagger(self):
        """The CRFsuite tagger, opened the first time it is needed."""
        if self._tagger is None:
            import pycrfsuite

            tagger = pycrfsuite.Tagger()
            tagger.open(self.tagger_path)
            self._tagger = tagger
        return self._tagger

    @property
    def gazetteer(self) -> dict[str, set[str]]:
        if not hasattr(self, "_gaz"):
            self._gaz = F.gazetteer(self.spec)
        return self._gaz

    def intent_scores(self, tokens) -> np.ndarray:
        """The raw score per class, before the temperature and the softmax."""
        vector = F.intent_vector(tokens, self.table_size)
        return self.weights @ vector + self.bias

    def intent_probabilities(self, tokens) -> np.ndarray:
        """Calibrated probabilities over the classes, in `classes` order."""
        return softmax(self.intent_scores(tokens) / self.temperature)

    def tag(self, tokens) -> tuple[list[str], float]:
        """The BIO tags for a sentence and how likely the tagger thinks they are."""
        tokens = list(tokens)
        if not tokens:
            return [], 1.0
        items = F.sentence_features(tokens, self.gazetteer)
        tagger = self.tagger
        tagger.set(items)
        tags = tagger.tag()
        try:
            probability = float(tagger.probability(tags))
        except Exception:
            probability = 1.0
        return tags, probability

    def confidence(self, intent_probability: float, slot_probability: float) -> float:
        """The one number the caller sees: the intent part times the calibrated tagger part."""
        return float(intent_probability) * float(max(slot_probability, 0.0)) ** self.slot_power


def softmax(scores: np.ndarray) -> np.ndarray:
    """Turn scores into probabilities, shifted so nothing overflows."""
    shifted = scores - np.max(scores)
    exp = np.exp(shifted)
    return exp / np.sum(exp)


def save(
    directory,
    spec: Spec,
    classes: list[str],
    weights: np.ndarray,
    bias: np.ndarray,
    temperature: float,
    slot_power: float,
    table_size: int,
    unsure_below: float,
    extra: dict | None = None,
) -> Path:
    """Write a bundle. The CRFsuite file is written separately by the trainer."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        directory / INTENT_FILE,
        weights=np.asarray(weights, dtype=np.float32),
        bias=np.asarray(bias, dtype=np.float32),
        temperature=np.asarray([temperature], dtype=np.float32),
        slot_power=np.asarray([slot_power], dtype=np.float32),
        classes=np.array(classes, dtype=object),
        table_size=np.asarray([table_size], dtype=np.int32),
    )

    meta = {
        "format_version": FORMAT_VERSION,
        "spec_source": spec.source,
        "spec_digest": spec_digest(spec.source) if spec.source else "",
        "features": {
            "hash": "fnv1a-32",
            "table_size": table_size,
            "intent": ["word unigrams", "word bigrams", "char 3-grams over bytes"],
            "intent_normalisation": "l2",
            "crf_offsets": list(F.OFFSETS),
            "gazetteer_dropout": F.GAZETTEER_DROPOUT,
        },
        "intent": {"classes": list(classes), "temperature": float(temperature)},
        "slots": {"sequence_probability_power": float(slot_power)},
        "confidence": "intent probability * crf sequence probability ** power",
        "unsure_below": float(unsure_below),
        "on_unsure": spec.fallback.on_unsure,
        "spec": spec_to_json(spec),
    }
    if extra:
        meta.update(extra)
    (directory / META_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return directory


def load(directory) -> Model:
    """Read a bundle back."""
    directory = Path(directory)
    meta = json.loads((directory / META_FILE).read_text(encoding="utf-8"))
    data = np.load(directory / INTENT_FILE, allow_pickle=True)
    spec = spec_from_json(meta["spec"])
    spec.source = meta.get("spec_source", "")
    return Model(
        spec=spec,
        classes=[str(c) for c in data["classes"]],
        weights=np.asarray(data["weights"], dtype=np.float64),
        bias=np.asarray(data["bias"], dtype=np.float64),
        temperature=float(data["temperature"][0]),
        slot_power=float(data["slot_power"][0]),
        table_size=int(data["table_size"][0]),
        unsure_below=float(meta["unsure_below"]),
        meta=meta,
        tagger_path=str(directory / SLOTS_FILE),
    )


def sizes(directory) -> list[tuple[str, int]]:
    """The three bundle files with their sizes in bytes, biggest first."""
    directory = Path(directory)
    out = []
    for name in (INTENT_FILE, SLOTS_FILE, META_FILE):
        path = directory / name
        if path.is_file():
            out.append((name, path.stat().st_size))
    out.sort(key=lambda pair: -pair[1])
    return out


def none_index(classes: list[str]) -> int:
    """Where the 'none' class sits, or -1 when the file turned it off."""
    return classes.index(NONE_COMMAND) if NONE_COMMAND in classes else -1
