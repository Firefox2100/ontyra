"""Recall suite: labelled requests against a loaded world; one result file per run.

With the hash embedder at default settings the suite also asserts (regression guard): every
case passes except those frozen in `data/baselines/hash.json`. Otherwise it only measures.
"""

from __future__ import annotations

import json

import pytest

from benchmark_test.framework.config import BenchSettings, Profile
from benchmark_test.framework.models import DATA, Case, load_cases, world_ids
from benchmark_test.framework.probe import Probe
from benchmark_test.framework.results import summarise
from benchmark_test.framework.run import play
from benchmark_test.framework.score import StepScore


def known_failures() -> dict[str, str]:
    """Cases the hash baseline failed when it was frozen (`baseline.py`)."""
    path = DATA / "baselines" / "hash.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["failing"]


async def test_recall_suite(
    world_app, worlds, bench: BenchSettings, profile: Profile, recorder, capsys
) -> None:
    cases = [c for c in load_cases("recall") if not bench.only or bench.only in c.id]
    if not cases:
        pytest.skip("no case matches BENCH_ONLY")
    outcomes: list[tuple[Case, list[StepScore]]] = []
    domains: dict[str, str] = {}
    for world_id in world_ids():
        mine = [c for c in cases if c.world == world_id]
        if not mine:
            continue
        world = worlds(world_id)
        app, caller, keys = await world_app(world)
        domains = {m.key: m.domain for m in world.memories}
        universal = frozenset(m.key for m in world.memories if m.scope == "universal")
        probe = Probe(app, caller, keys)
        await probe.prepare()
        for case in mine:
            outcomes.append((case, await play(app, caller, case, keys, domains, probe, universal)))

    path = recorder("recall", outcomes, domains)
    summary = summarise(outcomes)
    with capsys.disabled():
        print(f"\nrecall [{profile.name}] -> {path}")
        for tag, cell in summary["by_tag"].items():
            p, r = cell["precision"], cell["recall"]
            print(
                f"  {tag:<14} {cell['passed']:>2}/{cell['cases']:<2}"
                f" precision={'n/a' if p is None else f'{p:.2f}'}"
                f" recall={'n/a' if r is None else f'{r:.2f}'}"
            )
        print(f"  overall: {summary['overall']['passed']}/{summary['overall']['cases']}")

    asserting = bench.assert_results if bench.assert_results is not None else profile.baseline
    if not asserting:
        return
    known = known_failures()
    passed = {c.id: all(s.passed for s in steps) for c, steps in outcomes}
    regressions = [
        f"{case.id}: {'; '.join(p for s in steps for p in s.problems)}"
        for case, steps in outcomes
        if not passed[case.id] and case.id not in known
    ]
    fixed = sorted(i for i, ok in passed.items() if ok and i in known)
    assert not regressions, "cases that passed in the baseline now fail:\n" + "\n".join(regressions)
    assert not fixed, f"baseline failures that now pass (regenerate the baseline): {fixed}"
