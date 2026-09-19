"""The one place features are defined. The C runtime has to reproduce this byte for byte.

Everything below is written so a C99 port has no choices left to make.

Tokenising
----------
    tokens = text.lower().split()
Lowercasing is Python's str.lower on the whole string, then a split on ASCII whitespace
with empty pieces dropped. For ASCII input this is: fold A-Z to a-z, split on runs of
space, tab, newline, carriage return, vertical tab and form feed.

Hashing
-------
FNV-1a, 32 bit, over the UTF-8 bytes of the feature string:

    h = 2166136261
    for each byte b:
        h = h XOR b
        h = (h * 16777619) mod 2^32
    bucket = h AND (table_size - 1)

table_size is a power of two, 16384 by default, and is written into meta.json.

Intent features
---------------
Feature strings, all ASCII prefixes, built in this order:

    unigram   "u:" + token                                   for every token
    bigram    "b:" + token[i-1] + "\\x1f" + token[i]           for i = 1 .. n-1
    char 3    "c:" + three bytes of s                         for every 3-byte window of s

where s is the byte string "^" + (tokens joined by one space) + "$". The windows step one
byte at a time, so a string of L bytes gives L-2 windows, and none is emitted when L < 3.
Windows are taken over BYTES, not characters, so a multi-byte character can be split. That
is on purpose: it keeps the C side to a plain byte loop.

Each feature adds 1.0 to its bucket. When every feature is in, the vector is divided by its
L2 norm (skipped when the norm is 0). The classifier score is then

    score[k] = sum over buckets of weight[k][bucket] * vector[bucket] + bias[k]

CRF token features
------------------
For token i, for each offset o in -2, -1, 0, 1, 2, let j = i + o and let P be the ASCII
prefix "<o>:" written with a minus sign for negative offsets, so "-2:", "-1:", "0:", "1:",
"2:". If j is outside the sentence the only feature for that offset is

    P + "pad"

Otherwise, with w = tokens[j] (already lowercased):

    P + "w=" + w
    P + "p2=" + first 2 bytes of w         only when w has at least 2 bytes
    P + "p3=" + first 3 bytes of w         only when w has at least 3 bytes
    P + "s2=" + last 2 bytes of w          only when w has at least 2 bytes
    P + "s3=" + last 3 bytes of w          only when w has at least 3 bytes
    P + "sh=" + shape(w)
    P + "num=1"                            only when w reads as a number
    P + "g=" + slot type name              once per slot type whose word list holds w

shape(w) maps every byte of w to 'd' for '0'-'9', 'a' for 'a'-'z' and 'A'-'Z', and 'x' for
anything else, then collapses runs of the same letter to one. So "hello" is "a", "45" is
"d", "a1b" is "ada" and "a-b" is "axa".

"w reads as a number" means the token on its own parses as a number: digits, an English
number word or a Hindi number word.

On top of the offsets, every token also gets:

    "bias"
    "BOS"   only for the first token
    "EOS"   only for the last token

Feature strings go into CRFsuite with weight 1.0. The C runtime keeps the same strings.

Gazetteer dropout
-----------------
While training only, each "g=" feature is thrown away with probability 0.3, drawn from a
seeded generator. Nothing is dropped at decode time. This stops the tagger leaning on the
word lists so hard that a word it has never seen gets no tag.
"""

from __future__ import annotations

import random

import numpy as np

from .numbers import parse_number
from .schema import NUMBER, Spec

FNV_OFFSET = 2166136261
FNV_PRIME = 16777619
MASK32 = 0xFFFFFFFF

DEFAULT_BUCKETS = 1 << 14
GAZETTEER_DROPOUT = 0.3
OFFSETS = (-2, -1, 0, 1, 2)


