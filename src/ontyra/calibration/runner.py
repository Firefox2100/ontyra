"""Run the calibration: load the bundled world into a scratch store, record every lane's score
for every labelled request with the configured models, then search the settings offline.

The scratch store is in memory when the embedded engine is installed, otherwise a throwaway
database of the configured SurrealDB server in its own namespace (`ontyra_calibration`); the
deployed database is never touched. The recording is the only part that calls the models, and it
is saved (`recording`), so the search can be repeated or inspected without another call.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from ontyra.app import Application
from ontyra.calibration import blend, duplicates, search
from ontyra.calibration.models import Case, World, load_cases, load_world
from ontyra.calibration.probe import Probe
from ontyra.calibration.ranking import Row
from ontyra.calibration.world import populate
from ontyra.config import Settings
from ontyra.models import PolicyConfig
from ontyra.models.recall import RecallRequest
from ontyra.ports import Decider, Embedder, StatementComparer
from ontyra.services.recall_fusion import situation_text
from ontyra.storage import Database, SchemaManager

WORLD = "commons"
CALLS_PER_REQUEST = 3  # query embeddings per recorded request (the probe asks more than once)


class Counting:
    """Wraps an embedder and counts the requests made through it."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self.texts = 0
        self.requests = 0

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.requests += 1
        self.texts += len(texts)
        return await self._inner.embed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        self.requests += 1
        self.texts += 1
        return await self._inner.embed_query(text)


def requests_to_record(cases: list[Case]) -> list[Case]:
    return [c for c in cases if c.mode == "recall" and c.query and (c.include or c.silent)]


def estimate(settings: Settings | None = None) -> dict[str, int]:
    """What a run will ask of the models, before it asks."""
    world = load_world(WORLD)
    cases = requests_to_record(load_cases("recall"))
    pairs = duplicates.load_pairs()
    texts = len({t for p in pairs for t in (p.a, p.b)})
    out = {
        "memories": len(world.memories),
        "recall_requests": len(cases),
        "pair_texts": texts,
        "embedding_texts_at_most": len(world.memories) + CALLS_PER_REQUEST * len(cases) + texts,
        "rerank_requests": 0,
        "decision_requests": 0,
    }
    if settings is not None:
        if settings.rerank.provider != "none":
            out["rerank_requests"] = len(cases)
        if settings.decision.provider != "none":
            out["decision_requests"] = len(cases) + 2 * len(pairs)
    return out


async def _scratch(settings: Settings, embedding_dim: int) -> tuple[Database, Callable[[], Any]]:
    """An empty store to load the world into, and the function that throws it away."""
    try:
        db = await Database.connect("mem://", namespace="calibration", database=uuid4().hex[:10])
    except Exception:  # embedded engine not installed: a database of its own on the server
        name = "c" + uuid4().hex[:10]
        db = await Database.connect(
            settings.db.url,
            namespace="ontyra_calibration",
            database=name,
            username=settings.db.username,
            password=settings.db.password.get_secret_value(),
            timeout=settings.db.timeout,
        )

        async def drop() -> None:
            await db.query(f"REMOVE DATABASE IF EXISTS {name}")
            await db.close()

        try:
            await SchemaManager(db, embedding_dim=embedding_dim).apply()
        except BaseException:
            await drop()
            raise
        return db, drop

    async def close() -> None:
        await db.close()

    try:
        await SchemaManager(db, embedding_dim=embedding_dim).apply()
    except BaseException:
        await close()
        raise
    return db, close


async def _judge(
    decider: Decider,
    rows: dict[str, list[list[Any]]],
    cases: list[Case],
    texts: dict[str, str],
    candidates: int,
    progress: Callable[[str], None],
) -> dict[str, Any]:
    """Ask the decision model, as recall would, about the best candidates of every request."""
    by_id = {c.id: c for c in cases}
    gate = asyncio.Semaphore(4)
    out: dict[str, Any] = {}

    async def one(case: Case, parsed: list[Row]) -> None:
        ranked = sorted(
            (r for r in parsed if (r.fused if r.fused is not None else r.relevance or 0) > 0),
            key=lambda r: -(r.fused if r.fused is not None else r.relevance or 0),
        )[:candidates]
        if not ranked:
            return
        situation = situation_text(
            RecallRequest(context=case.context, query=case.query), case.query or ""
        )
        async with gate:
            try:
                answer = await decider.judge(situation, [texts[r.key] for r in ranked])
            except Exception as exc:  # one refused request must not lose the run
                progress(f"  decision model skipped {case.id}: {exc}")
                return
        out[case.id] = {
            "keys": [r.key for r in ranked],
            "scores": [s or 0.0 for s in answer.scores],
            "none": answer.none,
        }

    await asyncio.gather(
        *(one(by_id[i], [Row.from_list(r) for r in raw]) for i, raw in rows.items() if i in by_id)
    )
    return out


