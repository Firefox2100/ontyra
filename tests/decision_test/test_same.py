"""Live: the duplicate scan's question, asked of the decision model with each variant."""

from __future__ import annotations

from decision_test.framework.config import DecisionBenchSettings
from decision_test.framework.same import evaluate, variants
from ontyra.calibration.duplicates import load_pairs


async def test_same(kev, production, settings: DecisionBenchSettings, recorder, capsys) -> None:
    pairs = [p for p in load_pairs() if not settings.only or settings.only in p.id]
    results = {}
    for name, scorer in variants(kev, production()).items():
        before = len(kev.seconds)
        results[name] = await evaluate(pairs, scorer)
        results[name]["seconds_per_pair"] = round(sum(kev.seconds[before:]) / len(pairs), 3)
    path = recorder("same", {"variants": results, "pairs": len(pairs)})
    with capsys.disabled():
        print(f"\nsame: {len(pairs)} pairs -> {path.name}")
        for name, r in results.items():
            at, best = r["at_threshold"], r["best_cutoff"]
            print(
                f"  {name:<12} auc {r['auc_dup_vs_rest']} (vs update {r['auc_dup_vs_update']},"
                f" vs distinct {r['auc_dup_vs_distinct']})  at {r['threshold']}: "
                f"P {at['precision']} R {at['recall']} (updates accepted {r['updates_accepted']},"
                f" distinct {r['distinct_accepted']})  best cut {best['threshold']}: F1 {best['f1']}"
                f"  {r['seconds_per_pair']}s/pair"
            )
