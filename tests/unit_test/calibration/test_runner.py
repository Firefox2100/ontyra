"""The calibration run's bookkeeping: which requests are recorded, what a run will cost, and the
embedding counter that reports it. No model is called and no database is opened."""

from __future__ import annotations

from collections.abc import Sequence

from ontyra.calibration import duplicates, runner
from ontyra.calibration.models import Case, World, load_cases, load_world


class _Embedder:
    name = "fake"
    dimension = 8

    def __init__(self) -> None:
        self.documents: list[str] = []
        self.queries: list[str] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.documents.extend(texts)
        return [[0.0] * self.dimension for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.0] * self.dimension


class TestCounting:
    async def test_counts_requests_and_texts_of_both_calls(self) -> None:
        inner = _Embedder()
        counting = runner.Counting(inner)

        await counting.embed_documents(["a", "b"])
        await counting.embed_query("q")

        assert (counting.requests, counting.texts) == (2, 3)
        assert inner.documents == ["a", "b"]
        assert inner.queries == ["q"]

    async def test_passes_the_inner_identity_through(self) -> None:
        counting = runner.Counting(_Embedder())
        assert counting.name == "fake"
        assert counting.dimension == 8


class TestRequestsToRecord:
    def test_only_recall_requests_with_text_and_an_expectation(self) -> None:
        cases = [
            Case(id="a", world="w", tags=[], query="q", include=["m"], why=""),
            Case(id="b", world="w", tags=[], query="q", silent=True, why=""),
            Case(id="c", world="w", tags=[], query=None, include=["m"], why=""),
            Case(id="d", world="w", tags=[], query="q", why=""),  # nothing expected
            Case(id="e", world="w", tags=[], mode="briefing", query="q", include=["m"], why=""),
        ]
        assert [c.id for c in runner.requests_to_record(cases)] == ["a", "b"]


class TestEstimate:
    def test_counts_the_bundled_world_and_cases(self) -> None:
        world: World = load_world(runner.WORLD)
        cases = runner.requests_to_record(load_cases("recall"))
        pairs = duplicates.load_pairs()
        texts = len({t for p in pairs for t in (p.a, p.b)})

        out = runner.estimate()

        assert out["memories"] == len(world.memories)
        assert out["recall_requests"] == len(cases)
        assert out["pair_texts"] == texts
        assert out["embedding_texts_at_most"] == (
            len(world.memories) + runner.CALLS_PER_REQUEST * len(cases) + texts
        )
        assert out["rerank_requests"] == 0
        assert out["decision_requests"] == 0

    def test_counted_model_requests_follow_the_configured_providers(self) -> None:
        from ontyra.config import Settings

        settings = Settings()
        settings.rerank.provider = "http"
        settings.decision.provider = "systemone"
        cases = runner.requests_to_record(load_cases("recall"))
        pairs = duplicates.load_pairs()

        out = runner.estimate(settings)

        assert out["rerank_requests"] == len(cases)
        assert out["decision_requests"] == len(cases) + 2 * len(pairs)
