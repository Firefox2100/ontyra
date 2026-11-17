"""Composition root: the only module that wires concrete implementations together."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI

from ontyra.adapters import build_decider, build_embedder, build_reranker
from ontyra.config import Settings, load_settings
from ontyra.ports import Embedder
from ontyra.services import (
    ActorService,
    AnchorService,
    ExplorationService,
    FeedbackService,
    IndexService,
    MemoryService,
    OntologyChangeService,
    OntologyService,
    PolicyEngine,
    RecallService,
    RerankTrainingService,
)
from ontyra.services.recall_fusion import FusionRecallService
from ontyra.storage import Database, SchemaManager, SurrealStore
from ontyra.transports.rest import create_app


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Application:
    """The wired object graph a transport serves."""

    settings: Settings
    db: Database
    actors: ActorService
    memory: MemoryService
    ontology: OntologyService
    ontology_changes: OntologyChangeService
    anchors: AnchorService
    indexer: IndexService
    recall: RecallService
    explore: ExplorationService
    feedback: FeedbackService
    reranking: RerankTrainingService
    embedder: Embedder | None

    @classmethod
    def from_database(
        cls,
        db: Database,
        settings: Settings,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> Application:
        store = SurrealStore(db)
        embedder = build_embedder(settings.embedding)
        reranker = build_reranker(settings.rerank)
        ontology = OntologyService(store, now)
        anchors = AnchorService(store, now, ontology, settings.recall.machine)
        indexer = IndexService(store, embedder, now)
        memory = MemoryService(
            store, PolicyEngine(settings.policy), now, ontology, anchors, on_commit=indexer.embed
        )
        decider = build_decider(settings.decision)
        recall: RecallService
        if settings.recall.engine == "fusion":
            recall = FusionRecallService(
                store,
                ontology,
                anchors,
                settings.recall,
                now,
                embedder=embedder,
                reranker=reranker,
                rerank_top_n=settings.rerank.top_n,
                decider=decider,
            )
        elif decider is not None:
            raise ValueError("a decision model needs ONTYRA_RECALL__ENGINE=fusion")
        else:
            recall = RecallService(
                store,
                ontology,
                anchors,
                settings.recall,
                now,
                embedder=embedder,
                reranker=reranker,
                rerank_top_n=settings.rerank.top_n,
            )
        feedback = FeedbackService(store, now, settings.rerank.feedback_ttl_days)
        return cls(
            settings=settings,
            db=db,
            actors=ActorService(store, now, settings.actor_trust),
            memory=memory,
            ontology=ontology,
            ontology_changes=OntologyChangeService(
                store, ontology, now, settings.ontology.auto_apply
            ),
            anchors=anchors,
            indexer=indexer,
            recall=recall,
            explore=ExplorationService(store, memory, ontology, recall, now),
            feedback=feedback,
            reranking=RerankTrainingService(
                store, feedback, reranker, now, settings.rerank.min_examples
            ),
            embedder=embedder,
        )

    def schema(self) -> SchemaManager:
        """The schema manager matching this configuration (vector index dimension)."""
        return SchemaManager(
            self.db, embedding_dim=self.embedder.dimension if self.embedder else None
        )

    async def close(self) -> None:
        await self.db.close()


async def create_application(
    settings: Settings, *, apply_schema: bool = True, now: Callable[[], datetime] = utc_now
) -> Application:
    """Connect, make sure the schema and the global owner exist, and wire the services."""
    db = await Database.connect(
        settings.db.url,
        namespace=settings.db.namespace,
        database=settings.db.database,
        username=settings.db.username,
        password=settings.db.password.get_secret_value(),
        timeout=settings.db.timeout,
    )
    app = Application.from_database(db, settings, now=now)
    if apply_schema:
        await app.schema().apply()
    await app.actors.ensure_owner()
    return app


@asynccontextmanager
async def open_application(settings: Settings) -> AsyncGenerator[Application]:
    application = await create_application(settings)
    try:
        yield application
    finally:
        await application.close()


def create_rest_app(settings: Settings | None = None) -> FastAPI:
    """The REST daemon as an ASGI app (`uvicorn ontyra.app:create_rest_app --factory`)."""
    cfg = settings or load_settings()
    holder: dict[str, Any] = {}
    mounts: dict[str, Any] = {}
    if cfg.mcp.enabled:
        from ontyra.transports.mcp import create_mcp_app

        mounts[cfg.mcp.path] = create_mcp_app(
            lambda: holder["app"].state.services, profile=cfg.mcp.profile
        )
    routers: list[APIRouter] = []
    if cfg.ui.enabled:
        from ontyra.transports.web import ROUTERS

        routers = ROUTERS
    app = create_app(
        open_services=lambda: open_application(cfg),
        mounts=mounts,
        extra_routers=routers,
        ui_actor=cfg.ui.actor,
    )
    holder["app"] = app
    return app
