"""Scoring: a request is judged against the keys it must, must not and may return.

Everything returned that the case neither expects nor allows is a false positive, as in a
labelled check ("name the findings, and nothing else"). Results are aggregated per tag and
split, never averaged across them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from benchmark_test.framework.models import Step
from ontyra.models.recall import RecallPack


@dataclass
class Returned:
    key: str
    rank: int
    score: float
    relevance: float
    section: str
    caveats: list[str]
    checks: list[str]
    why: list[str]


@dataclass
class StepScore:
    returned: list[Returned]
    unknown: int  # returned memories that belong to no world key
    missing: list[str]
    leaked: list[str]  # returned although excluded
    extra: list[str]  # returned, neither expected nor allowed, from another domain: a leak
    tolerated: list[str]  # returned, not expected, but from the same domain as an expected memory
    ranked_low: list[str]
    problems: list[str] = field(default_factory=list)
    rows: list[list[Any]] | None = None  # per-memory lane scores (probe), when recorded
    tp: int = 0
    ms: float = 0.0
    tokens: int = 0

    @property
    def passed(self) -> bool:
        return not self.problems


SECTIONS = ("must_follow", "applicable", "context", "how_to", "open_tasks")


def returned_items(pack: RecallPack, keys: dict[str, str]) -> tuple[list[Returned], int]:
    """The pack's items ranked by score (the sections only group them), keyed by world key."""
    items: list[Returned] = []
    unknown = 0
    for section in SECTIONS:
        for item in getattr(pack, section):
            key = keys.get(str(item.memory))
            if key is None:
                unknown += 1
                continue
            items.append(
                Returned(
                    key=key,
                    rank=0,
                    score=item.score,
                    relevance=item.relevance,
                    section=section,
                    caveats=list(item.caveats),
                    checks=list(item.checks),
                    why=list(item.why),
                )
            )
    items.sort(key=lambda r: -r.score)
    for n, item in enumerate(items, 1):
        item.rank = n
    return items, unknown


def score_step(
    step: Step,
    pack: RecallPack,
    keys: dict[str, str],
    *,
    verdict: str | None = None,
    ms: float = 0.0,
    domains: dict[str, str] | None = None,
    universal: frozenset[str] = frozenset(),
    twins: Mapping[str, str] | None = None,
) -> StepScore:
    """`twins`: planted duplicate key -> the key it restates; returning one is never a false
    positive when the original is expected or allowed, and it satisfies an `include` of the original. `universal`: keys of `scope: universal` memories. They are meant to appear whatever the
    situation, so returning one is never a false positive (a case still may require one)."""
    items, unknown = returned_items(pack, keys)
    got = [i.key for i in items]
    wanted = set(step.include)
    allowed = wanted | set(step.allow)

    def ok(key: str) -> bool:
        original = (twins or {}).get(key)  # a planted duplicate is fine wherever its original is
        return (
            key in allowed
            or key in universal
            or key.startswith(tuple(step.allow_prefix))
            or (original is not None and original in allowed)
        )

    restated = {orig: [d for d, o in (twins or {}).items() if o == orig] for orig in step.include}
    # a restatement of a required memory delivers the same information
    missing = [
        k for k in step.include if k not in got and not any(d in got for d in restated.get(k, []))
    ]
    leaked = [k for k in step.exclude if k in got]
    unexpected = [k for k in got if not ok(k) and k not in step.exclude]
    near = {(domains or {}).get(k) for k in step.include} - {None}
    lenient = not step.strict and not step.silent and bool(domains)
    tolerated = [k for k in unexpected if lenient and (domains or {}).get(k) in near]
    extra = [k for k in unexpected if k not in tolerated]
    ranked_low = (
        [k for k in step.include if k in got and got.index(k) >= step.top] if step.top else []
    )
    out = StepScore(
        returned=items,
        unknown=unknown,
        missing=missing,
        leaked=leaked,
        extra=extra,
        tolerated=tolerated,
        ranked_low=ranked_low,
        tp=len(wanted) - len(missing),
        ms=ms,
        tokens=pack.tokens,
    )
    if missing:
        out.problems.append(f"missing {missing}")
    if leaked:
        out.problems.append(f"leaked {leaked}")
    if extra:
        out.problems.append(f"unexpected {extra}")
    if ranked_low:
        out.problems.append(f"ranked below top {step.top}: {ranked_low}")
    if step.silent and (items or not pack.silent):
        out.problems.append("expected silence")
    if len(pack.cautions) < step.min_cautions:
        out.problems.append(f"expected >= {step.min_cautions} cautions, got {len(pack.cautions)}")
    if step.verdict and verdict != step.verdict:
        out.problems.append(f"verdict {verdict!r}, expected {step.verdict!r}")
    by_key = {i.key: i for i in items}
    for key, texts in step.checks.items():
        have = " | ".join(by_key[key].checks) if key in by_key else ""
        out.problems += [f"{key}: no check mentions {t!r}" for t in texts if t not in have]
    for key, texts in step.caveats.items():
        have = " | ".join(by_key[key].caveats) if key in by_key else ""
        out.problems += [f"{key}: no caveat mentions {t!r}" for t in texts if t not in have]
    return out


@dataclass
class Cell:
    """Totals for one tag (or split): cases, passes, and item-level counts."""

    cases: int = 0
    passed: int = 0
    tp: int = 0
    fn: int = 0
    fp: int = 0
    tolerated: int = 0
    leaks: int = 0

    def add(self, steps: list[StepScore]) -> None:
        self.cases += 1
        self.passed += int(all(s.passed for s in steps))
        self.tp += sum(s.tp for s in steps)
        self.fn += sum(len(s.missing) for s in steps)
        self.fp += sum(len(s.extra) for s in steps)
        self.tolerated += sum(len(s.tolerated) for s in steps)
        self.leaks += sum(len(s.leaked) for s in steps)

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp + self.leaks
        return None if denom == 0 else self.tp / denom

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return None if denom == 0 else self.tp / denom

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "passed": self.passed,
            "tp": self.tp,
            "fn": self.fn,
            "fp": self.fp,
            "tolerated": self.tolerated,
            "leaks": self.leaks,
            "precision": self.precision,
            "recall": self.recall,
        }
