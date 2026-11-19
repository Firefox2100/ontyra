"""Presentation: does the model act on what recall delivers, and in which rendering?

Each scenario runs a real recall over the world and renders the pack twice (`compact`, the
hook's default, and `rich`), plus once with no memory at all. The answer is checked with
patterns (the right tool, the stale value absent, the rule obeyed). `none` shows what the
memory is worth; compact against rich shows what the extra tokens buy.
"""

from __future__ import annotations

from evaluation_test.framework.config import EvalSettings
from evaluation_test.framework.llm import Chat
from evaluation_test.framework.models import load_presentation_cases
from evaluation_test.framework.run import run_presentation_case
from ontyra.calibration.models import load_world
from ontyra.models.recall import RecallRequest
from ontyra.services.recall_pack import estimate_tokens


async def test_presentation(
    chat: Chat, settings: EvalSettings, world_app, recorder, capsys
) -> None:
    cases = [c for c in load_presentation_cases() if not settings.only or settings.only in c.id]
    app, agent, _ = await world_app(load_world("commons"))
    rows: list[dict] = []
    for case in cases:
        packs: dict[str, str | None] = {"none": None}
        for profile in ("compact", "rich"):
            request = RecallRequest(context=case.context, query=case.query, profile=profile)  # type: ignore[arg-type]
            run = app.recall.briefing if case.mode == "briefing" else app.recall.recall
            packs[profile] = (await run(agent, request)).rendered or None
        for variant, rendered in packs.items():
            for sample in range(settings.repeat):
                problems, answer = await run_presentation_case(chat, case, rendered)
                rows.append(
                    {
                        "variant": variant,
                        "id": case.id,
                        "sample": sample,
                        "tags": case.tags,
                        "split": case.split,
                        "passed": not problems,
                        "problems": problems,
                        "context_tokens": estimate_tokens(rendered) if rendered else 0,
                        "answer": answer,
                    }
                )
    path = recorder("presentation", rows)
    with capsys.disabled():
        print(f"\npresentation [{settings.llm_model}] -> {path.name}")
        for variant in ("none", "compact", "rich"):
            mine = [r for r in rows if r["variant"] == variant]
            tokens = sum(r["context_tokens"] for r in mine) / max(len(mine), 1)
            print(
                f"  {variant:<8} {sum(r['passed'] for r in mine)}/{len(mine)}"
                f"  mean context tokens {tokens:.0f}"
            )
