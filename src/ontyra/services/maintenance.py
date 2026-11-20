"""Scheduled maintenance: run task detection on a timer, for the global memory and for every
user's own memory (multi-user mode), as a service caller of that owner.

Detection (`TaskService.detect`) only queues tasks and closes obsolete ones; nothing is merged,
revised or retracted without a person or an agent deciding the task. It may ask the optional
decision model about duplicate candidates, like a manual run. A failure is logged and the next
round tries again; the loop never takes the daemon down.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ontyra.models.tasks import DetectReport, TaskSettings
from ontyra.ports import Store
from ontyra.services.actors import ActorService
from ontyra.services.indexing import IndexService
from ontyra.services.ontology import OntologyService
from ontyra.services.tasks import TaskService

log = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


class Maintenance:
    def __init__(
        self,
        actors: ActorService,
        tasks: TaskService,
        store: Store,
        settings: TaskSettings,
        *,
        ontology: OntologyService | None = None,
        indexer: IndexService | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.ontology = ontology
        self.indexer = indexer
        self.actors = actors
        self.tasks = tasks
        self.store = store
        self.settings = settings
        self._sleep = sleep
        self.rounds = 0
        self.last: dict[str, DetectReport] = {}

    async def run_once(self) -> dict[str, DetectReport]:
        """One pass per owner; returns each owner's report. An owner whose pass fails is logged
        and skipped: the others still get theirs."""
        reports: dict[str, DetectReport] = {}
        if self.indexer is not None:
            try:  # memories whose embedding failed at commit time get one now
                await self.indexer.backfill()
            except Exception:
                log.warning("index backfill failed", exc_info=True)
        callers = [await self.actors.caller(kind="service", name="scheduler")]
        for owner in await self.store.select("owner", {"kind": "user"}, limit=10_000):
            if owner.id is not None:
                callers.append(
                    await self.actors.caller(kind="service", name="scheduler", owner=owner.id)
                )
        for caller in callers:
            try:
                reports[str(caller.owner)] = await self.tasks.detect(caller)
                if self.ontology is not None:
                    await self.ontology.canonicalise_index(caller)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("scheduled detection failed for %s", caller.owner, exc_info=True)
        self.last = reports
        return reports

    async def run_forever(self) -> None:
        """Wait, detect, repeat, until cancelled."""
        cfg = self.settings
        await self._sleep(cfg.detect_first_after_minutes * 60)
        while True:
            try:
                reports = await self.run_once()
                created = sum(len(r.created) for r in reports.values())
                log.info("scheduled detection: %d new task(s)", created)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("scheduled detection failed", exc_info=True)
            self.rounds += 1
            await self._sleep(cfg.detect_every_hours * 3600)
