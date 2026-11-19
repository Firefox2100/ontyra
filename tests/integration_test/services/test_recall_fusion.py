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


async def _trace_of(app: Application, caller: Caller, query: str):
    from ontyra.models.search import RecallTrace

    pack = await app.recall.recall(caller, RecallRequest(query=query))
    assert pack.trace is not None
    trace = await app.recall.store.get(pack.trace)
    assert isinstance(trace, RecallTrace)
    return pack, trace


async def test_a_trace_keeps_the_lane_scores_of_the_candidates(
    fusion: Application, agent: Caller, remember
) -> None:
    await remember("fact", "tidewatch releases are tagged on the main branch")
    await remember("fact", "the oven runs hotter than its dial says")
    await fusion.indexer.backfill()
    pack, trace = await _trace_of(fusion, agent, "how are tidewatch releases tagged")
    assert trace.scoring is not None
    rows = trace.scoring["candidates"]
    assert trace.scoring["abstained"] is False and trace.scoring["pool"] >= 1
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert {"cosine", "bm25", "lexical", "dense", "fused", "final", "shown"} <= set(rows[0])
    shown = {r["rev"] for r in rows if r["shown"]}
    assert shown == {str(i.revision) for i in pack.items()} and shown


async def test_traces_keep_the_cut_off_candidates_and_the_silence(
    fusion: Application, agent: Caller, remember
) -> None:
    await remember("fact", "the oven runs hotter than its dial says")
    await fusion.indexer.backfill()
    _, trace = await _trace_of(fusion, agent, "zzzz qqqq xxxx")
    assert trace.scoring is not None
    assert trace.scoring["candidates"] == [] or not any(
        r["shown"] for r in trace.scoring["candidates"]
    )


async def test_trace_candidates_zero_keeps_no_scores(make_app, agent: Caller) -> None:
    app = await make_app(recall=RecallSettings(engine="fusion", trace_candidates=0))
    _, trace = await _trace_of(app, agent, "anything")
    assert trace.scoring is None


async def test_old_traces_are_pruned_unless_feedback_cites_them(make_app, clock, remember) -> None:
    from datetime import timedelta

    app = await make_app(recall=RecallSettings(engine="fusion", trace_retention_days=30))
    caller = await app.actors.caller(kind="agent", name="claude", harness="claude-code")
    memory = await remember("fact", "tidewatch releases are tagged on the main branch")
    await app.indexer.backfill()
    _, cited = await _trace_of(app, caller, "how are tidewatch releases tagged")
    _, plain = await _trace_of(app, caller, "tidewatch releases tags")
    from ontyra.models.feedback import FeedbackRequest

    await app.feedback.record(
        caller,
        FeedbackRequest(
            memory=memory,
            query="x",
            label="helpful",
            trace=cited.id,  # type: ignore[arg-type]
        ),
    )
    clock._t += timedelta(days=60)
    _, fresh = await _trace_of(app, caller, "tidewatch releases tagged")
    assert await app.recall.store.get(cited.id) is not None
    assert await app.recall.store.get(plain.id) is None
    assert await app.recall.store.get(fresh.id) is not None


async def test_the_feedback_summary_counts_events_with_features(
    fusion: Application, agent: Caller, remember
) -> None:
    from ontyra.models.feedback import FeedbackRequest

    memory = await remember("fact", "tidewatch releases are tagged on the main branch")
    await fusion.indexer.backfill()
    _, trace = await _trace_of(fusion, agent, "how are tidewatch releases tagged")
    request = FeedbackRequest(memory=memory, query="q", label="helpful", trace=trace.id)  # type: ignore[arg-type]
    await fusion.feedback.record(agent, request)
    await fusion.feedback.record(
        agent, FeedbackRequest(memory=memory, query="other", label="helpful")
    )
    summary = await fusion.feedback.summary()
    assert summary.active == 2 and summary.with_features == 1


async def test_a_note_says_when_the_thresholds_were_tuned_for_another_embedder(make_app) -> None:
    from types import SimpleNamespace

    app = await make_app(recall=RecallSettings(engine="fusion"))
    assert app.recall.calibration_note() is None  # the hash embedder needs no calibration
    app.recall.embedder = SimpleNamespace(name="big-embedder")  # type: ignore[assignment]
    assert "not marked as calibrated" in (app.recall.calibration_note() or "")
    app.recall.cfg = RecallSettings(engine="fusion", calibrated_for="small-embedder")
    assert "small-embedder" in (app.recall.calibration_note() or "")
    app.recall.cfg = RecallSettings(engine="fusion", calibrated_for="big-embedder")
    assert app.recall.calibration_note() is None


async def test_a_trigger_fires_in_fusion_recall_too(
    fusion: Application, agent: Caller, remember, concepts
) -> None:
    from ontyra.models.recall import Signal

    await remember(
        "lesson",
        "pin the base image digest when you touch a Dockerfile",
        qualifiers=[{"concept": str(concepts["docker"]), "role": "trigger"}],
    )
    await fusion.indexer.backfill()
    asked = RecallRequest(query="what is the weather like on thursday")
    assert not (await fusion.recall.recall(agent, asked)).items()
    ctx = RecallContext(signals=[Signal(concept="docker")])
    pack = await fusion.recall.recall(agent, asked.model_copy(update={"context": ctx}))
    assert pack.items() and pack.items()[0].flags == ["triggered"]
