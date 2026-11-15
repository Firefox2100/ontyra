"""The `ontyra` entry point against an embedded file database (sync tests: the CLI runs its
own event loop)."""

from pathlib import Path

import pytest

from ontyra.cli import main
from ontyra.models import registry


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ONTYRA_DB__URL", f"surrealkv://{tmp_path}/cli")
    monkeypatch.setenv("ONTYRA_DB__NAMESPACE", "cli")
    monkeypatch.setenv("ONTYRA_DB__DATABASE", "cli")
    monkeypatch.chdir(tmp_path)  # no stray .env


def test_apply_then_status_reports_every_table_and_no_pending_changes(
    db_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["db", "apply"]) == 0
    assert main(["db", "status"]) == 0
    out = capsys.readouterr().out
    assert f"tables: {len(registry.tables)}" in out and "pending schema changes: 0" in out


def test_wipe_needs_confirmation_and_then_removes_the_tables(
    db_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["db", "apply"]) == 0
    assert main(["db", "wipe"]) == 2
    assert main(["db", "status"]) == 0
    assert f"tables: {len(registry.tables)}" in capsys.readouterr().out
    assert main(["db", "wipe", "--yes"]) == 0
    capsys.readouterr()
    assert main(["db", "status"]) == 0
    assert "tables: 0" in capsys.readouterr().out


def test_sync_is_idempotent(db_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["db", "sync"]) == 0
    assert main(["db", "sync"]) == 0
    assert "removed 0 stale item(s)" in capsys.readouterr().out
