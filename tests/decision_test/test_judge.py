"""Live: recall's question (which candidates matter?) on the candidates of real recall requests."""

from __future__ import annotations

from decision_test.framework.config import DecisionBenchSettings
from decision_test.framework.judge import collect, evaluate
from decision_test.framework.metrics import latency


async def test_judge(
    kev, production, world_app, worlds, settings: DecisionBenchSettings, recorder, capsys
) -> None:
    world = worlds("commons")
    app, caller, keys = await world_app(world)
    requests = await collect(
        app,
        caller,
        keys,
        world,
        limit=settings.judge_cases,
        candidates=settings.judge_candidates,
        only=settings.only,
    )
    assert requests, "no recall case produced candidates"
    runs = {
        "choice": (production(format="choice").judge, False),
        "choice-shuffled": (production(format="choice").judge, True),
        "choice-200": (production(format="choice", memory_chars=200).judge, False),
        "noul": (production(format="noul").judge, False),
    }
    results = {}
    for name, (judge, shuffle) in runs.items():
        before = len(kev.seconds)

        async def timed(situation, memories, judge=judge):
            return await kev.timed(lambda: judge(situation, memories))

        results[name] = await evaluate(timed, requests, shuffle=shuffle)
        results[name]["latency"] = latency(kev.seconds[before:])
    path = recorder(
        "judge",
        {"variants": results, "requests": len(requests)},
        {"candidates": settings.judge_candidates, "blend": 0.5},
    )
    with capsys.disabled():
        print(f"\njudge: {len(requests)} requests -> {path.name}")
        for name, r in results.items():
            print(
                f"  {name:<16} top1 pipeline {r['top1_pipeline']} model {r['top1_model']} "
                f"blend {r['top1_blend']} | auc pipeline {r['auc_pipeline']} model "
                f"{r['auc_model']} blend {r['auc_blend']} | abstention auc {r['abstention_auc']}"
                f" ({r['silent']} silent) | p50 {r['latency']['p50']:.2f}s"
            )
