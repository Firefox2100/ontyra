import threading
import time
from collections.abc import Callable, Sequence

import pytest

from ontyra.worker.model import Scores


class FakeScorer:
    """Scores a pair by its document's length; records the batches and the thread."""

    def __init__(self, delay: float = 0.0) -> None:
        self.state = "free"
        self.batches: list[int] = []
        self.threads: set[str] = set()
        self.delay = delay
        self.fail = False

    def load(self) -> None:
        self.state = "loaded"

    def score(self, pairs: Sequence[tuple[str, str]]) -> Scores:
        self.load()
        self.batches.append(len(pairs))
        self.threads.add(threading.current_thread().name)
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return Scores([float(len(d)) for _, d in pairs], tokens=len(pairs))

    def offload(self) -> None:
        self.state = "offloaded"

    def release(self) -> None:
        self.state = "free"


@pytest.fixture
def make_scorer() -> Callable[..., FakeScorer]:
    return FakeScorer
