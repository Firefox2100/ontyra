"""Freeze the hash baseline: `PYTHONPATH=tests uv run python -m benchmark_test.baseline RESULT.json`.

Writes `data/baselines/hash.json`: the cases the built-in hash embedder is known to fail, with
the problems it had. The regression guard (`test_recall.py`) then asserts that no *other* case
fails and that none of the listed ones starts passing. Regenerate deliberately, after reading
the diff: a case leaving the list is an improvement, one joining it is a regression.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmark_test.framework.models import DATA


def freeze(result: Path) -> Path:
    run = json.loads(result.read_text(encoding="utf-8"))
    failing = {
        c["id"]: "; ".join(p for s in c["steps"] for p in s["problems"])
        for c in run["cases"]
        if not c["passed"]
    }
    out = DATA / "baselines" / "hash.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "written_from": result.name,
        "git": run.get("git"),
        "failing": dict(sorted(failing.items())),
    }
    out.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(freeze(Path(sys.argv[1])))