async def _same(decider: StatementComparer, pairs: list[duplicates.Pair]) -> list[float | None]:
    """The decision model's "same thing" score of every pair: the lower of both orders."""
    gate = asyncio.Semaphore(4)

    async def one(pair: duplicates.Pair) -> float | None:
        async with gate:
            try:
                return min(await decider.same(pair.a, pair.b), await decider.same(pair.b, pair.a))
            except Exception:
                return None

    return list(await asyncio.gather(*(one(p) for p in pairs)))


async def record(
    settings: Settings, *, progress: Callable[[str], None] = lambda _: None
) -> dict[str, Any]:
    """Call the configured models; returns the recording (rows of every request, pair cosines)."""
    from ontyra.adapters import build_decider, build_embedder

    embedder = build_embedder(settings.embedding)
    if embedder is None:
        raise RuntimeError("no embedder is configured (ONTYRA_EMBEDDING__*): nothing to calibrate")
    db, close = await _scratch(settings, embedder.dimension)
    try:
        run_settings = settings.model_copy(update={"policy": PolicyConfig(default="auto_commit")})
        app = Application.from_database(db, run_settings)
        counter = Counting(embedder)
        app.embedder = counter  # type: ignore[assignment]
        app.indexer.embedder = counter  # type: ignore[attr-defined]
        await app.actors.ensure_owner()
        human = await app.actors.caller(kind="human", name="owner")
        agent = await app.actors.caller(kind="agent", name="calibrate", harness="calibrate")
        world: World = load_world(WORLD)
        progress(f"loading {len(world.memories)} memories and embedding them")
        keys = await populate(app, human, agent, world)
        probe = Probe(app, agent, keys)
        await probe.prepare()
        cases = requests_to_record(load_cases("recall"))
        rows: dict[str, list[list[Any]]] = {}
        began = time.perf_counter()
        for i, case in enumerate(cases, 1):
            recorded = await probe.rows(case, None)
            if recorded:
                rows[case.id] = [r for r in recorded if r[5]]  # applicable memories only
            if i % 50 == 0:
                progress(f"  {i}/{len(cases)} requests")
        pairs = duplicates.load_pairs()
        progress(f"embedding {len(pairs)} statement pairs")
        cosines = await duplicates.measure(counter, pairs)
        decider = build_decider(settings.decision)
        judged: dict[str, Any] = {}
        same: list[float | None] = []
        if decider is not None:
            progress(f"asking the decision model about {len(rows)} requests")
            texts = {m.key: m.text for m in world.memories}
            judged = await _judge(
                decider, rows, cases, texts, settings.recall.decision_candidates, progress
            )
            progress(f"asking it whether {len(pairs)} statement pairs say the same")
            if isinstance(decider, StatementComparer):
                same = await _same(decider, pairs)
        return {
            "embedder": embedder.name,
            "dimension": embedder.dimension,
            "recall": settings.recall.model_dump(),
            "consolidation": settings.consolidation.model_dump(),
            "rows": rows,
            "pair_cosines": cosines,
            "decision": settings.decision.model_dump(exclude={"api_key"}),
            "judged": judged,
            "pair_same": same,
            "reranked": any(
                r.rerank is not None for raw in rows.values() for r in map(Row.from_list, raw)
            ),
            "embedding_requests": counter.requests,
            "embedding_texts": counter.texts,
            "seconds": round(time.perf_counter() - began, 1),
        }
    finally:
        await close()


