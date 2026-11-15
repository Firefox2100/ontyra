import math

import pytest

from ontyra.adapters import HashEmbedder, LangChainEmbedder, LangChainReranker, build_embedder
from ontyra.adapters.factory import build_reranker
from ontyra.config import EmbeddingSettings, RerankSettings


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hash_embedder_is_deterministic_and_normalised() -> None:
    emb = HashEmbedder(64)
    a = await emb.embed_query("use pytest for testing")
    b = (await emb.embed_documents(["use pytest for testing"]))[0]
    assert a == b and len(a) == 64
    assert math.isclose(cosine(a, a), 1.0, rel_tol=1e-9)
    assert emb.name == "hash-64" and emb.dimension == 64


async def test_hash_embedder_prefers_overlapping_text() -> None:
    emb = HashEmbedder(256)
    q = await emb.embed_query("how do I run the tests")
    near, far = await emb.embed_documents(
        ["run the tests with pytest", "docker networks exhausted address pools"]
    )
    assert cosine(q, near) > cosine(q, far) + 0.2


async def test_hash_embedder_survives_inflection() -> None:
    emb = HashEmbedder(256)
    q = await emb.embed_query("testing")
    assert cosine(q, await emb.embed_query("tests")) > cosine(q, await emb.embed_query("docker"))


def test_hash_embedder_rejects_tiny_dimensions() -> None:
    with pytest.raises(ValueError):
        HashEmbedder(4)


async def test_langchain_embedder_wraps_any_embeddings_object() -> None:
    from langchain_core.embeddings import DeterministicFakeEmbedding

    emb = LangChainEmbedder(DeterministicFakeEmbedding(size=32), name="fake", dimension=32)
    assert len(await emb.embed_query("hi")) == 32
    docs = await emb.embed_documents(["a", "b"])
    assert len(docs) == 2 and docs[0] != docs[1]


async def test_langchain_embedder_checks_the_configured_dimension() -> None:
    from langchain_core.embeddings import DeterministicFakeEmbedding

    emb = LangChainEmbedder(DeterministicFakeEmbedding(size=8), name="fake", dimension=32)
    with pytest.raises(ValueError, match="8 dimensions"):
        await emb.embed_query("hi")


def test_factory_builds_each_provider() -> None:
    assert build_embedder(EmbeddingSettings(provider="none")) is None
    hashed = build_embedder(EmbeddingSettings(provider="hash", dimension=64))
    assert hashed is not None and hashed.dimension == 64
    lc = build_embedder(
        EmbeddingSettings(
            provider="langchain",
            class_path="langchain_core.embeddings.DeterministicFakeEmbedding",
            kwargs={"size": 16},
            dimension=16,
            name="fake-16",
        )
    )
    assert lc is not None and lc.name == "fake-16"


def test_factory_reports_a_missing_integration() -> None:
    with pytest.raises(ValueError, match="cannot import"):
        build_embedder(EmbeddingSettings(provider="langchain", class_path="no_such_pkg.Embeddings"))
    with pytest.raises(ValueError, match="dotted path"):
        build_embedder(EmbeddingSettings(provider="langchain"))
    assert build_reranker(RerankSettings()) is None


async def test_langchain_reranker_maps_scores_back_to_input_order() -> None:
    from langchain_core.documents import BaseDocumentCompressor, Document

    class Fake(BaseDocumentCompressor):
        def compress_documents(self, documents, query, callbacks=None):  # type: ignore[no-untyped-def]
            kept = [d for d in documents if "keep" in d.page_content]
            for d in kept:
                d.metadata["relevance_score"] = 0.9 if "best" in d.page_content else 0.4
            return list(reversed(kept))  # order must not matter

    rr = LangChainReranker(Fake())
    scores = await rr.rerank("q", ["drop me", "keep best", "keep ok"])
    assert scores == [0.0, 0.9, 0.4]
    assert Document  # imported to prove langchain_core is available
