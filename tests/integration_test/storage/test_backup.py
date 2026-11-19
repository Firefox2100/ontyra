"""Export a database to SurrealQL text and import it into an empty one (server mode)."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from ontyra.storage import Database, SchemaManager, backup
from ontyra.storage.errors import StorageError


@pytest.fixture
async def server(surreal_server_url: str) -> str:
    return surreal_server_url


async def test_an_export_restores_into_an_empty_database(server: str, tmp_path: Path) -> None:
    creds: dict[str, Any] = {"username": "root", "password": "root", "namespace": "test"}
    source, target = "s" + uuid4().hex[:10], "t" + uuid4().hex[:10]
    db = await Database.connect(
        server, namespace="test", database=source, username="root", password="root"
    )
    try:
        await SchemaManager(db, embedding_dim=256).apply()
        await db.query("CREATE actor:a SET kind = 'human', name = 'alice'")
    finally:
        await db.close()
    text = await backup.export_sql(server, database=source, **creds)
    assert "DEFINE TABLE actor" in text and "alice" in text
    file = tmp_path / "x" / "ontyra-20260101-000000.surql"
    backup.write_private(file, text)
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    await backup.import_sql(server, file.read_text("utf-8"), database=target, **creds)
    copy = await Database.connect(
        server, namespace="test", database=target, username="root", password="root"
    )
    try:
        rows = (await copy.query("SELECT name FROM actor"))[0]
        assert [r["name"] for r in rows] == ["alice"]
    finally:
        await copy.close()


async def test_export_needs_a_server() -> None:
    with pytest.raises(StorageError, match="embedded"):
        await backup.export_sql("mem://", namespace="n", database="d", username="u", password="p")


def test_prune_keeps_the_newest(tmp_path: Path) -> None:
    for day in range(1, 6):
        (tmp_path / f"ontyra-2026010{day}-000000.surql").write_text("x")
    (tmp_path / "notes.txt").write_text("keep me")
    removed = backup.prune(tmp_path, 2)
    assert [p.name for p in removed] == [f"ontyra-2026010{d}-000000.surql" for d in (1, 2, 3)]
    assert (tmp_path / "notes.txt").exists()
    assert len(list(tmp_path.glob("ontyra-*.surql"))) == 2
