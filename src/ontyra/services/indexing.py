"""The derived search index: documents, embeddings, rebuild.

`search_doc` rows are written in the same atomic batch as the revision they index (see
`planner`); embeddings are computed after commit (best effort) and can be backfilled, so a
missing or slow embedder never blocks writing a memory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ontyra.models import RecordId
from ontyra.models.core import Memory
from ontyra.models.kinds import RevisionBase
from ontyra.models.search import Embedding, SearchDoc
from ontyra.ports import Embedder, Store, Upsert

log = logging.getLogger(__name__)

_SKIP = {
    "id",
    "memory",
    "seq",
    "recorded_at",
    "proposed_by",
    "confidence",
    "expires_at",
    "about",
    "anchors",
    "qualifiers",
    "scope",
    "evidence",
}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, BaseModel):
        return [s for v in value.model_dump().values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]  # pyright: ignore[reportUnknownVariableType]
    return []  # numbers, datetimes, record ids: not text


def search_text_of(rev: RevisionBase) -> str:
    """The indexed text of a revision: its canonical sentence plus the kind's own text fields."""
    parts = [rev.text]
    for name in type(rev).model_fields:
        if name not in _SKIP and name != "text":
            parts += _strings(getattr(rev, name))
    return ". ".join(p for p in dict.fromkeys(parts) if p)


def doc_for(
    rev: RevisionBase, rev_id: RecordId, kind: str, owner: RecordId, now: datetime
) -> SearchDoc:
    return SearchDoc(
        rev=rev_id,
        memory=rev.memory,
        kind=kind,  # type: ignore[arg-type]
        owner=owner,
        text=search_text_of(rev),
        anchors=[a.uri for a in rev.anchors],
        about=list(rev.about),
        expires_at=rev.expires_at,
        created_at=now,
    )


def doc_id(rev: RecordId) -> RecordId:
    """The index row of a revision shares its id: `fact_rev:X` ↔ `search_doc:X`."""
    return RecordId("search_doc", rev.id)


def embedding_id(rev: RecordId) -> RecordId:
    return RecordId("embedding", rev.id)


class IndexService:
    def __init__(
        self, store: Store, embedder: Embedder | None, now: Callable[[], datetime]
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.now = now

    async def embed(self, revs: Sequence[RecordId]) -> int:
        """Embed the live documents of `revs` (best effort: failures are logged, not raised)."""
        if self.embedder is None or not revs:
            return 0
        try:
            docs = await self.store.get_many([doc_id(r) for r in revs])
            return await self._embed_docs([d for d in docs.values() if isinstance(d, SearchDoc)])
        except Exception:
            log.exception("embedding failed; documents stay searchable by text")
            return 0

    async def _embed_docs(self, docs: Sequence[SearchDoc]) -> int:
        if self.embedder is None or not docs:
            return 0
        vectors = await self.embedder.embed_documents([d.text for d in docs])
        now = self.now()
        await self.store.apply(
            [
                Upsert.of(
                    embedding_id(d.rev),
                    Embedding(
                        rev=d.rev,
                        owner=d.owner,
                        kind=d.kind,
                        model=self.embedder.name,
                        vector=v,
                        created_at=now,
                    ),
                )
                for d, v in zip(docs, vectors, strict=True)
            ]
        )
        return len(docs)

    async def backfill(self, batch: int = 64, max_batches: int = 100) -> int:
        """Embed every live document that has no vector under the current model."""
        if self.embedder is None:
            return 0
        total = 0
        for _ in range(max_batches):
            docs = await self.store.unembedded(self.embedder.name, limit=batch)
            if not docs:
                break
            total += await self._embed_docs(docs)
        return total

    async def rebuild_docs(self) -> int:
        """Recreate the search index from the revisions (derived data is always rebuildable).
        Does not remove rows; run on an empty index or after a schema reset."""
        memories = await self.store.select("memory", {"status": "active"}, limit=100_000)
        count = 0
        for mem in memories:
            if not isinstance(mem, Memory) or mem.current is None:
                continue
            rev = await self.store.get(mem.current)
            if isinstance(rev, RevisionBase):
                await self.store.apply(
                    [
                        Upsert.of(
                            doc_id(mem.current),
                            doc_for(rev, mem.current, mem.kind, mem.owner, self.now()),
                        )
                    ]
                )
                count += 1
        return count