def analyse(recording: dict[str, Any]) -> dict[str, Any]:
    """The suggestion and its evidence, from a recording (no model calls)."""
    world = load_world(WORLD)
    domains = {m.key: m.domain for m in world.memories}
    reqs = search.requests(recording["rows"], load_cases("recall"), domains)
    out: dict[str, Any] = {"embedder": recording["embedder"], "requests": len(reqs)}
    if reqs:
        s = search.suggest(reqs)
        recall = recording["recall"]
        current = search.Setting(
            recall["fusion_alpha"],
            recall["fusion_baseline"],
            recall["fusion_margin"],
            recall["abstain_cosine"],
            recall.get("concept_boost", 0.0),
        )
        recall_out: dict[str, Any] = {
            "suggested": s.setting.env(),
            "tune": s.tune,
            "holdout": s.holdout,
            "current": {
                "env": current.env(),
                "tune": search.score(reqs, current, "tune"),
                "holdout": search.score(reqs, current, "holdout"),
            },
            "plateau": s.plateau,
            "knife_edge": s.knife_edge,
            "best_cosine": s.best_cosine_percentiles,
            **search.separation(reqs),
        }
        recall_out["suggested"]["ONTYRA_RECALL__CALIBRATED_FOR"] = recording["embedder"]
        out["recall"] = recall_out
        out["blends"] = _blends(recording, reqs, s.setting, current)
    pairs = duplicates.load_pairs()
    if len(recording.get("pair_cosines", [])) == len(pairs):
        found = duplicates.suggest(pairs, recording["pair_cosines"])
        suggested = {}
        if found["duplicate_cosine"] is not None:
            suggested["ONTYRA_CONSOLIDATION__DUPLICATE_COSINE"] = f"{found['duplicate_cosine']:.3f}"
        if found["candidate_cosine"] is not None:
            suggested["ONTYRA_CONSOLIDATION__CANDIDATE_COSINE"] = f"{found['candidate_cosine']:.3f}"
        out["duplicates"] = {**found, "suggested": suggested}
    confirm = _confirm(recording)
    if confirm:
        out["confirm"] = confirm
    return out


def _blends(
    recording: dict[str, Any],
    reqs: list[search.Request],
    setting: search.Setting,
    deployed: search.Setting,
) -> dict[str, Any]:
    """Weight and margin for the decision model and for the reranker, when they were recorded."""
    recall = recording["recall"]
    out: dict[str, Any] = {}
    judged: dict[str, Any] = recording.get("judged") or {}
    if judged:
        signals = {i: dict(zip(j["keys"], j["scores"], strict=False)) for i, j in judged.items()}
        found = blend.suggest(
            reqs,
            setting,
            signals,
            current=(recall["decision_weight"], recall["decision_margin"]),
            deployed=deployed,
        )
        if found:
            out["decision"] = {
                **_blend_report(found),
                "suggested": {
                    "ONTYRA_RECALL__DECISION_WEIGHT": f"{found.weight:g}",
                    "ONTYRA_RECALL__DECISION_MARGIN": f"{found.margin:g}",
                },
            }
    if recording.get("reranked"):
        signals = {r.id: {x.key: x.rerank for x in r.pool if x.rerank is not None} for r in reqs}
        found = blend.suggest(
            reqs,
            setting,
            signals,
            current=(recall["rerank_weight"], recall["fusion_margin"]),
            deployed=deployed,
        )
        if found:
            out["rerank"] = {
                **_blend_report(found),
                "suggested": {
                    "ONTYRA_RECALL__RERANK_WEIGHT": f"{found.weight:g}",
                    "ONTYRA_RECALL__FUSION_MARGIN": f"{found.margin:g}",
                },
            }
    return out


def _blend_report(found: blend.Blend) -> dict[str, Any]:
    return {
        "weight": found.weight,
        "margin": found.margin,
        "tune": found.tune,
        "holdout": found.holdout,
        "plateau": found.plateau,
        "without": found.without,
        "current": found.current,
    }


