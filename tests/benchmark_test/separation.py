"""How well does an embedder separate relevant from irrelevant memories, with no thresholds?

`PYTHONPATH=tests uv run python -m benchmark_test.separation profiles.json [world]` embeds every memory of
the world and each recall case's query, then reports per profile: mean AUC (chance that a
relevant memory outranks an irrelevant one), cases where the query's top hit is relevant, and
cases where *every* relevant memory outranks *every* irrelevant one (a perfect cut exists).
It separates "this model cannot tell" from "the pipeline's cut-off cannot use what it tells".
Relevant = `include`; irrelevant = everything not included, allowed or excluded.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

from benchmark_test.framework.config import Profile
from benchmark_test.framework.models import load_cases, load_world
from ontyra.adapters import build_embedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def measure(profile: Profile, world_id: str) -> dict[str, float]:
    world = load_world(world_id)
    cases = [
        c
        for c in load_cases("recall")
        if c.world == world_id and c.mode == "recall" and c.query and c.include and not c.silent
    ]
    embedder = build_embedder(profile.embedding())
    if embedder is None:
        raise ValueError("profile has no embedder")
    texts = {
        m.key: f"{m.text} {m.fields.get('rule') or m.fields.get('chosen') or ''}".strip()
        for m in world.memories
    }
    keys = list(texts)
    vectors = dict(zip(keys, await embedder.embed_documents([texts[k] for k in keys]), strict=True))
    aucs: list[float] = []
    top1 = perfect = 0
    relevant_cos: list[float] = []
    best_irrelevant: list[float] = []
    for case in cases:
        query = await embedder.embed_query(case.query or "")
        score = {k: cosine(query, vectors[k]) for k in keys}
        pos = list(case.include)
        neg = [k for k in keys if k not in {*case.include, *case.allow, *case.exclude}]
        aucs.append(
            statistics.mean(
                (score[a] > score[b]) + 0.5 * (score[a] == score[b]) for a in pos for b in neg
            )
        )
        top1 += max(score, key=lambda k: score[k]) in pos
        perfect += min(score[a] for a in pos) > max(score[b] for b in neg)
        relevant_cos += [score[k] for k in pos]
        best_irrelevant.append(max(score[k] for k in neg))
    return {
        "cases": len(cases),
        "auc": statistics.mean(aucs),
        "top1": top1,
        "perfect": perfect,
        "relevant_median": statistics.median(relevant_cos),
        "best_irrelevant_median": statistics.median(best_irrelevant),
    }


async def main(raw_profiles: list[dict[str, object]], world_id: str) -> None:
    head = (
        f"{'profile':<14} {'AUC':>6} {'top1':>7} {'perfect':>8} {'rel cos':>8} {'best irrel':>11}"
    )
    print(head)
    for raw in raw_profiles:
        profile = Profile.model_validate({k: v for k, v in raw.items() if k != "sweep"})
        m = await measure(profile, world_id)
        print(
            f"{profile.name:<14} {m['auc']:6.3f} {m['top1']:>3}/{m['cases']:<3} {m['perfect']:>4}/{m['cases']:<3}"
            f" {m['relevant_median']:8.3f} {m['best_irrelevant_median']:11.3f}"
        )


if __name__ == "__main__":
    loaded = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    asyncio.run(main(loaded, sys.argv[2] if len(sys.argv) > 2 else "quayside"))
