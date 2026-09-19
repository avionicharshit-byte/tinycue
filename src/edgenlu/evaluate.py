"""Measure a bundle: intent accuracy, slot F1, full-command accuracy and calibration."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .calibrate import (
    Report,
    apply_cutoff,
    choose_cutoff,
    expected_calibration_error,
    reliability,
)
from .decode import decode, spans_from_tags
from .model import Model
from .schema import Example


@dataclass
class Answer:
    """What the model said about one sentence, next to what the sentence really was."""

    example: Example
    command: str
    slots: dict
    missing: list
    tags: list
    intent_confidence: float
    slot_confidence: float
    confidence: float
    intent_right: bool
    full_right: bool


def answer(model: Model, example: Example) -> Answer:
    """Run one sentence through the bundle."""
    tokens = list(example.tokens)
    probabilities = model.intent_probabilities(tokens)
    best = int(np.argmax(probabilities))
    command = model.classes[best]
    intent_confidence = float(probabilities[best])

    tags, slot_confidence = model.tag(tokens)
    result = decode(tokens, tags, command, model.spec)

    intent_right = command == example.command
    full_right = intent_right and result.slots == dict(example.slots)
    return Answer(
        example=example,
        command=command,
        slots=result.slots,
        missing=result.missing,
        tags=tags,
        intent_confidence=intent_confidence,
        slot_confidence=slot_confidence,
        confidence=model.confidence(intent_confidence, slot_confidence),
        intent_right=intent_right,
        full_right=full_right,
    )


def run(model: Model, examples) -> list[Answer]:
    """Run a whole set."""
    return [answer(model, e) for e in examples]


def slot_scores(answers) -> tuple[float, float, float]:
    """Span level precision, recall and F1 over the BIO tags, counted across the set."""
    hits = predicted = gold = 0
    for item in answers:
        want = set(spans_from_tags(item.example.tokens, item.example.tags))
        got = set(spans_from_tags(item.example.tokens, item.tags))
        hits += len(want & got)
        predicted += len(got)
        gold += len(want)
    precision = hits / predicted if predicted else 1.0
    recall = hits / gold if gold else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def report(model: Model, examples, cutoff: float | None = None) -> tuple[Report, list[Answer]]:
    """Every number the eval command prints, plus the per-sentence answers."""
    answers = run(model, examples)
    out = Report(count=len(answers))
    if not answers:
        return out, answers

    confidences = np.array([a.confidence for a in answers])
    correct = np.array([a.full_right for a in answers])

    out.intent_accuracy = float(np.mean([a.intent_right for a in answers]))
    out.full_accuracy = float(correct.mean())
    out.slot_precision, out.slot_recall, out.slot_f1 = slot_scores(answers)
    out.ece = expected_calibration_error(confidences, correct)
    out.buckets = reliability(confidences, correct)
    out.cutoff = (
        apply_cutoff(confidences, correct, cutoff)
        if cutoff is not None
        else choose_cutoff(confidences, correct)
    )
    return out, answers


def failures(answers, limit: int = 10) -> list[Answer]:
    """The worst mistakes: wrong answers the model was most sure about."""
    wrong = [a for a in answers if not a.full_right]
    wrong.sort(key=lambda a: -a.confidence)
    return wrong[:limit]


def why(item: Answer) -> str:
    """One line saying what went wrong with an answer."""
    gold = item.example
    if not item.intent_right:
        return f"read as {item.command}, it is {gold.command}"
    want = dict(gold.slots)
    got = dict(item.slots)
    missing = [k for k in want if k not in got]
    extra = [k for k in got if k not in want]
    changed = [k for k in want if k in got and got[k] != want[k]]
    parts = []
    if missing:
        parts.append("missed " + ", ".join(f"{k}={want[k]}" for k in missing))
    if extra:
        parts.append("invented " + ", ".join(f"{k}={got[k]}" for k in extra))
    if changed:
        parts.append(
            "wrong value " + ", ".join(f"{k}={got[k]} not {want[k]}" for k in changed)
        )
    return "; ".join(parts) if parts else "right command and slots"
