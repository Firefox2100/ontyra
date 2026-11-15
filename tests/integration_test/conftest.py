"""Integration-test fixtures: a real SurrealDB, in embedded mode and in server mode.

Embedded (`mem://`) always runs. Server mode starts SurrealDB with testcontainers, using the
image pinned in `compose.dev.yaml`; it is skipped when Docker is unavailable. Every test gets
its own fresh database, so tests are independent and can run in any order.
"""

import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from ontyra.models import RecordId
from ontyra.models.core import Actor, Memory, Owner
from ontyra.models.kinds import (
    DecisionRev,
    DirectiveRev,
    EventRev,
    FactRev,
    LessonRev,
    ProcedureRev,
    Step,
    TaskRev,
)
from ontyra.storage import Database, SchemaManager

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def _surreal_image() -> str:
    """The SurrealDB image pinned in compose.dev.yaml (single source of truth)."""
    text = (REPO_ROOT / "compose.dev.yaml").read_text(encoding="utf-8")
    match = re.search(r"image:\s*(surrealdb/surrealdb:\S+)", text)
    assert match, "compose.dev.yaml must pin a surrealdb/surrealdb image"
    return match.group(1)


@pytest.fixture(scope="session")
def surreal_server_url() -> Iterator[str]:
    """A running SurrealDB server (container), shared by the whole session."""
    try:
        container = (
            DockerContainer(_surreal_image())
            .with_exposed_ports(8000)
            .with_command("start --user root --pass root --bind 0.0.0.0:8000 memory")
        )
        container.waiting_for(LogMessageWaitStrategy("Started web server").with_startup_timeout(60))
        container.start()
    except Exception as exc:
        pytest.skip(f"SurrealDB container unavailable: {exc}")
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(8000)
        yield f"ws://{host}:{port}/rpc"
    finally:
        container.stop()


@pytest.fixture(params=["embedded", "server"])
async def database(request: pytest.FixtureRequest) -> AsyncIterator[Database]:
    """A fresh, empty database in each connection mode (same test, both modes)."""
    name = "t" + uuid4().hex[:12]
    if request.param == "embedded":
        db = await Database.connect("mem://", namespace="test", database=name)
    else:
        url: str = request.getfixturevalue("surreal_server_url")
        db = await Database.connect(
            url, namespace="test", database=name, username="root", password="root"
        )
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def schema(database: Database) -> SchemaManager:
    """The database with the full registered schema applied."""
    manager = SchemaManager(database)
    await manager.apply()
    return manager


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def actor_id() -> RecordId:
    return RecordId("actor", "claude")


@pytest.fixture
async def seeded(database: Database, schema: SchemaManager) -> RecordId:
    """Schema plus an owner, an actor and a memory; returns the memory id."""
    from ontyra.storage.codec import record_id, to_content

    await database.query(
        "CREATE $o CONTENT $co; CREATE $a CONTENT $ca; CREATE $m CONTENT $cm",
        {
            "o": RecordId("owner", "global"),
            "co": to_content(Owner(kind="global", name="global")),
            "a": RecordId("actor", "claude"),
            "ca": to_content(Actor(kind="agent", name="claude", trust=0.8)),
            "m": RecordId("memory", "m1"),
            "cm": to_content(
                Memory(kind="directive", owner=RecordId("owner", "global"), created_at=NOW)  # type: ignore[arg-type]
            ),
        },
    )
    del record_id
    return RecordId("memory", "m1")


@pytest.fixture
def revisions(now: datetime) -> dict[str, object]:
    """One valid revision per memory kind, keyed by kind name."""
    base = {
        "memory": RecordId("memory", "m1"),
        "seq": 1,
        "text": "t",
        "recorded_at": now,
        "proposed_by": RecordId("actor", "claude"),
    }
    return {
        "fact": FactRev(  # type: ignore[arg-type]
            **base,
            subject=RecordId("concept", "pytest"),
            predicate="is_a",
            object="testing framework",
        ),
        "directive": DirectiveRev(**base, strength="must", rule="use pytest, not unittest"),  # type: ignore[arg-type]
        "decision": DecisionRev(  # type: ignore[arg-type]
            **base, chosen="SurrealDB", alternatives=["Postgres"], rationale="graph", decided_at=now
        ),
        "event": EventRev(**base, occurred_at=now, participants=["x"]),  # type: ignore[arg-type]
        "procedure": ProcedureRev(  # type: ignore[arg-type]
            **base, trigger="release", steps=[Step(text="tag"), Step(text="push", optional=True)]
        ),
        "task": TaskRev(**base, due=now),  # type: ignore[arg-type]
        "lesson": LessonRev(  # type: ignore[arg-type]
            **base, situation="s", insight="i", evidence=[RecordId("episode", "e1")]
        ),
    }
