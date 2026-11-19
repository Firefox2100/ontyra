"""The `fusion` recall engine (plans/21): over-retrieve, filter by applicability first, one fused
score, a relative cut-off, an optional reranker.

1. Each lane (BM25, dense cosine, concept) is over-fetched (`overfetch`).
2. Lifecycle, anchors and qualifiers are applied *before* any ranking.
3. One score per memory: `alpha * lexical + (1 - alpha) * dense`, where lexical is BM25 relative
   to the best applicable hit times query coverage, and dense is the cosine rescaled between
   the mean (or minimum) cosine of the candidates and the best one. No lane maximum, no RRF.
4. If a reranker is configured it rescoring the top `rerank_candidates`; its probability is
   blended in with weight `rerank_weight`.
5. Silence when the best cosine is below `abstain_cosine` (calibrated per embedder).
6. If a decision model is configured (`ONTYRA_DECISION__*`) it is asked, in one request, which of
   the top `decision_candidates` matter for the situation; its probability is blended in with
   weight `decision_weight`. It is a signal, not an authority: when it fails or times out the
   deterministic result is used.
7. Keep candidates within the margin of the best score (`fusion_margin`, or `decision_margin`
   when the decision model answered).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ontyra.models.base import RecordId
from ontyra.models.decision import Judgement
from ontyra.models.recall import RecallPack, RecallRequest, RecallSettings
from ontyra.ports import Decider, DecisionError, Embedder, Reranker, Store
from ontyra.services.anchors import AnchorService
from ontyra.services.context import Caller
from ontyra.services.ontology import OntologyService
from ontyra.services.recall import Candidate, RecallService, Resolved
from ontyra.services.text import coverage

log = logging.getLogger(__name__)


@dataclass
class Scored:
    """Per-candidate numbers behind a fused score (also what the benchmark probe records)."""

    lexical: float = 0.0
    dense: float = 0.0
    cosine: float | None = None
    bm25: float | None = None
    concept: float = 0.0
    fused: float = 0.0
    rerank: float | None = None
    decision: float | None = None  # the decision model's probability, when it answered
    final: float = 0.0


@dataclass
class FusionResult:
    ranked: list[Candidate]
    scores: dict[RecordId, Scored]
    abstained: bool = False
    dropped: list[dict[str, Any]] = field(default_factory=lambda: [])
    decided: bool = False  # the decision model answered for this request
    none: float | None = None  # its probability that none of the candidates applies
    order: list[Candidate] = field(default_factory=lambda: [])  # every candidate, best first


def _probability(score: float) -> float:
    """Rerankers return probabilities or raw logits; map logits into (0, 1)."""
    return score if 0.0 <= score <= 1.0 else 1.0 / (1.0 + math.exp(-score))


def situation_text(request: RecallRequest, text: str) -> str:
    """The situation as a decision model reads it: where, under which conditions, what is asked."""
    lines: list[str] = []
    if request.context.uris:
        lines.append("Working at: " + ", ".join(request.context.uris))
    facts: list[str] = []
    for signal in request.context.signals:
        value = ""
        if signal.value not in (None, True):
            unit = f" {signal.unit}" if signal.unit else ""
            value = f" = {signal.value}{unit}"
        facts.append(f"{signal.concept}{value}")
    if facts:
        lines.append("Known conditions: " + ", ".join(facts))
    lines.append(f"Request from the user: {text}")
    return "\n".join(lines)


class FusionRecallService(RecallService):
    def __init__(
        self,
        store: Store,
        ontology: OntologyService,
        anchors: AnchorService,
        settings: RecallSettings,
        now: Callable[[], datetime],
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        rerank_top_n: int = 30,
        decider: Decider | None = None,
    ) -> None:
        super().__init__(
            store,
            ontology,
            anchors,
            settings,
            now,
            embedder=embedder,
            reranker=reranker,
            rerank_top_n=rerank_top_n,
        )
        self.decider = decider

    async def fuse(
        self,
        caller: Caller,
        text: str,
        ctx: Resolved,
        kinds: Sequence[str] = (),
        *,
        cut: bool = True,
        situation: str | None = None,
    ) -> FusionResult:
        """Rank (and, with `cut`, select) the candidates for `text`. `situation` is what the
        decision model is shown; without it, or without a decision model, none is asked."""
        owners = caller.visible_owners()
        cfg = self.cfg
        n = cfg.overfetch
        raw: dict[RecordId, Scored] = {}
        cands: dict[RecordId, Candidate] = {}

        def entry(rev: RecordId) -> Scored:
            cands.setdefault(rev, Candidate(rev))
            return raw.setdefault(rev, Scored())

        for hit in await self.store.search_text(text, owners, kinds=kinds, limit=n):
            entry(hit.rev).bm25 = hit.score
        if self.embedder is not None:
            vector = await self.embedder.embed_query(text)
            for hit in await self.store.search_vector(vector, owners, kinds=kinds, limit=n):
                entry(hit.rev).cosine = hit.score
        for w, doc, why in await self.concept_lane(text, ctx, owners, kinds, n):
            entry(doc.rev).concept = w
            cands[doc.rev].doc = doc
            cands[doc.rev].why.append(why)

        visible = await self.hydrate(cands, ctx)  # applicability before ranking
        pool = {c.rev: c for c in visible}
        top_bm25 = max((raw[r].bm25 or 0.0 for r in pool), default=0.0)
        cosines: list[float] = [c for r in pool if (c := raw[r].cosine) is not None]
        base = top_cos = 0.0
        if cosines:
            top_cos = max(cosines)
            base = sum(cosines) / len(cosines) if cfg.fusion_baseline == "mean" else min(cosines)
        span: float = (top_cos - base) or 1.0
        alpha = cfg.fusion_alpha if self.embedder is not None else 1.0
        for rev, c in pool.items():
            s = raw[rev]
            assert c.doc is not None
            if s.bm25 is not None and top_bm25 > 0:
                s.lexical = s.bm25 / top_bm25 * coverage(text, c.doc.text)
            if s.cosine is not None:
                s.dense = max(0.0, (s.cosine - base) / span)
            s.fused = alpha * s.lexical + (1 - alpha) * s.dense + cfg.concept_boost * s.concept
            s.final = s.fused
        ranked = sorted(pool.values(), key=lambda c: (-raw[c.rev].fused, str(c.rev)))
        if self.reranker is not None and ranked:
            await self._blend_reranker(text, ranked, raw)
            ranked.sort(key=lambda c: (-raw[c.rev].final, str(c.rev)))
        if not cut:
            self._describe(ranked, raw)
            return FusionResult(ranked, raw, order=ranked)
        if cosines and top_cos < cfg.abstain_cosine:
            self._describe(ranked, raw)
            dropped = [
                {"rev": str(c.rev), "reason": "silent: best cosine below abstain"} for c in ranked
            ]
            return FusionResult([], raw, True, dropped, order=ranked)
        judgement = None
        if self.decider is not None and situation is not None and ranked:
            judgement = await self._decide(situation, ranked, raw)
            if judgement is not None:
                ranked.sort(key=lambda c: (-raw[c.rev].final, str(c.rev)))
        self._describe(ranked, raw)
        margin = cfg.decision_margin if judgement is not None else cfg.fusion_margin
        best = raw[ranked[0].rev].final if ranked else 0.0
        keep: list[Candidate] = []
        dropped: list[dict[str, Any]] = []
        for c in ranked:
            if raw[c.rev].final >= best - margin and raw[c.rev].final > 0:
                keep.append(c)
            else:
                dropped.append(
                    {"rev": str(c.rev), "reason": f"below margin ({raw[c.rev].final:.2f})"}
                )
        return FusionResult(
            keep,
            raw,
            False,
            dropped,
            judgement is not None,
            judgement.none if judgement else None,
            ranked,
        )

    @staticmethod
    def _describe(ranked: list[Candidate], raw: dict[RecordId, Scored]) -> None:
        """Set each candidate's relevance and score to its final score, and say how it came."""
        for c in ranked:
            s = raw[c.rev]
            c.relevance = c.score = s.final
            c.why.append(
                f"fused {s.final:.2f} (lexical {s.lexical:.2f}, dense {s.dense:.2f}"
                + (f", cosine {s.cosine:.2f}" if s.cosine is not None else "")
                + (f", rerank {s.rerank:.2f}" if s.rerank is not None else "")
                + (f", decision {s.decision:.2f}" if s.decision is not None else "")
                + ")"
            )

    async def _decide(
        self, situation: str, ranked: list[Candidate], raw: dict[RecordId, Scored]
    ) -> Judgement | None:
        """Blend the decision model's probabilities into the final scores of the top
        candidates; None (and nothing changed) when it cannot answer."""
        assert self.decider is not None
        cfg = self.cfg
        top = ranked[: cfg.decision_candidates]
        try:
            judgement = await self.decider.judge(
                situation, [c.doc.text for c in top if c.doc is not None]
            )
        except DecisionError as exc:
            log.warning("decision model skipped: %s", exc)
            return None
        w = cfg.decision_weight
        for c, p in zip(top, judgement.scores, strict=False):
            raw[c.rev].decision = p
            raw[c.rev].final = (1 - w) * raw[c.rev].final + w * (p or 0.0)
        for c in ranked[len(top) :]:  # never ranked above a candidate the model scored
            raw[c.rev].final = (1 - w) * raw[c.rev].final
        return judgement

    async def _blend_reranker(
        self, text: str, ranked: list[Candidate], raw: dict[RecordId, Scored]
    ) -> None:
        assert self.reranker is not None
        top = ranked[: self.cfg.rerank_candidates]
        scores = await self.reranker.rerank(text, [c.doc.text for c in top if c.doc])
        w = self.cfg.rerank_weight
        for c, s in zip(top, scores, strict=False):
            p = _probability(s)
            raw[c.rev].rerank = p
            raw[c.rev].final = (1 - w) * raw[c.rev].fused + w * p
        for c in ranked[len(top) :]:  # never ranked above a reranked candidate by accident
            raw[c.rev].final = (1 - w) * raw[c.rev].fused

    async def recall(self, caller: Caller, request: RecallRequest) -> RecallPack:
        ctx = await self.resolve_context(request.context)
        text = " ".join([request.query or "", *request.hints]).strip()
        if not text:
            return await self._finish(caller, request, "recall", [], [], ctx, [])
        situation = situation_text(request, text) if request.decide else None
        result = await self.fuse(caller, text, ctx, request.kinds, situation=situation)
        return await self._finish(
            caller,
            request,
            "recall",
            [*result.ranked, *await self.triggered_extras(caller, request, result.ranked)],
            [],
            ctx,
            result.dropped,
            self._scoring(result),
        )

    def _scoring(self, result: FusionResult) -> dict[str, Any]:
        """The features of this request for the trace: lane scores of the best candidates in
        fused order, plus what decided the cut. Calibration replays the cut from them."""
        limit = self.cfg.trace_candidates
        if limit <= 0:
            return {}
        rows: list[dict[str, Any]] = []
        for rank, c in enumerate(result.order[:limit], 1):
            s = result.scores[c.rev]
            rows.append(
                {
                    "rev": str(c.rev),
                    "rank": rank,
                    "cosine": s.cosine,
                    "bm25": s.bm25,
                    "lexical": round(s.lexical, 4),
                    "dense": round(s.dense, 4),
                    "concept": s.concept,
                    "fused": round(s.fused, 4),
                    "rerank": s.rerank,
                    "decision": s.decision,
                    "final": round(s.final, 4),
                }
            )
        return {
            "engine": "fusion",
            "abstained": result.abstained,
            "decided": result.decided,
            "none": result.none,
            "pool": len(result.scores),
            "best_cosine": max(
                (s.cosine for s in result.scores.values() if s.cosine is not None), default=None
            ),
            "candidates": rows,
        }
