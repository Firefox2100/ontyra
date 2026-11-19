"""Scorers, parsers and runners, offline (a scripted model stands in for the real one)."""

from __future__ import annotations

import json

from fastmcp import Client

from evaluation_test.framework.agent import Executed, load_contract, run_agent, tool_specs
from evaluation_test.framework.llm import Reply, ScriptedChat, ToolCall, clean
from evaluation_test.framework.models import (
    DecisionCase,
    PresentationCase,
    ToolCase,
    WriteCase,
)
from evaluation_test.framework.results import summarise
from evaluation_test.framework.run import (
    presentation_messages,
    run_decision_case,
    run_tool_case,
    run_write_case,
    system_prompt,
)
from evaluation_test.framework.score import (
    check_text,
    extract_json,
    schema_errors,
    score_decision,
    score_tool_call,
    score_write,
)
from ontyra.calibration.models import World
from ontyra.transports.mcp import create_mcp

SCHEMAS = {t["name"]: t["input_schema"] for t in load_contract("full")}
LITE = {t["name"]: t["input_schema"] for t in load_contract("lite")}


def tool_case(**kw) -> ToolCase:
    base = {"id": "x", "tags": ["t"], "why": "w", "say": "hi"}
    return ToolCase.model_validate({**base, **kw})


def test_extract_json_finds_fenced_bare_and_embedded_objects() -> None:
    assert extract_json('```json\n{"decision": "merge", "note": "same"}\n```') == {
        "decision": "merge",
        "note": "same",
    }
    assert extract_json('Sure. {"decision": "keep"} Done.') == {"decision": "keep"}
    assert extract_json('{"a": {"b": 1}}') == {"a": {"b": 1}}
    assert extract_json("no json here") is None
    assert extract_json("{broken") is None


def test_clean_drops_a_reasoning_block() -> None:
    assert clean("<think>hmm\nlet me see</think>The answer.") == "The answer."


def test_check_text_wants_and_avoids() -> None:
    answer = "Run `uv add requests`.\n$ sudo apt install redis"
    assert check_text(answer, [r"\buv\s+add\b"], []) == []
    assert check_text(answer, [r"poetry"], [r"^\s*[$#]?\s*sudo\b"]) == [
        "missing /poetry/",
        r"contains /^\s*[$#]?\s*sudo\b/",
    ]


def test_tool_score_selection_arguments_and_safety() -> None:
    case = tool_case(expect="recall", contains=["tidewatch"])
    good = ToolCall("recall", {"request": {"query": "lint tidewatch"}})
    assert score_tool_call(case, [good], SCHEMAS).passed
    wrong = score_tool_call(case, [ToolCall("search", {"query": "x"})], SCHEMAS)
    assert wrong.problems == ["called search; expected recall"]
    assert "no tool called" in score_tool_call(case, [], SCHEMAS).problems[0]
    bad_args = score_tool_call(case, [ToolCall("recall", {"query": "tidewatch"})], SCHEMAS)
    assert bad_args.invalid_args and not bad_args.passed
    lacking = score_tool_call(case, [ToolCall("recall", {"request": {"query": "x"}})], SCHEMAS)
    assert lacking.problems == ["arguments lack ['tidewatch']"]


def test_tool_score_none_expected_forbid_accept_and_skip() -> None:
    quiet = tool_case(forbid=["remember"])
    assert score_tool_call(quiet, [], SCHEMAS).passed
    assert not score_tool_call(quiet, [ToolCall("remember", {})], SCHEMAS).passed
    lenient = tool_case(accept=["recall"], forbid=["approve"])
    assert score_tool_call(
        lenient, [ToolCall("recall", {"request": {"query": "x"}})], SCHEMAS
    ).passed
    both = score_tool_call(
        lenient, [ToolCall("recall", {"request": {"query": "x"}}), ToolCall("approve", {})], SCHEMAS
    )
    assert "called forbidden tool approve" in both.problems
    full_only = tool_case(expect="pending_tasks")
    skipped = score_tool_call(full_only, [], LITE)
    assert skipped.skipped and not skipped.passed
    any_of = tool_case(expect="feedback", contains_any=["helpful", "applied"])
    ok = ToolCall("feedback", {"request": {"memory": "memory:a", "query": "q", "label": "applied"}})
    assert score_tool_call(any_of, [ok], SCHEMAS).passed


def test_schema_errors_names_the_path() -> None:
    errors = schema_errors(SCHEMAS["recall"], {"request": {"query": 3}})
    assert errors and "query" in errors[0]


