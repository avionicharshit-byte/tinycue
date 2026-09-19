"""The unsure gate: how much to trust one answer.

The old confidence was the intent probability times the tagger's own probability. Both
come from the same hashed n-gram features, and a word the model has never seen adds
nothing to those features, so the known words decide alone and the answer stays confident.
That is exactly the case the gate has to catch.

So the gate looks at a few more pieces of evidence, all of them a handful of microseconds
on a microcontroller:

  - how many words of the sentence appear in no training sentence,
  - whether every word outside a slot span is one of those,
  - how far the top command is ahead of the second one,
  - whether a required slot is missing, whether a slot value is one nobody listed, and
    whether the answer is "no command" at all.

A logistic regression over those, fitted on a dev set and a stressed copy of it, predicts
the chance the whole answer is right. That number becomes the confidence. The two
probabilities are still reported separately, because they say different things.

Every feature is defined here once, and `runtime/edgenlu.c` computes the same numbers in
the same order. `docs/model-format.md` writes the format down.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .features import fnv1a
from .numbers import parse_number
from .schema import OUTSIDE

# Feature slots, fixed for ever: the blob stores an id per weight, so a model may use any
# subset in any order and an older runtime still reads it.
INTENT_LOGIT = 0
SLOT_LOGPROB = 1
UNKNOWN_SHARE = 2
ALL_CARRIER_UNKNOWN = 3
MARGIN = 4
MISSING_REQUIRED = 5
OPEN_VALUE = 6
PREDICTED_NONE = 7
FEATURE_COUNT = 8

FEATURE_NAMES = (
    "intent_logit",
    "slot_logprob",
    "unknown_share",
    "all_carrier_unknown",
    "margin",
    "missing_required",
    "open_value",
    "predicted_none",
)

# The two log features are divided by this so every feature sits in roughly the same
# range and one L2 penalty is fair to all of them. It is part of the format.
LOG_SCALE = 5.0
# Probabilities are pinned away from 0 and 1 before a logarithm.
PROB_FLOOR = 1e-6
SLOT_FLOOR = 1e-9

# The five the calibrator uses by default: the two probabilities restated, the unknown
# word evidence, and the margin.
CORE_FEATURES = (INTENT_LOGIT, SLOT_LOGPROB, UNKNOWN_SHARE, ALL_CARRIER_UNKNOWN, MARGIN)
# Every signal, for a model that wants the decoder's own doubts as well.
WIDE_FEATURES = CORE_FEATURES + (MISSING_REQUIRED, OPEN_VALUE, PREDICTED_NONE)


def hash_words(words) -> list[int]:
    """The training vocabulary as sorted, deduplicated FNV-1a 32 hashes."""
    return sorted({fnv1a(str(word)) for word in words})


def known_flags(tokens, vocabulary: set[int], gaz_words: set[str]) -> list[bool]:
    """Which tokens the model has evidence for. Numbers and slot words always count."""
    out = []
    for token in tokens:
        if fnv1a(token) in vocabulary:
            out.append(True)
        elif parse_number([token]) is not None:
            out.append(True)
        else:
            out.append(token in gaz_words)
    return out


def evidence(
    tokens,
    tags,
    known,
    probabilities,
    slot_probability: float,
    missing,
    open_value: bool,
    is_none: bool,
) -> dict:
    """Every raw signal for one answer, before it is turned into a feature vector."""
    count = len(tokens)
    unknown = sum(1 for flag in known if not flag)
    carriers = [i for i in range(count) if i < len(tags) and tags[i] == OUTSIDE]
    carrier_unknown = sum(1 for i in carriers if not known[i])

    ordered = sorted((float(p) for p in probabilities), reverse=True)
    top = ordered[0] if ordered else 0.0
    margin = top - ordered[1] if len(ordered) > 1 else 1.0

    return {
        "token_count": count,
        "unknown_count": unknown,
        "unknown_share": unknown / count if count else 0.0,
        "carrier_count": len(carriers),
        "carrier_unknown": carrier_unknown,
        "any_carrier_unknown": bool(carrier_unknown),
        "all_carrier_unknown": bool(carriers) and carrier_unknown == len(carriers),
        "intent_probability": top,
        "margin": float(margin),
        "slot_probability": float(slot_probability),
        "missing_required": bool(missing),
        "open_value": bool(open_value),
        "predicted_none": bool(is_none),
    }


def feature_vector(signals: dict) -> list[float]:
    """The eight features, in their fixed order, from the raw signals."""
    intent = min(max(float(signals["intent_probability"]), PROB_FLOOR), 1.0 - PROB_FLOOR)
    slot = min(max(float(signals["slot_probability"]), SLOT_FLOOR), 1.0)
    out = [0.0] * FEATURE_COUNT
    out[INTENT_LOGIT] = float(np.log(intent / (1.0 - intent)) / LOG_SCALE)
    out[SLOT_LOGPROB] = float(np.log(slot) / LOG_SCALE)
    out[UNKNOWN_SHARE] = float(signals["unknown_share"])
    out[ALL_CARRIER_UNKNOWN] = 1.0 if signals["all_carrier_unknown"] else 0.0
    out[MARGIN] = float(signals["margin"])
    out[MISSING_REQUIRED] = 1.0 if signals["missing_required"] else 0.0
    out[OPEN_VALUE] = 1.0 if signals["open_value"] else 0.0
    out[PREDICTED_NONE] = 1.0 if signals["predicted_none"] else 0.0
    return out


def sigmoid(z: float) -> float:
    """The logistic function, written so a large negative z cannot overflow."""
    if z >= 0.0:
        return float(1.0 / (1.0 + np.exp(-z)))
    value = float(np.exp(z))
    return value / (1.0 + value)


@dataclass
class Calibrator:
    """A handful of weights that turn the signals into a chance of being right."""

    feature_ids: list[int] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0

    def score_vector(self, vector) -> float:
        total = float(self.bias)
        for identifier, weight in zip(self.feature_ids, self.weights):
            total += float(weight) * float(vector[identifier])
        return sigmoid(total)

    def score(self, signals: dict) -> float:
        return self.score_vector(feature_vector(signals))

    def as_json(self) -> dict:
        return {
            "feature_ids": [int(i) for i in self.feature_ids],
            "feature_names": [FEATURE_NAMES[i] for i in self.feature_ids],
            "weights": [float(w) for w in self.weights],
            "bias": float(self.bias),
        }


def from_json(raw) -> Calibrator | None:
    """Read a calibrator back out of meta.json, or None when the model has none."""
    if not raw or not raw.get("feature_ids"):
        return None
    return Calibrator(
        feature_ids=[int(i) for i in raw["feature_ids"]],
        weights=[float(w) for w in raw["weights"]],
        bias=float(raw["bias"]),
    )


def _as_float32(value: float) -> float:
    """Round a weight to the float32 the blob carries, so C and Python agree exactly."""
    return float(np.float32(value))


def fit(vectors, correct, feature_ids=CORE_FEATURES, strength: float = 1.0) -> Calibrator | None:
    """Fit the logistic regression. None when the answers are all right or all wrong."""
    from sklearn.linear_model import LogisticRegression

    labels = np.asarray(list(correct), dtype=int)
    if len(labels) < 8 or labels.min() == labels.max():
        return None
    ids = list(feature_ids)
    matrix = np.asarray([[float(v[i]) for i in ids] for v in vectors], dtype=np.float64)

    classifier = LogisticRegression(C=strength, solver="lbfgs", max_iter=1000)
    classifier.fit(matrix, labels)
    weights = [_as_float32(w) for w in np.atleast_2d(classifier.coef_)[0]]
    bias = _as_float32(float(np.atleast_1d(classifier.intercept_)[0]))
    return Calibrator(feature_ids=ids, weights=weights, bias=bias)


def out_of_fold(
    vectors, correct, groups, feature_ids=CORE_FEATURES, folds: int = 5, strength: float = 1.0
) -> list[float]:
    """Scores from calibrators that never saw the row they are scoring.

    Rows are kept together by group, so a stressed copy of a sentence can never sit in a
    different fold from the sentence it came from. Without that the cut-off is fitted on
    its own training data wearing a hat.
    """
    labels = np.asarray(list(correct), dtype=int)
    names = list(groups)
    unique = sorted(set(names))
    if len(unique) < folds or labels.min() == labels.max():
        return [float(labels.mean())] * len(labels)

    assignment = {name: index % folds for index, name in enumerate(unique)}
    out = [0.0] * len(labels)
    for fold in range(folds):
        train_rows = [i for i, name in enumerate(names) if assignment[name] != fold]
        test_rows = [i for i, name in enumerate(names) if assignment[name] == fold]
        if not test_rows:
            continue
        model = fit(
            [vectors[i] for i in train_rows],
            labels[train_rows],
            feature_ids=feature_ids,
            strength=strength,
        )
        if model is None:
            share = float(labels[train_rows].mean()) if train_rows else float(labels.mean())
            for i in test_rows:
                out[i] = share
            continue
        for i in test_rows:
            out[i] = model.score_vector(vectors[i])
    return out
