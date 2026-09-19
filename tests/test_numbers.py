"""Numbers in digits, English words and Hindi words."""

from __future__ import annotations

import pytest

from tinycue.numbers import (
    DIGITS,
    ENGLISH,
    HINDI,
    english_words,
    hindi_words,
    parse_number,
    render_number,
)


@pytest.mark.parametrize(
    "text,value",
    [
        ("0", 0),
        ("5", 5),
        ("45", 45),
        ("180", 180),
    ],
)
def test_digits(text, value):
    assert parse_number(text.split()) == value


@pytest.mark.parametrize(
    "text,value",
    [
        ("zero", 0),
        ("ten", 10),
        ("fifteen", 15),
        ("twenty", 20),
        ("twenty five", 25),
        ("forty five", 45),
        ("fourty five", 45),
        ("ninety nine", 99),
        ("one hundred", 100),
        ("one hundred eighty", 180),
        ("twenty and five", 25),
    ],
)
def test_english_words(text, value):
    assert parse_number(text.split()) == value


@pytest.mark.parametrize(
    "text,value",
    [
        ("ek", 1),
        ("do", 2),
        ("teen", 3),
        ("char", 4),
        ("chaar", 4),
        ("paanch", 5),
        ("panch", 5),
        ("chhe", 6),
        ("saat", 7),
        ("aath", 8),
        ("nau", 9),
        ("das", 10),
        ("gyarah", 11),
        ("barah", 12),
        ("pandrah", 15),
        ("bees", 20),
        ("teis", 23),
        ("pachees", 25),
        ("tees", 30),
        ("chalis", 40),
        ("chalees", 40),
        ("pachaas", 50),
        ("saath", 60),
        ("sau", 100),
    ],
)
def test_hindi_words(text, value):
    assert parse_number(text.split()) == value


def test_saat_and_saath_are_different():
    assert parse_number(["saat"]) == 7
    assert parse_number(["saath"]) == 60


def test_teis_and_tees_are_different():
    assert parse_number(["teis"]) == 23
    assert parse_number(["tees"]) == 30


def test_hindi_covers_zero_to_one_eighty():
    for value in range(0, 181):
        word = hindi_words(value)
        assert word is not None, f"no Hindi word for {value}"
        assert parse_number(word.split()) == value


@pytest.mark.parametrize(
    "text,value",
    [
        ("ikasath", 61),
        ("sattar", 70),
        ("pachhattar", 75),
        ("assi", 80),
        ("nabbe", 90),
        ("ninyanave", 99),
        ("ek sau", 100),
        ("ek sau bees", 120),
        ("sau bees", 120),
        ("ek sau assi", 180),
    ],
)
def test_composed_hindi_numbers(text, value):
    assert parse_number(text.split()) == value


def test_hindi_spellings_never_collide():
    from tinycue.numbers import ENGLISH_NUMBER_WORDS, HINDI_SPELLINGS

    seen = {}
    for value, words in HINDI_SPELLINGS.items():
        for word in words:
            assert word not in seen, f"{word} means both {seen.get(word)} and {value}"
            assert word not in ENGLISH_NUMBER_WORDS, word
            seen[word] = value


def test_english_round_trip():
    for value in range(0, 181):
        assert parse_number(english_words(value).split()) == value


@pytest.mark.parametrize("text", ["", "light", "kitchen ka", "ten light", "hello"])
def test_not_a_number(text):
    assert parse_number(text.split()) is None


def test_render_styles():
    assert render_number(25, DIGITS) == "25"
    assert render_number(25, ENGLISH) == "twenty five"
    assert render_number(25, HINDI) == "pachees"
    assert render_number(175, HINDI) == "ek sau pachhattar"
    assert render_number(200, HINDI) is None


def test_unknown_style():
    with pytest.raises(ValueError):
        render_number(5, "klingon")