def decision_case(**kw) -> DecisionCase:
    base = {
        "id": "d",
        "tags": ["t"],
        "why": "w",
        "finding": "duplicates",
        "members": [{"text": "a"}, {"text": "b"}],
        "accept": ["merge"],
    }
    return DecisionCase.model_validate({**base, **kw})


def test_decision_score() -> None:
    choices = ["merge", "distinct", "defer"]
    case = decision_case()
    assert score_decision(case, '{"decision": "merge", "note": "same"}', choices).passed
    assert score_decision(case, '{"decision": "distinct", "note": "x"}', choices).problems == [
        "decided distinct; right: ['merge']"
    ]
    assert (
        score_decision(case, '{"decision": "supersede"}', choices)
        .problems[0]
        .startswith("decision 'supersede' is not one of")
    )
    assert score_decision(case, "I think merge", choices).problems == ["no JSON answer"]
    noted = decision_case(accept=["distinct"], note_needed=True)
    assert score_decision(noted, '{"decision": "distinct"}', choices).problems == [
        "no note saying why"
    ]
    assert score_decision(noted, '{"decision": "distinct", "note": "other place"}', choices).passed


class Done:
    """An executed `remember` as the server reports it."""

    def __init__(self, args: dict, status: str = "committed", findings=(), error=False) -> None:
        self.call = ToolCall("remember", args)
        self.data = {"status": status, "findings": [{"code": c} for c in findings]}
        self.text = ""
        self.error = error


def proposal(*ops: dict) -> dict:
    return {"proposal": {"ops": list(ops)}}


def add(kind: str, text: str, **extra) -> dict:
    return {"op": "add", "kind": kind, "fields": {"text": text}, **extra}


def write_case(**kw) -> WriteCase:
    base = {
        "id": "w",
        "tags": ["t"],
        "why": "w",
        "conversation": [("user", "always use pnpm")],
        "should_write": True,
        "kinds": ["directive"],
        "contains": ["pnpm"],
    }
    return WriteCase.model_validate({**base, **kw})


def test_write_score() -> None:
    case = write_case()
    ok = Done(proposal(add("directive", "use pnpm", evidence=[{"episode": "episode:e"}])))
    score = score_write(case, [ok])  # type: ignore[arg-type]
    assert score.passed and score.evidence
    assert score_write(case, []).problems == ["nothing was written"]
    wrong_kind = Done(proposal(add("fact", "use pnpm")))
    assert (
        "kind ['fact']; expected one of ['directive']" in score_write(case, [wrong_kind]).problems
    )  # type: ignore[arg-type]
    two = Done(proposal(add("directive", "use pnpm"), add("directive", "pnpm again")))
    assert "added 2 memories; expected 1" in score_write(case, [two]).problems  # type: ignore[arg-type]
    unscoped = Done(proposal(add("directive", "use pnpm")), findings=["unscoped_directive"])
    assert (
        "a directive without a scope (unscoped_directive)" in score_write(case, [unscoped]).problems
    )  # type: ignore[arg-type]
    held = Done(proposal(add("directive", "use pnpm")), status="pending")
    assert "held, not committed" in score_write(case, [held]).problems  # type: ignore[arg-type]
    rejected = Done(proposal(add("directive", "use pnpm")), status="rejected", findings=["bad"])
    retry = score_write(case, [rejected, ok])  # type: ignore[arg-type]
    assert retry.passed and retry.rejected == 1


def test_write_score_when_nothing_should_be_written() -> None:
    case = write_case(should_write=False, kinds=[], contains=[], count=1)
    assert score_write(case, []).passed
    assert not score_write(case, [Done(proposal(add("fact", "x")))]).passed  # type: ignore[arg-type]


def test_summarise_counts_per_variant_and_tag() -> None:
    rows = [
        {"variant": "a", "passed": True, "tags": ["x", "y"]},
        {"variant": "a", "passed": False, "tags": ["x"]},
        {"variant": "a", "passed": False, "tags": ["x"], "skipped": True},
        {"variant": "b", "passed": True, "tags": ["y"]},
    ]
    out = summarise(rows)
    assert out["a"]["passed"] == 1 and out["a"]["cases"] == 2
    assert out["a"]["by_tag"]["x"] == {"passed": 1, "cases": 2}
    assert out["b"]["by_tag"]["y"] == {"passed": 1, "cases": 1}


async def test_the_tool_runner_scores_the_first_call_and_executes_nothing() -> None:
    chat = ScriptedChat(
        lambda m, t, f: Reply(calls=[ToolCall("recall", {"request": {"query": "lint tidewatch"}})])
    )
    case = tool_case(expect="recall", contains=["tidewatch"], cwd="/src/tidewatch")
    specs = tool_specs(load_contract("lite"))
    score, trace = await run_tool_case(chat, case, specs, LITE)
    assert score.passed and len(trace.calls) == 1
    seen = chat.seen[0]
    assert seen[0]["role"] == "system" and "/src/tidewatch" in seen[0]["content"]
    assert "long-term memory" in seen[0]["content"]


