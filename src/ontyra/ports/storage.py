"""The persistence port: everything services need from storage, and nothing more."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from ontyra.models import Record, RecordId
from ontyra.models.core import Episode, Memory
from ontyra.models.search import SearchDoc
from ontyra.ports.writes import Write


@dataclass(frozen=True)
class Hit:
    """A search lane result: the revision and the lane's raw score (BM25, or cosine)."""

    rev: RecordId
    score: float


class Store(Protocol):
    async def get(self, id: RecordId) -> Record | None:
        """One record, as the model registered for its table; `None` if absent."""
        ...

    async def select(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        where_in: Mapping[str, Sequence[Any]] | None = None,
        where_any: Mapping[str, Any] | None = None,
        limit: int = 100,
        order_by: str | None = None,
        descending: bool = False,
    ) -> list[Record]:
        """Records matching equality/IN filters and an optional OR equality group."""
        ...

    async def revisions(self, memory: RecordId) -> list[Record]:
        """Every revision of a memory, oldest first."""
        ...

    async def active_memories(
        self, kind: str, owners: Sequence[RecordId], limit: int = 500
    ) -> list[tuple[Memory, Record]]:
        """Active memories of a kind for the given owners, with their current revision."""
        ...

    async def dependents(self, rev: RecordId) -> list[RecordId]:
        """Revisions derived from `rev` (via `derived_from` edges)."""
        ...

    async def get_many(self, ids: Sequence[RecordId]) -> dict[RecordId, Record]:
        """Records by id (missing ids are absent from the result)."""
        ...

    async def search_text(
        self, query: str, owners: Sequence[RecordId], *, kinds: Sequence[str] = (), limit: int = 50
    ) -> list[Hit]:
        """Full-text (BM25, any-term) hits over live revisions, best first."""
        ...

    async def search_vector(
        self,
        vector: Sequence[float],
        owners: Sequence[RecordId],
        *,
        kinds: Sequence[str] = (),
        limit: int = 50,
    ) -> list[Hit]:
        """Nearest live revisions by cosine similarity (`score` in [-1, 1]), best first."""
        ...

    async def docs(
        self,
        owners: Sequence[RecordId],
        *,
        kinds: Sequence[str] = (),
        anchors: Sequence[str] | None = None,
        unanchored: bool = False,
        about: Sequence[RecordId] | None = None,
        expires_before: datetime | None = None,
        limit: int = 500,
    ) -> list[SearchDoc]:
        """Live-revision index rows for the owners. A row is returned if it matches any given
        selector: anchored on one of `anchors`, unanchored (when `unanchored`), or about one of
        `about`. With no selector given, every row of the owners/kinds (paged by `limit`).
        `expires_before` keeps only rows whose revision expires before that time (a filter, not
        a selector)."""
        ...

    async def memory_edges(self, edge: str, memories: Sequence[RecordId]) -> list[dict[str, Any]]:
        """Edge rows touching any revision of the given memories, with `in_memory` and
        `out_memory` added. Links between revisions follow their memories across revisions."""
        ...

    async def recent_episodes(
        self, owners: Sequence[RecordId], since: datetime, limit: int
    ) -> list[Episode]:
        """Episodes of the owners at or after `since`, newest first."""
        ...

    async def uncited_episodes(self, before: datetime, limit: int) -> list[RecordId]:
        """Episodes older than `before` that no revision cites (`derived_from`)."""
        ...

    async def uncited_traces(self, before: datetime, limit: int) -> list[RecordId]:
        """Recall traces created before `before` that no feedback event cites."""
        ...

    async def unembedded(self, model: str, limit: int = 64) -> list[SearchDoc]:
        """Live index rows that have no vector under `model`."""
        ...

    async def edges(
        self,
        edge: str,
        *,
        source: RecordId | None = None,
        target: RecordId | None = None,
        sources: Sequence[RecordId] | None = None,
        targets: Sequence[RecordId] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Rows of an edge table (`in`, `out`, and the edge's fields), record ids as `RecordId`."""
        ...

    async def apply(self, writes: Sequence[Write]) -> None:
        """Execute all writes as one atomic batch. Raises on any failure; nothing persists."""
        ...
