"""Scheduled exports: timing from the newest file, private files, pruning, failure survival."""

from __future__ import annotations

import asyncio
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ontyra.services.backups import BackupService


def service(
    tmp_path: Path, export, *, keep: int = 3, sleep=None, hours: float = 24
) -> BackupService:  # type: ignore[no-untyped-def]
    kwargs = {"sleep": sleep} if sleep else {}
    return BackupService(
        export, tmp_path, every_hours=hours, keep=keep, now=lambda: datetime.now(UTC), **kwargs
    )


def everything(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob("ontyra-*.surql"))


async def test_an_export_is_private_and_old_ones_are_pruned(tmp_path: Path) -> None:
    async def export() -> str:
        return "DEFINE TABLE x;"

    svc = service(tmp_path, export)
    for day in range(1, 6):
        (tmp_path / f"ontyra-2026010{day}-000000.surql").write_text("old")
    path = await svc.run_once()
    assert path.read_text() == "DEFINE TABLE x;"
    if os.name == "posix":
        # Windows ignores the read bits chmod sets, so a mode there says nothing.
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    kept = names(tmp_path)
    assert len(kept) == 3 and path.name in kept  # the newest three survive


async def test_the_first_export_waits_out_what_is_left_of_the_interval(tmp_path: Path) -> None:
    async def export() -> str:
        return "DEFINE TABLE x;"

    svc = service(tmp_path, export, hours=24)
    assert svc.seconds_until_due() == 0  # nothing yet: due at once
    recent = tmp_path / "ontyra-20260101-000000.surql"
    recent.write_text("x")
    assert 23.9 * 3600 < svc.seconds_until_due() <= 24 * 3600 + 1  # exported just now
    old = time.time() - 30 * 3600
    os.utime(recent, (old, old))
    assert svc.seconds_until_due() == 0  # older than the interval


async def test_the_loop_survives_a_failed_export_and_stops_when_cancelled(tmp_path: Path) -> None:
    calls = 0
    waits: list[float] = []

    async def export() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database away")
        return "DEFINE TABLE ok;"

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) >= 3:
            raise asyncio.CancelledError

    svc = service(tmp_path, export, sleep=sleep, hours=2)
    with pytest.raises(asyncio.CancelledError):
        await svc.run_forever()
    assert calls == 2 and waits == [0.0, 900, 7200]  # a failure is retried sooner
    assert len(names(tmp_path)) == 1  # the second one worked


async def test_a_failed_write_leaves_no_file_that_looks_like_a_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def export() -> str:
        return "DEFINE TABLE x;"

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        await service(tmp_path, export).run_once()
    assert everything(tmp_path) == []  # no backup, not even a temporary file


async def test_an_unreadable_directory_means_due_now_not_a_dead_task(tmp_path: Path) -> None:
    async def export() -> str:
        return "DEFINE TABLE x;"

    gone = service(tmp_path / "missing" / "deeper", export)
    assert gone.seconds_until_due() == 0.0


async def test_something_that_is_not_an_export_is_refused_and_a_leftover_temp_is_swept(
    tmp_path: Path,
) -> None:
    async def export() -> str:
        return "<html>bad gateway</html>"

    svc = service(tmp_path, export)
    with pytest.raises(ValueError, match="not an export"):
        await svc.run_once()
    assert everything(tmp_path) == []
    (tmp_path / "ontyra-20260101-000000.surql.tmp").write_text("half")
    svc._sweep_temporaries()
    assert everything(tmp_path) == []