def _confirm(recording: dict[str, Any]) -> dict[str, Any] | None:
    """The duplicate scan's `confirm` from the decision model's pair scores."""
    pairs = duplicates.load_pairs()
    scores: list[float | None] = recording.get("pair_same") or []
    if len(scores) != len(pairs):
        return None
    usable = [(p, s) for p, s in zip(pairs, scores, strict=True) if s is not None]
    dup = sorted(s for p, s in usable if p.label == "dup")
    rest = sorted(s for p, s in usable if p.label != "dup")
    if not dup or not rest:
        return None
    found = duplicates.precision_threshold(dup, rest)
    current = recording["consolidation"]["confirm"]
    at = (sum(s >= current for s in dup), sum(s >= current for s in rest))
    return {
        "pairs": len(usable),
        "failed": len(pairs) - len(usable),
        "dup_median": round(dup[len(dup) // 2], 3),
        "rest_p95": round(rest[int(len(rest) * 0.95)], 3),
        "suggested": (
            {"ONTYRA_CONSOLIDATION__CONFIRM": f"{found[0]:.3f}"} if found is not None else {}
        ),
        "recall_at_suggested": None if found is None else round(found[1], 3),
        "at_current": {"confirm": current, "duplicates": at[0], "others": at[1], "of": len(dup)},
    }


def _passes(cell: dict[str, Any]) -> str:
    (t, tn), (h, hn) = cell["tune"], cell["holdout"]
    return f"tune {t}/{tn}  holdout {h}/{hn}"


def render(report: dict[str, Any]) -> str:
    lines = [f"Calibration for embedder {report['embedder']} ({report['requests']} requests)"]
    r = report.get("recall")
    if r:
        c = r["current"]
        lines += [
            "",
            "Recall (fusion engine):",
            f"  current settings : {_passes(c)}",
            f"  suggested        : {_passes(r)}",
            f"  relevant above irrelevant: AUC {r['relevant_vs_irrelevant_auc']};"
            f" silent requests separated: AUC {r['silence_auc']}",
            f"  best-cosine percentiles of the requests: {r['best_cosine']}",
        ]
        if r["relevant_vs_irrelevant_auc"] is not None and r["relevant_vs_irrelevant_auc"] < 0.9:
            lines.append("  WARNING: this model separates poorly; no threshold will fix that.")
        if r["knife_edge"]:
            lines.append(
                "  NOTE: only one setting reaches the best score (a knife edge): treat the"
                " suggestion as approximate."
            )
        else:
            lines.append(
                f"  {r['plateau']} settings tie for the best tune score (the suggestion is"
                " the middle of them)."
            )
        lines += [
            "",
            "# suggested recall settings",
            *(f"{k}={v}" for k, v in r["suggested"].items()),
        ]
    d = report.get("duplicates")
    if d:
        lines += [
            "",
            f"Duplicate scan ({d['pairs']} labelled pairs): median cosine of duplicates"
            f" {d['dup_median']}, of other close pairs {d['rest_median']}"
            f" (95th percentile {d['rest_p95']}).",
        ]
        lines += [
            "# suggested duplicate-scan settings",
            *(f"{k}={v}" for k, v in d["suggested"].items()),
        ]
        if not d["suggested"]:
            lines.append(
                "# (cosine alone does not separate duplicates; leave that to a decision model)"
            )
    for name, title in (("decision", "decision model"), ("rerank", "reranker")):
        b = report.get("blends", {}).get(name)
        if not b:
            continue
        lines += [
            "",
            f"Blending the {title} into recall ({b['plateau']} settings tie for the best):",
            f"  without it       : {_passes(b['without'])}",
            f"  current          : {_passes(b['current'])}"
            f"  (weight {b['current']['weight']}, margin {b['current']['margin']})",
            f"  suggested        : {_passes(b)}  (weight {b['weight']}, margin {b['margin']})",
            f"# suggested {title} settings",
            *(f"{k}={v}" for k, v in b["suggested"].items()),
        ]
        if b["tune"][0] <= b["without"]["tune"][0]:
            lines.append(f"  NOTE: the {title} does not improve on the plain ranking here.")
    c = report.get("confirm")
    if c:
        cur = c["at_current"]
        lines += [
            "",
            f"Duplicate scan, decision model ({c['pairs']} pairs, {c['failed']} unanswered): median"
            f" score of duplicates {c['dup_median']}, of the others 95th percentile"
            f" {c['rest_p95']}."
            f" At confirm {cur['confirm']} it accepts {cur['duplicates']}/{cur['of']} duplicates"
            f" and {cur['others']} other pairs.",
            "# suggested confirm",
            *(f"{k}={v}" for k, v in c["suggested"].items()),
        ]
    lines += [
        "",
        "These values fit the bundled invented world: they set the model's score scale, not your",
        "domain. Apply them in the daemon's environment and restart.",
    ]
    return "\n".join(lines)


def save(path: Path, recording: dict[str, Any], report: dict[str, Any]) -> None:
    import json

    path.write_text(json.dumps({"recording": recording, "report": report}), encoding="utf-8")
