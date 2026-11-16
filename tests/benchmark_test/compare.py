"""Compare benchmark result files: `uv run python tests/benchmark_test/compare.py FILE...`.

With `--lanes [overall|tag:X|split:Y]` prints instead the threshold-free ranking quality of each
lane (R@k, MRR, nDCG, AUC; recorded by the suite). Prints one row per file (pass counts per split, precision, recall, latency), then pass counts
per tag, then the cases on which the files disagree. Reads JSON only; nothing is re-run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.2f}"


def load(paths: list[str]) -> list[dict[str, Any]]:
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    return sorted(runs, key=lambda r: (r.get("profile", ""), r["label"]))


def table(runs: list[dict[str, Any]]) -> list[str]:
    head = (
        f"{'profile':<46} {'pass':>7} {'tune':>7} {'hold':>7} {'prec':>5} {'rec':>5} {'ms/req':>7}"
    )
    lines = [head, "-" * len(head)]
    for run in runs:
        s = run["summary"]
        o = s["overall"]
        sp = s["by_split"]
        times = [st["ms"] for c in run["cases"] for st in c["steps"]]
        mean_ms = sum(times) / len(times) if times else 0.0

        def frac(cell: dict[str, Any]) -> str:
            return f"{cell['passed']}/{cell['cases']}"

        lines.append(
            f"{run.get('profile', run['label']):<46} {frac(o):>7} {frac(sp.get('tune', o)):>7}"
            f" {frac(sp.get('holdout', o)) if 'holdout' in sp else '-':>7}"
            f" {_pct(o['precision'])} {_pct(o['recall'])} {mean_ms:7.0f}"
        )
    return lines


def by_tag(runs: list[dict[str, Any]]) -> list[str]:
    tags = sorted({t for r in runs for t in r["summary"]["by_tag"]})
    names = [r.get("profile", r["label"]) for r in runs]
    width = max(len(n) for n in names)
    lines = [f"{'tag':<14} " + " ".join(f"{n[-12:]:>12}" for n in names)]
    for tag in tags:
        row = []
        for run in runs:
            cell = run["summary"]["by_tag"].get(tag)
            row.append("-" if cell is None else f"{cell['passed']}/{cell['cases']}")
        lines.append(f"{tag:<14} " + " ".join(f"{v:>12}" for v in row))
    del width
    return lines


def lanes(runs: list[dict[str, Any]], group: str = "overall") -> list[str]:
    """Threshold-free ranking quality of each lane, per run (`group`: overall, tag:X, split:Y)."""
    head = f"{'profile':<34} {'lane':<10} {'R@1':>5} {'R@3':>5} {'R@5':>5} {'MRR':>5} {'nDCG':>5} {'AUC':>6}"
    out = [f"[{group}]", head, "-" * len(head)]
    for run in runs:
        cells = run.get("retrieval", {}).get(group, {})
        for lane, m in cells.items():
            out.append(
                f"{run.get('profile', run['label']):<34} {lane:<10} {m['r@1']:5.2f} {m['r@3']:5.2f}"
                f" {m['r@5']:5.2f} {m['mrr']:5.2f} {m['ndcg@10']:5.2f} {m['auc']:6.3f}"
            )
    return out


def disagreements(runs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    ids = [c["id"] for c in runs[0]["cases"]]
    for cid in ids:
        verdicts = {}
        for run in runs:
            case = next((c for c in run["cases"] if c["id"] == cid), None)
            if case:
                verdicts[run.get("profile", run["label"])] = case["passed"]
        if len(set(verdicts.values())) > 1:
            ok = [n for n, v in verdicts.items() if v]
            lines.append(f"{cid:<30} passes in: {', '.join(ok) or 'none'}")
    return lines


def main(argv: list[str]) -> int:
    group = None
    if "--lanes" in argv:
        i = argv.index("--lanes")
        group = (
            argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].endswith(".json") else "overall"
        )
        argv = [a for a in argv if a not in ("--lanes", group)]
    if not argv:
        print(__doc__)
        return 2
    runs = load(argv)
    if group:
        print("\n".join(lanes(runs, group)))
        return 0
    print("\n".join(table(runs)))
    print()
    print("\n".join(by_tag(runs)))
    diff = disagreements(runs)
    if diff:
        print("\ncases that differ:")
        print("\n".join(diff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
