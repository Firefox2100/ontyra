"""Immutable result files of the decision benchmark: `<task>-<label>.json`, never overwritten."""

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


def write_result(
    task: str, label: str, meta: dict[str, Any], body: dict[str, Any], directory: Path | None = None
) -> Path:
    target = directory or RESULTS
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{task}-{slug(label)}.json"
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        rev = ""
    document = {
        "task": task,
        "label": label,
        "written_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "git": rev,
        **meta,
        **body,
    }
    with path.open("x", encoding="utf-8") as fh:
        json.dump(document, fh, indent=1, default=str, ensure_ascii=False)
    return path


def headline(summary: dict[str, Any], keys: tuple[str, ...]) -> str:
    return "  ".join(f"{k}={summary.get(k)}" for k in keys)
