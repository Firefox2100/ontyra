"""Scheduled detection: one pass per owner, a loop that survives failures and can be cancelled."""

from __future__ import annotations

import asyncio

import pytest

from ontyra.app import Application
from ontyra.models.tasks import TaskSettings
from ontyra.services.maintenance import Maintenance


async def test_a_pass_queues_tasks_once(app: Application, remember) -> None:
    await remember("fact", "tidewatch releases are tagged on the main branch")
    await remember("fact", "tidewatch releases are tagged on the main branch.")
    await app.indexer.backfill()
    first = await app.maintenance.run_once()
    again = await app.maintenance.run_once()
    assert "owner:global" in first
    assert all(not r.created for r in again.values())  # already queued: nothing new


async def test_the_loop_waits_runs_and_survives_a_failure(app: Application) -> None:
    waits: list[float] = []
    calls = 0

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) >= 4:
            raise asyncio.CancelledError

    real = app.maintenance.run_once

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return await real()

    maintenance = Maintenance(
        app.actors,
        app.tasks,
        app.recall.store,
        TaskSettings(detect_every_hours=2, detect_first_after_minutes=5),
        sleep=sleep,
    )
    maintenance.run_once = flaky  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await maintenance.run_forever()
    assert waits == [300, 7200, 7200, 7200] and calls == 3 and maintenance.rounds == 3
