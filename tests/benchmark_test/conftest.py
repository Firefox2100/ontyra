"""Benchmark fixtures: an embedded database, an application with the embedder under test, a
loaded world, and a recorder that writes one immutable result file per suite and run.

Everything about the run is chosen by `BENCH_*` environment variables (see
`framework/config.py`); with none set it runs against the built-in hash embedder.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from benchmark_test.framework.config import BenchSettings, Profile
from benchmark_test.framework.models import Case, World, load_world
from benchmark_test.framework.results import write_result
from benchmark_test.framework.run import load_world as populate
from benchmark_test.framework.score import StepScore
from ontyra.app import Application
from ontyra.config import Settings
from ontyra.models import PolicyConfig
from ontyra.services import Caller
from ontyra.storage import Database, SchemaManager

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


class Clock:
    """Deterministic clock: one second per call, starting at NOW."""

    def __init__(self) -> None:
        self._t = NOW

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


STAMP = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _profiles() -> list[Profile]:
    return BenchSettings().profile_list()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Every test that asks for `profile` runs once per configured profile."""
    if "profile" in metafunc.fixturenames:
        profiles = _profiles()
        metafunc.parametrize("profile", profiles, ids=[p.name for p in profiles])


@pytest.fixture(scope="session")
def bench() -> BenchSettings:
    return BenchSettings()


@pytest.fixture
async def world_app(profile: Profile) -> AsyncIterator[Callable[[World], Any]]:
    """`await world_app(world)` → (application, caller, memory-id → key map), fresh each time."""
    opened: list[Database] = []

    async def build(world: World) -> tuple[Application, Caller, dict[str, str]]:
        embedding = profile.embedding()
        db = await Database.connect("mem://", namespace="bench", database="b" + uuid4().hex[:10])
        opened.append(db)
        await SchemaManager(db, embedding_dim=embedding.dimension).apply()
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            policy=PolicyConfig(default="auto_commit"),
            embedding=embedding,
            rerank=profile.rerank(),
            decision=profile.decision_settings(),
            recall=profile.recall_settings(),
        )
        app = Application.from_database(db, settings, now=Clock())
        await app.actors.ensure_owner()
        human = await app.actors.caller(kind="human", name="owner")
        agent = await app.actors.caller(kind="agent", name="claude", harness="bench")
        keys = await populate(app, human, agent, world)
        return app, agent, keys

    yield build
    for db in opened:
        await db.close()


@pytest.fixture
def recorder(bench: BenchSettings, profile: Profile) -> Callable[..., Path]:
    """Writes `<suite>-<label>-<profile>.json`; a label already used is refused."""

    def record(
        suite: str,
        outcomes: list[tuple[Case, list[StepScore]]],
        domains: dict[str, str] | None = None,
    ) -> Path:
        embedding = profile.embedding()
        meta = {
            "profile": profile.name,
            "embedding": embedding.model_dump(exclude={"kwargs"}),
            "embedding_kwargs": sorted(embedding.kwargs),
            "rerank": profile.rerank().model_dump(exclude={"kwargs"}),
            "decision": profile.decision_settings().model_dump(exclude={"kwargs", "api_key"}),
            "recall_overrides": profile.recall,
            "recall": profile.recall_settings().model_dump(),
        }
        directory = Path(bench.results_dir) if bench.results_dir else None
        return write_result(
            suite, bench.run_label(profile, STAMP), meta, outcomes, directory, domains
        )

    return record


@pytest.fixture
def worlds() -> Callable[[str], World]:
    return load_world
