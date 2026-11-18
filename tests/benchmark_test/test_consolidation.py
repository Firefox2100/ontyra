"""Duplicate scan suite: how many of the planted duplicates are clustered, and how many pairs it
puts together that are not the same statement. Measures, never averages with recall; with the
hash embedder (lexical, no model) it only guards against false merges.

A profile may configure a decision model (`decision`) and the scan thresholds
(`consolidation`); the result file records which.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmark_test.conftest import STAMP
from benchmark_test.framework.config import BenchSettings, Profile
from benchmark_test.framework.duplicates import score
from benchmark_test.framework.models import world_ids
from benchmark_test.framework.results import RESULTS, git_state
from ontyra.models.consolidation import ScanRequest


async def test_duplicate_scan(world_app, worlds, bench: BenchSettings, profile: Profile, capsys):
    out: dict[str, object] = {}
    for world_id in world_ids():
        world = worlds(world_id)
        if not any(m.duplicate_of for m in world.memories):
            continue
        app, caller, keys = await world_app(world)
        report = await app.consolidation.scan(caller, ScanRequest())
        s = score(world, report, keys)
        out[world_id] = {
            "planted": s.planted,
            "found": s.found,
            "recall": round(s.recall, 3),
            "reported_pairs": s.reported,
            "precision": round(s.precision, 3),
            "missed": s.missed,
            "false_pairs": s.false,
            "clusters": len(report.duplicates),
            "updates_reported": len(report.updates),
            "rejected_by_model": report.rejected,
            "model_errors": report.model_errors,
            "judged_by_model": sum(
                p.judged_by == "model" for c in report.duplicates for p in c.pairs
            ),
            "decision_model": report.decision_model,
            "scanned": report.scanned,
            "unembedded": report.unembedded,
        }
        with capsys.disabled():
            print(
                f"\nduplicates [{profile.name}] {world_id}: recall {s.recall:.2f} "
                f"({s.found}/{s.planted}) precision {s.precision:.2f} "
                f"({s.correct}/{s.reported} pairs) model={report.decision_model} "
                f"rejected={report.rejected} errors={report.model_errors}"
            )
            for pair in s.false:
                print("  false:", pair)
        assert report.unembedded == 0
        if profile.baseline:  # the lexical embedder: only never merge things that differ
            assert s.precision >= 0.9, s.false

    directory = Path(bench.results_dir) if bench.results_dir else RESULTS
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"duplicates-{bench.run_label(profile, STAMP)}.json"
    meta = {
        "profile": profile.name,
        "embedding": profile.embedding().model_dump(exclude={"kwargs"}),
        "decision": profile.decision_settings().model_dump(exclude={"kwargs", "api_key"}),
        "consolidation": profile.consolidation_settings().model_dump(),
        "git": git_state(),
    }
    path.write_text(json.dumps({"meta": meta, "worlds": out}, indent=2), encoding="utf-8")
