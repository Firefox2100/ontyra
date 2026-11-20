"""An atomic batch the engine asks to be retried is retried; a guard conflict of ours is not."""

from __future__ import annotations

from typing import Any

import pytest

from ontyra.storage.database import Database, capabilities_for
from ontyra.storage.errors import ConflictError

ENGINE = "Cannot COMMIT: Transaction conflict: Write conflict, retry the transaction. This transaction can be retried"
GUARD = "An error occurred: ontyra:conflict memory:x"


class Fake:
    def __init__(self, *messages: str) -> None:
        self.messages = list(messages)
        self.calls = 0

    async def query_raw(self, sql: str, params: Any = None) -> dict[str, Any]:
        self.calls += 1
        if self.messages:
            message = self.messages.pop(0)
            return {"result": [{"status": "ERR", "kind": "Internal", "result": message}]}
        return {"result": [{"status": "OK", "result": [1]}]}


def database(conn: Fake) -> Database:
    return Database(conn, "ws://x/rpc", capabilities_for("ws://x/rpc"))


async def test_a_batch_the_engine_asks_to_retry_is_run_again() -> None:
    conn = Fake(ENGINE, ENGINE)
    assert await database(conn).run_atomic(["CREATE a"]) == [[1]]
    assert conn.calls == 3


async def test_a_batch_that_keeps_conflicting_gives_up() -> None:
    conn = Fake(*[ENGINE] * 10)
    with pytest.raises(ConflictError):
        await database(conn).run_atomic(["CREATE a"])
    assert conn.calls == 4


async def test_our_own_guard_conflict_is_never_retried() -> None:
    conn = Fake(GUARD)
    with pytest.raises(ConflictError):
        await database(conn).run_atomic(["CREATE a"])
    assert conn.calls == 1
