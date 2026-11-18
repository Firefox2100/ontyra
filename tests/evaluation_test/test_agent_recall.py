"""The agent writes the recall query: the model sees the conversation so far and the `recall`
tool, and the query (and hints) it sends are scored like the heuristics of the dialogue suite
in `benchmark_test` (same dialogues, same world, same scoring). It is the "llm" strategy:
does a model that understands the conversation beat concatenating earlier messages?
"""

from __future__ import annotations

from benchmark_test.framework.config import BenchSettings
from benchmark_test.framework.dialogue import Built, Turn, load_dialogues, play_dialogue
from benchmark_test.framework.models import load_world
from benchmark_test.framework.results import summarise
from evaluation_test.framework.agent import load_contract, tool_specs
from evaluation_test.framework.config import EvalSettings
from evaluation_test.framework.llm import Chat, Message
from evaluation_test.framework.run import system_prompt
from ontyra.models.recall import RecallContext, RecallRequest


async def test_agent_written_queries(
    chat: Chat, settings: EvalSettings, world_app, recorder, capsys
) -> None:
    dialogues = [d for d in load_dialogues() if not settings.only or settings.only in d.id]
    world = load_world("commons")
    app, agent, keys = await world_app(world)
    domains = {m.key: m.domain for m in world.memories}
    universal = frozenset(m.key for m in world.memories if m.scope == "universal")
    twins = {m.key: m.duplicate_of for m in world.memories if m.duplicate_of}
    specs = [s for s in tool_specs(load_contract("lite")) if s["name"] == "recall"]
    sent: list[dict] = []

    async def builder(history: list[tuple[str, str]], turn: Turn, ctx: RecallContext) -> Built:
        if turn.mode == "briefing":
            return Built(None, [])
        messages: list[Message] = [
            {
                "role": "system",
                "content": system_prompt(
                    None,
                    "Call `recall` for the user's latest message; the location is added for you.",
                ),
            }
        ]
        for said, replied in history:
            messages.append({"role": "user", "content": said})
            messages.append({"role": "assistant", "content": replied or "(done)"})
        messages.append({"role": "user", "content": turn.say})
        reply = await chat.reply(messages, specs, force="recall")
        args = (reply.calls[0].args.get("request") or {}) if reply.calls else {}
        query = args.get("query") if isinstance(args.get("query"), str) else turn.say
        hints = (
            [h for h in args.get("hints", []) if isinstance(h, str)]
            if isinstance(args.get("hints"), list)
            else []
        )
        sent.append({"say": turn.say, "query": query, "hints": hints})
        return Built(query, hints)

    outcomes = []
    for dialogue in dialogues:
        played = await play_dialogue(
            app, agent, dialogue, "llm", keys, domains, universal, twins, builder
        )
        outcomes += [(case, [score]) for case, score in played]
    rows = [
        {
            "variant": "llm",
            "id": case.id,
            "tags": case.tags,
            "split": case.split,
            "passed": all(s.passed for s in scores),
            "problems": [p for s in scores for p in s.problems],
        }
        for case, scores in outcomes
    ]
    path = recorder(
        "agent-recall", rows, {"queries": sent, "embedding": BenchSettings().embed_model}
    )
    by_tag = summarise(outcomes)["by_tag"]
    with capsys.disabled():
        print(f"\nagent-written queries [{settings.llm_model}] -> {path.name}")
        for tag in ("standalone", "needs_history"):
            c = by_tag.get(tag)
            if c:
                print(
                    f"  {tag}: {c['passed']}/{c['cases']} recall={c['recall']} precision={c['precision']}"
                )


async def test_agent_decides_to_recall(
    chat: Chat, settings: EvalSettings, world_app, recorder, capsys
) -> None:
    """What the skill asks for: the hook injects recall for the user's literal message; the model
    may call `recall` itself with a standalone query. Scored on both packs merged, like `union`,
    plus how often it recalls: it should on messages that need history and stay quiet otherwise."""
    dialogues = [d for d in load_dialogues() if not settings.only or settings.only in d.id]
    world = load_world("commons")
    app, agent, keys = await world_app(world)
    domains = {m.key: m.domain for m in world.memories}
    universal = frozenset(m.key for m in world.memories if m.scope == "universal")
    twins = {m.key: m.duplicate_of for m in world.memories if m.duplicate_of}
    specs = [s for s in tool_specs(load_contract("lite")) if s["name"] == "recall"]
    sent: list[dict] = []

    async def builder(history: list[tuple[str, str]], turn: Turn, ctx: RecallContext) -> Built:
        if turn.mode != "recall":
            return Built(turn.say if turn.mode == "check" else None, [])
        # what the hook would inject: recall for the literal message (no session: no ledger)
        preview = await app.recall.recall(
            agent,
            RecallRequest(context=ctx.model_copy(update={"session": None}), query=turn.say),
        )
        injected = (
            f"\n\n<ontyra-context>\n{preview.rendered}\n</ontyra-context>"
            if preview.rendered
            else ""
        )
        messages: list[Message] = [{"role": "system", "content": system_prompt(None)}]
        for said, replied in history:
            messages.append({"role": "user", "content": said})
            messages.append({"role": "assistant", "content": replied or "(done)"})
        messages.append({"role": "user", "content": turn.say + injected})
        reply = await chat.reply(messages, specs)
        calls = [c for c in reply.calls if c.name == "recall"]
        args = (calls[0].args.get("request") or {}) if calls else {}
        query = args.get("query") if isinstance(args.get("query"), str) else None
        sent.append(
            {
                "say": turn.say,
                "needs_history": turn.needs_history,
                "recalled": bool(calls),
                "query": query,
            }
        )
        also = (Built(query, []),) if query else ()
        return Built(turn.say, [], also)

    outcomes = []
    for dialogue in dialogues:
        played = await play_dialogue(
            app, agent, dialogue, "agent", keys, domains, universal, twins, builder
        )
        outcomes += [(case, [score]) for case, score in played]
    rows = [
        {
            "variant": "agent",
            "id": case.id,
            "tags": case.tags,
            "split": case.split,
            "passed": all(s.passed for s in scores),
            "problems": [p for s in scores for p in s.problems],
        }
        for case, scores in outcomes
    ]
    path = recorder(
        "agent-decides", rows, {"turns": sent, "embedding": BenchSettings().embed_model}
    )
    by_tag = summarise(outcomes)["by_tag"]
    with capsys.disabled():
        print(f"\nagent decides to recall [{settings.llm_model}] -> {path.name}")
        for tag in ("standalone", "needs_history"):
            c = by_tag.get(tag)
            if c:
                print(
                    f"  {tag}: {c['passed']}/{c['cases']} recall={c['recall']} precision={c['precision']}"
                )
        for label, wanted in (("needs_history", True), ("standalone", False)):
            turns = [t for t in sent if t["needs_history"] is wanted]
            if turns:
                print(
                    f"  recalled itself on {label}: {sum(t['recalled'] for t in turns)}/{len(turns)}"
                )
