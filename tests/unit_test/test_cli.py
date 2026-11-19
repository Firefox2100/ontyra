"""The process entry point: argument dispatch, and the guards that keep it safe to run.

`main` is the only place that wires arguments to commands, so these tests pin the dispatch (and
that calibrate paths stay inside the directory the operator started in) with the commands
themselves replaced. `_db_command` is exercised against a stand-in database only for its guards:
a wipe without `--yes`, and the reports of the other actions.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ontyra import cli
from ontyra.config import Settings


def _exports(directory: Path) -> list[Path]:
    """The export files in `directory`, oldest first."""
    return sorted(directory.glob("ontyra-*.surql"))


class TestConfinedPath:
    def test_a_relative_path_is_resolved_inside_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert cli._confined_path("recording.json") == tmp_path / "recording.json"
        assert cli._confined_path("nested/recording.json") == tmp_path / "nested" / "recording.json"

    def test_the_working_directory_itself_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert cli._confined_path(".") == tmp_path

    def test_an_absolute_path_inside_the_working_directory_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "saved.json"
        assert cli._confined_path(str(target)) == target

    def test_a_path_that_escapes_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit, match="outside"):
            cli._confined_path("../escape.json")

    def test_a_sibling_with_the_same_prefix_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        with pytest.raises(SystemExit, match="outside"):
            cli._confined_path(str(tmp_path / "work-elsewhere" / "x.json"))


class TestMainDispatch:
    def test_version_exits_successfully(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.main(["--version"])
        assert exc.value.code == 0
        assert cli.__version__ in capsys.readouterr().out

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.main([])
        assert exc.value.code == 2

    def test_serve_passes_host_and_port_to_uvicorn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = Settings()
        bound: dict[str, Any] = {}

        def fake_bind(host: str, port: int) -> Settings:
            bound.update(host=host, port=port)
            return settings

        monkeypatch.setattr(cli, "load_settings", lambda: settings)
        monkeypatch.setattr(Settings, "for_bind", lambda self, host, port: fake_bind(host, port))
        monkeypatch.setattr(cli, "create_rest_app", lambda s: "app")
        monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: bound.update(run=app, **kw))

        assert cli.main(["serve", "--host", "0.0.0.0", "--port", "9123"]) == 0
        assert bound["host"] == "0.0.0.0"
        assert bound["port"] == 9123
        assert bound["run"] == "app"
        assert bound["log_level"] == settings.log_level.lower()

    def test_calibrate_own_runs_the_own_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        async def fake(settings: Settings, *, yes: bool) -> int:
            seen["yes"] = yes
            return 3

        monkeypatch.setattr(cli, "load_settings", Settings)
        monkeypatch.setattr(cli, "_own_command", fake)
        assert cli.main(["calibrate", "--own", "--yes"]) == 3
        assert seen["yes"] is True

    def test_calibrate_reads_a_recording_inside_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "saved.json").write_text(json.dumps({"recording": {"rows": {}}}), "utf-8")
        seen: dict[str, Any] = {}

        async def fake(
            settings: Settings, saved: dict[str, Any] | None, out: Path | None, *, yes: bool
        ) -> int:
            seen.update(saved=saved, out=out, yes=yes)
            return 0

        monkeypatch.setattr(cli, "load_settings", Settings)
        monkeypatch.setattr(cli, "_calibrate_command", fake)

        assert cli.main(["calibrate", "--from", "saved.json", "--out", "report.json"]) == 0
        assert seen["saved"] == {"recording": {"rows": {}}}
        assert seen["out"] == tmp_path / "report.json"
        assert seen["yes"] is False

    def test_calibrate_refuses_a_recording_outside_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        called = False

        async def fake(*args: Any, **kwargs: Any) -> int:
            nonlocal called
            called = True
            return 0

        monkeypatch.setattr(cli, "load_settings", Settings)
        monkeypatch.setattr(cli, "_calibrate_command", fake)
        with pytest.raises(SystemExit, match="outside"):
            cli.main(["calibrate", "--from", "../saved.json"])
        assert called is False

    def test_db_export_goes_to_the_backup_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        async def fake(
            settings: Settings, command: str, path: str | None, *, keep: int | None, yes: bool
        ) -> int:
            seen.update(command=command, path=path, keep=keep, yes=yes)
            return 0

        monkeypatch.setattr(cli, "load_settings", Settings)
        monkeypatch.setattr(cli, "_backup_command", fake)
        assert cli.main(["db", "export", "backups", "--keep", "3"]) == 0
        assert seen == {"command": "export", "path": "backups", "keep": 3, "yes": False}

    def test_other_db_actions_go_to_the_schema_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        async def fake(settings: Settings, command: str, *, yes: bool) -> int:
            seen.update(command=command, yes=yes)
            return 0

        monkeypatch.setattr(cli, "load_settings", Settings)
        monkeypatch.setattr(cli, "_db_command", fake)
        assert cli.main(["db", "apply"]) == 0
        assert seen == {"command": "apply", "yes": False}


class TestBackupCommand:
    async def test_import_needs_a_file(self) -> None:
        assert await cli._backup_command(Settings(), "import", None, keep=None, yes=False) == 2

    async def test_export_writes_the_server_export(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ontyra.storage import backup

        seen: dict[str, Any] = {}

        async def fake_export(url: str, **kwargs: Any) -> str:
            seen.update(url=url, **kwargs)
            return "SELECT 1;\n"

        monkeypatch.setattr(backup, "export_sql", fake_export)
        target = tmp_path / "dump.surql"
        assert (
            await cli._backup_command(Settings(), "export", str(target), keep=None, yes=False) == 0
        )
        assert target.read_text("utf-8") == "SELECT 1;\n"
        assert seen["url"] == Settings().db.url
        assert seen["namespace"] == Settings().db.namespace

    async def test_export_to_a_directory_prunes_older_exports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ontyra.storage import backup

        async def fake_export(url: str, **kwargs: Any) -> str:
            return "SELECT 1;\n"

        monkeypatch.setattr(backup, "export_sql", fake_export)
        (tmp_path / "ontyra-20200101-000000.surql").write_text("old", "utf-8")
        (tmp_path / "ontyra-20200102-000000.surql").write_text("older", "utf-8")

        assert (
            await cli._backup_command(Settings(), "export", str(tmp_path), keep=1, yes=False) == 0
        )
        exported = _exports(tmp_path)
        assert len(exported) == 1
        assert exported[0].read_text("utf-8") == "SELECT 1;\n"


class _FakeManager:
    def __init__(self, db: Any, *, embedding_dim: int | None = None) -> None:
        self.db = db
        self.embedding_dim = embedding_dim
        self.applied = False
        self.wiped = False

    async def apply(self) -> None:
        self.applied = True

    async def sync(self) -> list[str]:
        return ["REMOVE TABLE stale;"]

    async def wipe(self) -> None:
        self.wiped = True

    async def drift(self) -> list[str]:
        return ["DEFINE FIELD x ON memory;"]


class _FakeDb:
    capabilities = SimpleNamespace(mode="server")

    def __init__(self) -> None:
        self.closed = False

    async def info_db(self) -> dict[str, Any]:
        return {"tables": {"memory": {}, "concept": {}}}

    async def server_version(self) -> str:
        return "3.0.0"

    async def close(self) -> None:
        self.closed = True


class TestDbCommand:
    @pytest.fixture
    def fake_db(self, monkeypatch: pytest.MonkeyPatch) -> _FakeDb:
        db = _FakeDb()

        async def connect(*args: Any, **kwargs: Any) -> _FakeDb:
            return db

        monkeypatch.setattr(cli, "Database", SimpleNamespace(connect=connect))
        monkeypatch.setattr(cli, "SchemaManager", _FakeManager)
        monkeypatch.setattr(cli, "build_embedder", lambda cfg: None)
        return db

    async def test_wipe_without_yes_is_refused(
        self, fake_db: _FakeDb, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert await cli._db_command(Settings(), "wipe", yes=False) == 2
        assert "without --yes" in capsys.readouterr().err
        assert fake_db.closed is True

    async def test_wipe_with_yes_runs(self, fake_db: _FakeDb) -> None:
        assert await cli._db_command(Settings(), "wipe", yes=True) == 0
        assert fake_db.closed is True

    async def test_apply_and_sync(
        self, fake_db: _FakeDb, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert await cli._db_command(Settings(), "apply", yes=False) == 0
        assert await cli._db_command(Settings(), "sync", yes=False) == 0
        out = capsys.readouterr().out
        assert "schema applied" in out
        assert "REMOVE TABLE stale;" in out

    async def test_status_reports_tables_and_drift(
        self, fake_db: _FakeDb, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert await cli._db_command(Settings(), "status", yes=False) == 0
        out = capsys.readouterr().out
        assert "server 3.0.0" in out
        assert "tables: 2" in out
        assert "DEFINE FIELD x ON memory;" in out


class TestOwnCommand:
    async def test_the_own_check_needs_the_fusion_engine(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = Settings()
        settings.recall.engine = "legacy"
        assert await cli._own_command(settings, yes=True) == 2
        assert "ENGINE=fusion" in capsys.readouterr().err


class TestCalibrateCommand:
    async def test_a_paid_embedder_without_yes_is_refused(self) -> None:
        settings = Settings()
        settings.embedding.provider = "langchain"
        assert await cli._calibrate_command(settings, None, None, yes=False) == 2


class TestModuleEntryPoint:
    def test_python_m_ontyra_exits_with_mains_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ontyra.cli

        monkeypatch.setattr(ontyra.cli, "main", lambda: 7)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("ontyra", run_name="__main__", alter_sys=True)
        assert exc.value.code == 7
