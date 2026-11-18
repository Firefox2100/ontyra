"""The model-driven suites' data is consistent (offline: no model is called)."""

from __future__ import annotations

import pytest

from benchmark_test.framework.models import load_world
from evaluation_test.framework.agent import load_contract
from evaluation_test.framework.models import (
    load_decision_cases,
    load_presentation_cases,
    load_tool_cases,
    load_write_cases,
)
from evaluation_test.framework.run import KIND_OF, build_card, skill_section
from ontyra.models.recall import RecallRequest
from ontyra.services.tasks import CHOICES


def test_tool_cases_name_real_tools() -> None:
    full = {t["name"] for t in load_contract("full")}
    cases = load_tool_cases()
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        named = [x for x in [case.expect, *case.accept, *case.forbid] if x]
        assert set(named) <= full, f"{case.id}: unknown tools {set(named) - full}"
        assert not (set(case.accept) & set(case.forbid)), case.id
        assert case.expect or case.forbid or case.accept, f"{case.id}: expects nothing"


def test_every_tool_is_asked_for_by_some_case() -> None:
    full = {t["name"] for t in load_contract("full")}
    wanted = {c.expect for c in load_tool_cases() if c.expect}
    assert full - wanted <= {"approve_ontology_change"} - wanted or not (full - wanted), (
        f"tools no case expects: {sorted(full - wanted)}"
    )


def test_tool_cases_cover_the_negative_and_the_safety_cases() -> None:
    cases = load_tool_cases()
    tags = {t for c in cases for t in c.tags}
    assert {"none", "transient", "safety", "review", "maintenance", "feedback"} <= tags
    assert any(c.expect is None and c.forbid for c in cases)
    assert {c.split for c in cases} == {"tune", "holdout"}


def test_write_cases_are_well_formed() -> None:
    cases = load_write_cases()
    assert len({c.id for c in cases}) == len(cases)
    kinds = {k for c in cases for k in c.kinds}
    assert kinds <= KIND_OF
    assert {"fact", "directive", "decision", "event", "procedure", "task", "lesson"} <= kinds
    assert any(not c.should_write for c in cases)
    assert any(c.count > 1 for c in cases)
    for case in cases:
        assert case.conversation[-1][0] == "user", case.id


def test_decision_cases_use_real_memories_and_choices() -> None:
    world = load_world("commons")
    keys = {m.key for m in world.memories}
    texts = {m.key: (m.text, m.kind) for m in world.memories}
    kind_of = {
        "duplicates": "consolidate",
        "update": "review_contradiction",
        "feedback": "review_feedback",
        "expiry": "expire_check",
    }
    cases = load_decision_cases()
    assert len({c.id for c in cases}) == len(cases)
    for case in cases:
        assert {m.key for m in case.members if m.key} <= keys, case.id
        assert all(m.key or m.text for m in case.members), case.id
        card = build_card(case, texts)
        assert card["finding"] == case.finding
        assert set(case.accept) <= set(CHOICES[kind_of[case.finding]]), case.id  # type: ignore[index]
        assert card["choices"] == CHOICES[kind_of[case.finding]]  # type: ignore[index]
    assert {c.finding for c in cases} == set(kind_of)
    right = {d for c in cases for d in c.accept}
    assert {"merge", "distinct", "supersede", "keep", "retract", "defer"} <= right


def test_the_skill_has_the_section_the_decisions_quote() -> None:
    assert "claim_task" in skill_section(
        "Maintenance tasks (full MCP profile, or `/v1/tasks` over REST)"
    )


@pytest.mark.parametrize("profile", ["compact", "rich"])
async def test_presentation_scenarios_get_the_memory_they_need(profile: str, world_app) -> None:
    """Recall over the world (hash embedder) returns every memory a scenario relies on; else
    the scenario would measure recall, not presentation."""
    from benchmark_test.framework.score import returned_items

    world = load_world("commons")
    app, agent, keys = await world_app(world)
    cases = load_presentation_cases()
    assert len({c.id for c in cases}) == len(cases)
    missing: dict[str, list[str]] = {}
    for case in cases:
        assert {m.key for m in world.memories} >= set(case.include), case.id
        assert case.mode in ("recall", "briefing")
        request = RecallRequest(context=case.context, query=case.query, profile=profile)  # type: ignore[arg-type]
        run = app.recall.briefing if case.mode == "briefing" else app.recall.recall
        pack = await run(agent, request)
        got = {r.key for r in returned_items(pack, keys)[0]}
        lost = [k for k in case.include if k not in got]
        if lost:
            missing[case.id] = lost
    assert not missing, f"packs lack the memories their scenarios need: {missing}"
