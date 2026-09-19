"""Number words for English and Hinglish, and the reader that turns them into an int.

Everything here is a table on purpose. Numbers in Hindi are irregular, so spelling them out
is shorter and safer than trying to build them from rules.
"""

from __future__ import annotations

DIGITS = "digits"
ENGLISH = "english"
HINDI = "hindi"
STYLES = (DIGITS, ENGLISH, HINDI)

# Words that carry no value but show up between numbers.
FILLER_WORDS = {"and", "aur"}

# English, the plain spelling of each value.
ENGLISH_ONES = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}

ENGLISH_TENS = {
    2: "twenty",
    3: "thirty",
    4: "forty",
    5: "fifty",
    6: "sixty",
    7: "seventy",
    8: "eighty",
    9: "ninety",
}

ENGLISH_NUMBER_WORDS: dict[str, int] = {}
for _value, _word in ENGLISH_ONES.items():
    ENGLISH_NUMBER_WORDS[_word] = _value
for _tens, _word in ENGLISH_TENS.items():
    ENGLISH_NUMBER_WORDS[_word] = _tens * 10
ENGLISH_NUMBER_WORDS["hundred"] = 100
# Spellings people type by habit.
ENGLISH_NUMBER_WORDS["fourty"] = 40

# Hindi in Latin letters, 0 to 60 plus 100. The first spelling is the one we write out,
# the rest are variants people type.
HINDI_SPELLINGS: dict[int, list[str]] = {
    0: ["shunya", "sunya"],
    1: ["ek", "eak"],
    2: ["do", "doh"],
    3: ["teen", "theen"],
    4: ["char", "chaar"],
    5: ["paanch", "panch", "paanj"],
    6: ["chhe", "che", "chah", "chhah"],
    # "saat" is 7 and "saath" is 60, so neither one takes the other's spelling.
    7: ["saat"],
    8: ["aath", "ath"],
    9: ["nau", "nao"],
    10: ["das", "dus", "dass"],
    11: ["gyarah", "gyaarah", "gyara"],
    12: ["barah", "baarah", "bara"],
    13: ["terah", "teraah", "tera"],
    14: ["chaudah", "chaudha", "choudah"],
    15: ["pandrah", "pandra", "pandhrah"],
    16: ["solah", "sola"],
    17: ["satrah", "satra"],
    18: ["atharah", "athara", "attharah"],
    19: ["unnis", "unis", "unnees"],
    20: ["bees", "bis", "bish"],
    21: ["ikkis", "ekkis", "ikkees"],
    22: ["bais", "baees", "bayees"],
    # "teis" is 23 and "tees" is 30, so neither one takes the other's spelling.
    23: ["teis", "taees"],
    24: ["chaubis", "chaubees"],
    25: ["pachees", "pachis", "pachchis", "pacchees"],
    26: ["chhabbis", "chabbis", "chhabbees"],
    27: ["sattais", "sattaees"],
    28: ["atthais", "athais", "atthaees"],
    29: ["untis", "unattis", "unntis"],
    30: ["tees", "tis", "thees"],
    31: ["ikattis", "ikatis"],
    32: ["battis", "batis"],
    33: ["taintis", "tetis"],
    34: ["chauntis", "chautis"],
    35: ["paintis", "pentis"],
    36: ["chhattis", "chattis"],
    37: ["saintis", "sentis"],
    38: ["adtis", "athtis"],
    39: ["untaalis", "untalis"],
    40: ["chalis", "chaalis", "chalees"],
    41: ["iktalis", "ektalis"],
    42: ["bayalis", "byalis"],
    43: ["taintalis", "tetalis"],
    44: ["chauwalis", "chavalis"],
    45: ["paintalis", "pentalis"],
    46: ["chhiyalis", "chiyalis"],
    47: ["saintalis", "sentalis"],
    48: ["adtalis", "athtalis"],
    49: ["unchas", "unnchas"],
    50: ["pachaas", "pachas", "pachhas"],
    51: ["ikyavan", "ikavan"],
    52: ["bavan", "bawan"],
    53: ["tirpan", "trepan"],
    54: ["chauvan", "chawan"],
    55: ["pachpan", "pachpann"],
    56: ["chhappan", "chappan"],
    57: ["sattavan", "satavan"],
    58: ["atthavan", "athavan"],
    59: ["unsath", "unnsath"],
    60: ["saath", "sath"],
    100: ["sau", "sao"],
}

HINDI_NUMBER_WORDS: dict[str, int] = {}
for _value, _words in HINDI_SPELLINGS.items():
    for _word in _words:
        HINDI_NUMBER_WORDS[_word] = _value

NUMBER_WORDS: dict[str, int] = dict(ENGLISH_NUMBER_WORDS)
NUMBER_WORDS.update(HINDI_NUMBER_WORDS)


def parse_number(tokens) -> int | None:
    """Read digits, English words or Hindi words as one int. None if it is not a number."""
    if isinstance(tokens, str):
        tokens = tokens.split()
    total = 0
    seen = False
    for raw in tokens:
        token = str(raw).strip().lower().strip(".,!?")
        if not token or token in FILLER_WORDS:
            continue
        if token.isdigit():
            total += int(token)
            seen = True
            continue
        if token not in NUMBER_WORDS:
            return None
        value = NUMBER_WORDS[token]
        if value == 100:
            total = (total or 1) * 100
        else:
            total += value
        seen = True
    return total if seen else None


def english_words(value: int) -> str | None:
    """The English spelling of a value, or None when we do not spell it out."""
    if value < 0 or value > 999:
        return None
    if value < 20:
        return ENGLISH_ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        word = ENGLISH_TENS[tens]
        return word if ones == 0 else f"{word} {ENGLISH_ONES[ones]}"
    hundreds, rest = divmod(value, 100)
    word = f"{ENGLISH_ONES[hundreds]} hundred"
    if rest == 0:
        return word
    return f"{word} {english_words(rest)}"


def hindi_words(value: int) -> str | None:
    """The Hindi spelling of a value, or None when the table does not have it."""
    spellings = HINDI_SPELLINGS.get(value)
    if not spellings:
        return None
    return spellings[0]


def render_number(value: int, style: str) -> str | None:
    """Write a value as digits, English words or Hindi words."""
    if style == DIGITS:
        return str(value)
    if style == ENGLISH:
        return english_words(value)
    if style == HINDI:
        return hindi_words(value)
    raise ValueError(f"unknown number style: {style}")
