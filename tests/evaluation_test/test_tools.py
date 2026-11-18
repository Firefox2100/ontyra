"""Tool selection: given a request, does the model call the right tool with usable arguments?

Played against three tool sets: `lite` (8 tools), `full` (25) and `full-bare` (25 with the
descriptions removed, names and schemas only). The gap between `full` and `full-bare` is what
the descriptions are worth; the cases that fail in `full` but not `lite` are tools that crowd
each other out. Nothing is executed: the first tool call is scored.
"""

from __future__ import annotations

import json

from evaluation_test.framework.agent import load_contract, tool_specs
from evaluation_test.framework.config import EvalSettings
from evaluation_test.framework.llm import Chat
from evaluation_test.framework.models import load_tool_cases
from evaluation_test.framework.run import run_tool_case

VARIANTS = {"lite": ("lite", True), "full": ("full", True), "full-bare": ("full", False)}


async def test_tool_selection(chat: Chat, settings: EvalSettings, recorder, capsys) -> None:
    cases = [c for c in load_tool_cases() if not settings.only or settings.only in c.id]
    rows: list[dict] = []
    for variant, (profile, descriptions) in VARIANTS.items():
        tools = load_contract(profile)
        specs = tool_specs(tools, descriptions=descriptions)
        schemas = {t["name"]: t["input_schema"] for t in tools}
        for case in cases:
            for sample in range(settings.repeat):
                score, trace = await run_tool_case(chat, case, specs, schemas)
                rows.append(
                    {
                        "variant": variant,
                        "id": case.id,
                        "sample": sample,
                        "tags": case.tags,
                        "split": case.split,
                        "skipped": score.skipped,
                        "passed": score.passed,
                        "chosen": score.chosen,
                        "expected": case.expect,
                        "problems": score.problems,
                        "invalid_args": score.invalid_args,
                        "say": case.say,
                        "calls": [{"name": e.call.name, "args": e.call.args} for e in trace.calls],
                        "answer": trace.final,
                    }
                )
    path = recorder("tools", rows, {"tool_sets": {k: v[0] for k, v in VARIANTS.items()}})
    summary = json.loads(path.read_text("utf-8"))["summary"]
    with capsys.disabled():
        print(f"\ntool selection [{settings.llm_model}] -> {path.name}")
        for variant, cell in summary.items():
            bad_args = sum(1 for r in rows if r["variant"] == variant and r["invalid_args"])
            print(
                f"  {variant:<10} {cell['passed']}/{cell['cases']}"
                f"  (calls with schema-invalid arguments: {bad_args})"
            )
        for row in rows:
            if row["variant"] == "full" and not row["passed"] and not row["skipped"]:
                print(f"    full  {row['id']}: {'; '.join(row['problems'])}")