def fnv1a(text) -> int:
    """FNV-1a over the UTF-8 bytes of a string, or over raw bytes, 32 bit."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    h = FNV_OFFSET
    for byte in text:
        h = ((h ^ byte) * FNV_PRIME) & MASK32
    return h


def bucket(text, table_size: int = DEFAULT_BUCKETS) -> int:
    """Which slot of the hash table a feature string lands in."""
    return fnv1a(text) & (table_size - 1)


def intent_features(tokens) -> list[bytes]:
    """Every intent feature key for one sentence, as bytes, in the documented order."""
    tokens = list(tokens)
    out = [b"u:" + t.encode("utf-8") for t in tokens]
    for i in range(1, len(tokens)):
        out.append(b"b:" + tokens[i - 1].encode("utf-8") + b"\x1f" + tokens[i].encode("utf-8"))
    raw = ("^" + " ".join(tokens) + "$").encode("utf-8")
    for i in range(len(raw) - 2):
        out.append(b"c:" + raw[i : i + 3])
    return out


def intent_vector(tokens, table_size: int = DEFAULT_BUCKETS) -> np.ndarray:
    """The hashed, L2 normalised feature vector for one sentence."""
    vector = np.zeros(table_size, dtype=np.float64)
    for feature in intent_features(tokens):
        vector[fnv1a(feature) & (table_size - 1)] += 1.0
    norm = float(np.sqrt(vector @ vector))
    if norm > 0.0:
        vector /= norm
    return vector


def intent_matrix(sentences, table_size: int = DEFAULT_BUCKETS):
    """One row per sentence, sparse, because most of a 16384 wide row is zero."""
    from scipy import sparse

    indptr = [0]
    indices: list[int] = []
    data: list[float] = []
    for tokens in sentences:
        counts: dict[int, float] = {}
        for feature in intent_features(tokens):
            key = fnv1a(feature) & (table_size - 1)
            counts[key] = counts.get(key, 0.0) + 1.0
        norm = np.sqrt(sum(v * v for v in counts.values()))
        for key in sorted(counts):
            indices.append(key)
            data.append(counts[key] / norm if norm > 0.0 else 0.0)
        indptr.append(len(indices))
    return sparse.csr_matrix(
        (np.array(data, dtype=np.float64), np.array(indices, dtype=np.int32), np.array(indptr)),
        shape=(len(indptr) - 1, table_size),
    )


def shape(word: str) -> str:
    """Letters to 'a', digits to 'd', anything else to 'x', with runs collapsed."""
    out: list[str] = []
    for char in word:
        if "0" <= char <= "9":
            code = "d"
        elif ("a" <= char <= "z") or ("A" <= char <= "Z"):
            code = "a"
        else:
            code = "x"
        if not out or out[-1] != code:
            out.append(code)
    return "".join(out)


def is_number_word(word: str) -> bool:
    """True when the token on its own reads as a number."""
    return parse_number([word]) is not None


def gazetteer(spec: Spec) -> dict[str, set[str]]:
    """Every word that shows up in a slot type's value list, keyed by slot type name."""
    table: dict[str, set[str]] = {}
    for name, slot_type in spec.slot_types.items():
        if slot_type.kind == NUMBER:
            continue
        words: set[str] = set()
        for forms in slot_type.values.values():
            for form in forms:
                words.update(form.split())
        if words:
            table[name] = words
    return table


def token_features(
    tokens,
    index: int,
    gaz: dict[str, set[str]],
    rng: random.Random | None = None,
    dropout: float = 0.0,
) -> list[str]:
    """The CRF feature strings for one token. Pass an rng to drop gazetteer flags."""
    out = ["bias"]
    if index == 0:
        out.append("BOS")
    if index == len(tokens) - 1:
        out.append("EOS")

    for offset in OFFSETS:
        prefix = f"{offset}:"
        position = index + offset
        if position < 0 or position >= len(tokens):
            out.append(prefix + "pad")
            continue
        word = tokens[position]
        out.append(prefix + "w=" + word)
        if len(word) >= 2:
            out.append(prefix + "p2=" + word[:2])
            out.append(prefix + "s2=" + word[-2:])
        if len(word) >= 3:
            out.append(prefix + "p3=" + word[:3])
            out.append(prefix + "s3=" + word[-3:])
        out.append(prefix + "sh=" + shape(word))
        if is_number_word(word):
            out.append(prefix + "num=1")
        for name in sorted(gaz):
            if word not in gaz[name]:
                continue
            if rng is not None and dropout > 0.0 and rng.random() < dropout:
                continue
            out.append(prefix + "g=" + name)
    return out


def sentence_features(
    tokens,
    gaz: dict[str, set[str]],
    rng: random.Random | None = None,
    dropout: float = 0.0,
) -> list[list[str]]:
    """The CRF features for a whole sentence, one list per token."""
    tokens = list(tokens)
    return [token_features(tokens, i, gaz, rng, dropout) for i in range(len(tokens))]
