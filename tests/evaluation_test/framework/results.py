"""Result files of the model-driven suites: one immutable JSON file per area and run."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parents[1] / "results"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def summarise(rows: list[dict[str, Any]], *, by: str = "variant") -> dict[str, Any]:
    """Pass counts per variant and per tag within a variant (skipped rows are not counted)."""
    out: dict[str, Any] = {}
    for row in rows:
        if row.get("skipped"):
            continue
        cell = out.setdefault(row.get(by, "all"), {"passed": 0, "cases": 0, "by_tag": {}})
        cell["cases"] += 1
        cell["passed"] += bool(row["passed"])
        for tag in row.get("tags", []):
            t = cell["by_tag"].setdefault(tag, {"passed": 0, "cases": 0})
            t["cases"] += 1
            t["passed"] += bool(row["passed"])
    return out


def write_result(
    area: str,
    label: str,
    meta: dict[str, Any],
    rows: list[dict[str, Any]],
    directory: Path | None = None,
) -> Path:
    target = directory or RESULTS
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{area}-{slug(label)}.json"
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        rev = ""
    body = {
        "area": area,
        "label": label,
        "written_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "git": rev,
        **meta,
        "summary": summarise(rows),
        "rows": rows,
    }
    with path.open("x", encoding="utf-8") as fh:
        json.dump(body, fh, indent=1, default=str, ensure_ascii=False)
    return path
