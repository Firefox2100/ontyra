"""Duplicate detection: a read-only scan that reports clusters of memories saying the same
thing, and restatements with a changed value (updates).

Candidates come from the vectors the index already holds: live memories of the same kind and
owner, close in cosine, and applicable in a common place. A pair that differs only in a value is
an update, not a duplicate (`dedupe`). Otherwise an optional decision model is asked, in both
orders, whether the two say the same thing; without one (or when it cannot answer) the cosine
rule decides. The scan changes nothing: a duplicate is resolved by an ordinary proposal.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from ontyra.models import RecordId
from ontyra.models.consolidation import (
    ConsolidationSettings,
    DuplicateCluster,
    Member,
    PairScore,
    ScanReport,
    ScanRequest,
    UpdateCandidate,
)
from ontyra.models.kinds import RevisionBase
from ontyra.models.search import Embedding, SearchDoc
from ontyra.ports import DecisionError, Embedder, StatementComparer, Store
from ontyra.services.context import Caller
from ontyra.services.dedupe import anchors_compatible, is_update
from ontyra.services.indexing import embedding_id

log = logging.getLogger(__name__)


class ConsolidationService:
    def __init__(
        self,
        store: Store,
        embedder: Embedder | None,
        settings: ConsolidationSettings,
        comparer: StatementComparer | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.settings = settings
        self.comparer = comparer

    async def scan(self, caller: Caller, request: ScanRequest) -> ScanReport:
        cfg = self.settings
        docs = await self.store.docs(
            caller.visible_owners(), kinds=request.kinds, limit=cfg.max_memories
        )
        report = ScanReport(
            scanned=len(docs),
            unembedded=len(docs),
            decision_model=self.comparer.name if self.comparer else None,
        )
        if self.embedder is None:
            report.note = "no embedder is configured: duplicates are found by similarity"
            return report
        by_rev = {d.rev: d for d in docs}
        found = await self.store.get_many([embedding_id(d.rev) for d in docs])
        vectors = {
            e.rev: e.vector
            for e in found.values()
            if isinstance(e, Embedding) and e.model == self.embedder.name
        }
        report.unembedded = len(docs) - len(vectors)

        pairs = await self._candidates(docs, by_rev, vectors)
        texts = await self._texts({r for pair in pairs for r in pair[:2]})
        duplicates: list[tuple[PairScore, SearchDoc, SearchDoc]] = []
        for a_rev, b_rev, cosine in pairs:
            a, b = by_rev[a_rev], by_rev[b_rev]
            ta, tb = texts.get(a_rev, a.text), texts.get(b_rev, b.text)
            if is_update(ta, tb):
                if request.updates:
                    old, new = sorted((a, b), key=lambda d: d.created_at)
                    report.updates.append(
                        UpdateCandidate(
                            older=_member(old, texts), newer=_member(new, texts), cosine=cosine
                        )
                    )
                continue
            score = await self._judge(a, b, ta, tb, cosine, report)
            if score is not None:
                duplicates.append((score, a, b))
        report.duplicates = _clusters(duplicates, texts)
        report.updates.sort(key=lambda u: -u.cosine)
        return report

    async def _candidates(
        self,
        docs: Sequence[SearchDoc],
        by_rev: dict[RecordId, SearchDoc],
        vectors: dict[RecordId, list[float]],
    ) -> list[tuple[RecordId, RecordId, float]]:
        """Close same-kind, same-owner pairs that apply in a common place, each once."""
        seen: set[tuple[str, str]] = set()
        out: list[tuple[RecordId, RecordId, float]] = []
        for doc in docs:
            vector = vectors.get(doc.rev)
            if vector is None:
                continue
            hits = await self.store.search_vector(
                vector, [doc.owner], kinds=[doc.kind], limit=self.settings.neighbours + 1
            )
            for hit in hits:
                other = by_rev.get(hit.rev)
                if (
                    other is None
                    or other.rev == doc.rev
                    or hit.score < self.settings.candidate_cosine
                ):
                    continue
                if not anchors_compatible(doc.anchors, other.anchors):
                    continue
                first, second = sorted((str(doc.rev), str(other.rev)))
                key = (first, second)
                if key in seen:
                    continue
                seen.add(key)
                out.append((doc.rev, other.rev, hit.score))
        return out

    async def _texts(self, revs: set[RecordId]) -> dict[RecordId, str]:
        """The statement of each revision (its `text`, not the longer indexed text)."""
        found = await self.store.get_many(list(revs))
        return {r: rec.text for r, rec in found.items() if isinstance(rec, RevisionBase)}

    async def _judge(
        self, a: SearchDoc, b: SearchDoc, ta: str, tb: str, cosine: float, report: ScanReport
    ) -> PairScore | None:
        """A `PairScore` when the pair is a duplicate, `None` when it is not."""
        cfg = self.settings
        if self.comparer is not None:
            try:
                ab, ba = await asyncio.gather(
                    self.comparer.same(ta, tb), self.comparer.same(tb, ta)
                )
            except DecisionError as exc:
                log.warning("decision model could not compare a pair: %s", exc)
                report.model_errors += 1
            else:
                same = min(ab, ba)
                if same < cfg.confirm:
                    report.rejected += 1
                    return None
                return PairScore(
                    a=a.memory, b=b.memory, cosine=cosine, same=same, judged_by="model"
                )
        if cosine >= cfg.duplicate_cosine:
            return PairScore(a=a.memory, b=b.memory, cosine=cosine, judged_by="cosine")
        return None


def _member(doc: SearchDoc, texts: dict[RecordId, str]) -> Member:
    return Member(
        memory=doc.memory,
        revision=doc.rev,
        kind=doc.kind,
        text=texts.get(doc.rev, doc.text),
        created_at=doc.created_at,
    )


def _clusters(
    found: Sequence[tuple[PairScore, SearchDoc, SearchDoc]], texts: dict[RecordId, str]
) -> list[DuplicateCluster]:
    """Connected components of the duplicate pairs, largest first."""
    parent: dict[RecordId, RecordId] = {}

    def find(x: RecordId) -> RecordId:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    docs: dict[RecordId, SearchDoc] = {}
    for _, a, b in found:
        docs[a.memory], docs[b.memory] = a, b
        parent[find(a.memory)] = find(b.memory)
    groups: dict[RecordId, list[SearchDoc]] = {}
    pairs: dict[RecordId, list[PairScore]] = {}
    for memory, doc in docs.items():
        groups.setdefault(find(memory), []).append(doc)
    for score, a, _ in found:
        pairs.setdefault(find(a.memory), []).append(score)
    out: list[DuplicateCluster] = []
    for root, members in groups.items():
        members.sort(key=lambda d: d.created_at)
        out.append(
            DuplicateCluster(
                members=[_member(d, texts) for d in members],
                pairs=sorted(pairs[root], key=lambda p: -p.cosine),
                keep=members[0].memory,
            )
        )
    return sorted(out, key=lambda c: (-len(c.members), -c.pairs[0].cosine))
