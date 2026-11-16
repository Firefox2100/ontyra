"""The fusion engine behind the same operations: engine choice by settings, applicability first,
a relative cut-off, abstention, and reranker blending."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.recall import RecallContext, RecallRequest, RecallSettings
from ontyra.services import FusionRecallService, RecallService
from ontyra.services.context import Caller


@pytest.fixture
async def fusion(make_app) -> Application:
    return await make_app(recall=RecallSettings(engine="fusion", fusion_margin=0.3))


async def test_settings_choose_the_engine(app: Application, fusion: Application) -> None:
    assert type(app.recall) is RecallService
    assert isinstance(fusion.recall, FusionRecallService)


async def test_fusion_returns_the_relevant_memory_and_stays_silent_otherwise(
    fusion: Application, agent: Caller
) -> None:
    from ontyra.models.proposals import AddMemory, ProposalRequest

    ids: dict[str, RecordId] = {}
    for key, text in {
        "release": "Release tidewatch: bump the version, tag it and push the tag",
        "oven": "The oven runs fifteen degrees hotter than its dial says",
        "pytest": "Use pytest for tests in tidewatch",
    }.items():
        body = {"text": text, "trigger": text, "steps": [{"text": text}]}
        result = await fusion.memory.submit(
            agent, ProposalRequest(ops=[AddMemory(kind="procedure", ref="m", fields=body)])
        )
        ids[key] = result.created["m"]
    await fusion.indexer.backfill()
    pack = await fusion.recall.recall(agent, RecallRequest(query="how do I cut a release"))
    got = [i.memory for i in pack.applicable + pack.how_to + pack.context + pack.must_follow]
    assert ids["release"] in got and ids["oven"] not in got
    assert not pack.silent
    nothing = await fusion.recall.recall(agent, RecallRequest(query="zzzz qqqq xxxx"))
    assert nothing.silent


async def test_an_applicability_filter_runs_before_ranking(
    fusion: Application, agent: Caller, remember
) -> None:
    from ontyra.models.anchors import Anchor

    del Anchor
    mine = await remember(
        "directive", "use pytest for tests", anchors=[{"uri": "file:///home/u/proj-a"}]
    )
    other = await remember(
        "directive", "use unittest for tests", anchors=[{"uri": "file:///home/u/proj-b"}]
    )
    await fusion.indexer.backfill()
    pack = await fusion.recall.recall(
        agent,
        RecallRequest(
            query="which framework for tests",
            context=RecallContext(uris=["file:///home/u/proj-a/x.py"]),
        ),
    )
    got = {i.memory for i in pack.applicable + pack.how_to + pack.context + pack.must_follow}
    assert mine in got and other not in got


class _Boost:
    """A reranker that loves one text."""

    def __init__(self, favourite: str) -> None:
        self.favourite = favourite

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        return [5.0 if self.favourite in t else -5.0 for t in texts]  # raw logits


async def test_a_reranker_blends_into_the_fused_score(make_app, agent: Caller, remember) -> None:
    app = await make_app(
        recall=RecallSettings(engine="fusion", fusion_margin=1.0, rerank_weight=0.9)
    )
    app.recall.reranker = _Boost("pytest")  # type: ignore[attr-defined]
    a = await remember_in(app, agent, "use pytest for tests")
    b = await remember_in(app, agent, "use tests with unittest classes")
    await app.indexer.backfill()
    pack = await app.recall.recall(agent, RecallRequest(query="tests"))
    ordered = [i.memory for i in pack.must_follow + pack.applicable + pack.context + pack.how_to]
    assert a in ordered and b in ordered
    first = max(
        pack.must_follow + pack.applicable + pack.context + pack.how_to, key=lambda i: i.score
    )
    assert first.memory == a


async def remember_in(app: Application, caller: Caller, text: str) -> RecordId:
    from ontyra.models.proposals import AddMemory, ProposalRequest

    result = await app.memory.submit(
        caller,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="directive",
                    ref="m",
                    fields={"text": text, "strength": "should", "rule": text},
                )
            ]
        ),
    )
    return result.created["m"]
