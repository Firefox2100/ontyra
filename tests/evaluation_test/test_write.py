"""Writing memory: from a conversation, does the model write the right memory, well formed,
scoped, once per statement, and nothing when nothing durable was said?

The model has the lite tools and the real server answers (validation, findings, commit), so
a badly formed proposal is rejected like in production and the model may correct itself.
"""

from __future__ import annotations

from fastmcp import Client

from benchmark_test.framework.models import load_world
from evaluation_test.framework.agent import tool_specs
from evaluation_test.framework.config import EvalSettings
from evaluation_test.framework.llm import Chat
from evaluation_test.framework.models import load_write_cases
from evaluation_test.framework.run import run_write_case
from ontyra.transports.mcp import create_mcp


async def test_writing(chat: Chat, settings: EvalSettings, world_app, recorder, capsys) -> None:
    cases = [c for c in load_write_cases() if not settings.only or settings.only in c.id]
    # the ontology of the commons world, but no memories: the statements below must be new, or a
    # careful model rightly annotates what is already stored instead of writing it again
    empty = load_world("commons").model_copy(update={"memories": []})
    rows: list[dict] = []
    for case in cases:
        app, agent, _ = await world_app(empty)  # a fresh store per case: no case sees another's
        async with Client(create_mcp(lambda app=app: app, profile="lite")) as client:  # type: ignore[arg-type]
            listed = await client.list_tools()
            specs = tool_specs(
                [
                    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                    for t in listed
                ]
            )
            for sample in range(settings.repeat):
                for role, text in case.conversation:  # what the harness hook logs
                    await app.memory.record_episode(
                        agent, session=f"{case.id}-{sample}", role=role, content=text
                    )
                score, trace = await run_write_case(chat, case, specs, client)
                rows.append(
                    {
                        "variant": "write",
                        "id": case.id,
                        "sample": sample,
                        "tags": case.tags,
                        "split": case.split,
                        "passed": score.passed,
                        "problems": score.problems,
                        "rejected_calls": score.rejected,
                        "cited_evidence": score.evidence,
                        "calls": [
                            {
                                "name": e.call.name,
                                "args": e.call.args,
                                "status": e.data.get("status"),
                                "findings": [
                                    f"{f.get('code')}: {f.get('message')}"
                                    for f in (e.data.get("findings") or [])
                                ],
                                "text": e.text[:300] if e.error else "",
                            }
                            for e in trace.calls
                        ],
                        "answer": trace.final,
                    }
                )
    path = recorder("write", rows)
    with capsys.disabled():
        passed = sum(r["passed"] for r in rows)
        print(f"\nwriting [{settings.llm_model}] {passed}/{len(rows)} -> {path.name}")
        print(f"  retries after a rejected call: {sum(r['rejected_calls'] for r in rows)}")
        print(f"  proposals citing the episode: {sum(r['cited_evidence'] for r in rows)}")
        for row in rows:
            if not row["passed"]:
                print(f"    {row['id']}: {'; '.join(row['problems'])}")
