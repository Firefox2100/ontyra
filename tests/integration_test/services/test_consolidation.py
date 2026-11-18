"""A scan reports duplicate clusters and updates; a decision model, when there is one, vetoes
or confirms; without one the cosine rule decides. Nothing is changed."""

from __future__ import annotations

import pytest

from ontyra.app import Application
from ontyra.models.consolidation import ConsolidationSettings, ScanRequest
from ontyra.ports.errors import DecisionError
from ontyra.services import Caller

# the built-in hash embedder is lexical: word overlap stands in for meaning
SETTINGS = ConsolidationSettings(candidate_cosine=0.4, duplicate_cosine=0.75, confirm=0.85)
A = "Use uv for Python dependency management in tidewatch and never run pip install directly."
B = "Use uv for Python dependency management in tidewatch, never run pip install directly."
OTHER = "Water the ferns every Sunday morning before the market opens."
OLD = "Tidewatch production runs PostgreSQL 14 on the main database host."
NEW = "Tidewatch production runs PostgreSQL 16 on the main database host."


class Comparer:
    name = "fake-comparer"

    def __init__(self, score: float | Exception) -> None:
        self.score = score
        self.asked: list[tuple[str, str]] = []

    async def same(self, a: str, b: str) -> float:
        self.asked.append((a, b))
        if isinstance(self.score, Exception):
            raise self.score
        return self.score


@pytest.fixture
async def seeded(make_app, agent: Caller):
    app: Application = await make_app(consolidation=SETTINGS)

    async def add(kind: str, text: str, **fields: object):
        from ontyra.models.proposals import AddMemory, ProposalRequest

        body = {"text": text, "strength": "should", "rule": text, **fields}
        result = await app.memory.submit(
            agent,
            ProposalRequest(ops=[AddMemory(kind=kind, ref="m", fields=body)]),  # type: ignore[arg-type]
        )
        assert result.status == "committed", result.findings
        return result.created["m"]

    first = await add("directive", A)
    second = await add("directive", B)
    await add("directive", OTHER)
    await add("directive", OLD)
    await add("directive", NEW)
    await app.indexer.backfill()
    return app, first, second


async def test_without_a_model_a_close_pair_is_a_duplicate_by_cosine(seeded, agent: Caller) -> None:
    app, first, second = seeded
    report = await app.consolidation.scan(agent, ScanRequest())
    assert report.decision_model is None and report.unembedded == 0
    assert report.scanned == 5
    assert len(report.duplicates) == 1
    cluster = report.duplicates[0]
    assert {m.memory for m in cluster.members} == {first, second}
    assert cluster.keep == first  # the oldest
    assert cluster.pairs[0].judged_by == "cosine" and cluster.pairs[0].same is None


async def test_a_changed_value_is_an_update_and_not_a_duplicate(seeded, agent: Caller) -> None:
    app, *_ = seeded
    report = await app.consolidation.scan(agent, ScanRequest())
    assert len(report.updates) == 1
    update = report.updates[0]
    assert "14" in update.older.text and "16" in update.newer.text
    assert all("PostgreSQL" not in m.text for c in report.duplicates for m in c.members)
    assert (await app.consolidation.scan(agent, ScanRequest(updates=False))).updates == []


async def test_the_model_confirms_in_both_orders(seeded, agent: Caller) -> None:
    app, _first, _second = seeded
    comparer = Comparer(0.95)
    app.consolidation.comparer = comparer  # type: ignore[assignment]
    report = await app.consolidation.scan(agent, ScanRequest())
    assert report.decision_model == "fake-comparer"
    pair = report.duplicates[0].pairs[0]
    assert pair.judged_by == "model" and pair.same == 0.95
    assert len(comparer.asked) == 2
    assert comparer.asked[0] == (comparer.asked[1][1], comparer.asked[1][0])


async def test_the_model_can_veto_what_cosine_would_accept(seeded, agent: Caller) -> None:
    app, *_ = seeded
    app.consolidation.comparer = Comparer(0.2)  # type: ignore[assignment]
    report = await app.consolidation.scan(agent, ScanRequest())
    assert report.duplicates == [] and report.rejected >= 1


async def test_a_model_that_fails_falls_back_to_cosine(seeded, agent: Caller) -> None:
    app, _first, _second = seeded
    app.consolidation.comparer = Comparer(DecisionError("down"))  # type: ignore[assignment]
    report = await app.consolidation.scan(agent, ScanRequest())
    assert report.model_errors >= 1 and report.rejected == 0
    assert report.duplicates[0].pairs[0].judged_by == "cosine"


async def test_a_scan_changes_nothing(seeded, agent: Caller) -> None:
    app, *_ = seeded
    store = app.memory.store  # type: ignore[attr-defined]

    async def state() -> list[tuple[str, str]]:
        rows = await store.select("memory", limit=100)
        return sorted((str(m.id), str(m.status)) for m in rows)  # type: ignore[attr-defined]

    before = await state()
    await app.consolidation.scan(agent, ScanRequest())
    assert await state() == before
    assert all(status == "active" for _, status in before) and len(before) == 5


async def test_kinds_never_mix_and_a_kind_filter_narrows_the_scan(make_app, agent: Caller) -> None:
    from ontyra.models.proposals import AddMemory, ProposalRequest

    app = await make_app(consolidation=SETTINGS)
    for ref, kind, fields in (
        ("d", "directive", {"strength": "should", "rule": A}),
        ("l", "lesson", {"situation": A, "insight": A}),
    ):
        result = await app.memory.submit(
            agent,
            ProposalRequest(ops=[AddMemory(kind=kind, ref=ref, fields={"text": A, **fields})]),  # type: ignore[arg-type]
        )
        assert result.status == "committed", result.findings
    await app.indexer.backfill()
    assert (await app.consolidation.scan(agent, ScanRequest())).duplicates == []
    assert (await app.consolidation.scan(agent, ScanRequest(kinds=["lesson"]))).scanned == 1
