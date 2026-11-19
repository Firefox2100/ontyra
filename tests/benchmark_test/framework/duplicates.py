"""Scoring a duplicate scan against the planted duplicates (`duplicate_of` in a world)."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from ontyra.calibration.models import World
from ontyra.models.consolidation import ScanReport


def roots(world: World) -> dict[str, str]:
    """memory key -> the original it ultimately restates (a memory with no duplicate_of is its own)."""
    parent = {m.key: m.duplicate_of for m in world.memories if m.duplicate_of}

    def root(key: str) -> str:
        while key in parent:
            key = parent[key]
        return key

    return {m.key: root(m.key) for m in world.memories}


@dataclass
class DuplicateScore:
    planted: int  # (duplicate, original) pairs in the world
    found: int  # planted pairs that landed in one reported cluster
    reported: int  # pairs inside reported clusters
    correct: int  # of those, truly the same statement
    missed: list[tuple[str, str]] = field(default_factory=list)
    false: list[tuple[str, str]] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.found / self.planted if self.planted else 1.0

    @property
    def precision(self) -> float:
        return self.correct / self.reported if self.reported else 1.0


def score(world: World, report: ScanReport, keys: dict[str, str]) -> DuplicateScore:
    """`keys` maps a memory id to its world key."""
    truth = roots(world)
    clusters = [
        [keys[str(m.memory)] for m in c.members if str(m.memory) in keys] for c in report.duplicates
    ]
    together = {frozenset(p) for members in clusters for p in combinations(members, 2)}
    planted = [(m.key, m.duplicate_of) for m in world.memories if m.duplicate_of]
    found = [p for p in planted if frozenset(p) in together]
    false: list[tuple[str, str]] = []
    for pair in together:
        a, b = sorted(pair)
        if truth[a] != truth[b]:
            false.append((a, b))
    false.sort()
    return DuplicateScore(
        planted=len(planted),
        found=len(found),
        reported=len(together),
        correct=len(together) - len(false),
        missed=[p for p in planted if p not in found],
        false=false,
    )