def test_bare_specs_drop_descriptions_only() -> None:
    bare = tool_specs(load_contract("full"), descriptions=False)
    assert all(s["description"] == "" and s["parameters"] for s in bare)
    assert len(bare) == len(load_contract("full"))


async def test_the_decision_runner_builds_the_prompt_and_scores_the_answer() -> None:
    chat = ScriptedChat(
        lambda m, t, f: Reply(text='```json\n{"decision": "merge", "note": "same"}\n```')
    )
    case = decision_case(members=[{"text": "Use pytest."}, {"text": "Tests: pytest."}])
    from evaluation_test.framework.run import build_card

    card = build_card(case, {})
    score, _ = await run_decision_case(chat, case, card)
    assert score.passed
    system, user = chat.seen[0][0]["content"], chat.seen[0][1]["content"]
    assert "Never merge memories that merely share a topic" in system
    assert json.loads(user.strip("`json\n"))["finding"] == "duplicates"


def test_presentation_messages_inject_the_pack_only_when_given() -> None:
    case = PresentationCase(
        id="p", tags=["t"], why="w", query="q", include=["k"], task="Do the thing."
    )
    with_pack = presentation_messages(case, "Must follow:\n- use uv")
    assert "use uv" in with_pack[0]["content"] and with_pack[1]["content"] == "Do the thing."
    assert "Must follow" not in presentation_messages(case, None)[0]["content"]


async def test_the_agent_loop_runs_tools_on_the_real_server_and_feeds_back_results(
    world_app,
) -> None:
    app, agent, _ = await world_app(World(id="empty", description="x", concepts=[], memories=[]))
    sent = proposal(
        {
            "op": "add",
            "kind": "directive",
            "fields": {
                "text": "Always answer in British spelling.",
                "rule": "use British spelling",
                "strength": "should",
                "scope": "universal",
            },
        }
    )

    def script(messages, tools, force):
        if not any(m["role"] == "tool" for m in messages):
            return Reply(calls=[ToolCall("remember", sent, id="c1")])
        return Reply(text="Saved.")

    case = write_case(contains=["british"])
    await app.memory.record_episode(agent, session="s", role="user", content="always British")
    async with Client(create_mcp(lambda: app, profile="lite")) as client:  # type: ignore[arg-type]
        listed = await client.list_tools()
        specs = tool_specs(
            [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in listed
            ]
        )
        score, trace = await run_write_case(ScriptedChat(script), case, specs, client)
    assert score.passed, score.problems
    assert trace.final == "Saved." and len(trace.steps) == 1
    assert trace.calls[0].data["status"] == "committed"


async def test_a_rejected_proposal_is_fed_back_and_a_retry_can_pass(world_app) -> None:
    app, agent, _ = await world_app(World(id="empty", description="x", concepts=[], memories=[]))
    bad = proposal({"op": "retract", "memory": "memory:nothing", "reason": "x"})
    good = proposal(
        {
            "op": "add",
            "kind": "directive",
            "fields": {
                "text": "Use pnpm.",
                "rule": "use pnpm",
                "strength": "should",
                "scope": "universal",
            },
        }
    )
    calls = iter([bad, good])

    def script(messages, tools, force):
        nxt = next(calls, None)
        return Reply(calls=[ToolCall("remember", nxt, id="c")]) if nxt else Reply(text="ok")

    case = write_case()
    await app.memory.record_episode(agent, session="s", role="user", content="pnpm")
    async with Client(create_mcp(lambda: app, profile="lite")) as client:  # type: ignore[arg-type]
        listed = await client.list_tools()
        specs = tool_specs(
            [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in listed
            ]
        )
        score, _ = await run_write_case(ScriptedChat(script), case, specs, client)
    assert score.passed and score.rejected == 1


async def test_without_a_client_the_loop_stops_after_the_first_call() -> None:
    chat = ScriptedChat(lambda m, t, f: Reply(calls=[ToolCall("recall", {})]))
    trace = await run_agent(chat, [{"role": "user", "content": "x"}], [], None)
    assert len(trace.steps) == 1 and isinstance(trace.steps[0][0], Executed)


def test_system_prompt_carries_the_server_instructions() -> None:
    text = system_prompt("/work", "extra note")
    assert "call `recall`" in text and "/work" in text and text.endswith("extra note")
