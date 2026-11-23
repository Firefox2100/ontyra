"""Reranker implementations: hosted (cannot be retrained) and the reranker worker (can be)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ontyra.models.feedback import (
    GateRequest,
    LiveResult,
    RerankExample,
    TrainOptions,
    TrainResult,
)


class LangChainReranker:
    """Wraps a LangChain `BaseDocumentCompressor` that scores documents (cross-encoders,
    hosted rerank APIs). The compressor returns documents with a `relevance_score` in their
    metadata; documents it drops get score 0. Not retrainable."""

    def __init__(self, compressor: Any) -> None:
        self._compressor = compressor

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        from langchain_core.documents import Document

        docs = [Document(page_content=t, metadata={"i": i}) for i, t in enumerate(texts)]
        kept = await self._compressor.acompress_documents(docs, query)
        scores = [0.0] * len(texts)
        for doc in kept:
            scores[int(doc.metadata["i"])] = float(doc.metadata.get("relevance_score", 1.0))
        return scores


class HttpReranker:
    """A rerank API in the Jina/Cohere/LocalAI shape: `POST {url}/rerank` with a model, a query
    and documents; the answer lists `{index, relevance_score}`. Scores may be raw logits.
    Hosted, so it cannot be retrained here."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: Any = None,
    ) -> None:
        self._url = url.rstrip("/") + "/rerank"
        self._model = model
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._transport = transport  # tests inject an httpx transport

    @property
    def name(self) -> str:
        return f"http:{self._model}"

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        import httpx

        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(
                self._url,
                headers=self._headers,
                json={
                    "model": self._model,
                    "query": query,
                    "documents": list(texts),
                    "top_n": len(texts),
                },
            )
            response.raise_for_status()
        scores = [0.0] * len(texts)
        for item in response.json()["results"]:
            scores[int(item["index"])] = float(item["relevance_score"])
        return scores


class WorkerReranker(HttpReranker):
    """The reranker worker (`ontyra worker`): the same `/rerank` API, plus its admin routes, so
    it can be retrained. Training and activation take the worker's model for exclusive use, and
    while that lasts `rerank` is refused (recall falls back to its plain ranking)."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
        train_timeout: float = 3600.0,
        transport: Any = None,
    ) -> None:
        super().__init__(url, model, api_key=api_key, timeout=timeout, transport=transport)
        self._admin = url.rstrip("/").removesuffix("/v1") + "/admin"
        self._train_timeout = train_timeout
        self._version = "unknown"
        self._versions: list[str] = []

    @property
    def name(self) -> str:
        return f"worker:{self._model}"

    @property
    def version(self) -> str:
        return self._version

    def versions(self) -> list[str]:
        return list(self._versions)

    async def _call(self, method: str, path: str, *, limit: float, body: Any = None) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=limit, transport=self._transport) as client:
            response = await client.request(
                method, f"{self._admin}{path}", headers=self._headers, json=body
            )
            if response.status_code >= 400:
                detail = response.json().get("detail", response.text) if response.content else ""
                raise ValueError(f"the reranker worker refused: {detail}")
            return response.json()

    async def refresh(self) -> None:
        """Read the worker's active version and its versions."""
        got = await self._call("GET", "/versions", limit=self._timeout)
        self._version = got["active"]
        self._versions = [v["version"] for v in got["versions"]]

    async def train(
        self,
        examples: Sequence[RerankExample],
        options: TrainOptions,
        gate: Sequence[GateRequest] = (),
    ) -> TrainResult:
        body = {
            "examples": [
                {"query": e.query, "text": e.text, "relevance": e.relevance, "weight": e.weight}
                for e in examples
            ],
            "gate": [g.model_dump(mode="json") for g in gate],
            "options": {
                "epochs": options.epochs,
                "learning_rate": options.learning_rate,
                "batch_size": options.batch_size,
                "lora_rank": options.lora_rank,
                "head_only": options.head_only,
                "validation_fraction": options.validation_fraction,
                "seed": options.seed,
            },
        }
        got = await self._call("POST", "/train", limit=self._train_timeout, body=body)
        await self.refresh()
        return TrainResult(
            version=got["version"], examples=len(examples), metrics=got["metrics"], activated=False
        )

    async def activate(self, version: str) -> None:
        await self._call("POST", "/activate", limit=self._train_timeout, body={"version": version})
        await self.refresh()

    async def live_status(self) -> dict[str, Any]:
        return await self._call("GET", "/live", limit=self._timeout)

    async def live_update(
        self,
        examples: Sequence[RerankExample],
        gate: Sequence[GateRequest],
        *,
        max_distance: float,
        tolerance: float,
    ) -> LiveResult:
        body = {
            "examples": [
                {"query": e.query, "text": e.text, "relevance": e.relevance, "weight": e.weight}
                for e in examples
            ],
            "gate": [g.model_dump(mode="json") for g in gate],
            "options": {"max_distance": max_distance, "tolerance": tolerance},
        }
        got = await self._call("POST", "/live", limit=self._train_timeout, body=body)
        metrics = {k: got[k] for k in ("loss", "gate_before", "gate_after") if k in got}
        return LiveResult(
            applied=bool(got["applied"]),
            reverted=bool(got["reverted"]),
            distance=float(got["distance"]),
            examples=int(got["examples"]),
            metrics=metrics,
        )

    async def live_reset(self) -> None:
        await self._call("POST", "/live/reset", limit=self._timeout)
