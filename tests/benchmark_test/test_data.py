"""The benchmark data is consistent: worlds are valid and every case names real things."""

from __future__ import annotations

import pytest

from benchmark_test.framework.models import Case, World, load_cases, load_world, world_ids
from benchmark_test.framework.validate import (
    case_problems,
    coverage_report,
    matrix,
    trap_counts,
    world_problems,
)


@pytest.fixture(scope="module")
def all_worlds() -> dict[str, World]:
    return {w: load_world(w) for w in world_ids()}


@pytest.fixture(scope="module")
def recall_cases() -> list[Case]:
    return load_cases("recall")


def test_worlds_are_consistent(all_worlds: dict[str, World]) -> None:
    assert all_worlds
    problems = [p for w in all_worlds.values() for p in world_problems(w)]
    assert not problems, "\n".join(problems)


def test_recall_cases_are_consistent(
    recall_cases: list[Case], all_worlds: dict[str, World]
) -> None:
    assert recall_cases
    problems = case_problems(recall_cases, all_worlds)
    assert not problems, "\n".join(problems)


def test_every_memory_is_used_by_some_case(
    recall_cases: list[Case], all_worlds: dict[str, World]
) -> None:
    report = {w: coverage_report(recall_cases, world) for w, world in all_worlds.items()}
    unused = {w: r["unused"] for w, r in report.items() if r["unused"]}
    assert not unused, f"memories no case includes, excludes or allows: {unused}"


def test_the_world_covers_every_kind_in_every_domain_group(all_worlds: dict[str, World]) -> None:
    kinds = {"fact", "directive", "decision", "event", "procedure", "task", "lesson"}
    for world in all_worlds.values():
        table = matrix(world)
        totals = {k: sum(row.get(k, 0) for row in table.values()) for k in kinds}
        assert all(n >= 8 for n in totals.values()), f"too few memories of some kind: {totals}"
        thin = {g: sorted(kinds - set(row)) for g, row in table.items() if len(row) < 5}
        assert not thin, f"domain groups missing too many kinds: {thin}"
        assert len(table) >= 5, f"too few domain groups: {sorted(table)}"


def test_every_planted_trap_is_present_often_enough(all_worlds: dict[str, World]) -> None:
    from benchmark_test.framework.models import TRAPS

    for world in all_worlds.values():
        counts = trap_counts(world)
        assert all(counts.get(t, 0) >= 3 for t in TRAPS), (
            f"traps with fewer than 3 memories: {counts}"
        )


def test_paraphrase_cases_exist_in_every_domain_group(recall_cases: list[Case]) -> None:
    groups = {t for c in recall_cases if "paraphrase" in c.tags for t in c.tags}
    assert {"dev", "office", "research", "daily"} <= groups


def test_tuning_and_holdout_splits_both_exist(recall_cases: list[Case]) -> None:
    splits = {c.split for c in recall_cases}
    assert splits == {"tune", "holdout"}
