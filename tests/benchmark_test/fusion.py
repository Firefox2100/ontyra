"""Compare retrieval methods offline: `PYTHONPATH=tests uv run python -m benchmark_test.fusion RESULT...`.

Reads result files that carry per-memory lane scores (written by the recall suite), and prints,
per file, each lane and fusion (RRF, convex combination swept over alpha) with its metrics on
the tune and holdout splits. The best convex combination is chosen on tune only. Metrics are
threshold-free: they say how well a method *orders* memories, before any cut-off.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmark_test.framework.fusion import alpha_sweep, evaluate, pick_best, standard_methods
from benchmark_test.framework.results import read_rows
from ontyra.calibration.models import Case, Step, load_cases, load_world
from ontyra.calibration.ranking import Row


def requests_of(result: Path, cases: dict[str, Case]) -> list[tuple[Case, Step, list[Row]]]:
    out: list[tuple[Case, Step, list[Row]]] = []
    for (case_id, index), rows in read_rows(result).items():
        case = cases.get(case_id)
        if case is None:
            continue
        step = [case, *case.then][index]
        out.append((case, step, [Row.from_list(r) for r in rows]))
    return out


def fmt(m: dict[str, float]) -> str:
    if not m:
        return "      -"
    return f"{m['r@1']:.2f} {m['r@5']:.2f} {m['mrr']:.2f} {m['ndcg@10']:.2f} {m['auc']:.3f}"


def main(paths: list[str]) -> int:
    cases = {c.id: c for c in load_cases("recall")}
    world = load_world("commons")
    domains = {m.key: m.domain for m in world.memories}
    for path in map(Path, paths):
        name = json.loads(path.read_text(encoding="utf-8")).get("profile", path.stem)
        requests = requests_of(path, cases)
        if not requests:
            print(f"{path.name}: no recorded lane scores")
            continue
        print(f"\n== {name}  ({len(requests)} requests)")
        print(
            f"{'method':<32} {'tune: R@1  R@5  MRR  nDCG  AUC':<34} holdout: R@1  R@5  MRR  nDCG  AUC"
        )
        for label, method in standard_methods().items():
            tune = evaluate(method, requests, domains, split="tune")
            hold = evaluate(method, requests, domains, split="holdout")
            print(f"{label:<32} {fmt(tune):<34} {fmt(hold)}")
        best, result = pick_best(alpha_sweep(), requests, domains)
        print(f"{best + ' (best on tune)':<32} {fmt(result['tune']):<34} {fmt(result['holdout'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
