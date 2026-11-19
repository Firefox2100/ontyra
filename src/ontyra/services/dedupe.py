"""Deterministic helpers for telling a restatement from an update (no model, no I/O).

A pair of near-identical memories is usually one of two things: the same statement worded
twice (a duplicate), or the same statement with a changed value (PostgreSQL 14 -> 16, a 300 EUR
limit -> 500 EUR). Cosine similarity cannot tell them apart; the values can.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "twelve": "12",
    "fifteen": "15",
    "twenty": "20",
    "thirty": "30",
    "sixty": "60",
}
_MONTH_NAMES = (
    "january february march april may june july august september october november december"
)
_MONTHS = {name: i for i, name in enumerate(_MONTH_NAMES.split(), 1)}
_TOKEN = re.compile(r"[a-z]++|\d++(?:\.\d++)?")


def _at_word_end(text: str, index: int) -> bool:
    """Whether `index` sits on a regex word boundary, i.e. no word character starts there."""
    if index >= len(text):
        return True
    return not (text[index].isalnum() or text[index] == "_")


def _expand_numerals(text: str) -> str:
    """Expand `450k` to `450000` and strip ordinal suffixes (`3rd` to `3`) in one linear pass.

    The equivalent regexes could restart at every digit of a long run and were therefore
    super-linear on adversarial input (Sonar S8786); the scan below advances monotonically.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        start = i
        while i < len(text) and text[i].isdigit():
            i += 1
        if i == start:
            out.append(text[i])
            i += 1
            continue
        digits = text[start:i]
        if text.startswith("k", i) and _at_word_end(text, i + 1):
            out.append(str(int(digits) * 1000))
            i += 1
        elif text[i : i + 2] in ("st", "nd", "rd", "th") and _at_word_end(text, i + 2):
            out.append(digits)
            i += 2
        else:
            out.append(digits)
    return "".join(out)


def values(text: str) -> set[str]:
    """The numbers, versions, dates and identifiers with digits in a text, normalised
    (`450k` = `450,000`, `3rd` = `3`, `three` = `3`, `March` = `m3`)."""
    t = text.lower()
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = _expand_numerals(t)
    out: set[str] = set()
    for word in _TOKEN.findall(t):
        if word in _NUMBER_WORDS:
            out.add(_NUMBER_WORDS[word])
        elif word in _MONTHS:
            out.add(f"m{_MONTHS[word]}")
        elif re.search(r"\d", word):
            out.add(word)
    return out


def conflicting_values(a: str, b: str) -> bool:
    """Each text carries a value the other lacks. A paraphrase that merely adds or spells out a
    number (one side only) is not a conflict."""
    x, y = values(a), values(b)
    return bool(x - y) and bool(y - x)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", re.sub(r"\d[\w.\-]*", "", text.lower())))


def is_update(a: str, b: str, floor: float = 0.5) -> bool:
    """The same wording with a changed value: the values conflict and the other words (without
    the values) overlap by at least `floor` (Jaccard). Two different facts that merely contain
    different numbers fail the overlap and are not updates."""
    if not conflicting_values(a, b):
        return False
    x, y = _words(a), _words(b)
    return bool(x | y) and len(x & y) / len(x | y) >= floor


def anchors_compatible(a: Sequence[str], b: Sequence[str]) -> bool:
    """Do two memories apply in a common place? Unanchored applies everywhere; otherwise some
    anchor of one must contain (or equal) some anchor of the other, by path."""
    if not a or not b:
        return True

    def within(x: str, y: str) -> bool:
        x, y = x.rstrip("/"), y.rstrip("/")
        return x == y or x.startswith(y + "/") or y.startswith(x + "/")

    return any(within(x, y) for x in a for y in b)
