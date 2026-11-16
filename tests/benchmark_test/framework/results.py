"""Immutable result files: configuration + per-case outcomes + per-tag cells."""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmark_test.framework.models import Case
from benchmark_test.framework.ranking import COLUMNS, Row, mean_metrics, step_metrics
from benchmark_test.framework.score import Cell, StepScore

RESULTS = Path(__file__).resolve().parents[1] / "results"


def git_state() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                check=False,
                cwd=Path(__file__).parent,
            ).stdout.strip()
        except OSError:
            return ""

    return {"rev": run("rev-parse", "--short", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def summarise(outcomes: list[tuple[Case, list[StepScore]]]) -> dict[str, Any]:
    by_tag: dict[str, Cell] = {}
    by_split: dict[str, Cell] = {}
    overall = Cell()
    for case, steps in outcomes:
        for cell in (overall, by_split.setdefault(case.split, Cell())):
            cell.add(steps)
        for tag in case.tags:
            by_tag.setdefault(tag, Cell()).add(steps)
    return {
        "overall": overall.as_dict(),
        "by_split": {k: v.as_dict() for k, v in sorted(by_split.items())},
        "by_tag": {k: v.as_dict() for k, v in sorted(by_tag.items())},
    }


def step_lane_metrics(
    case: Case, scores: list[StepScore], domains: dict[str, str]
) -> list[dict[str, dict[str, float]]]:
    """Threshold-free metrics of each request of a case (empty dicts where no rows or labels)."""
    out: list[dict[str, dict[str, float]]] = []
    for step, scored in zip([case, *case.then], scores, strict=False):
        if scored.rows is None:
            out.append({})
            continue
        out.append(step_metrics([Row.from_list(r) for r in scored.rows], step, domains))
    return out


def retrieval_summary(
    outcomes: list[tuple[Case, list[StepScore]]], domains: dict[str, str]
) -> dict[str, Any]:
    """Mean lane metrics overall, per split and per tag: how good each lane ranks, before any cut-off."""
    per: list[tuple[Case, dict[str, dict[str, float]]]] = []
    for case, scores in outcomes:
        per += [(case, m) for m in step_lane_metrics(case, scores, domains) if m]
    groups: dict[str, list[dict[str, dict[str, float]]]] = {"overall": [m for _, m in per]}
    for case, m in per:
        groups.setdefault(f"split:{case.split}", []).append(m)
        for tag in case.tags:
            groups.setdefault(f"tag:{tag}", []).append(m)
    return {name: mean_metrics(items) for name, items in sorted(groups.items())}


def write_result(
    suite: str,
    label: str,
    meta: dict[str, Any],
    outcomes: list[tuple[Case, list[StepScore]]],
    directory: Path | None = None,
    domains: dict[str, str] | None = None,
) -> Path:
    """Write `<dir>/<suite>-<label>.json`; refuses to overwrite (results are immutable)."""
    target = directory or RESULTS
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{suite}-{label}.json"
    body = {
        "suite": suite,
        "label": label,
        "written_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "git": git_state(),
        **meta,
        "summary": summarise(outcomes),
        "retrieval": retrieval_summary(outcomes, domains or {}),
        "columns": list(COLUMNS),
        "cases": [
            {
                "id": case.id,
                "world": case.world,
                "tags": case.tags,
                "split": case.split,
                "passed": all(s.passed for s in steps),
                "steps": [
                    {
                        "problems": s.problems,
                        "ms": round(s.ms, 1),
                        "tokens": s.tokens,
                        "tolerated": s.tolerated,
                        "metrics": metrics,
                        "returned": [asdict(r) for r in s.returned],
                    }
                    for s, metrics in zip(
                        steps, step_lane_metrics(case, steps, domains or {}), strict=True
                    )
                ],
            }
            for case, steps in outcomes
        ],
    }
    with path.open("x", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2, default=str)
    write_rows(path, outcomes)
    return path


def rows_path(result: Path) -> Path:
    return result.with_suffix(".rows.jsonl.gz")


def write_rows(result: Path, outcomes: list[tuple[Case, list[StepScore]]]) -> None:
    """Per-memory lane scores of every request: one compact JSON line per (case, step), gzipped."""
    lines = [
        json.dumps({"case": case.id, "step": i, "rows": scored.rows}, separators=(",", ":"))
        for case, steps in outcomes
        for i, scored in enumerate(steps)
        if scored.rows is not None
    ]
    if lines:
        with gzip.open(rows_path(result), "wt", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def read_rows(result: Path) -> dict[tuple[str, int], list[list[Any]]]:
    """The recorded rows of a result file, by (case id, step index)."""
    path = rows_path(result)
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    return {(r["case"], r["step"]): r["rows"] for r in records}
