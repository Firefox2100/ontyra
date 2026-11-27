"""Backend-neutral write operations. Services build lists of these; storage executes them
atomically. Keeps SurrealQL out of the services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ontyra.models import Record, RecordId


def content_of(model: BaseModel) -> dict[str, Any]:
    """Record payload for storage: no `id`, no `None` fields (those are NONE in the database)."""
    exclude = {"id"} if isinstance(model, Record) else None
    return model.model_dump(mode="python", exclude=exclude, exclude_none=True)


@dataclass(frozen=True)
class Create:
    id: RecordId
    content: dict[str, Any]

    @classmethod
    def of(cls, id: RecordId, model: BaseModel) -> Create:
        return cls(id, content_of(model))


@dataclass(frozen=True)
class Update:
    """Merge `patch` into an existing record (immutable fields are rejected by the database)."""

    id: RecordId
    patch: dict[str, Any]


@dataclass(frozen=True)
class Relate:
    edge: str
    source: RecordId
    target: RecordId
    content: dict[str, Any] = field(default_factory=lambda: {})


@dataclass(frozen=True)
class Upsert:
    """Create the record or replace its content (derived data such as embeddings)."""

    id: RecordId
    content: dict[str, Any]

    @classmethod
    def of(cls, id: RecordId, model: BaseModel) -> Upsert:
        return cls(id, content_of(model))


@dataclass(frozen=True)
class Delete:
    """Remove a record (derived/mutable data only; revisions are immutable)."""

    id: RecordId


@dataclass(frozen=True)
class ExpectCurrent:
    """Optimistic-concurrency guard: abort the whole batch if the memory's current revision
    is no longer `revision`."""

    memory: RecordId
    revision: RecordId


@dataclass(frozen=True)
class ExpectFields:
    """Compare-and-set guard: abort the batch unless every field has the expected value."""

    id: RecordId
    expected: dict[str, Any]


Write = Create | Update | Upsert | Delete | Relate | ExpectCurrent | ExpectFields
