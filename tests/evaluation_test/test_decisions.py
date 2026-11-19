"""Maintenance decisions: shown a task card, does the model choose what a careful reviewer
would (merge only true duplicates, supersede only a newer value, keep what cannot go stale,
defer when it cannot know)? The cards are built by the daemon's own card functions."""

from __future__ import annotations

from evaluation_test.framework.config import EvalSettings
from evaluation_test.framework.llm import Chat
from evaluation_test.framework.models import load_decision_cases
from evaluation_test.framework.run import build_card, run_decision_case
from ontyra.calibration.models import load_world


async def test_decisions(chat: Chat, settings: EvalSettings, recorder, capsys) -> None:
    cases = [c for c in load_decision_cases() if not settings.only or settings.only in c.id]
    world = load_world("commons")
    texts = {m.key: (m.text, m.kind) for m in world.memories}
    rows: list[dict] = []
    for case in cases:
        card = build_card(case, texts)
        for sample in range(settings.repeat):
            score, answer = await run_decision_case(chat, case, card)
            rows.append(
                {
                    "variant": "decide",
                    "id": case.id,
                    "sample": sample,
                    "tags": case.tags,
                    "split": case.split,
                    "passed": score.passed,
                    "decision": score.decision,
                    "accept": case.accept,
                    "problems": score.problems,
                    "answer": answer,
                }
            )
    path = recorder("decisions", rows)
    with capsys.disabled():
        print(
            f"\ndecisions [{settings.llm_model}] {sum(r['passed'] for r in rows)}/{len(rows)}"
            f" -> {path.name}"
        )
        for row in rows:
            if not row["passed"]:
                print(f"    {row['id']}: {'; '.join(row['problems'])}")
