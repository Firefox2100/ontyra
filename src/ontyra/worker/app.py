"""The worker's HTTP API: the standard rerank endpoint (Jina/Cohere/LocalAI shape) and a status."""

from __future__ import annotations

import hmac
import math
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ontyra.config import WorkerSettings
from ontyra.worker import live, training
from ontyra.worker.admin import Admin, TrainingRefused
from ontyra.worker.engine import Busy, Engine
from ontyra.worker.probe import Capabilities, Hardware
from ontyra.worker.versions import Manifest

MAX_DOCUMENTS = 1000


class Document(BaseModel):
    text: str


class RerankRequest(BaseModel):
    """`POST /rerank` and `/v1/rerank`, as Jina, Cohere and LocalAI accept it."""

    model: str | None = None  # one model is served: any name is accepted
    query: str
    documents: Annotated[list[str | Document], Field(min_length=1, max_length=MAX_DOCUMENTS)]
    top_n: Annotated[int | None, Field(ge=1)] = None
    return_documents: bool = False


class ExampleIn(BaseModel):
    query: str
    text: str
    relevance: float = Field(ge=0.0, le=1.0)
    weight: float = Field(default=1.0, ge=0.0)


class GateCandidate(BaseModel):
    text: str
    relevant: bool


class GateRequest(BaseModel):
    query: str
    candidates: list[GateCandidate]


class OptionsIn(BaseModel):
    epochs: int = Field(default=4, ge=1, le=50)
    learning_rate: float = Field(default=1e-4, gt=0)
    batch_size: int = Field(default=8, ge=1, le=64)
    lora_rank: int = Field(default=8, ge=1, le=128)
    lora_alpha: int = Field(default=16, ge=1)
    head_only: bool = False
    validation_fraction: float = Field(default=0.15, ge=0.0, le=0.5)
    seed: int = 13


class TrainRequest(BaseModel):
    examples: list[ExampleIn] = Field(min_length=2)
    gate: list[GateRequest] = []
    options: OptionsIn = OptionsIn()


class LiveOptionsIn(BaseModel):
    steps: int = Field(default=200, ge=1, le=2000)
    learning_rate: float = Field(default=0.05, gt=0)
    l2: float = Field(default=1.0, ge=0)
    max_distance: float = Field(default=0.25, gt=0, le=1.0)
    tolerance: float = Field(default=0.01, ge=0)
    seed: int = 13


class LiveRequest(BaseModel):
    examples: list[ExampleIn] = []
    gate: list[GateRequest] = []
    options: LiveOptionsIn = LiveOptionsIn()


class ActivateRequest(BaseModel):
    version: str


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def create_worker_app(
    engine: Engine,
    cfg: WorkerSettings,
    hardware: Hardware,
    capabilities: Capabilities,
    manifest: Manifest,
    *,
    warm: bool = True,
    admin: Admin | None = None,
) -> FastAPI:
    token = cfg.token.get_secret_value() if cfg.token else None

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if token is None:
            return
        given = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "a bearer token is required")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await engine.start(warm=warm and capabilities.serve)
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(title="Ontyra reranker worker", lifespan=lifespan)

    @app.exception_handler(Busy)
    async def busy(_: Request, error: Busy) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=503, headers={"Retry-After": "1"})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", dependencies=[Depends(authorize)])
    async def status() -> dict[str, Any]:
        c = engine.counters
        return {
            "model": cfg.model,
            "version": (admin.manifest if admin else manifest).version,
            "architecture": manifest.architecture,
            "state": engine.state,
            "leased": engine.leased,
            "queued": engine.queued,
            "idle_seconds": round(engine.idle_seconds, 1),
            "device": {
                "device": hardware.device,
                "name": hardware.name,
                "capability": hardware.capability,
                "total_mib": hardware.total_bytes and hardware.total_bytes >> 20,
                "free_mib": hardware.free_bytes and hardware.free_bytes >> 20,
                "problems": list(hardware.problems),
            },
            "capabilities": {
                "serve": capabilities.serve,
                "train": capabilities.train,
                "live_edit": capabilities.live_edit,
                "dtype": capabilities.dtype,
                "reasons": capabilities.reasons,
            },
            "counters": {
                "requests": c.requests,
                "pairs": c.pairs,
                "batches": c.batches,
                "refused": c.refused,
                "failed": c.failed,
                "loads": c.loads,
                "offloads": c.offloads,
                "releases": c.releases,
            },
        }

    async def rerank(body: RerankRequest) -> dict[str, Any]:
        if not capabilities.serve:
            raise HTTPException(503, capabilities.reasons.get("serve", "serving is off"))
        texts = [d if isinstance(d, str) else d.text for d in body.documents]
        scored = await engine.score(body.query, texts)
        order = sorted(range(len(texts)), key=lambda i: scored.logits[i], reverse=True)
        results: list[dict[str, Any]] = []
        for i in order[: body.top_n or len(texts)]:
            item: dict[str, Any] = {"index": i, "relevance_score": sigmoid(scored.logits[i])}
            if body.return_documents:
                item["document"] = {"text": texts[i]}
            results.append(item)
        return {
            "id": uuid.uuid4().hex,
            "model": body.model or cfg.model,
            "usage": {"total_tokens": scored.tokens},
            "results": results,
        }

    if admin is not None:

        @app.get("/admin/versions", dependencies=[Depends(authorize)])
        async def versions() -> dict[str, Any]:
            return admin.versions()

        @app.post("/admin/train", dependencies=[Depends(authorize)])
        async def train(body: TrainRequest) -> dict[str, Any]:
            try:
                return await admin.train(
                    [
                        training.Example(e.query, e.text, e.relevance, e.weight)
                        for e in body.examples
                    ],
                    [g.model_dump() for g in body.gate],
                    training.Options(**body.options.model_dump()),
                )
            except TrainingRefused as error:
                raise HTTPException(409, str(error)) from error
            except ValueError as error:
                raise HTTPException(422, str(error)) from error

        @app.get("/admin/live", dependencies=[Depends(authorize)])
        async def live_status() -> dict[str, Any]:
            return admin.live_status()

        @app.post("/admin/live", dependencies=[Depends(authorize)])
        async def live_update(body: LiveRequest) -> dict[str, Any]:
            try:
                return await admin.live_update(
                    [
                        training.Example(e.query, e.text, e.relevance, e.weight)
                        for e in body.examples
                    ],
                    [g.model_dump() for g in body.gate],
                    live.LiveOptions(**body.options.model_dump()),
                )
            except TrainingRefused as error:
                raise HTTPException(409, str(error)) from error
            except ValueError as error:
                raise HTTPException(422, str(error)) from error

        @app.post("/admin/live/reset", dependencies=[Depends(authorize)])
        async def live_reset() -> dict[str, Any]:
            return await admin.live_reset()

        @app.post("/admin/activate", dependencies=[Depends(authorize)])
        async def activate(body: ActivateRequest) -> dict[str, Any]:
            try:
                manifest_now = await admin.activate(body.version)
            except (KeyError, FileNotFoundError) as error:
                raise HTTPException(404, f"no version {body.version} of this model") from error
            return {"version": manifest_now.version}

    for path in ("/rerank", "/v1/rerank"):
        app.post(path, dependencies=[Depends(authorize)])(rerank)
    return app
