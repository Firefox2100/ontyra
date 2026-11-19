"""Loading a benchmark world and probing it: the write path the calibration depends on.

`populate` plants supersession, links and a retraction in one proposal per memory, so the
benchmark's trap memories exist exactly as the labels expect; `Probe` then records every lane's
score for a request without writing anything. Both are driven here over a tiny world.
"""

from __future__ import annotations

from ontyra.app import Application
from ontyra.calibration.models import LinkSpec, MemorySpec, Step, World
from ontyra.calibration.probe import Probe
from ontyra.calibration.world import populate
from ontyra.services import Caller


def tiny_world() -> World:
    return World(
        id="tiny",
        description="three memories: one superseded, one linked, one retracted",
        concepts=[],
        memories=[
            MemorySpec(
                key="m1",
                kind="fact",
                text="the tide table is kept on the kitchen wall",
                fields={
                    "subject": "concept:tide_table",
                    "predicate": "location",
                    "object": "kitchen wall",
                },
            ),
            MemorySpec(
                key="m2",
                kind="fact",
                text="the tide table moved to the shed after the kitchen was painted",
                fields={
                    "subject": "concept:tide_table",
                    "predicate": "location",
                    "object": "shed",
                },
                supersedes="m1",
            ),
            MemorySpec(
                key="m3",
                kind="fact",
                text="the tide table is printed every January",
                fields={
                    "subject": "concept:tide_table",
                    "predicate": "printed",
                    "object": "every January",
                },
                links=[LinkSpec(relation="refines", target="m2")],
                retracted=True,
            ),
        ],
    )


async def test_populate_commits_every_planted_memory(
    make_app, human: Caller, agent: Caller
) -> None:
    app: Application = await make_app()
    keys = await populate(app, human, agent, tiny_world())
    assert set(keys.values()) == {"m1", "m2", "m3"}
    assert len(set(keys)) == 3  # a distinct record per world key


async def test_probe_records_a_lane_score_for_every_memory_of_the_pool(
    make_app, human: Caller, agent: Caller
) -> None:
    app: Application = await make_app()
    keys = await populate(app, human, agent, tiny_world())
    probe = Probe(app, agent, keys)
    await probe.prepare()

    rows = await probe.rows(Step(query="tide table kitchen wall"), None)

    assert rows is not None
    # Only the live memory is in the pool: the index drops the superseded and retracted ones.
    assert {row[0] for row in rows} == {"m2"}
    assert all(len(row) == 11 for row in rows)
    found = rows[0]
    assert found[1] is not None and found[1] > 0  # raw BM25 found the exact text
    assert found[5] == 1  # it applies in this context


async def test_probe_returns_nothing_for_a_request_without_text(
    make_app, human: Caller, agent: Caller
) -> None:
    app: Application = await make_app()
    keys = await populate(app, human, agent, tiny_world())
    probe = Probe(app, agent, keys)
    await probe.prepare()
    assert await probe.rows(Step(), None) is None
