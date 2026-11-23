"""The score layer through the real pipeline: feedback citing recall traces turns it on, and it
adjusts the next recall from the owner's own feedback only."""

from __future__ import annotations

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.feedback import FeedbackLabel, FeedbackRequest, LearningSettings
from ontyra.models.recall import RecallRequest, RecallSettings
from ontyra.models.search import RecallTrace
from ontyra.services import Caller

QUERIES = ["run the tests with pytest", "format the code before commit", "how to run tests"]


@pytest.fixture
async def learning(make_app) -> Application:
    return await make_app(
        recall=RecallSettings(engine="fusion", fusion_margin=0.9),
        learning=LearningSettings(
            enabled=True, min_examples=4, canary_fraction=0.0, canary_min_groups=0
        ),
    )


async def _memories(app: Application, caller: Caller) -> dict[str, RecordId]:
    from ontyra.models.proposals import AddMemory, ProposalRequest

    out: dict[str, RecordId] = {}
    for key, text in {
        "pytest": "Run the tests with pytest before every commit",
        "format": "Format the code with ruff before commit",
        "tests": "Tests live in the tests directory and run through pytest",
    }.items():
        body = {"text": text, "strength": "should", "rule": text}
        result = await app.memory.submit(
            caller, ProposalRequest(ops=[AddMemory(kind="directive", ref="m", fields=body)])
        )
        out[key] = result.created["m"]
    await app.indexer.backfill()
    return out


async def test_it_is_off_unless_enabled(app: Application, agent: Caller) -> None:
    state = await app.feedback.layer_state(agent)
    assert state.status == "off" and not state.enabled


async def test_feedback_on_recalls_turns_it_on_and_it_moves_the_ranked_memory(
    learning: Application, agent: Caller
) -> None:
    ids = await _memories(learning, agent)
    assert (await learning.feedback.layer_state(agent)).status == "learning"
    for query in QUERIES:
        pack = await learning.recall.recall(agent, RecallRequest(query=query))
        assert pack.trace is not None
        verdicts: list[tuple[FeedbackLabel, RecordId]] = [
            ("irrelevant", ids["format"]),
            ("applied", ids["pytest"]),
        ]
        for label, memory in verdicts:
            await learning.feedback.record(
                agent, FeedbackRequest(memory=memory, query=query, label=label, trace=pack.trace)
            )
    state = await learning.feedback.layer_state(agent)
    assert state.status == "active" and state.examples >= 4 and state.priors == 2
    pack = await learning.recall.recall(agent, RecallRequest(query="run the tests with pytest"))
    assert pack.trace is not None
    trace = await learning.recall.store.get(pack.trace)
    assert isinstance(trace, RecallTrace) and trace.scoring
    rows = {r["rev"]: r for r in trace.scoring["candidates"]}
    changed = [r for r in rows.values() if r["base"] != r["final"]]
    assert changed  # the layer changed at least one candidate's score


async def test_another_owners_feedback_does_not_move_my_ranking(
    learning: Application, agent: Caller
) -> None:
    ids = await _memories(learning, agent)
    other = await learning.actors.caller(kind="agent", name="other", owner=RecordId("user", "bob"))
    for query in QUERIES:
        pack = await learning.recall.recall(agent, RecallRequest(query=query))
        for memory in ids.values():
            await learning.feedback.record(
                agent,
                FeedbackRequest(memory=memory, query=query, label="irrelevant", trace=pack.trace),
            )
    assert (await learning.feedback.layer_state(agent)).status == "active"
    assert (await learning.feedback.layer_state(other)).status == "learning"


async def test_a_retraction_changes_what_the_layer_sees(
    learning: Application, agent: Caller
) -> None:
    ids = await _memories(learning, agent)
    events = []
    for query in QUERIES:
        pack = await learning.recall.recall(agent, RecallRequest(query=query))
        for memory in (ids["pytest"], ids["format"]):
            events.append(
                await learning.feedback.record(
                    agent,
                    FeedbackRequest(memory=memory, query=query, label="applied", trace=pack.trace),
                )
            )
    before = await learning.feedback.layer_state(agent)
    await learning.feedback.retract(agent, events[0].id)
    after = await learning.feedback.layer_state(agent)
    assert after.examples == before.examples - 1
