"""Integration-test fixtures: a real SurrealDB, in embedded mode and in server mode.

Embedded (`mem://`) always runs. Server mode starts SurrealDB with testcontainers, using the
image pinned in `compose.dev.yaml`; it is skipped when Docker is unavailable. Every test gets
its own fresh database, so tests are independent and can run in any order.
"""

import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from ontyra.app import Application
from ontyra.config import Settings
from ontyra.models import PolicyConfig, RecordId
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
from ontyra.services import Caller
from ontyra.storage import Database, SchemaManager
from ontyra.transports.rest import create_app

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
    manager = SchemaManager(database, embedding_dim=256)  # the default hash embedder
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


class TickClock:
    """Deterministic clock: every call is one second after the previous one."""

    def __init__(self, start: datetime = NOW) -> None:
        self._t = start

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


@pytest.fixture
def clock() -> TickClock:
    return TickClock()


@pytest.fixture
async def make_app(database: Database, schema: SchemaManager, clock: TickClock):
    """Factory for a fully wired `Application` on the test database with a given policy."""

    async def _make(policy: PolicyConfig | None = None) -> Application:
        settings = Settings(_env_file=None, policy=policy or PolicyConfig(default="auto_commit"))  # type: ignore[call-arg]
        app = Application.from_database(database, settings, now=clock)
        await app.actors.ensure_owner()
        return app

    return _make


@pytest.fixture
async def app(make_app) -> Application:
    """Wired application whose policy auto-commits everything (the common test setup)."""
    return await make_app()


@pytest.fixture
async def review_app(make_app) -> Application:
    """Wired application with the safe default policy: everything waits for review."""
    return await make_app(PolicyConfig())


@pytest.fixture
async def agent(app: Application) -> Caller:
    return await app.actors.caller(kind="agent", name="claude", harness="claude-code")


@pytest.fixture
async def human(app: Application) -> Caller:
    return await app.actors.caller(kind="human", name="owner")


def _client(application: Application) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(application))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def client(app: Application) -> AsyncIterator[httpx.AsyncClient]:
    """REST client over the auto-commit application (in-process, no sockets)."""
    async with _client(app) as c:
        yield c


@pytest.fixture
async def review_client(review_app: Application) -> AsyncIterator[httpx.AsyncClient]:
    """REST client over the application with the safe default policy (review everything)."""
    async with _client(review_app) as c:
        yield c


# ---- ontology and memory-writing helpers (shared by the recall tests) --------------------


@pytest.fixture
async def concepts(app: Application, human: Caller) -> dict[str, RecordId]:
    """A small ontology: operating systems (Linux/Windows disjoint), Python (a PEP 440
    versioned qualifier), conda, and testing tools. Keys are the concept slugs."""
    from ontyra.models.ontology import ConceptDefinition, RelationRequest

    async def define(label: str, **kw: object) -> RecordId:
        return (await app.ontology.define(human, ConceptDefinition(label=label, **kw))).id  # type: ignore[arg-type]

    ids = {
        "linux": await define("Linux", scheme="qualifier", aliases=["gnu/linux"]),
        "windows": await define("Windows", scheme="qualifier"),
        "debian_family": await define("Debian family", is_a=["linux"]),
        "ubuntu": await define("Ubuntu", scheme="qualifier", is_a=["debian family"]),
        "python": await define(
            "Python", scheme="qualifier", value_type="version", version_scheme="pep440"
        ),
        "conda": await define("conda", scheme="qualifier"),
        "ram": await define("RAM", scheme="qualifier", value_type="number", dimension="memory"),
        "testing_framework": await define("testing framework"),
        "pytest": await define("pytest", aliases=["py.test"], is_a=["testing framework"]),
        "unittest": await define("unittest", is_a=["testing framework"]),
        "docker": await define("Docker", aliases=["docker compose"]),
    }
    await app.ontology.relate(
        human, RelationRequest(source="linux", relation="disjoint_with", target="windows")
    )
    await app.ontology.relate(
        human, RelationRequest(source="pytest", relation="alternative_to", target="unittest")
    )
    return ids


@pytest.fixture
def remember(app: Application, agent: Caller):
    """`await remember(kind, text, **fields)` → memory id (committed through the real pipeline)."""
    from ontyra.models.proposals import AddMemory, ProposalRequest

    async def _remember(
        kind: str, text: str, *, caller: Caller | None = None, **fields: object
    ) -> RecordId:
        defaults: dict[str, dict[str, object]] = {
            "directive": {"strength": "should", "rule": text},
            "decision": {
                "chosen": text,
                "rationale": "because",
                "decided_at": NOW.isoformat(),
            },
            "procedure": {"trigger": text, "steps": [{"text": text}]},
            "lesson": {"situation": text, "insight": text},
            "event": {"occurred_at": NOW.isoformat()},
            "fact": {
                "subject": str(fields.get("subject", "concept:linux")),
                "predicate": "has",
                "object": text,
            },
        }
        body = {"text": text, **defaults.get(kind, {}), **fields}
        result = await app.memory.submit(
            caller or agent,
            ProposalRequest(ops=[AddMemory(kind=kind, ref="m", fields=body)]),  # type: ignore[arg-type]
        )
        assert result.status == "committed", result.findings
        return result.created["m"]

    return _remember
