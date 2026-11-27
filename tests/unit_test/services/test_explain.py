"""`ask explain` against trace ids: the table is checked before the store is asked.

A recall trace is shown to a model as `recall_trace:abc`; a copy slip (`trace:abc`, or a bare id
the MCP shape rejected) used to reach the store as an unknown table and answer 500. It must read
as a plain "not found", and a real trace that is simply absent must too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from ontyra.models import RecordId
from ontyra.models.core import Actor
from ontyra.models.recall import AskRequest
from ontyra.services import Caller, errors
from ontyra.services.explore import ExplorationService


class _Store:
    """Records what was asked; answers nothing (the trace is absent)."""

    def __init__(self) -> None:
        self.asked: list[RecordId] = []

    async def get(self, record: RecordId) -> None:
        self.asked.append(record)
        return None


def _service(store: _Store) -> ExplorationService:
    return ExplorationService(
        store=cast(Any, store),
        memory=cast(Any, None),
        ontology=cast(Any, None),
        recall=cast(Any, None),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def _caller() -> Caller:
    return Caller(actor=Actor(id=None, kind="agent", name="tester"))  # type: ignore[arg-type]


async def test_a_foreign_table_is_not_found_and_the_store_is_not_asked() -> None:
    store = _Store()
    with pytest.raises(errors.NotFoundError):
        await _service(store).ask(
            _caller(), AskRequest(question="explain", trace=RecordId.parse("trace:abc"))
        )
    assert store.asked == []


async def test_a_real_table_the_database_lacks_is_not_found() -> None:
    store = _Store()
    with pytest.raises(errors.NotFoundError):
        await _service(store).ask(
            _caller(), AskRequest(question="explain", trace=RecordId.parse("recall_trace:abc"))
        )
    assert [str(record) for record in store.asked] == ["recall_trace:abc"]


async def test_explain_still_needs_a_trace() -> None:
    with pytest.raises(errors.InvalidRequestError):
        await _service(_Store()).ask(_caller(), AskRequest(question="explain"))
