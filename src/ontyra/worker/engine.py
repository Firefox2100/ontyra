"""Concurrency and memory for one model: a bounded queue, micro-batching, idle offload, a lease.

Everything that touches the model runs on one thread, one batch at a time. Requests wait in a
bounded queue and requests that arrive together share a forward pass. After a quiet spell the
weights move to host memory, and after a longer one they are dropped; the next request reloads
them. A lease takes the model for exclusive use (training): serving answers `Busy` at once, so
callers fall back to their plain ranking instead of waiting.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from ontyra.config import WorkerSettings
from ontyra.worker.model import Scorer, Scores


class Busy(Exception):
    """The model cannot take this request now (queue full, leased, stopping)."""


@dataclass
class Job:
    pairs: list[tuple[str, str]]
    future: asyncio.Future[Scores]


@dataclass
class Counters:
    requests: int = 0
    pairs: int = 0
    batches: int = 0
    refused: int = 0
    failed: int = 0
    loads: int = 0
    offloads: int = 0
    releases: int = 0


@dataclass
class Engine:
    scorer: Scorer
    cfg: WorkerSettings
    clock: Callable[[], float] = time.monotonic
    counters: Counters = field(default_factory=Counters)

    def __post_init__(self) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=max(1, self.cfg.queue_size))
        self._gpu = asyncio.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ontyra-model")
        self._tasks: list[asyncio.Task[None]] = []
        self._leased = False
        self._stopping = False
        self._last_used = self.clock()
        self._limit = max(1, self.cfg.batch_size) * 4  # pairs that may share one pass

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    @property
    def leased(self) -> bool:
        return self._leased

    @property
    def state(self) -> str:
        return self.scorer.state

    @property
    def idle_seconds(self) -> float:
        return self.clock() - self._last_used

    async def start(self, *, warm: bool = True) -> None:
        if warm:
            await self._run(self._load)  # a bad model or card fails now, not at the first request
        self._tasks = [asyncio.create_task(self._loop()), asyncio.create_task(self._watch())]

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        self._refuse_waiting("the worker is stopping")
        self._pool.shutdown(wait=True)
        self.scorer.release()

    async def score(self, query: str, documents: Sequence[str]) -> Scores:
        """Logits of each document for the query, in order. Raises `Busy` when it cannot wait."""
        if self._stopping or self._leased:
            self.counters.refused += 1
            raise Busy("the model is busy" if self._leased else "the worker is stopping")
        job = Job([(query, d) for d in documents], asyncio.get_running_loop().create_future())
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self.counters.refused += 1
            raise Busy("the queue is full") from None
        return await job.future

    @asynccontextmanager
    async def lease(self) -> AsyncGenerator[None]:
        """Exclusive use of the model: queued requests are refused, running ones finish."""
        self._leased = True
        self._refuse_waiting("the model is busy")
        try:
            async with self._gpu:
                yield
        finally:
            self._leased = False
            self._last_used = self.clock()

    async def run_exclusive[T](self, work: Callable[[], T]) -> T:
        """Run blocking work on the model thread (inside a lease): training, saving a head."""
        return await self._run(work)

    # -- internals ----------------------------------------------------------------------------

    def _load(self) -> None:
        was = self.scorer.state
        self.scorer.load()
        if was != "loaded":
            self.counters.loads += 1

    async def _run[T](self, work: Callable[[], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._pool, work)

    def _refuse_waiting(self, reason: str) -> None:
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not job.future.done():
                self.counters.refused += 1
                job.future.set_exception(Busy(reason))

    async def _gather(self) -> list[Job]:
        jobs = [await self._queue.get()]
        total = len(jobs[0].pairs)
        deadline = self.clock() + self.cfg.batch_wait_ms / 1000
        while total < self._limit:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                wait = deadline - self.clock()
                if wait <= 0:
                    break
                await asyncio.sleep(min(wait, 0.001))
                continue
            jobs.append(job)
            total += len(job.pairs)
        return jobs

    async def _loop(self) -> None:
        while True:
            jobs = await self._gather()
            live = [j for j in jobs if not j.future.done()]  # callers that gave up are skipped
            if not live:
                continue
            async with self._gpu:
                if self._leased:
                    for job in live:
                        self.counters.refused += 1
                        job.future.set_exception(Busy("the model is busy"))
                    continue
                await self._serve(live)

    async def _serve(self, jobs: list[Job]) -> None:
        pairs = [p for j in jobs for p in j.pairs]
        self.counters.batches += 1
        try:
            was = self.scorer.state
            scores = await self._run(lambda: self.scorer.score(pairs))
            if was != "loaded":
                self.counters.loads += 1
        except Exception as error:
            self.counters.failed += len(jobs)
            for job in jobs:
                if not job.future.done():
                    job.future.set_exception(error)
            return
        finally:
            self._last_used = self.clock()
        at = 0
        for job in jobs:
            n = len(job.pairs)
            self.counters.requests += 1
            self.counters.pairs += n
            share = scores.tokens * n // max(1, len(pairs))
            if not job.future.done():
                job.future.set_result(Scores(scores.logits[at : at + n], share))
            at += n

    async def _watch(self) -> None:
        """Move idle weights to host memory, then drop them."""
        offload, free = self.cfg.offload_after_seconds, self.cfg.free_after_seconds
        steps = [s for s in (offload, free) if s > 0]
        if not steps:
            return
        interval = max(0.05, min(30.0, min(steps) / 4))
        while True:
            await asyncio.sleep(interval)
            if self._leased or self._gpu.locked() or not self._queue.empty():
                continue
            idle, state = self.idle_seconds, self.scorer.state
            if free > 0 and idle >= free and state != "free":
                await self._run(self.scorer.release)
                self.counters.releases += 1
            elif offload > 0 and idle >= offload and state == "loaded":
                await self._run(self.scorer.offload)
                self.counters.offloads += 1
