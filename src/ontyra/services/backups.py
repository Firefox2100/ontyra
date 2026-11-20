"""Scheduled exports of the whole database (plans/24 follow-up; docs: Deployment, Backups).

The daemon asks the database for its own export (a SurrealQL file, injected as `export` so this
layer stays free of storage details), writes it readable by its owner only into a directory and
keeps the newest `keep`. It runs on a timer, first after whatever is left of the interval since
the newest file (so restarts do not export again and again). A failure is logged and retried at
the next round; it never stops the daemon.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]
PREFIX, SUFFIX = "ontyra-", ".surql"
RETRY_SECONDS = 900  # after a failed export, try again sooner than the whole interval
MARKERS = ("DEFINE ", "OPTION IMPORT")  # what a real SurrealQL export contains


class BackupService:
    def __init__(
        self,
        export: Callable[[], Awaitable[str]],
        directory: Path,
        *,
        every_hours: float,
        keep: int,
        now: Callable[[], datetime],
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.export = export
        self.directory = directory
        self.every_hours = every_hours
        self.keep = keep
        self.now = now
        self._sleep = sleep

    def newest(self) -> Path | None:
        found = sorted(self.directory.glob(f"{PREFIX}*{SUFFIX}"))
        return found[-1] if found else None

    def seconds_until_due(self) -> float:
        """How long until the next export is due, judged by the newest file's age."""
        try:
            newest = self.newest()
            if newest is None:
                return 0.0
            age = self.now().timestamp() - newest.stat().st_mtime
        except OSError:  # a file removed between listing and reading, an unreadable directory
            return 0.0
        return max(0.0, self.every_hours * 3600 - age)

    async def run_once(self) -> Path:
        """Export now and prune; returns the file written."""
        text = await self.export()
        if not text.strip() or not any(m in text for m in MARKERS):
            raise ValueError("the database returned something that is not an export")
        stamp = self.now().strftime("%Y%m%d-%H%M%S")
        path = self.directory / f"{PREFIX}{stamp}{SUFFIX}"
        await asyncio.to_thread(self._write, path, text)
        await asyncio.to_thread(self._prune)
        return path

    def _write(self, path: Path, text: str) -> None:
        """Write to a temporary name and rename it, so a half-written file (a full disk, a
        crash) never looks like the newest backup."""
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def _prune(self) -> None:
        found = sorted(self.directory.glob(f"{PREFIX}*{SUFFIX}"))
        for stale in found[: max(0, len(found) - self.keep)]:
            stale.unlink()

    def _sweep_temporaries(self) -> None:
        """A kill or a full disk can leave `*.tmp` files (a whole private copy each)."""
        for leftover in self.directory.glob(f"{PREFIX}*{SUFFIX}.tmp"):
            leftover.unlink(missing_ok=True)

    async def run_forever(self) -> None:
        try:
            await asyncio.to_thread(self._sweep_temporaries)
            first = await asyncio.to_thread(self.seconds_until_due)
        except Exception:
            log.warning("could not tell when the last export was made", exc_info=True)
            first = 0.0
        await self._sleep(first)
        while True:
            wait = self.every_hours * 3600
            try:
                path = await self.run_once()
                log.info("database exported to %s", path)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("scheduled export failed", exc_info=True)
                wait = min(wait, RETRY_SECONDS)
            await self._sleep(wait)
