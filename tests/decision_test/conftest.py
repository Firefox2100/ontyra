"""Fixtures of the decision-model benchmark. Live tests need `BENCH_LIVE=1` and a reachable
decision model (`framework/config.py`); everything else runs offline."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from benchmark_test.framework.config import BenchSettings, Profile
from decision_test.framework.client import Kev
from decision_test.framework.config import DecisionBenchSettings
from decision_test.framework.results import write_result
from ontyra.adapters.decider import SystemOneDecider
from ontyra.app import Application
from ontyra.calibration.models import World, load_world
from ontyra.calibration.world import populate
from ontyra.config import Settings
from ontyra.models import PolicyConfig
from ontyra.services import Caller
from ontyra.storage import Database, SchemaManager

STAMP = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


@pytest.fixture(scope="session")
def settings() -> DecisionBenchSettings:
    return DecisionBenchSettings()


@pytest.fixture(scope="session")
def profile() -> Profile:
    """The embedder (and recall settings) under the live tasks: the benchmark's first profile."""
    return BenchSettings().profile_list()[0]


@pytest.fixture
def kev(settings: DecisionBenchSettings) -> Kev:
    if not settings.live:
        pytest.skip("model-driven suite: set BENCH_LIVE=1 (and BENCH_DECISION_URL if not :8083)")
    return Kev(
        settings.url,
        settings.model,
        api_key=settings.api_key,
        timeout=settings.timeout,
        concurrency=settings.concurrency,
    )


@pytest.fixture
def production(settings: DecisionBenchSettings) -> Callable[..., SystemOneDecider]:
    """The daemon's own adapter, as `ONTYRA_DECISION__*` would configure it."""

    def build(**kwargs: Any) -> SystemOneDecider:
        return SystemOneDecider(
            settings.url,
            settings.model,
            api_key=settings.api_key,
            timeout=settings.timeout,
            **kwargs,
        )

    return build


@pytest.fixture
async def world_app(profile: Profile) -> AsyncIterator[Callable[[World], Any]]:
    """`await world_app(world)` -> (application, agent caller, memory id -> key)."""
    opened: list[Database] = []

    async def build(world: World) -> tuple[Application, Caller, dict[str, str]]:
        embedding = profile.embedding()
        db = await Database.connect("mem://", namespace="decision", database="d" + uuid4().hex[:10])
        opened.append(db)
        await SchemaManager(db, embedding_dim=embedding.dimension).apply()
        app = Application.from_database(
            db,
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                policy=PolicyConfig(default="auto_commit"),
                embedding=embedding,
                rerank=profile.rerank(),
                recall=profile.recall_settings(),
            ),
        )
        await app.actors.ensure_owner()
        human = await app.actors.caller(kind="human", name="owner")
        agent = await app.actors.caller(kind="agent", name="claude", harness="bench")
        return app, agent, await populate(app, human, agent, world)

    yield build
    for db in opened:
        await db.close()


@pytest.fixture
def worlds() -> Callable[[str], World]:
    return load_world


@pytest.fixture
def recorder(settings: DecisionBenchSettings, kev: Kev, profile: Profile) -> Callable[..., Path]:
    def record(task: str, body: dict[str, Any], meta: dict[str, Any] | None = None) -> Path:
        info = {
            "decision_model": kev.model,
            "decision_url_host": kev.url.split("//")[-1].split("/")[0],
            "embedder": profile.embedding().name or profile.embedding().provider,
            "profile": profile.name,
            "recall_overrides": profile.recall,
            **(meta or {}),
        }
        directory = Path(settings.results_dir) if settings.results_dir else None
        return write_result(task, settings.label or STAMP, info, body, directory)

    return record
