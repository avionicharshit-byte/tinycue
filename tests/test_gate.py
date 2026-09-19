"""The unsure gate: the evidence it gathers, the features it builds and the fit."""

from __future__ import annotations

import pytest

from tinycue import gate
from tinycue.features import fnv1a


def test_the_vocabulary_is_sorted_and_deduplicated():
    hashes = gate.hash_words(["one", "two", "one", "three"])
    assert hashes == sorted(hashes)
    assert len(hashes) == 3
    assert fnv1a("two") in hashes


def test_a_word_the_model_trained_on_is_known():
    vocabulary = set(gate.hash_words(["switch"]))
    assert gate.known_flags(["switch"], vocabulary, set()) == [True]


def test_a_number_counts_as_known_even_when_nobody_wrote_it():
    assert gate.known_flags(["45"], set(), set()) == [True]
    assert gate.known_flags(["seventeen"], set(), set()) == [True]
    assert gate.known_flags(["saatis"], set(), set()) == [False]


def test_a_word_from_a_slot_value_list_counts_as_known():
    assert gate.known_flags(["upstairs"], set(), {"upstairs"}) == [True]


def test_a_word_from_nowhere_is_unknown():
    assert gate.known_flags(["quorbek"], set(), {"upstairs"}) == [False]


def _evidence(tokens, tags, known, probabilities=(0.8, 0.15, 0.05), slot=0.9, **extra):
    return gate.evidence(
        tokens,
        tags,
        known,
        probabilities,
        slot,
        extra.get("missing", []),
        extra.get("open_value", False),
        extra.get("is_none", False),
    )


def test_the_unknown_share_counts_every_word():
    signals = _evidence(["a", "b", "c", "d"], ["O"] * 4, [True, False, False, True])
    assert signals["unknown_count"] == 2
    assert signals["unknown_share"] == pytest.approx(0.5)


def test_only_words_outside_a_slot_span_count_as_carriers():
    tags = ["O", "B-room", "O"]
    signals = _evidence(["zap", "attic", "light"], tags, [False, False, True])
    assert signals["carrier_count"] == 2
    assert signals["carrier_unknown"] == 1
    assert signals["any_carrier_unknown"] is True
    assert signals["all_carrier_unknown"] is False


def test_all_carrier_words_unknown_fires_when_every_carrier_is_new():
    tags = ["O", "B-room", "O"]
    signals = _evidence(["zap", "attic", "quorbek"], tags, [False, True, False])
    assert signals["all_carrier_unknown"] is True


def test_a_sentence_that_is_all_slot_never_fires_the_carrier_flag():
    signals = _evidence(["attic"], ["B-room"], [False])
    assert signals["carrier_count"] == 0
    assert signals["all_carrier_unknown"] is False


def test_the_margin_is_the_top_two_probabilities_apart():
    signals = _evidence(["a"], ["O"], [True], probabilities=(0.6, 0.3, 0.1))
    assert signals["margin"] == pytest.approx(0.3)


def test_one_class_leaves_nothing_to_compare():
    signals = _evidence(["a"], ["O"], [True], probabilities=(1.0,))
    assert signals["margin"] == pytest.approx(1.0)


def test_the_feature_vector_is_in_the_order_the_format_fixes():
    signals = _evidence(
        ["a", "b"],
        ["O", "B-room"],
        [False, True],
        probabilities=(0.8, 0.2),
        slot=0.5,
        missing=["room"],
        open_value=True,
        is_none=True,
    )
    vector = gate.feature_vector(signals)
    assert len(vector) == gate.FEATURE_COUNT
    assert vector[gate.UNKNOWN_SHARE] == pytest.approx(0.5)
    assert vector[gate.ALL_CARRIER_UNKNOWN] == 1.0
    assert vector[gate.MARGIN] == pytest.approx(0.6)
    assert vector[gate.MISSING_REQUIRED] == 1.0
    assert vector[gate.OPEN_VALUE] == 1.0
    assert vector[gate.PREDICTED_NONE] == 1.0
    # A probability of 0.8 is a logit of log(4), divided by the scale the format fixes.
    import math

    assert vector[gate.INTENT_LOGIT] == pytest.approx(math.log(4.0) / gate.LOG_SCALE)
    assert vector[gate.SLOT_LOGPROB] == pytest.approx(math.log(0.5) / gate.LOG_SCALE)


