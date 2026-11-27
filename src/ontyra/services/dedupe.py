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
_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*")


def values(text: str) -> set[str]:
    """The numbers, versions, dates and identifiers with digits in a text, normalised
    (`450k` = `450,000`, `3rd` = `3`, `three` = `3`, `March` = `m3`)."""
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text.casefold())  # 450,000 is one number; 1,2 is two
    out: set[str] = set()
    for raw in _TOKEN.findall(t):
        parts = raw.split("-") if "-" in raw and not any(char.isdigit() for char in raw) else [raw]
        for part in parts:
            word = part.strip(".-")
            if word.endswith("k") and word[:-1].isdigit():
                word = str(int(word[:-1]) * 1000)
            elif len(word) > 2 and word[-2:] in {"st", "nd", "rd", "th"} and word[:-2].isdigit():
                word = word[:-2]
            if word in _NUMBER_WORDS:
                out.add(_NUMBER_WORDS[word])
            elif word in _MONTHS:
                out.add(f"m{_MONTHS[word]}")
            elif any(char.isdigit() for char in word):
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
