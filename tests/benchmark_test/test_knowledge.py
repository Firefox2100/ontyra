"""Knowledge suite: facts that cite sources, recalled beside a directive, under the settings a
deployment would choose for source tiers and the knowledge budget.

It measures; it asserts only that the data is consistent. Each run is compared across variants
(`default`, no tiers, no budget of its own) and written to `results/knowledge-<label>.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from benchmark_test.conftest import STAMP
from benchmark_test.framework.config import BenchSettings, Profile
from benchmark_test.framework.paths import TEST_DATA
from ontyra.app import Application
from ontyra.config import Settings
from ontyra.models import PolicyConfig
from ontyra.models.ontology import ConceptDefinition, concept_slug
from ontyra.models.proposals import AddMemory, Evidence, ProposalRequest
from ontyra.models.recall import RecallRequest, RecallSettings, SourceTier
from ontyra.models.sources import SourceCheckRequest, SourceRef
from ontyra.services import Caller
from ontyra.storage import Database, SchemaManager

DATA = TEST_DATA / "knowledge"
VARIANTS: dict[str, dict[str, Any]] = {
    "default": {},
    "no-tiers": {"source_tiers": []},
    "no-own-budget": {"knowledge_budget_tokens": 0},
}


def world() -> dict[str, Any]:
    return json.loads((DATA / "world.json").read_text(encoding="utf-8"))


def cases() -> list[dict[str, Any]]:
    return json.loads((DATA / "cases.json").read_text(encoding="utf-8"))


def test_the_data_is_consistent() -> None:
    w, cs = world(), cases()
    keys = [f["key"] for f in w["facts"]] + [d["key"] for d in w["directives"]]
    assert len(keys) == len(set(keys))
    concepts = {concept_slug(label) for label in w["concepts"]}
    for fact in w["facts"]:
        assert fact["subject"] in concepts, fact["key"]
        assert fact["sources"] and all(s["src"] in w["sources"] for s in fact["sources"])
    assert all(c["src"] in w["sources"] for c in w["checks"])
    assert len({c["id"] for c in cs}) == len(cs)
    for case in cs:
        named = [*case["include"], *case["exclude"], *case["allow"], *case.get("caveats", {})]
        assert set(named) <= set(keys), case["id"]
        assert bool(case["include"]) != case["silent"], case["id"]


async def build(
    profile: Profile, overrides: dict[str, Any]
) -> tuple[Application, Caller, dict[str, str], list[Database]]:
    w = world()
    embedding = profile.embedding()
    db = await Database.connect("mem://", namespace="bench", database="k" + uuid4().hex[:10])
    await SchemaManager(db, embedding_dim=embedding.dimension).apply()
    recall: RecallSettings = profile.recall_settings().model_copy(
        update={"source_tiers": [SourceTier(**t) for t in w["tiers"]], **overrides}
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        policy=PolicyConfig(default="auto_commit"),
        embedding=embedding,
        rerank=profile.rerank(),
        decision=profile.decision_settings(),
        recall=recall,
    )
    app = Application.from_database(db, settings)
    await app.actors.ensure_owner()
    human = await app.actors.caller(kind="human", name="owner")
    agent = await app.actors.caller(kind="agent", name="claude", harness="bench")
    for label in w["concepts"]:
        await app.ontology.define(human, ConceptDefinition(label=label))
    keys: dict[str, str] = {}
    ref = {k: v["uri"] for k, v in w["sources"].items()}
    for fact in w["facts"]:
        evidence = [
            Evidence(source=SourceRef(uri=ref[s["src"]], locator=s["locator"], quote=s["quote"]))
            for s in fact["sources"]
        ]
        op = AddMemory(
            kind="fact",
            ref=fact["key"],
            fields={
                "text": fact["text"],
                "subject": f"concept:{fact['subject']}",
                "predicate": "relates_to",
                "object": fact["object"],
                "scope": "universal",
            },
            evidence=evidence,
        )
        result = await app.memory.submit(agent, ProposalRequest(ops=[op]))
        assert result.status == "committed", (fact["key"], result.findings)
        keys[str(result.created[fact["key"]])] = fact["key"]
    for d in w["directives"]:
        op = AddMemory(
            kind="directive",
            ref=d["key"],
            fields={
                "text": d["text"],
                "rule": d["rule"],
                "strength": "must",
                "polarity": "do",
                "scope": "universal",
            },
        )
        result = await app.memory.submit(human, ProposalRequest(ops=[op]))
        assert result.status == "committed", (d["key"], result.findings)
        keys[str(result.created[d["key"]])] = d["key"]
    for check in w["checks"]:
        await app.sources.check(
            agent,
            SourceCheckRequest(uri=ref[check["src"]], state=check["state"], note=check["note"]),
        )
    await app.indexer.backfill()
    return app, agent, keys, [db]


async def play(app: Application, agent: Caller, keys: dict[str, str], case: dict[str, Any]) -> dict:
    pack = await app.recall.recall(agent, RecallRequest(query=case["query"], profile="rich"))
    order = [keys.get(str(i.memory), str(i.memory)) for i in pack.items()]
    in_lane = {keys.get(str(i.memory)) for i in pack.knowledge}
    problems: list[str] = []
    if case["silent"] and order:
        problems.append(f"expected silence, got {order}")
    problems += [f"missing {k}" for k in case["include"] if k not in order]
    problems += [f"unexpected {k}" for k in case["exclude"] if k in order]
    allowed = {*case["include"], *case["allow"]}
    stray = [k for k in order if k not in allowed]
    if case.get("top") and any(
        k in order and order.index(k) >= case["top"] for k in case["include"]
    ):
        problems.append(f"not in the top {case['top']}: {order}")
    for key, parts in case.get("caveats", {}).items():
        item = next((i for i in pack.items() if keys.get(str(i.memory)) == key), None)
        text = " ".join(item.caveats) if item else ""
        problems += [f"{key} lacks a caveat with {p!r}" for p in parts if p not in text]
    return {
        "id": case["id"],
        "tags": case["tags"],
        "passed": not problems,
        "problems": problems,
        "returned": order,
        "stray": stray,
        "knowledge": sorted(k for k in in_lane if k),
        "tokens": pack.tokens,
    }


async def test_knowledge_suite(
    profile: Profile, bench: BenchSettings, capsys: pytest.CaptureFixture[str]
) -> None:
    report: dict[str, Any] = {"profile": profile.name, "variants": {}}
    opened: list[Database] = []
    try:
        for name, overrides in VARIANTS.items():
            app, agent, keys, dbs = await build(profile, overrides)
            opened += dbs
            outcomes = [await play(app, agent, keys, case) for case in cases()]
            tags: dict[str, list[bool]] = {}
            for o in outcomes:
                for tag in o["tags"]:
                    tags.setdefault(tag, []).append(o["passed"])
            report["variants"][name] = {
                "passed": sum(o["passed"] for o in outcomes),
                "cases": len(outcomes),
                "by_tag": {t: f"{sum(v)}/{len(v)}" for t, v in sorted(tags.items())},
                "stray_total": sum(len(o["stray"]) for o in outcomes),
                "outcomes": outcomes,
            }
    finally:
        for db in opened:
            await db.close()
    directory = Path(bench.results_dir) if bench.results_dir else Path(__file__).parent / "results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"knowledge-{bench.run_label(profile, STAMP)}.json"
    if path.exists():
        pytest.fail(f"{path} exists: results are never overwritten (set BENCH_LABEL)")
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    with capsys.disabled():
        print(f"\nknowledge [{profile.name}] -> {path}")
        for name, v in report["variants"].items():
            print(
                f"  {name:<14} {v['passed']}/{v['cases']}  stray={v['stray_total']}  {v['by_tag']}"
            )
