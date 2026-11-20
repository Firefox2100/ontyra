"""Live: end-to-end scenarios in four arms (no memory, injected, tools, both).

`BENCH_LIVE=1 uv run pytest tests/evaluation_test/test_scenarios.py -s` (it costs model calls:
about 25 scenarios x 4 arms, several steps each). `BENCH_ARMS=none,both` runs fewer arms,
`BENCH_ONLY` filters scenario ids, `BENCH_LLM_*` picks the model (any OpenAI-compatible server,
e.g. LiteLLM in front of a hosted one). The result file keeps every transcript.
"""

from __future__ import annotations

from fastmcp import Client

from evaluation_test.framework.agent import tool_specs
from evaluation_test.framework.config import EvalSettings
from evaluation_test.framework.llm import Chat
from evaluation_test.framework.results import summarise
from evaluation_test.framework.scenario import compare, load_scenarios, run_scenario
from ontyra.calibration.models import load_world
from ontyra.transports.mcp import create_mcp


async def _specs(client: Client) -> list:  # type: ignore[type-arg]
    return tool_specs(
        [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in await client.list_tools()
        ]
    )


async def test_scenarios(chat: Chat, settings: EvalSettings, world_app, recorder, capsys) -> None:
    arms = [a.strip() for a in settings.arms.split(",") if a.strip()]
    scenarios = [s for s in load_scenarios() if not settings.only or settings.only in s.id]
    world = load_world("commons")
    shared = await world_app(world)
    rows: list[dict] = []
    for scenario in scenarios:
        for arm in (a for a in arms if a in scenario.arms):
            app, agent, _ = (
                await world_app(world.model_copy(update={"memories": []}))
                if scenario.fresh
                else shared
            )
            async with Client(create_mcp(lambda app=app: app, profile="lite")) as client:  # type: ignore[arg-type]
                row = await run_scenario(
                    chat,
                    scenario,
                    arm,
                    app=app,
                    agent=agent,
                    client=client,
                    specs=await _specs(client),
                )
            rows.append(row)
    path = recorder("scenarios", rows, {"arms": arms})
    summary = summarise(rows)
    with capsys.disabled():
        print(f"\nscenarios: {len(rows)} runs -> {path.name}")
        for arm, cell in summary.items():
            print(
                f"  {arm:<9} {cell['passed']}/{cell['cases']}  "
                + ", ".join(
                    f"{t} {v['passed']}/{v['cases']}" for t, v in sorted(cell["by_tag"].items())
                )
            )
        print("  ", compare(summary))
        for row in rows:
            if not row["passed"]:
                print(f"  FAIL {row['variant']:<9} {row['id']}: {'; '.join(row['failed'])}")
