"""Live: can the decision model skip recall for pure pleasantries without ever skipping a request?

The daemon sees only the message (no history), so the gate is asked with none. A wrong "filler"
costs a missed recall (the worst error), a wrong "not filler" costs one needless lookup, so the
threshold is chosen for zero false positives first. Items: the gate's hand-written pleasantries
(`gate.json`) and the extra near-misses of `filler.json` (a thanks followed by a real request).
"""

from __future__ import annotations

import json
from pathlib import Path

from decision_test.framework.config import DecisionBenchSettings
from decision_test.framework.gate import Item, classifiers, load_items
from decision_test.framework.metrics import auc, latency, rounded

DATA = Path(__file__).resolve().parent / "data"
THRESHOLDS = (0.5, 0.7, 0.85, 0.95, 0.99)


def load_extra() -> list[Item]:
    return [
        Item.model_validate(raw) for raw in json.loads((DATA / "filler.json").read_text("utf-8"))
    ]


async def test_filler_without_history(
    kev, settings: DecisionBenchSettings, recorder, capsys
) -> None:
    items = [*load_items(), *load_extra()]
    classify = classifiers(kev, 0)["choice3-h0"]
    before = len(kev.seconds)
    scores = [(await classify([], i.say))["filler"] for i in items]
    truth = [i.label == "filler" for i in items]
    out = {
        "items": len(items),
        "fillers": sum(truth),
        "auc": rounded(
            auc(
                [s for s, t in zip(scores, truth, strict=True) if t],
                [s for s, t in zip(scores, truth, strict=True) if not t],
            )
        ),
        "latency": latency(kev.seconds[before:]),
        "thresholds": {},
        "wrong_skips": {},
    }
    for cut in THRESHOLDS:
        caught = sum(s >= cut and t for s, t in zip(scores, truth, strict=True))
        wrong = [i.say for i, s, t in zip(items, scores, truth, strict=True) if s >= cut and not t]
        out["thresholds"][str(cut)] = {
            "recall": rounded(caught / max(1, sum(truth))),
            "false_skips": len(wrong),
        }
        out["wrong_skips"][str(cut)] = wrong
    path = recorder("filler", out)
    with capsys.disabled():
        print(
            f"\nfiller: {len(items)} messages ({sum(truth)} pleasantries) -> {path.name}; auc {out['auc']}"
        )
        for cut, r in out["thresholds"].items():
            print(
                f"  p(filler) >= {cut}: catches {r['recall']:.0%} of pleasantries, {r['false_skips']} wrong skip(s)"
            )
