from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from ontyra.adapters.rerank import HttpReranker
from ontyra.config import WorkerSettings
from ontyra.worker.app import create_worker_app
from ontyra.worker.engine import Engine
from ontyra.worker.probe import Capabilities, Hardware
from ontyra.worker.versions import Manifest

HW = Hardware("cuda:0", "cuda", "Test GPU", (7, 0), 16 << 30, 8 << 30)
CAPS = Capabilities(True, False, True, "float16", {"train": "not built"})
MANIFEST = Manifest("v0001", "org/model", "qwen3", "now")


@pytest.fixture
async def worker(make_scorer: Any) -> AsyncIterator[tuple[httpx.AsyncClient, Engine, Any]]:
    scorer = make_scorer()
    cfg = WorkerSettings(model="org/model")
    engine = Engine(scorer, cfg)
    app = create_worker_app(engine, cfg, HW, CAPS, MANIFEST)
    await engine.start()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker"
    ) as client:
        yield client, engine, scorer
    await engine.stop()


async def test_the_standard_rerank_shape(worker: tuple[httpx.AsyncClient, Engine, Any]) -> None:
    client, _, _ = worker
    r = await client.post(
        "/v1/rerank", json={"model": "x", "query": "q", "documents": ["a", "bbbb", "cc"]}
    )
    body = r.json()
    assert r.status_code == 200
    assert body["model"] == "x"
    assert body["usage"]["total_tokens"] == 3
    assert [x["index"] for x in body["results"]] == [1, 2, 0]  # best first
    assert all(0 < x["relevance_score"] < 1 for x in body["results"])
    assert body["results"][0]["relevance_score"] > body["results"][1]["relevance_score"]
    assert "document" not in body["results"][0]


async def test_top_n_return_documents_and_document_objects(
    worker: tuple[httpx.AsyncClient, Engine, Any],
) -> None:
    client, _, _ = worker
    r = await client.post(
        "/rerank",
        json={
            "query": "q",
            "documents": [{"text": "a"}, {"text": "bbbb"}, "cc"],
            "top_n": 2,
            "return_documents": True,
        },
    )
    results = r.json()["results"]
    assert [x["index"] for x in results] == [1, 2]
    assert results[0]["document"] == {"text": "bbbb"}
    assert r.json()["model"] == "org/model"  # the served model when none is named


@pytest.mark.parametrize(
    "body",
    [
        {"query": "q", "documents": []},
        {"query": "q", "documents": ["a"], "top_n": 0},
        {"documents": ["a"]},
    ],
)
async def test_bad_requests_are_422(
    worker: tuple[httpx.AsyncClient, Engine, Any], body: dict[str, Any]
) -> None:
    client, _, _ = worker
    assert (await client.post("/rerank", json=body)).status_code == 422


async def test_busy_is_503_with_retry_after(worker: tuple[httpx.AsyncClient, Engine, Any]) -> None:
    client, engine, _ = worker
    async with engine.lease():
        r = await client.post("/rerank", json={"query": "q", "documents": ["a"]})
    assert r.status_code == 503
    assert r.headers["retry-after"] == "1"


async def test_status_reports_the_machine_and_the_reasons(
    worker: tuple[httpx.AsyncClient, Engine, Any],
) -> None:
    client, _, _ = worker
    await client.post("/rerank", json={"query": "q", "documents": ["a"]})
    s = (await client.get("/status")).json()
    assert s["version"] == "v0001"
    assert s["state"] == "loaded"
    assert s["device"]["name"] == "Test GPU"
    assert s["capabilities"]["train"] is False
    assert s["capabilities"]["reasons"] == {"train": "not built"}
    assert s["counters"]["requests"] == 1
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_a_token_protects_everything_but_health(make_scorer: Any) -> None:
    scorer = make_scorer()
    cfg = WorkerSettings(token="secret")  # type: ignore[arg-type]
    engine = Engine(scorer, cfg)
    app = create_worker_app(engine, cfg, HW, CAPS, MANIFEST)
    await engine.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://worker"
        ) as client:
            body = {"query": "q", "documents": ["a"]}
            assert (await client.post("/rerank", json=body)).status_code == 401
            bad = {"authorization": "Bearer nope"}
            assert (await client.post("/rerank", json=body, headers=bad)).status_code == 401
            good = {"authorization": "Bearer secret"}
            assert (await client.post("/rerank", json=body, headers=good)).status_code == 200
            assert (await client.get("/status")).status_code == 401
            assert (await client.get("/health")).status_code == 200
    finally:
        await engine.stop()


async def test_serving_off_answers_503_with_the_reason(make_scorer: Any) -> None:
    cfg = WorkerSettings()
    engine = Engine(make_scorer(), cfg)
    off = Capabilities(False, False, False, "float16", {"serve": "no kernels for sm_70"})
    app = create_worker_app(engine, cfg, HW, off, MANIFEST)
    await engine.start(warm=False)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://worker"
        ) as client:
            r = await client.post("/rerank", json={"query": "q", "documents": ["a"]})
            assert r.status_code == 503
            assert "sm_70" in r.json()["detail"]
    finally:
        await engine.stop()


async def test_the_daemons_http_reranker_talks_to_it(
    worker: tuple[httpx.AsyncClient, Engine, Any],
) -> None:
    """The contract with the daemon (and any other client) is the standard API, nothing more."""
    client, _, _ = worker
    adapter = HttpReranker("http://worker", "m", transport=client._transport)  # pyright: ignore[reportPrivateUsage]
    scores = await adapter.rerank("q", ["a", "bbbb", "cc"])
    assert scores[1] > scores[2] > scores[0]
