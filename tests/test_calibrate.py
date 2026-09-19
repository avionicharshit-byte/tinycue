"""Calibration maths on toy cases where the right answer can be worked out by hand."""

from __future__ import annotations

import numpy as np
import pytest

from tinycue.calibrate import (
    apply_cutoff,
    choose_cutoff,
    expected_calibration_error,
    fit_slot_power,
    fit_temperature,
    log_loss,
    negative_log_likelihood,
    reliability,
)


def test_a_perfectly_honest_set_has_no_calibration_error():
    """Eight sentences at 0.75 confidence, six of them right. 0.75 is exactly honest."""
    confidences = [0.75] * 8
    correct = [True] * 6 + [False] * 2
    assert expected_calibration_error(confidences, correct) == pytest.approx(0.0)


def test_an_overconfident_set_shows_the_gap():
    confidences = [0.9] * 10
    correct = [True] * 5 + [False] * 5
    assert expected_calibration_error(confidences, correct) == pytest.approx(0.4)


def test_ece_weights_buckets_by_size():
    # 90 answers at 0.95 that are all right, 10 at 0.15 that are all right too.
    confidences = [0.95] * 90 + [0.15] * 10
    correct = [True] * 100
    expected = 0.9 * abs(1.0 - 0.95) + 0.1 * abs(1.0 - 0.15)
    assert expected_calibration_error(confidences, correct) == pytest.approx(expected)


def test_reliability_bands():
    rows = reliability([0.05, 0.15, 0.95, 0.99], [False, True, True, True])
    assert [r.count for r in rows] == [1, 1, 0, 0, 0, 0, 0, 0, 0, 2]
    assert rows[0].accuracy == 0.0
    assert rows[9].mean_confidence == pytest.approx(0.97)
    assert rows[9].accuracy == 1.0


def test_reliability_puts_one_point_oh_in_the_last_band():
    rows = reliability([1.0], [True])
    assert rows[9].count == 1


def test_temperature_cools_an_overconfident_model():
    """Scores that are much too spread out for a model that is only right half the time."""
    rng = np.random.default_rng(0)
    scores = []
    labels = []
    for index in range(400):
        row = np.zeros(2)
        row[index % 2] = 8.0
        scores.append(row)
        # Right only about half the time, so the 8.0 gap is far too confident.
        labels.append(index % 2 if rng.random() < 0.5 else 1 - index % 2)
    temperature = fit_temperature(np.array(scores), np.array(labels))
    assert temperature > 2.0


def test_temperature_leaves_an_honest_model_alone():
    scores = np.array([[2.0, 0.0], [0.0, 2.0]] * 100)
    labels = np.array([0, 1] * 100)
    temperature = fit_temperature(scores, labels)
    assert 0.5 <= temperature <= 2.0


def test_negative_log_likelihood_of_a_certain_correct_model_is_near_zero():
    scores = np.array([[20.0, 0.0]])
    assert negative_log_likelihood(scores, np.array([0]), 1.0) < 1e-6


def test_log_loss_of_a_perfect_confidence_is_near_zero():
    assert log_loss([1.0, 0.0], [True, False]) < 1e-6


def test_slot_power_pulls_an_underconfident_tagger_up():
    """The tagger says 0.5 every time but is right 99 times in 100."""
    intent = [1.0] * 100
    slot = [0.5] * 100
    correct = [True] * 99 + [False]
    power = fit_slot_power(intent, slot, correct)
    # The floor is 0.25, which is as far as the fit can pull it.
    assert power == pytest.approx(0.25)
    assert 1.0 * 0.5**power > 0.8


def test_slot_power_is_one_when_nothing_can_be_learned():
    assert fit_slot_power([1.0] * 5, [0.9] * 5, [True] * 5) == 1.0
    assert fit_slot_power([], [], []) == 1.0


def test_choose_cutoff_finds_the_lowest_one_that_works():
    # Everything at 0.8 and up is right, everything below is wrong.
    confidences = [0.2, 0.4, 0.6, 0.8, 0.9, 1.0]
    correct = [False, False, False, True, True, True]
    cut = choose_cutoff(confidences, correct, target=0.99)
    assert cut.value == pytest.approx(0.8)
    assert cut.accepted == 3
    assert cut.unsure_rate == pytest.approx(0.5)
    assert cut.accepted_accuracy == pytest.approx(1.0)
    assert cut.wrong_caught == pytest.approx(1.0)
    assert cut.reached_target


def test_choose_cutoff_accepts_everything_when_everything_is_right():
    cut = choose_cutoff([0.3, 0.5, 0.9], [True, True, True])
    assert cut.value == pytest.approx(0.0)
    assert cut.unsure_rate == pytest.approx(0.0)
    assert cut.wrong_caught == pytest.approx(1.0)


def test_choose_cutoff_gives_up_when_no_cutoff_works():
    """The most confident answer is wrong, so nothing can be accepted at 99%."""
    cut = choose_cutoff([0.9, 0.5, 0.1], [False, True, True])
    assert not cut.reached_target
    assert cut.unsure_rate == pytest.approx(1.0)
    assert cut.accepted == 0


def test_apply_cutoff_measures_a_pinned_value():
    confidences = [0.2, 0.4, 0.6, 0.8]
    correct = [False, True, False, True]
    cut = apply_cutoff(confidences, correct, 0.5)
    assert cut.accepted == 2
    assert cut.accepted_accuracy == pytest.approx(0.5)
    assert cut.wrong_caught == pytest.approx(0.5)
    assert cut.unsure_rate == pytest.approx(0.5)


def test_empty_sets_do_not_blow_up():
    assert expected_calibration_error([], []) == 0.0
    assert choose_cutoff([], []).total == 0
    assert apply_cutoff([], [], 0.5).total == 0
