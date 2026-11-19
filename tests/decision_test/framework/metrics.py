"""Small, dependency-free metrics shared by the tasks: ranking quality, thresholds, latency."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence


def auc(positive: Sequence[float], negative: Sequence[float]) -> float | None:
    """Probability that a random positive scores above a random negative (ties count half);
    None when either side is empty."""
    if not positive or not negative:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def prf(predicted: Sequence[bool], actual: Sequence[bool]) -> dict[str, float | int]:
    tp = sum(p and a for p, a in zip(predicted, actual, strict=True))
    fp = sum(p and not a for p, a in zip(predicted, actual, strict=True))
    fn = sum(a and not p for p, a in zip(predicted, actual, strict=True))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def best_threshold(scores: Sequence[float], actual: Sequence[bool]) -> dict[str, float]:
    """The cut-off with the best F1 (the lowest wins a tie), as an upper bound on what tuning
    the threshold on this data could give."""
    best = {"threshold": 0.5, "f1": -1.0, "precision": 0.0, "recall": 0.0}
    for t in sorted(set(scores)):
        r = prf([s >= t for s in scores], actual)
        if r["f1"] > best["f1"]:
            best = {
                "threshold": t,
                "f1": float(r["f1"]),
                "precision": float(r["precision"]),
                "recall": float(r["recall"]),
            }
    return best


def brier(scores: Sequence[float], actual: Sequence[bool]) -> float | None:
    """Mean squared error of the probabilities (0 is perfect, 0.25 is a coin)."""
    if not scores:
        return None
    return sum((s - a) ** 2 for s, a in zip(scores, actual, strict=True)) / len(scores)


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def latency(seconds: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "calls": len(seconds),
        "p50": percentile(seconds, 0.5),
        "p95": percentile(seconds, 0.95),
        "total": sum(seconds),
    }


def confusion(predicted: Sequence[str], actual: Sequence[str]) -> dict[str, dict[str, int]]:
    """`{actual: {predicted: count}}`."""
    out: dict[str, Counter[str]] = {}
    for p, a in zip(predicted, actual, strict=True):
        out.setdefault(a, Counter())[p] += 1
    return {a: dict(c) for a, c in sorted(out.items())}


def mean(values: Sequence[float | None]) -> float | None:
    real = [v for v in values if v is not None]
    return sum(real) / len(real) if real else None


def rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)
