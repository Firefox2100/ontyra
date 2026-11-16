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


async def test_langchain_embedder_applies_prefixes_to_queries_and_documents() -> None:
    seen: list[str] = []

    class Recording:
        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            seen.extend(texts)
            return [[0.0] * 4 for _ in texts]

        async def aembed_query(self, text: str) -> list[float]:
            seen.append(text)
            return [0.0] * 4

    emb = LangChainEmbedder(
        Recording(), name="r", dimension=4, query_prefix="Q: ", document_prefix="D: "
    )
    await emb.embed_query("hello")
    await emb.embed_documents(["a", "b"])
    assert seen == ["Q: hello", "D: a", "D: b"]


async def test_http_reranker_posts_to_rerank_and_maps_scores_by_index() -> None:
    import httpx

    from ontyra.adapters import HttpReranker

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 3.5},
                    {"index": 0, "relevance_score": -2.0},
                ]
            },
        )

    rr = HttpReranker("http://x/v1/", "m", api_key="k", transport=httpx.MockTransport(handler))
    assert await rr.rerank("q", ["a", "b"]) == [-2.0, 3.5]
    assert seen["url"] == "http://x/v1/rerank" and '"top_n":2' in str(seen["body"]).replace(" ", "")
    assert await rr.rerank("q", []) == []
    assert rr.name == "http:m"


def test_a_hosted_reranker_is_not_trainable_and_the_local_one_is() -> None:
    from ontyra.adapters import CrossEncoderReranker, HttpReranker
    from ontyra.ports.adapters import TrainableReranker

    assert not isinstance(HttpReranker("http://x", "m"), TrainableReranker)
    assert isinstance(CrossEncoderReranker(), TrainableReranker)  # lazy: nothing is loaded


def test_factory_builds_each_reranker_provider() -> None:
    from ontyra.adapters import CrossEncoderReranker, HttpReranker

    assert build_reranker(RerankSettings(provider="none")) is None
    http = build_reranker(RerankSettings(provider="http", url="http://x/v1", model="m"))
    assert isinstance(http, HttpReranker)
    local = build_reranker(RerankSettings(provider="cross_encoder"))
    assert isinstance(local, CrossEncoderReranker)
    with pytest.raises(ValueError, match="URL"):
        build_reranker(RerankSettings(provider="http"))
