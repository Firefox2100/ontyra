"""Show what one case looked like to each lane: `PYTHONPATH=tests uv run python -m benchmark_test.explain CASE RESULT...`.

For each result file prints the labelled memories (expected, excluded, allowed) and the best few
others, with every lane's score and its rank among the memories that apply, and whether the
pipeline returned it. This answers "which was recalled, which was not, and by how much".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmark_test.framework.results import read_rows
from ontyra.calibration.models import Case, load_cases
from ontyra.calibration.ranking import LANES, Row, lane_value


def _rank_table(rows: list[Row]) -> dict[str, dict[str, int]]:
    top = max((r.bm25 or 0.0 for r in rows), default=0.0)
    pool = [r for r in rows if r.applies]
    out: dict[str, dict[str, int]] = {}
    for lane in LANES:
        scored = sorted(
            (r for r in pool if lane_value(r, lane, top)),
            key=lambda r: -(lane_value(r, lane, top) or 0),
        )
        out[lane] = {r.key: i for i, r in enumerate(scored, start=1)}
    return out


def show(case: Case, index: int, result: Path, rows: list[Row], returned: dict[str, int]) -> None:
    step = [case, *case.then][index]
    label = {k: "INCLUDE" for k in step.include}
    label.update({k: "exclude" for k in step.exclude})
    label.update({k: "allow" for k in step.allow if k not in label})
    top = max((r.bm25 or 0.0 for r in rows), default=0.0)
    ranks = _rank_table(rows)
    by_key = {r.key: r for r in rows}
    best = sorted((r for r in rows if r.applies), key=lambda r: -(r.score or 0))[:6]
    keys = list(dict.fromkeys([*label, *(r.key for r in best)]))
    head = f"{'memory':<22} {'label':<8} {'ret':>3} {'appl':>4} " + " ".join(
        f"{n:>9}" for n in ("bm25", "lexical", "cosine", "concept", "relev", "score")
    )
    print(f"\n-- {result.stem}  step {index}  query: {step.query!r}")
    print(head)
    for key in keys:
        r = by_key.get(key)
        if r is None:
            print(
                f"{key:<22} {label.get(key, ''):<8}  (not in the index: superseded, retracted or expired)"
            )
            continue
        cells = []
        for lane in ("bm25", "lexical", "cosine", "concept", "relevance", "score"):
            v = lane_value(r, lane, top)
            rank = ranks[lane].get(key)
            cells.append("        -" if v is None else f"{v:6.3f}#{rank or '-':<2}"[:9].rjust(9))
        print(
            f"{key:<22} {label.get(key, ''):<8} {returned.get(key, '-')!s:>3} {'yes' if r.applies else 'NO':>4} "
            + " ".join(cells)
        )


def main(case_id: str, paths: list[str]) -> int:
    cases = {c.id: c for c in load_cases("recall")}
    case = cases[case_id]
    print(f"{case.id}  tags={case.tags}\n{case.why}")
    for path in map(Path, paths):
        body = json.loads(path.read_text(encoding="utf-8"))
        recorded = next(c for c in body["cases"] if c["id"] == case_id)
        rows = read_rows(path)
        for index, step in enumerate(recorded["steps"]):
            got = rows.get((case_id, index))
            if got is None:
                continue
            returned = {r["key"]: r["rank"] for r in step["returned"]}
            show(case, index, path, [Row.from_list(r) for r in got], returned)
            if step["problems"]:
                print("   problems:", "; ".join(step["problems"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