def test_a_probability_of_one_does_not_blow_the_logit_up():
    signals = _evidence(["a"], ["O"], [True], probabilities=(1.0, 0.0), slot=0.0)
    vector = gate.feature_vector(signals)
    assert vector[gate.INTENT_LOGIT] < 5.0
    assert vector[gate.SLOT_LOGPROB] > -10.0


def test_the_sigmoid_is_symmetric_and_does_not_overflow():
    assert gate.sigmoid(0.0) == pytest.approx(0.5)
    assert gate.sigmoid(-800.0) == pytest.approx(0.0)
    assert gate.sigmoid(800.0) == pytest.approx(1.0)


def test_a_calibrator_scores_what_its_weights_say():
    model = gate.Calibrator(feature_ids=[gate.UNKNOWN_SHARE], weights=[-4.0], bias=2.0)
    vector = [0.0] * gate.FEATURE_COUNT
    vector[gate.UNKNOWN_SHARE] = 0.5
    assert model.score_vector(vector) == pytest.approx(gate.sigmoid(0.0))


def test_a_calibrator_survives_a_round_trip_through_json():
    model = gate.Calibrator(feature_ids=[0, 2], weights=[1.5, -2.5], bias=0.25)
    back = gate.from_json(model.as_json())
    assert back.feature_ids == [0, 2]
    assert back.weights == [1.5, -2.5]
    assert back.bias == pytest.approx(0.25)
    assert gate.from_json(None) is None
    assert gate.from_json({"feature_ids": []}) is None


def _separable(n: int = 60):
    """Rows where a high unknown share always means a wrong answer."""
    vectors = []
    correct = []
    groups = []
    for i in range(n):
        share = 0.0 if i % 2 == 0 else 0.9
        vector = [0.0] * gate.FEATURE_COUNT
        vector[gate.INTENT_LOGIT] = 0.4
        vector[gate.SLOT_LOGPROB] = -0.1
        vector[gate.UNKNOWN_SHARE] = share
        vectors.append(vector)
        correct.append(share == 0.0)
        groups.append(f"s{i}")
    return vectors, correct, groups


def test_the_fit_learns_that_unknown_words_mean_trouble():
    vectors, correct, _ = _separable()
    model = gate.fit(vectors, correct, feature_ids=[gate.UNKNOWN_SHARE])
    assert model is not None
    assert model.weights[0] < 0.0
    clean = [0.0] * gate.FEATURE_COUNT
    dirty = [0.0] * gate.FEATURE_COUNT
    dirty[gate.UNKNOWN_SHARE] = 0.9
    assert model.score_vector(clean) > model.score_vector(dirty)


def test_the_weights_are_already_rounded_to_what_the_blob_carries():
    import numpy as np

    vectors, correct, _ = _separable()
    model = gate.fit(vectors, correct, feature_ids=[gate.UNKNOWN_SHARE])
    for weight in model.weights + [model.bias]:
        assert float(np.float32(weight)) == weight


def test_a_set_with_one_answer_in_it_gets_no_calibrator():
    vectors, _, _ = _separable()
    assert gate.fit(vectors, [True] * len(vectors)) is None
    assert gate.fit(vectors[:4], [True, False, True, False]) is None


def test_out_of_fold_scores_never_come_from_a_fit_that_saw_the_row():
    vectors, correct, groups = _separable()
    scores = gate.out_of_fold(vectors, correct, groups, feature_ids=[gate.UNKNOWN_SHARE])
    assert len(scores) == len(vectors)
    clean = [s for s, ok in zip(scores, correct) if ok]
    dirty = [s for s, ok in zip(scores, correct) if not ok]
    assert min(clean) > max(dirty)


def test_copies_of_one_sentence_stay_in_the_same_fold():
    """Two rows sharing a group must be scored by the same fold's calibrator."""
    vectors, correct, _ = _separable()
    groups = [f"s{i // 2}" for i in range(len(vectors))]
    scores = gate.out_of_fold(vectors, correct, groups, feature_ids=[gate.UNKNOWN_SHARE])
    assert len(scores) == len(vectors)
    assert all(0.0 <= s <= 1.0 for s in scores)
