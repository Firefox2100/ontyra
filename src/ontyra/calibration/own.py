"""A label-free check of the recall thresholds on the user's own store (read-only).

No labels and no model but the embedder are needed. Two kinds of probes are asked of the real
store through the fusion engine's scoring (`fuse(cut=False)`: nothing is recorded, no trace, no
usage):

* **on-topic**: the first words of a stored memory as the query. Its own memory should come
  first, and the best cosine of such a query is how high a *relevant* hit scores on this data;
* **off-topic**: the silent queries of the bundled invented world (things no memory here is
  about, unless the store is about the same things, which the report cannot know). The best
  cosine of those is how high an *irrelevant* hit scores.

The abstention threshold belongs between the two. The check reports where the current one sits
and whether the scale of this embedder on this data agrees with the one the benchmark found. It
suggests nothing on its own: the world calibration (`ontyra calibrate`) sets the values.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable
from typing import Any

from ontyra.app import Application
from ontyra.calibration.models import load_cases
from ontyra.config import Settings
from ontyra.models import RecordId
from ontyra.models.core import Actor
from ontyra.models.recall import RecallContext
from ontyra.models.search import SearchDoc
from ontyra.services.context import Caller
from ontyra.storage import Database

LEAD_WORDS = 8
MIN_WORDS = 5


def lead(text: str) -> str:
    """The first words of a memory: a short query that says what it says."""
    words = text.split()
    return " ".join(words[: max(MIN_WORDS, min(LEAD_WORDS, len(words) * 2 // 3))])


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * q / 100))], 3)


def verdict(report: dict[str, Any], abstain: float) -> list[str]:
    """What the two distributions say about the current abstention threshold."""
    on, off = report["on_topic_best_cosine"], report["off_topic_best_cosine"]
    notes: list[str] = []
    if abstain <= 0:
        notes.append("abstain_cosine is 0: recall never stays silent on a weak match.")
    elif abstain > on["p5"]:
        notes.append(
            f"abstain_cosine {abstain} is above the 5th percentile of on-topic probes"
            f" ({on['p5']}): relevant queries may be silenced on this data."
        )
    if abstain < off["p95"]:
        notes.append(
            f"abstain_cosine {abstain} is below the 95th percentile of off-topic probes"
            f" ({off['p95']}): unrelated queries may still get an answer on this data."
        )
    if on["p5"] <= off["p95"]:
        notes.append(
            "the cosine scales of on-topic and off-topic probes overlap: this embedder does not"
            " separate them on this data (or the store covers the off-topic themes)."
        )
    return notes or ["the current abstention threshold sits between the two distributions."]


async def probe(
    settings: Settings,
    *,
    limit: int = 60,
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """Run the probes against the configured database; only reads."""
    db = await Database.connect(
        settings.db.url,
        namespace=settings.db.namespace,
        database=settings.db.database,
        username=settings.db.username,
        password=settings.db.password.get_secret_value(),
        timeout=settings.db.timeout,
    )
    try:
        return await probe_app(Application.from_database(db, settings), limit, progress)
    finally:
        await db.close()


async def probe_app(
    app: Application, limit: int, progress: Callable[[str], None] = lambda _: None
) -> dict[str, Any]:
    service = app.recall
    if service.embedder is None:
        raise RuntimeError("no embedder is configured (ONTYRA_EMBEDDING__*)")
    fuse = getattr(service, "fuse", None)
    if fuse is None:
        raise RuntimeError("the probe needs ONTYRA_RECALL__ENGINE=fusion")
    caller = Caller(
        actor=Actor(id=RecordId("actor", "calibrate"), kind="service", name="calibrate")
    )
    docs = [
        d
        for d in await service.store.select(
            "search_doc", where_in={"owner": caller.visible_owners()}, limit=5000
        )
        if isinstance(d, SearchDoc)
    ]
    if len(docs) < 10:
        raise RuntimeError(f"only {len(docs)} live memories: too few to probe")
    sample = random.Random(7).sample(docs, min(limit, len(docs)))
    ctx = await service.resolve_context(RecallContext())

    async def best(text: str) -> tuple[float | None, list[Any]]:
        result = await fuse(caller, text, ctx, [], cut=False)
        cosines = [s.cosine for s in result.scores.values() if s.cosine is not None]
        return (max(cosines) if cosines else None), result.ranked

    progress(f"probing {len(sample)} memories of your store")
    on: list[float] = []
    first = 0
    for doc in sample:
        cosine, ranked = await best(lead(doc.text))
        if cosine is not None:
            on.append(cosine)
        first += bool(ranked) and ranked[0].rev == doc.rev
    silent = [c.query for c in load_cases("recall") if c.silent and c.query]
    progress(f"probing {len(silent)} off-topic queries")
    off = [c for q in silent if (c := (await best(q))[0]) is not None]
    if not on or not off:
        raise RuntimeError("the embedder returned no cosines: nothing to compare")
    abstain = service.cfg.abstain_cosine
    report: dict[str, Any] = {
        "embedder": service.embedder.name,
        "memories": len(docs),
        "probes": len(sample),
        "own_memory_first": round(first / len(sample), 3),
        "on_topic_best_cosine": {
            "p5": percentile(on, 5),
            "p50": percentile(on, 50),
            "mean": round(statistics.mean(on), 3),
        },
        "off_topic_best_cosine": {
            "p50": percentile(off, 50),
            "p95": percentile(off, 95),
            "max": round(max(off), 3),
        },
        "abstain_cosine": abstain,
    }
    report["notes"] = verdict(report, abstain)
    return report


def render(report: dict[str, Any]) -> str:
    on, off = report["on_topic_best_cosine"], report["off_topic_best_cosine"]
    return "\n".join(
        [
            f"Check on your own store ({report['memories']} live memories, embedder"
            f" {report['embedder']}; {report['probes']} on-topic probes):",
            f"  a memory's first words find the memory itself first in"
            f" {report['own_memory_first']:.0%} of probes",
            f"  best cosine of on-topic probes : median {on['p50']}, 5th percentile {on['p5']}",
            f"  best cosine of off-topic probes: median {off['p50']}, 95th percentile {off['p95']}"
            f" (highest {off['max']})",
            f"  current abstain_cosine: {report['abstain_cosine']}",
            *(f"  - {n}" for n in report["notes"]),
            "  (read-only; on-topic probes are easier than real requests, so treat the"
            " on-topic figures as a ceiling.)",
        ]
    )
