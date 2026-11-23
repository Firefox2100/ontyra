"""Retraining the reranker on simulated feedback (plans/25, phase 3), off the production system.

The bundled worlds' labels stand in for a user's verdicts: pairs from the `tune` requests are the
feedback (clean, or with a share of the requests' verdicts flipped), and the `holdout` requests
are the trusted gate set. For each variant a Qwen3-style reranker is retrained from its base with
the worker's own training code and the gate numbers (mean reciprocal rank and pairwise accuracy
before and after) are printed: clean feedback should not lose, poisoned feedback should be caught
by the gate even though it fits its own validation pairs.

Needs a GPU-capable torch environment (`ontyra[worker]`) and a local Qwen3-Reranker directory:
`BENCH_WORKER_MODEL=/path/to/Qwen3-Reranker-0.6B uv run pytest tests/benchmark_test/test_worker_training.py -s`
(`BENCH_WORKER_DEVICE` picks the device, default auto). Skipped otherwise. Nothing is written
outside a temporary directory.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import random
from pathlib import Path
from typing import Any

import pytest

from ontyra.calibration.anchors import _requests, gate_requests  # pyright: ignore[reportPrivateUsage]
from ontyra.config import WorkerSettings
from ontyra.worker.live import Features, LiveOptions, update
from ontyra.worker.model import TorchScorer, template
from ontyra.worker.probe import probe, resolve_dtype
from ontyra.worker.training import Example, Options, train
from ontyra.worker.versions import Manifest

MODEL = os.environ.get("BENCH_WORKER_MODEL")
TOLERANCE = 0.01


def pairs(requests: list[dict[str, Any]], flip: float = 0.0) -> list[Example]:
    out: list[Example] = []
    for r in requests:
        digest = int(hashlib.sha1(f"poison{r['id']}".encode()).hexdigest()[:6], 16)
        bad = digest / 0xFFFFFF < flip
        for c in r["candidates"]:
            relevant = bool(c["relevant"]) != bad
            out.append(Example(r["query"], c["text"], 1.0 if relevant else 0.0))
    return out


@pytest.mark.skipif(not MODEL, reason="set BENCH_WORKER_MODEL to a local Qwen3-Reranker directory")
def test_retraining_on_clean_and_poisoned_feedback(tmp_path: Path, capsys: Any) -> None:
    torch: Any = importlib.import_module("torch")
    cfg = WorkerSettings(model=MODEL or "", batch_size=16, max_length=512)
    hw = probe(torch, os.environ.get("BENCH_WORKER_DEVICE", "auto"))
    assert not hw.problems, hw.problems
    manifest = Manifest("v0001", cfg.model, "qwen3", "now", template=template(cfg), note="baseline")
    scorer = TorchScorer(cfg, hw, resolve_dtype("auto", hw), manifest, tmp_path)
    gate = [{"query": r["query"], "candidates": r["candidates"]} for r in gate_requests()]
    tune = _requests("tune")
    rng = random.Random(5)
    small = rng.sample(tune, 40)
    variants = [
        ("clean, 40 requests", pairs(small), Options(epochs=3)),
        ("clean, all tune requests", pairs(tune), Options(epochs=2)),
        ("30% poisoned, 120 requests", pairs(rng.sample(tune, 120), 0.3), Options(epochs=3)),
        ("all flipped, 40 requests", pairs(small, 1.0), Options(epochs=3)),
    ]
    lines = [
        f"\nreranker retraining [{Path(cfg.model).name}, {hw.name}]: {len(tune)} tune / "
        f"{len(gate)} gate requests, tolerance {TOLERANCE}",
        f"{'feedback':<28} {'val auc':>13} {'gate mrr':>15} {'pairwise':>15} {'gate':>9}",
    ]
    verdicts: dict[str, bool] = {}
    for i, (name, examples, options) in enumerate(variants, start=1):
        m = train(scorer, examples, gate, options, tmp_path / f"v{i:04d}")
        b, a = m["gate_before"], m["gate_after"]
        passed = a["mrr"] >= b["mrr"] - TOLERANCE and a["pairwise"] >= b["pairwise"] - TOLERANCE
        verdicts[name] = passed
        lines.append(
            f"{name:<28} {m['auc_before']!s:>6}->{m['auc_after']!s:<6} "
            f"{b['mrr']:>6.3f}->{a['mrr']:<6.3f} {b['pairwise']:>6.3f}->{a['pairwise']:<6.3f} "
            f"{'activate' if passed else 'REJECT':>9}"
        )
    with capsys.disabled():
        print("\n".join(lines))
    assert verdicts["clean, all tune requests"]  # clean feedback must not be rejected
    assert not verdicts["all flipped, 40 requests"]  # the gate must catch a poisoned pool


@pytest.mark.skipif(not MODEL, reason="set BENCH_WORKER_MODEL to a local Qwen3-Reranker directory")
def test_live_head_edit_is_bounded_replayable_and_gated(tmp_path: Path, capsys: Any) -> None:
    """The live tier: the head alone, refitted from scratch from the snapshot each time."""
    torch: Any = importlib.import_module("torch")
    cfg = WorkerSettings(model=MODEL or "", batch_size=16, max_length=512)
    hw = probe(torch, os.environ.get("BENCH_WORKER_DEVICE", "auto"))
    assert not hw.problems, hw.problems
    manifest = Manifest("v0001", cfg.model, "qwen3", "now", template=template(cfg), note="baseline")
    scorer = TorchScorer(cfg, hw, resolve_dtype("auto", hw), manifest, tmp_path)
    gate = [{"query": r["query"], "candidates": r["candidates"]} for r in gate_requests()]
    tune = _requests("tune")
    small = random.Random(5).sample(tune, 40)
    features = Features()
    options = LiveOptions(tolerance=TOLERANCE)

    def run(name: str, examples: list[Example]) -> dict[str, Any]:
        m = update(scorer, features, examples, gate, options)
        g = m.get("gate_after") or {}
        with capsys.disabled():
            print(
                f"live {name:<26} applied={m['applied']!s:<5} reverted={m['reverted']!s:<5} "
                f"distance={m['distance']:<6} gate mrr {m['gate_before']['mrr']}->{g.get('mrr')}"
            )
        return m

    clean = run("clean, 40 requests", pairs(small))
    again = run("clean, same examples", pairs(small))
    poisoned = run("30% poisoned, 120 requests", pairs(random.Random(6).sample(tune, 120), 0.3))
    replay = run("replay without the poison", pairs(small))
    assert clean["applied"] and clean["distance"] <= options.max_distance + 1e-3
    assert again["distance"] == clean["distance"]  # a replay: the same examples, the same edit
    assert (poisoned["reverted"] and scorer.live_distance() == 0.0) or poisoned["reverted"]
    assert replay["applied"] and replay["distance"] == clean["distance"]  # nothing accumulated
