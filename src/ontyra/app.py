"""Composition root: the only module that wires concrete implementations together."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI

from ontyra.adapters import build_decider, build_embedder, build_reranker
from ontyra.config import Settings, load_settings
from ontyra.models import RecordId
from ontyra.models.feedback import GateRequest, RerankExample, TrainingPolicy
from ontyra.ports import Embedder, MessageClassifier, StatementComparer
from ontyra.services import (
    ActorService,
    AnchorService,
    AuthService,
    ConsolidationService,
    ExplorationService,
    FeedbackReviewService,
    FeedbackService,
    IndexService,
    MemoryService,
    OntologyChangeService,
    OntologyService,
    PolicyEngine,
    RecallService,
    RerankTrainingService,
    SourceService,
    TaskService,
)
from ontyra.services.backups import BackupService
from ontyra.services.maintenance import Maintenance
from ontyra.services.ontology_check import OntologyChecker
from ontyra.services.recall_fusion import FusionRecallService
from ontyra.services.scorelayer import ScoreLayer
from ontyra.storage import Database, SchemaManager, SurrealStore, backup
from ontyra.transports.rest import create_app


def utc_now() -> datetime:
    return datetime.now(UTC)


log = logging.getLogger(__name__)


@dataclass
class Application:
    """The wired object graph a transport serves."""

    settings: Settings
    db: Database
    actors: ActorService
    auth: AuthService
    memory: MemoryService
    ontology: OntologyService
    ontology_changes: OntologyChangeService
    ontology_check: OntologyChecker
    anchors: AnchorService
    indexer: IndexService
    recall: RecallService
    explore: ExplorationService
    feedback: FeedbackService
    feedback_review: FeedbackReviewService
    sources: SourceService
    consolidation: ConsolidationService
    tasks: TaskService
    maintenance: Maintenance
    backups: BackupService | None
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
            store,
            PolicyEngine(settings.policy),
            now,
            ontology,
            anchors,
            on_commit=indexer.embed,
            episodes=settings.episodes,
        )
        decider = build_decider(settings.decision)
        if settings.recall.filler_gate and not isinstance(decider, MessageClassifier):
            log.warning(
                "ONTYRA_RECALL__FILLER_GATE is on but no decision model that can classify "
                "messages is configured (ONTYRA_DECISION__*): the gate does nothing"
            )
        feedback = FeedbackService(store, now, settings.rerank.feedback_ttl_days)
        layer: ScoreLayer | None = None
        if settings.learning.enabled:
            if settings.recall.engine != "fusion":
                raise ValueError("ONTYRA_LEARNING__ENABLED needs ONTYRA_RECALL__ENGINE=fusion")
            layer = ScoreLayer(store, feedback.active, settings.learning, now)
            feedback.on_change = layer.invalidate
            feedback.layer = layer
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
                layer=layer,
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
        comparer = decider if isinstance(decider, StatementComparer) else None
        consolidation = ConsolidationService(store, embedder, settings.consolidation, comparer)
        tasks = TaskService(
            store,
            now,
            consolidation,
            memory,
            settings.tasks.expiry_warn_days,
            ontology,
            settings.tasks.refine_cap,
        )
        actors = ActorService(store, now, settings.actor_trust)
        token = settings.auth.token
        admin_token = settings.auth.admin_token
        reranking = RerankTrainingService(
            store,
            feedback,
            reranker,
            now,
            TrainingPolicy(
                min_examples=settings.rerank.min_examples,
                pool=settings.rerank.pool,
                train_sources=settings.rerank.train_sources,
                source_cap=settings.rerank.source_cap,
                anchor_ratio=settings.rerank.anchor_ratio,
                gate_tolerance=settings.rerank.gate_tolerance,
                live=settings.rerank.live,
                live_min_examples=settings.rerank.live_min_examples,
                live_every_seconds=settings.rerank.live_every_seconds,
                live_max_distance=settings.rerank.live_max_distance,
            ),
            _anchors if settings.rerank.provider == "worker" else None,
        )
        previous = feedback.on_change

        def feedback_changed(owner: RecordId | None) -> None:
            if previous is not None:
                previous(owner)
            reranking.mark_stale()

        feedback.on_change = feedback_changed
        return cls(
            settings=settings,
            db=db,
            actors=actors,
            auth=AuthService(
                store,
                actors,
                now,
                multi_user=settings.auth.multi_user,
                token=token.get_secret_value() if token else None,
                admin_token=admin_token.get_secret_value() if admin_token else None,
                single_user_kind=settings.auth.single_user_kind,
            ),
            memory=memory,
            ontology=ontology,
            ontology_changes=OntologyChangeService(
                store, ontology, now, settings.ontology.auto_apply
            ),
            ontology_check=OntologyChecker(ontology),
            anchors=anchors,
            indexer=indexer,
            recall=recall,
            explore=ExplorationService(store, memory, ontology, recall, now),
            feedback=feedback,
            feedback_review=FeedbackReviewService(store, feedback, now),
            sources=SourceService(store, now, settings.recall.machine),
            consolidation=consolidation,
            tasks=tasks,
            maintenance=Maintenance(
                actors, tasks, store, settings.tasks, ontology=ontology, indexer=indexer
            ),
            backups=_backups(db, settings, now),
            reranking=reranking,
            embedder=embedder,
        )

    def schema(self) -> SchemaManager:
        """The schema manager matching this configuration (vector index dimension)."""
        return SchemaManager(
            self.db, embedding_dim=self.embedder.dimension if self.embedder else None
        )

    async def close(self) -> None:
        self.reranking.close()
        await self.db.close()


def _anchors() -> tuple[list[RerankExample], list[GateRequest]]:
    """The clean benchmark pairs and the trusted requests a retrained reranker is judged on."""
    from ontyra.calibration import anchors

    return (
        [RerankExample(source="anchor", **e) for e in anchors.anchor_examples()],
        [
            GateRequest.model_validate({"query": r["query"], "candidates": r["candidates"]})
            for r in anchors.gate_requests()
        ],
    )


def _backups(db: Database, settings: Settings, now: Callable[[], datetime]) -> BackupService | None:
    """The scheduled export, when a directory is configured and the database is a server."""
    cfg = settings.backup
    if not cfg.dir:
        return None
    if cfg.every_hours <= 0 or not db.capabilities.export_import:
        log.warning(
            "ONTYRA_BACKUP__DIR is set but scheduled exports are off: they need a SurrealDB "
            "server (not an embedded database) and ONTYRA_BACKUP__EVERY_HOURS above 0"
        )
        return None
    db_cfg = settings.db

    async def export() -> str:
        return await backup.export_sql(
            db_cfg.url,
            namespace=db_cfg.namespace,
            database=db_cfg.database,
            username=db_cfg.username,
            password=db_cfg.password.get_secret_value(),
        )

    return BackupService(
        export, Path(cfg.dir), every_hours=cfg.every_hours, keep=max(1, cfg.keep), now=now
    )


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
    await app.auth.bootstrap()
    return app


@asynccontextmanager
async def open_application(settings: Settings) -> AsyncGenerator[Application]:
    application = await create_application(settings)
    scheduled: list[asyncio.Task[None]] = []
    if settings.tasks.detect_every_hours > 0:
        scheduled.append(asyncio.create_task(application.maintenance.run_forever()))
    if application.backups is not None:
        scheduled.append(asyncio.create_task(application.backups.run_forever()))
    try:
        yield application
    finally:
        for task in scheduled:
            task.cancel()
        await asyncio.gather(*scheduled, return_exceptions=True)
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
        ui_session_hours=cfg.auth.ui_session_hours,
        ui_cookie_secure=cfg.ui_cookie_secure,
        ui_allowed_origins=cfg.auth.ui_allowed_origins,
        max_body_bytes=cfg.server.max_body_bytes,
    )
    holder["app"] = app
    return app
