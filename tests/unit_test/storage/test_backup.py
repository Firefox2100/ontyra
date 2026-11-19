"""The backup module: URL mapping, private files, retention and the server's HTTP contract.

`export`/`import` talk to SurrealDB's HTTP endpoints with a namespace/database pair in headers and
basic auth, so the tests drive them over a fake client and pin what the daemon sends: the mapped
scheme, the headers, the error messages, and the refusal of an import that has a failing statement.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from ontyra.storage import backup, errors


class TestHttpBase:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("ws://localhost:8000/rpc", "http://localhost:8000"),
            ("wss://db.example:443/rpc", "https://db.example:443"),
            ("http://localhost:8000", "http://localhost:8000"),
            ("https://db.example/rpc", "https://db.example"),
        ],
    )
    def test_schemes_map_to_http(self, url: str, expected: str) -> None:
        assert backup.http_base(url) == expected

    @pytest.mark.parametrize("url", ["mem://", "surrealkv://D:/data/ontyra.db", "file:///tmp/db"])
    def test_embedded_urls_are_refused(self, url: str) -> None:
        with pytest.raises(errors.StorageError, match="need a SurrealDB server"):
            backup.http_base(url)


class TestWritePrivate:
    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "dump.surql"
        backup.write_private(target, "SELECT 1;")
        assert target.read_text("utf-8") == "SELECT 1;"

    def test_refuses_to_overwrite_an_existing_export(self, tmp_path: Path) -> None:
        target = tmp_path / "dump.surql"
        backup.write_private(target, "first")
        with pytest.raises(FileExistsError):
            backup.write_private(target, "second")
        assert target.read_text("utf-8") == "first"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_the_export_is_readable_by_the_owner_only(self, tmp_path: Path) -> None:
        target = tmp_path / "dump.surql"
        backup.write_private(target, "SELECT 1;")
        assert target.stat().st_mode & 0o777 == 0o600


class TestDefaultName:
    def test_stamps_the_utc_time_into_a_surql_name(self) -> None:
        assert backup.default_name(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == (
            "ontyra-20260102-030405.surql"
        )


class TestPrune:
    def test_keeps_the_newest_and_deletes_the_rest(self, tmp_path: Path) -> None:
        names = [
            "ontyra-20200101-000000.surql",
            "ontyra-20200102-000000.surql",
            "ontyra-20200103-000000.surql",
        ]
        for name in names:
            (tmp_path / name).write_text(name, "utf-8")
        (tmp_path / "notes.txt").write_text("keep me", "utf-8")

        stale = backup.prune(tmp_path, keep=2)

        assert stale == [tmp_path / names[0]]
        assert sorted(p.name for p in tmp_path.glob("ontyra-*.surql")) == names[1:]
        assert (tmp_path / "notes.txt").exists()

    def test_keep_zero_removes_every_export(self, tmp_path: Path) -> None:
        (tmp_path / "ontyra-20200101-000000.surql").write_text("x", "utf-8")
        assert backup.prune(tmp_path, keep=0) == [tmp_path / "ontyra-20200101-000000.surql"]

    def test_keeping_more_than_exists_deletes_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "ontyra-20200101-000000.surql").write_text("x", "utf-8")
        assert backup.prune(tmp_path, keep=5) == []


class _FakeClient:
    """Serves the calls `export_sql`/`import_sql` make, recording them."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.handler()

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.handler()


class TestExportSql:
    async def test_sends_the_namespace_and_database_to_the_export_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(lambda: httpx.Response(200, text="SELECT 1;"))
        monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kwargs: client)

        text = await backup.export_sql(
            "ws://db:8000/rpc", namespace="ns", database="main", username="u", password="p"
        )

        assert text == "SELECT 1;"
        call = client.calls[0]
        assert call["url"] == "http://db:8000/export"
        assert call["headers"] == {
            "surreal-ns": "ns",
            "surreal-db": "main",
            "Accept": "application/octet-stream",
        }
        assert call["auth"] == ("u", "p")

    async def test_an_http_error_is_a_storage_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            backup.httpx, "AsyncClient", lambda **kwargs: _FakeClient(lambda: httpx.Response(500))
        )
        with pytest.raises(errors.StorageError, match="export refused: HTTP 500"):
            await backup.export_sql(
                "http://db:8000", namespace="ns", database="main", username="u", password="p"
            )

    async def test_a_network_failure_is_a_storage_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse() -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kwargs: _FakeClient(refuse))
        with pytest.raises(errors.StorageError, match="export failed"):
            await backup.export_sql(
                "http://db:8000", namespace="ns", database="main", username="u", password="p"
            )


class TestImportSql:
    async def test_posts_the_sql_with_the_namespace_and_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(lambda: httpx.Response(200, json=[{"status": "OK"}]))
        monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kwargs: client)

        await backup.import_sql(
            "ws://db:8000/rpc",
            "SELECT 1;",
            namespace="ns",
            database="main",
            username="u",
            password="p",
        )

        call = client.calls[0]
        assert call["url"] == "http://db:8000/import"
        assert call["content"] == b"SELECT 1;"
        assert call["headers"]["Content-Type"] == "text/plain"
        assert call["headers"]["surreal-ns"] == "ns"
        assert call["headers"]["Accept"] == "application/json"

    async def test_a_failing_statement_fails_the_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = [{"status": "OK"}, {"status": "ERR", "detail": "boom"}]
        monkeypatch.setattr(
            backup.httpx,
            "AsyncClient",
            lambda **kwargs: _FakeClient(lambda: httpx.Response(200, json=response)),
        )
        with pytest.raises(errors.StorageError, match="1 failing statement"):
            await backup.import_sql(
                "http://db:8000",
                "SELECT 1;",
                namespace="ns",
                database="main",
                username="u",
                password="p",
            )

    async def test_a_refused_import_is_a_storage_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            backup.httpx, "AsyncClient", lambda **kwargs: _FakeClient(lambda: httpx.Response(403))
        )
        with pytest.raises(errors.StorageError, match="import refused: HTTP 403"):
            await backup.import_sql(
                "http://db:8000",
                "SELECT 1;",
                namespace="ns",
                database="main",
                username="u",
                password="p",
            )

    async def test_an_unreachable_server_fails_the_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse() -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(backup.httpx, "AsyncClient", lambda **kwargs: _FakeClient(refuse))
        with pytest.raises(errors.StorageError, match="import failed"):
            await backup.import_sql(
                "http://db:8000",
                "SELECT 1;",
                namespace="ns",
                database="main",
                username="u",
                password="p",
            )
