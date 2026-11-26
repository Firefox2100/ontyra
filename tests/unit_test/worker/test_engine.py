import asyncio
from typing import Any

import pytest

from ontyra.config import WorkerSettings
from ontyra.worker.engine import Busy, Engine


def engine(scorer: Any, **kw: object) -> Engine:
    return Engine(scorer, WorkerSettings(**kw))  # type: ignore[arg-type]


async def test_scores_come_back_in_order_on_the_model_thread(make_scorer: Any) -> None:
    s = make_scorer()
    e = engine(s)
    await e.start()
    try:
        got = await e.score("q", ["a", "bbb", "cc"])
        assert got.logits == [1.0, 3.0, 2.0]
        assert s.threads == {"ontyra-model_0"}
        assert s.state == "loaded"  # warmed at start
    finally:
        await e.stop()


async def test_requests_that_arrive_together_share_a_pass_and_get_their_own_scores(
    make_scorer: Any,
) -> None:
    s = make_scorer(delay=0.05)
    e = engine(s, batch_wait_ms=50)
    await e.start()
    try:
        results = await asyncio.gather(
            e.score("q1", ["a"]), e.score("q2", ["bb", "ccc"]), e.score("q3", ["dddd"])
        )
        assert [r.logits for r in results] == [[1.0], [2.0, 3.0], [4.0]]
        assert sum(s.batches) == 4
        assert len(s.batches) < 3  # shared, not one pass each
    finally:
        await e.stop()


async def test_a_full_queue_refuses_instead_of_waiting(make_scorer: Any) -> None:
    s = make_scorer(delay=0.2)
    e = engine(s, queue_size=1, batch_wait_ms=0)
    await e.start()
    try:
        first = asyncio.create_task(e.score("q", ["a"]))
        await asyncio.sleep(0.05)  # running now
        second = asyncio.create_task(e.score("q", ["b"]))  # waits in the queue
        await asyncio.sleep(0.01)
        with pytest.raises(Busy):
            await e.score("q", ["c"])
        assert (await first).logits == [1.0]
        assert (await second).logits == [1.0]
        assert e.counters.refused == 1
    finally:
        await e.stop()


async def test_a_lease_refuses_serving_and_lets_the_running_batch_finish(make_scorer: Any) -> None:
    s = make_scorer(delay=0.1)
    e = engine(s, batch_wait_ms=0)
    await e.start()
    try:
        running = asyncio.create_task(e.score("q", ["a"]))
        await asyncio.sleep(0.02)
        async with e.lease():
            assert running.done()  # the lease waited for it
            assert (await running).logits == [1.0]
            with pytest.raises(Busy):
                await e.score("q", ["b"])
            assert await e.run_exclusive(lambda: 7) == 7
        assert (await e.score("q", ["b"])).logits == [1.0]  # serving resumes
    finally:
        await e.stop()


async def test_a_failing_batch_fails_its_requests_and_the_engine_keeps_serving(
    make_scorer: Any,
) -> None:
    s = make_scorer()
    e = engine(s)
    await e.start()
    try:
        s.fail = True
        with pytest.raises(RuntimeError, match="boom"):
            await e.score("q", ["a"])
        s.fail = False
        assert (await e.score("q", ["a"])).logits == [1.0]
        assert e.counters.failed == 1
    finally:
        await e.stop()


async def test_idle_weights_are_offloaded_then_dropped_and_reload_on_demand(
    make_scorer: Any,
) -> None:
    s = make_scorer()
    e = engine(s, offload_after_seconds=0.2, free_after_seconds=0.6)
    await e.start()
    try:
        assert s.state == "loaded"
        await asyncio.sleep(0.45)
        assert s.state == "offloaded"
        await asyncio.sleep(0.6)
        assert s.state == "free"
        assert (await e.score("q", ["abc"])).logits == [3.0]  # reloaded by the request
        assert s.state == "loaded"
        assert e.counters.offloads >= 1
        assert e.counters.releases >= 1
    finally:
        await e.stop()


async def test_activity_keeps_the_weights_resident(make_scorer: Any) -> None:
    s = make_scorer()
    e = engine(s, offload_after_seconds=0.3, free_after_seconds=0)
    await e.start()
    try:
        for _ in range(6):
            await asyncio.sleep(0.1)
            await e.score("q", ["a"])
        assert s.state == "loaded"
    finally:
        await e.stop()


async def test_stopping_refuses_what_is_waiting_and_releases_the_model(make_scorer: Any) -> None:
    s = make_scorer()
    e = engine(s)
    await e.start()
    await e.stop()
    assert s.state == "free"
    with pytest.raises(Busy):
        await e.score("q", ["a"])


async def test_a_momentary_memory_shortage_is_busy_not_a_failure(make_scorer: Any) -> None:
    from ontyra.worker.model import MemoryShortage

    s = make_scorer()
    e = engine(s)
    await e.start()
    try:
        s.score = lambda pairs: (_ for _ in ()).throw(MemoryShortage("no room"))
        with pytest.raises(Busy, match="no room"):
            await e.score("q", ["a"])
        assert e.counters.refused == 1 and e.counters.failed == 0
    finally:
        await e.stop()
