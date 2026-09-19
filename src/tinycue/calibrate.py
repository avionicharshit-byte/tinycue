"""Make the confidence number honest, then pick the cut-off below which we say "unsure".

Confidence is the intent probability multiplied by the tagger's own probability for the
tag sequence it chose. Both parts are kept separately as well, because on a sentence with
no slots the tagger part is near 1 and tells you nothing.

Temperature scaling divides the intent scores by one number before the softmax. One number
fitted on held-out data is enough to stop a linear model reading 0.99 for everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import softmax

BUCKETS = 10

# How often an accepted answer has to be right. Below this the answer goes to the
# fallback instead. 0.99 was the old bar and it was fitted on generated data that the
# model found easy; on wording nobody generated, the same bar sends almost everything to
# the fallback and the tool stops being useful. 0.97 is the default and it is a flag.
TARGET_ACCURACY = 0.97

# The cut-offs the trade-off table walks, so the choice can be seen rather than trusted.
TABLE_STEPS = tuple(round(0.05 * i, 2) for i in range(20))


def negative_log_likelihood(scores: np.ndarray, labels: np.ndarray, temperature: float) -> float:
    """How surprised the model is by the true answers at this temperature. Lower is better."""
    if temperature <= 0.0:
        return float("inf")
    scaled = scores / temperature
    shifted = scaled - scaled.max(axis=1, keepdims=True)
    log_sum = np.log(np.exp(shifted).sum(axis=1))
    chosen = shifted[np.arange(len(labels)), labels]
    return float(np.mean(log_sum - chosen))


def fit_temperature(
    scores: np.ndarray, labels: np.ndarray, low: float = 0.5, high: float = 20.0
) -> float:
    """The temperature that fits the held-out answers best, found by a coarse then fine sweep."""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=int)
    if len(labels) == 0:
        return 1.0

    best = 1.0
    best_loss = negative_log_likelihood(scores, labels, 1.0)
    grid = np.geomspace(low, high, 60)
    for temperature in grid:
        loss = negative_log_likelihood(scores, labels, float(temperature))
        if loss < best_loss:
            best_loss = loss
            best = float(temperature)

    step = best * 0.25
    for _ in range(20):
        improved = False
        for candidate in (best - step, best + step):
            if candidate < low or candidate > high:
                continue
            loss = negative_log_likelihood(scores, labels, candidate)
            if loss < best_loss:
                best_loss = loss
                best = candidate
                improved = True
        if not improved:
            step *= 0.5
    return float(best)


@dataclass
class Bucket:
    """One row of the reliability table."""

    low: float
    high: float
    count: int
    mean_confidence: float
    accuracy: float


@dataclass
class Cutoff:
    """The chosen cut-off and what it does to the answers."""

    value: float
    unsure_rate: float = 0.0
    accepted_accuracy: float = 0.0
    wrong_caught: float = 0.0
    accepted: int = 0
    total: int = 0
    reached_target: bool = True


@dataclass
class Report:
    """Everything the eval command prints."""

    intent_accuracy: float = 0.0
    slot_precision: float = 0.0
    slot_recall: float = 0.0
    slot_f1: float = 0.0
    full_accuracy: float = 0.0
    ece: float = 0.0
    cutoff: Cutoff = field(default_factory=lambda: Cutoff(value=0.0))
    buckets: list[Bucket] = field(default_factory=list)
    count: int = 0
    # False when the set only carries answers, so the slot numbers are value level.
    spans_labelled: bool = True


def reliability(confidences, correct, buckets: int = BUCKETS) -> list[Bucket]:
    """Split the answers into confidence bands and see how often each band was right."""
    confidences = np.asarray(confidences, dtype=np.float64)
    correct = np.asarray(correct, dtype=bool)
    rows: list[Bucket] = []
    edges = np.linspace(0.0, 1.0, buckets + 1)
    for index in range(buckets):
        low, high = float(edges[index]), float(edges[index + 1])
        if index == buckets - 1:
            picked = (confidences >= low) & (confidences <= high)
        else:
            picked = (confidences >= low) & (confidences < high)
        count = int(picked.sum())
        rows.append(
            Bucket(
                low=low,
                high=high,
                count=count,
                mean_confidence=float(confidences[picked].mean()) if count else 0.0,
                accuracy=float(correct[picked].mean()) if count else 0.0,
            )
        )
    return rows


def expected_calibration_error(confidences, correct, buckets: int = BUCKETS) -> float:
    """How far the confidence numbers sit from the truth, averaged over the answers."""
    rows = reliability(confidences, correct, buckets)
    total = sum(r.count for r in rows)
    if total == 0:
        return 0.0
    return float(
        sum(r.count / total * abs(r.accuracy - r.mean_confidence) for r in rows)
    )


def choose_cutoff(confidences, correct, target: float = TARGET_ACCURACY) -> Cutoff:
    """The lowest cut-off whose accepted answers are right at least `target` of the time."""
    confidences = np.asarray(confidences, dtype=np.float64)
    correct = np.asarray(correct, dtype=bool)
    total = len(confidences)
    if total == 0:
        return Cutoff(value=0.0, total=0)

    candidates = sorted({0.0, *(float(c) for c in confidences)})
    chosen = None
    for candidate in candidates:
        accepted = confidences >= candidate
        if not accepted.any():
            continue
        if float(correct[accepted].mean()) >= target:
            chosen = candidate
            break

    reached = chosen is not None
    if chosen is None:
        # Nothing we can accept hits the target, so send everything to the fallback.
        chosen = float(confidences.max()) + 1e-9

    return _measure(confidences, correct, chosen, reached)


def _measure(confidences: np.ndarray, correct: np.ndarray, value: float, reached: bool) -> Cutoff:
    total = len(confidences)
    accepted = confidences >= value
    n_accepted = int(accepted.sum())
    wrong = ~correct
    n_wrong = int(wrong.sum())
    caught = int((wrong & ~accepted).sum())
    return Cutoff(
        value=float(value),
        unsure_rate=float((total - n_accepted) / total) if total else 0.0,
        accepted_accuracy=float(correct[accepted].mean()) if n_accepted else 0.0,
        wrong_caught=float(caught / n_wrong) if n_wrong else 1.0,
        accepted=n_accepted,
        total=total,
        reached_target=reached,
    )


def cutoff_table(confidences, correct, values=None) -> list[Cutoff]:
    """What every cut-off on a grid would do, so the trade-off can be read off a table."""
    confidences = np.asarray(confidences, dtype=np.float64)
    correct = np.asarray(correct, dtype=bool)
    if len(confidences) == 0:
        return []
    wanted = sorted({float(v) for v in (values if values is not None else TABLE_STEPS)})
    return [_measure(confidences, correct, value, True) for value in wanted]


def apply_cutoff(confidences, correct, value: float) -> Cutoff:
    """What a cut-off someone pinned by hand does to a set of answers."""
    confidences = np.asarray(confidences, dtype=np.float64)
    correct = np.asarray(correct, dtype=bool)
    if len(confidences) == 0:
        return Cutoff(value=float(value), total=0)
    return _measure(confidences, correct, float(value), True)


def calibrated_probabilities(scores: np.ndarray, temperature: float) -> np.ndarray:
    """Softmax after the temperature, one row per sentence."""
    return np.vstack([softmax(row / temperature) for row in np.atleast_2d(scores)])


def log_loss(probabilities, correct) -> float:
    """How surprised a confidence number is by whether the answer was actually right."""
    p = np.clip(np.asarray(probabilities, dtype=np.float64), 1e-9, 1.0 - 1e-9)
    y = np.asarray(correct, dtype=np.float64)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def fit_slot_power(intent_probabilities, slot_probabilities, correct) -> float:
    """How hard to lean on the tagger's own probability.

    The tagger's number for a whole tag sequence falls off with sentence length, so on a
    ten word sentence it reads far lower than the answer deserves. One exponent, fitted on
    dev against whether the whole command came out right, pulls it back into line. An
    exponent of 1 leaves it exactly as CRFsuite reported it.
    """
    intent = np.asarray(intent_probabilities, dtype=np.float64)
    slot = np.clip(np.asarray(slot_probabilities, dtype=np.float64), 1e-9, 1.0)
    correct = np.asarray(correct, dtype=bool)
    if len(correct) == 0 or correct.all() or not correct.any():
        return 1.0

    best = 1.0
    best_loss = log_loss(intent * slot, correct)
    # Never below 0.25: the tagger always gets a say in the final number.
    for power in np.linspace(0.25, 3.0, 112):
        loss = log_loss(intent * slot ** float(power), correct)
        if loss < best_loss:
            best_loss = loss
            best = float(power)
    return best
