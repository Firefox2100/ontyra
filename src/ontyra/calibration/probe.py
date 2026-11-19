"""Record every lane's score for every memory of the pool, for one request.

The pass/fail pipeline only shows what was returned. A probe runs the same lanes with the
candidate limit raised to the whole pool and records, per memory: raw BM25, raw cosine, concept
weight, query coverage, whether the memory applies in the context, and the pipeline's own
relevance and score. Nothing is written to the database: it calls read-only service stages.
"""

from __future__ import annotations

import copy
from typing import Any

from ontyra.app import Application
from ontyra.calibration.models import Step
from ontyra.models.search import SearchDoc
from ontyra.services import Caller
from ontyra.services.recall import Candidate
from ontyra.services.recall_fusion import FusionRecallService
from ontyra.services.text import coverage


class Probe:
    def __init__(self, app: Application, caller: Caller, keys: dict[str, str]) -> None:
        self.app = app
        self.caller = caller
        self.keys = keys  # memory id -> world key
        self.docs: list[SearchDoc] = []

    async def prepare(self) -> None:
        owners = self.caller.visible_owners()
        self.docs = await self.app.recall.store.docs(owners, limit=5000)
        service = copy.copy(self.app.recall)
        service.cfg = self.app.recall.cfg.model_copy(
            update={"candidates": max(len(self.docs), 50), "overfetch": max(len(self.docs), 50)}
        )
        self.service = service

    async def rows(self, step: Step, session: str | None) -> list[list[Any]] | None:
        """Per-memory scores for a request with text; None for a briefing (no lanes)."""
        text = " ".join([step.query or "", *step.hints]).strip()
        if not text:
            return None
        store = self.app.recall.store
        owners = self.caller.visible_owners()
        context = step.context.model_copy(update={"session": session or step.context.session})
        resolved = await self.service.resolve_context(context)
        pool = len(self.docs)
        bm25 = {h.rev: h.score for h in await store.search_text(text, owners, limit=pool)}
        cos: dict[Any, float] = {}
        if self.app.embedder is not None:
            vector = await self.app.embedder.embed_query(text)
            cos = {h.rev: h.score for h in await store.search_vector(vector, owners, limit=pool)}
        cands = await self.service.query_candidates(self.caller, text, resolved, [])
        for doc in self.docs:
            cands.setdefault(doc.rev, Candidate(doc.rev, doc=doc))
        hydrated = {
            c.rev: c
            for c in await self.service.hydrate(cands, resolved, only_applicable=False, query=text)
        }
        await self.service.rank(list(hydrated.values()), query=text, ranked_by_text=True)
        fused: dict[Any, Any] = {}
        if isinstance(self.service, FusionRecallService):
            result = await self.service.fuse(self.caller, text, resolved, [], cut=False)
            applicable = {c.rev for c in result.ranked}
            fused = {rev: s for rev, s in result.scores.items() if rev in applicable}
        out: list[list[Any]] = []
        for doc in self.docs:
            key = self.keys.get(str(doc.memory))
            if key is None:
                continue
            cand = hydrated.get(doc.rev)
            applies = bool(cand and cand.assessment and cand.assessment.include)
            lanes = cands[doc.rev].lanes
            out.append(
                [
                    key,
                    _r(bm25.get(doc.rev)),
                    _r(cos.get(doc.rev)),
                    _r(lanes.get("concept")),
                    _r(coverage(text, doc.text)),
                    int(applies),
                    _r(cand.relevance) if cand else None,
                    _r(cand.score) if cand else None,
                    _r(fused[doc.rev].fused) if doc.rev in fused else None,
                    _r(fused[doc.rev].rerank) if doc.rev in fused else None,
                    _r(fused[doc.rev].final) if doc.rev in fused else None,
                ]
            )
        return out


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)
