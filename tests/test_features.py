"""The feature definition. These numbers are a contract: the C runtime must match them."""

from __future__ import annotations

import random

import pytest

from tinycue import features as F
from tinycue.parser import tokenize


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", 2166136261),
        ("a", 3826002220),
        ("light", 3801947695),
        ("u:bedroom", 2027141722),
        ("b:turn\x1fon", 3403565042),
        ("c:^tu", 2195813029),
        ("0:w=fan", 326173712),
    ],
)
def test_fnv1a_is_stable(text, expected):
    """Fixed values. If one of these moves, every trained bundle is invalid."""
    assert F.fnv1a(text) == expected


def test_fnv1a_matches_the_reference_loop():
    """The same loop written out, so the C port has a second copy to check against."""
    for text in ["", "a", "turn on the light", "rasoi mein light jala do"]:
        h = 2166136261
        for byte in text.encode("utf-8"):
            h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
        assert F.fnv1a(text) == h


def test_bucket_is_inside_the_table():
    for size in (1 << 8, 1 << 14):
        for text in ["light", "fan", "u:bedroom", "0:pad"]:
            assert 0 <= F.bucket(text, size) < size


def test_intent_features_in_order():
    features = F.intent_features(["turn", "on"])
    assert features[0] == b"u:turn"
    assert features[1] == b"u:on"
    assert features[2] == b"b:turn\x1fon"
    assert features[3] == b"c:^tu"
    assert features[-1] == b"c:on$"
    # unigrams + bigrams + one 3-byte window per byte of "^turn on$" beyond the first two
    assert len(features) == 2 + 1 + (len("^turn on$") - 2)


def test_intent_features_on_one_token():
    assert F.intent_features(["hi"]) == [b"u:hi", b"c:^hi", b"c:hi$"]


def test_intent_features_on_nothing():
    """"^$" is only two bytes, so there is no 3-byte window and no feature at all."""
    assert F.intent_features([]) == []


def test_intent_vector_is_l2_normalised():
    vector = F.intent_vector(tokenize("turn on the bedroom light"))
    assert vector.shape == (F.DEFAULT_BUCKETS,)
    assert abs(float((vector * vector).sum()) - 1.0) < 1e-9


def test_intent_vector_of_an_empty_sentence_is_all_zeros():
    vector = F.intent_vector([])
    assert float(vector.sum()) == 0.0


def test_intent_matrix_rows_match_intent_vector():
    sentences = [tokenize("fan speed up"), tokenize("what is the time")]
    matrix = F.intent_matrix(sentences)
    for row, tokens in enumerate(sentences):
        dense = matrix.getrow(row).toarray()[0]
        assert dense == pytest.approx(F.intent_vector(tokens))


@pytest.mark.parametrize(
    "word,expected",
    [
        ("bedroom", "a"),
        ("45", "d"),
        ("a1b", "ada"),
        ("bed-room", "axa"),
        ("", ""),
        ("A1", "ad"),
        ("...", "x"),
    ],
)
def test_shape(word, expected):
    assert F.shape(word) == expected


@pytest.mark.parametrize("word", ["ten", "das", "10", "twenty", "nabbe"])
def test_is_number_word(word):
    assert F.is_number_word(word)


@pytest.mark.parametrize("word", ["light", "", "fanny", "kitchen"])
def test_is_not_a_number_word(word):
    assert not F.is_number_word(word)


def test_gazetteer_holds_every_word_of_every_surface(example_spec):
    gaz = F.gazetteer(example_spec)
    assert "minutes" not in gaz, "a number slot has no word list"
    assert "bedroom" in gaz["room"]
    assert "sone" in gaz["room"] and "kamra" in gaz["room"]
    assert "chalu" in gaz["state"]


def test_token_features_have_every_offset(example_spec):
    gaz = F.gazetteer(example_spec)
    tokens = tokenize("turn on the bedroom light")
    features = F.token_features(tokens, 3, gaz)
    assert "bias" in features
    assert "BOS" not in features and "EOS" not in features
    assert "0:w=bedroom" in features
    assert "0:p3=bed" in features
    assert "0:s3=oom" in features
    assert "0:sh=a" in features
    assert "0:g=room" in features
    assert "-1:w=the" in features
    assert "2:pad" in features
    assert "1:w=light" in features


def test_token_features_mark_the_ends(example_spec):
    gaz = F.gazetteer(example_spec)
    tokens = tokenize("fan speed up")
    assert "BOS" in F.token_features(tokens, 0, gaz)
    assert "-1:pad" in F.token_features(tokens, 0, gaz)
    assert "EOS" in F.token_features(tokens, 2, gaz)


def test_short_words_skip_the_longer_prefixes(example_spec):
    gaz = F.gazetteer(example_spec)
    features = F.token_features(["on"], 0, gaz)
    assert "0:p2=on" in features
    assert not any(f.startswith("0:p3=") for f in features)


def test_number_flag_fires(example_spec):
    gaz = F.gazetteer(example_spec)
    features = F.token_features(tokenize("timer 45 minutes"), 1, gaz)
    assert "0:num=1" in features
    assert "0:sh=d" in features


def test_gazetteer_dropout_removes_some_flags(example_spec):
    gaz = F.gazetteer(example_spec)
    tokens = ["bedroom"] * 200
    rng = random.Random(0)
    kept = sum(
        1
        for i in range(len(tokens))
        if "0:g=room" in F.token_features(tokens, i, gaz, rng, F.GAZETTEER_DROPOUT)
    )
    assert 0 < kept < len(tokens)


def test_no_dropout_without_an_rng(example_spec):
    gaz = F.gazetteer(example_spec)
    for i in range(50):
        assert "0:g=room" in F.token_features(["bedroom"], 0, gaz)


def test_sentence_features_one_list_per_token(example_spec):
    gaz = F.gazetteer(example_spec)
    tokens = tokenize("rasoi mein light jala do")
    items = F.sentence_features(tokens, gaz)
    assert len(items) == len(tokens)
    assert all(isinstance(f, str) for item in items for f in item)
