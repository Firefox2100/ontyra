"""Fixtures of the model-driven suites. Live tests need `BENCH_LIVE=1` and a reachable model
(see `framework/config.py`); everything else runs offline."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from benchmark_test.framework.config import BenchSettings
from evaluation_test.framework.config import EvalSettings
from evaluation_test.framework.llm import Chat, LangChainChat
from evaluation_test.framework.results import write_result
from ontyra.app import Application
from ontyra.calibration.models import World, load_world
from ontyra.calibration.world import populate
from ontyra.config import Settings
from ontyra.models import PolicyConfig
from ontyra.services import Caller
from ontyra.storage import Database, SchemaManager

STAMP = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


@pytest.fixture(scope="session")
def settings() -> EvalSettings:
    return EvalSettings()


@pytest.fixture
def chat(settings: EvalSettings) -> Chat:
    """The model under test (skips the test unless BENCH_LIVE=1)."""
    if not settings.live:
        pytest.skip("model-driven suite: set BENCH_LIVE=1 (and point BENCH_LLM_URL at a model)")
    return LangChainChat(
        url=settings.llm_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
        thinking=settings.llm_thinking,
    )


@pytest.fixture
async def world_app() -> AsyncIterator[Callable[[World], Any]]:
    """`await world_app(world)` -> (application, agent caller, memory id -> key); writes commit.
    The embedder is the benchmark's (hash unless `BENCH_EMBED_*` says otherwise)."""
    opened: list[Database] = []

    async def build(world: World) -> tuple[Application, Caller, dict[str, str]]:
        profile = BenchSettings().profile_list()[0]
        embedding = profile.embedding()
        db = await Database.connect("mem://", namespace="eval", database="e" + uuid4().hex[:10])
        opened.append(db)
        await SchemaManager(db, embedding_dim=embedding.dimension).apply()
        app = Application.from_database(
            db,
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                policy=PolicyConfig(default="auto_commit"),
                embedding=embedding,
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
def recorder(settings: EvalSettings) -> Callable[..., Path]:
    def record(area: str, rows: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> Path:
        directory = Path(settings.results_dir) if settings.results_dir else None
        label = f"{settings.label or STAMP}-{settings.llm_model}"
        info = {
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "thinking": settings.llm_thinking,
            **(meta or {}),
        }
        return write_result(area, label, info, rows, directory)

    return record
