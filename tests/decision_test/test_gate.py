"""Live: the gate. Classification of messages, then what the gate does to recall end to end."""

from __future__ import annotations

from benchmark_test.framework.dialogue import Builder, load_dialogues, play_dialogue
from benchmark_test.framework.results import summarise
from benchmark_test.framework.score import StepScore
from decision_test.framework.config import DecisionBenchSettings
from decision_test.framework.gate import (
    classifiers,
    dialogue_items,
    gate_builder,
    load_items,
    regex_verdict,
    score,
)
from decision_test.framework.metrics import latency
from ontyra.calibration.models import Case

THRESHOLDS = (0.5, 0.7, 0.85, 0.95)


def _history(item):
    return [(e.say, e.reply) for e in item.history]


async def test_gate_classification(kev, settings: DecisionBenchSettings, recorder, capsys) -> None:
    items = [*load_items(), *dialogue_items(load_dialogues())]
    items = [i for i in items if not settings.only or settings.only in i.id]
    variants = {}
    for turns in (1, 2):
        for name, classify in classifiers(kev, turns).items():
            before = len(kev.seconds)
            verdicts = [await classify(_history(i), i.say) for i in items]
            variants[name] = score(items, verdicts, threshold=settings.gate_threshold)
            variants[name]["latency"] = latency(kev.seconds[before:])
    variants["regex-is_thin"] = score(
        items, [regex_verdict(_history(i), i.say) for i in items], threshold=settings.gate_threshold
    )
    variants["always-standalone"] = score(
        items, [{"standalone": 1.0} for _ in items], threshold=settings.gate_threshold
    )
    path = recorder("gate", {"variants": variants, "items": len(items)})
    with capsys.disabled():
        print(f"\ngate: {len(items)} messages -> {path.name}")
        for name, r in variants.items():
            at = r["at_threshold"]
            lat = r.get("latency", {}).get("p50")
            print(
                f"  {name:<20} acc {r['accuracy']} | refers_back auc {r['auc_refers_back']} "
                f"P {at['precision']} R {at['recall']} F1 {at['f1']} (best cut "
                f"{r['best_cutoff']['threshold']}: {r['best_cutoff']['f1']}) | filler auc "
                f"{r['auc_filler']}" + (f" | p50 {lat:.2f}s" if lat else "")
            )


async def test_gate_end_to_end(
    kev, world_app, worlds, settings: DecisionBenchSettings, recorder, capsys
) -> None:
    dialogues = [d for d in load_dialogues() if not settings.only or settings.only in d.id]
    classify = classifiers(kev, 1)["choice3-h1"]
    cache: dict = {}
    strategies: dict[str, Builder | None] = {
        "last": None,
        "users2": None,
        "adaptive": None,
        "union": None,
        "oracle": None,
    }
    for t in THRESHOLDS:  # the model's answers are cached, so a threshold costs no model call
        for widen in ("users2", "union"):
            strategies[f"gate-{widen}@{t}"] = gate_builder(
                classify, widen=widen, threshold=t, cache=cache
            )
    outcomes: dict[str, list[tuple[Case, list[StepScore]]]] = {s: [] for s in strategies}
    domains: dict[str, str] = {}
    for world_id in sorted({d.world for d in dialogues}):
        world = worlds(world_id)
        app, caller, keys = await world_app(world)
        domains = {m.key: m.domain for m in world.memories}
        universal = frozenset(m.key for m in world.memories if m.scope == "universal")
        twins = {m.key: m.duplicate_of for m in world.memories if m.duplicate_of}
        for dialogue in (d for d in dialogues if d.world == world_id):
            for strategy, builder in strategies.items():
                played = await play_dialogue(
                    app, caller, dialogue, strategy, keys, domains, universal, twins, builder
                )
                outcomes[strategy] += [(case, [s]) for case, s in played]
    rows = {}
    with capsys.disabled():
        print(
            f"\ngate end to end ({len(dialogues)} dialogues, gate model p50 "
            f"{latency(kev.seconds)['p50']:.2f}s)"
        )
        for strategy, played in outcomes.items():
            by_tag = summarise(played)["by_tag"]
            cells = {}
            for tag in ("standalone", "needs_history"):
                c = by_tag.get(tag)
                if c:
                    cells[tag] = {
                        "passed": c["passed"],
                        "cases": c["cases"],
                        "recall": c["recall"],
                        "precision": c["precision"],
                    }
            rows[strategy] = cells
            print(
                "  "
                + f"{strategy:<12}"
                + " | ".join(
                    f"{t}: {c['passed']}/{c['cases']} recall={c['recall']}"
                    for t, c in cells.items()
                )
            )
    recorder("gate-end-to-end", {"strategies": rows, "gate_calls": len(cache)})
