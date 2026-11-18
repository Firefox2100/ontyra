"""Dialogue suite: scripted conversations, one recall per user turn, played once per strategy.

It answers one question: how should a recall request be built from the conversation so far?
The strategies (`framework/dialogue.py`) are compared on the same turns, split into turns that
stand alone and turns that cannot be understood without history. It asserts nothing (it is a
measurement); results go to `results/dialogue-<strategy>-<label>-<profile>.json`.
`BENCH_HISTORY` selects the strategies (comma-separated), `BENCH_ONLY` filters dialogue ids.
"""

from __future__ import annotations

import pytest

from benchmark_test.framework.config import BenchSettings, Profile
from benchmark_test.framework.dialogue import STRATEGIES, load_dialogues, play_dialogue
from benchmark_test.framework.models import Case
from benchmark_test.framework.results import summarise
from benchmark_test.framework.score import StepScore


async def test_dialogue_suite(
    world_app, worlds, bench: BenchSettings, profile: Profile, recorder, capsys
) -> None:
    strategies = [s.strip() for s in bench.history.split(",") if s.strip()]
    unknown = sorted(set(strategies) - set(STRATEGIES))
    assert not unknown, f"unknown strategies {unknown}; known: {STRATEGIES}"
    dialogues = [d for d in load_dialogues() if not bench.only or bench.only in d.id]
    if not dialogues:
        pytest.skip("no dialogue matches BENCH_ONLY")

    outcomes: dict[str, list[tuple[Case, list[StepScore]]]] = {s: [] for s in strategies}
    domains: dict[str, str] = {}
    for world_id in sorted({d.world for d in dialogues}):
        world = worlds(world_id)
        app, caller, keys = await world_app(world)
        domains = {m.key: m.domain for m in world.memories}
        universal = frozenset(m.key for m in world.memories if m.scope == "universal")
        twins = {m.key: m.duplicate_of for m in world.memories if m.duplicate_of}
        for dialogue in (d for d in dialogues if d.world == world_id):
            for strategy in strategies:
                played = await play_dialogue(
                    app, caller, dialogue, strategy, keys, domains, universal, twins
                )
                outcomes[strategy] += [(case, [score]) for case, score in played]

    with capsys.disabled():
        print(f"\ndialogue [{profile.name}]")
        for strategy in strategies:
            path = recorder(f"dialogue-{strategy}", outcomes[strategy], domains)
            by_tag = summarise(outcomes[strategy])["by_tag"]
            cells = []
            for tag in ("standalone", "needs_history"):
                c = by_tag.get(tag)
                if c:
                    r = c["recall"]
                    p = c["precision"]
                    cells.append(
                        f"{tag}: {c['passed']}/{c['cases']}"
                        f" recall={'n/a' if r is None else f'{r:.2f}'}"
                        f" precision={'n/a' if p is None else f'{p:.2f}'}"
                    )
            print(f"  {strategy:<8} " + " | ".join(cells) + f"  -> {path.name}")
