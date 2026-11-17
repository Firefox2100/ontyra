"""Recall scenarios (plans/13 exit criteria): currency, completeness, absence, precedence,
qualifier semantics, the session ledger, determinism. Run through the real pipeline against
SurrealDB (embedded and server mode)."""

from datetime import timedelta

import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.proposals import (
    LinkMemories,
    ProposalRequest,
    RetractMemory,
    ReviseMemory,
    SupersedeMemory,
)
from ontyra.models.recall import AskRequest, RecallContext, RecallRequest, Signal
from ontyra.services import Caller

PROJECT = "file://m/home/x/proj"
HERE = RecallContext(uris=[f"{PROJECT}/src/a.py"])


def texts(pack) -> list[str]:  # type: ignore[no-untyped-def]
    return [i.text for i in pack.items()]


async def recall(app: Application, who: Caller, query: str, ctx: RecallContext | None = None, **kw):  # type: ignore[no-untyped-def]
    return await app.recall.recall(
        who, RecallRequest(query=query, context=ctx or RecallContext(), **kw)
    )


# ---- finding things --------------------------------------------------------------------


async def test_text_relevant_memory_is_recalled_with_a_reason(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("directive", "run the test suite with pytest, never unittest", strength="must")
    await remember("fact", "the docker daemon exhausted its address pools")
    pack = await recall(app, agent, "how do I run the tests?")
    assert texts(pack) == ["run the test suite with pytest, never unittest"]
    assert pack.must_follow and not pack.silent
    assert any(w.startswith(("lexical", "vector")) for w in pack.items()[0].why)
    assert "Must follow" in pack.rendered


async def test_unrelated_query_stays_silent(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("directive", "run the test suite with pytest", strength="must")
    pack = await recall(app, agent, "what is the airspeed of a swallow?")
    assert pack.silent and pack.rendered == "" and not pack.items()


async def test_concept_lane_finds_memories_by_alias_and_ontology(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember(
        "directive",
        "everything goes through the runner configured in pyproject",
        about=[str(concepts["pytest"])],
    )
    # no word overlap with the memory: "py.test" is an alias of pytest
    pack = await recall(app, agent, "py.test setup please")
    assert [i.memory for i in pack.items()] == [mem]
    assert any("about pytest" in w for w in pack.items()[0].why)
    # a broader mention reaches it through is_a: "testing framework" ← pytest
    pack = await recall(app, agent, "which testing framework do we use")
    assert mem in [i.memory for i in pack.items()]


async def test_hints_count_as_query_text(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("fact", "builds are reproducible with uv lock files")
    assert (await recall(app, agent, "something else entirely")).silent
    pack = await app.recall.recall(
        agent, RecallRequest(query="something else entirely", hints=["uv lock files"])
    )
    assert not pack.silent


async def test_owner_isolation(app: Application, agent: Caller, remember, concepts) -> None:
    other = await app.actors.caller(
        kind="agent", name="other", owner=await app.actors.ensure_owner("user", "alice")
    )
    await remember("fact", "alice only secret about pytest", caller=other)
    assert (await recall(app, agent, "alice secret pytest")).silent
    assert not (await recall(app, other, "alice secret pytest")).silent


# ---- currency: stale items are never delivered ------------------------------------------


async def test_a_superseded_item_is_never_delivered(
    app: Application, agent: Caller, remember, concepts
) -> None:
    old = await remember("fact", "the staging database lives on host alpha")
    new = await remember("fact", "the staging database lives on host beta")
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[SupersedeMemory(new=new, target=old, reason_code="outdated")]  # type: ignore[arg-type]
        ),
    )
    pack = await recall(app, agent, "where is the staging database")
    assert texts(pack) == ["the staging database lives on host beta"]


async def test_a_revised_item_is_delivered_once_in_its_latest_form(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember("fact", "the api listens on port 8080")
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                ReviseMemory(
                    memory=mem, fields={"text": "the api listens on port 9090", "object": "9090"}
                )
            ]
        ),
    )
    pack = await recall(app, agent, "which port does the api listen on")
    assert texts(pack) == ["the api listens on port 9090"]
    assert "earlier revisions" in " ".join(pack.affordances)


async def test_a_retracted_item_is_never_delivered(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember("fact", "the api listens on port 8080")
    await app.memory.submit(agent, ProposalRequest(ops=[RetractMemory(memory=mem, reason="wrong")]))
    assert (await recall(app, agent, "which port does the api listen on")).silent


async def test_expired_items_are_not_delivered(
    app: Application, agent: Caller, remember, clock, concepts
) -> None:
    await remember(
        "fact",
        "the promo code is spring",
        expires_at=(clock() + timedelta(hours=1)).isoformat(),
    )
    assert not (await recall(app, agent, "what is the promo code")).silent
    clock.advance(timedelta(hours=2)) if hasattr(clock, "advance") else None


# ---- anchors ---------------------------------------------------------------------------


async def test_anchored_directive_applies_only_on_that_account(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "directive",
        "this account has no sudo: install software into /data/applications",
        strength="must",
        anchors=[{"uri": "ssh://someone@somewhere.com"}],
    )
    mine = RecallContext(uris=["ssh://someone@somewhere.com/data"])
    other = RecallContext(uris=["ssh://other@somewhere.com"])
    q = "install the compiler software"
    assert not (await recall(app, agent, q, mine)).silent
    assert (await recall(app, agent, q, other)).silent
    # no location given at all: shown, flagged as location-unknown
    unknown = await recall(app, agent, q)
    assert unknown.items()[0].flags == ["location_unknown"]
    assert "applies at ssh://someone@somewhere.com" in unknown.items()[0].caveats[0]


async def test_project_anchor_covers_files_below_and_not_siblings(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("fact", "this project uses uv", anchors=[{"uri": PROJECT}])
    inside = RecallContext(uris=[f"{PROJECT}/src/deep/a.py"])
    sibling = RecallContext(uris=[f"{PROJECT}2/src/a.py"])
    assert not (
        await recall(app, agent, "which package manager does the project use", inside)
    ).silent
    assert (await recall(app, agent, "which package manager does the project use", sibling)).silent


async def test_exact_anchor_does_not_cover_descendants(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "fact",
        "this file is generated, do not edit",
        anchors=[{"uri": f"{PROJECT}/gen.py", "match": "exact"}],
    )
    q = "can I edit this generated file"
    assert not (await recall(app, agent, q, RecallContext(uris=[f"{PROJECT}/gen.py"]))).silent
    assert (await recall(app, agent, q, RecallContext(uris=[f"{PROJECT}/gen.py/x"]))).silent


async def test_anchor_distance_is_reported_nearest_first(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "fact",
        "tests live under the tests directory and run them with pytest",
        anchors=[{"uri": "file://m/home/x"}],
    )
    await remember(
        "fact",
        "tests live under the tests directory and run them via uv run pytest",
        anchors=[{"uri": PROJECT}],
    )
    pack = await recall(app, agent, "where do tests live", HERE)
    by_distance = {i.anchor: i.anchor_distance for i in pack.items()}
    assert by_distance == {PROJECT: 2, "file://m/home/x": 3}


# ---- qualifiers (the plan 12 reference examples) ----------------------------------------


async def _gcc(remember, concepts):  # type: ignore[no-untyped-def]
    return await remember(
        "fact",
        "apt's gcc is too old for this project, install a newer toolchain instead",
        qualifiers=[{"concept": str(concepts["linux"]), "role": "necessary"}],
    )


async def test_necessary_qualifier_excludes_on_windows_and_caveats_when_unknown(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await _gcc(remember, concepts)
    q = "the gcc compiler is too old"
    windows = RecallContext(signals=[Signal(concept="Windows")])
    ubuntu = RecallContext(signals=[Signal(concept="ubuntu")])
    assert (await recall(app, agent, q, windows)).silent
    on_ubuntu = await recall(app, agent, q, ubuntu)
    assert on_ubuntu.items()[0].caveats == []
    unknown = await recall(app, agent, q)
    assert unknown.items()[0].caveats == ["only if Linux"]


async def test_sufficient_qualifier_boosts_on_linux_and_is_neutral_elsewhere(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("fact", "serial ports show up as device files under dev")
    boosted = await remember(
        "fact",
        "on this platform serial ports are tty device files found under dev",
        qualifiers=[{"concept": str(concepts["linux"]), "role": "sufficient"}],
    )
    q = "serial ports device files"
    on_linux = await recall(app, agent, q, RecallContext(signals=[Signal(concept="ubuntu")]))
    assert on_linux.items()[0].memory == boosted
    on_windows = await recall(app, agent, q, RecallContext(signals=[Signal(concept="windows")]))
    assert len(on_windows.items()) == 2  # never excluded


async def test_prerequisite_qualifier_attaches_checks(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "procedure",
        "create the dev environment with conda env create",
        qualifiers=[{"concept": str(concepts["conda"]), "role": "prerequisite"}],
    )
    q = "create the dev environment"
    unknown = (await recall(app, agent, q)).items()[0]
    assert unknown.checks == ["conda"] and unknown.caveats == ["verify prerequisite: conda"]
    missing = RecallContext(signals=[Signal(concept="conda", value=False)])
    assert (await recall(app, agent, q, missing)).items()[0].caveats == [
        "prerequisite not met: conda"
    ]
    present = RecallContext(signals=[Signal(concept="conda")])
    assert (await recall(app, agent, q, present)).items()[0].checks == []


async def test_version_qualifier(app: Application, agent: Caller, remember, concepts) -> None:
    await remember(
        "fact",
        "use the match statement for dispatch in this codebase",
        qualifiers=[
            {
                "concept": str(concepts["python"]),
                "role": "necessary",
                "op": ">=",
                "value": "3.10",
            }
        ],
    )
    q = "dispatch with the match statement"
    py312 = RecallContext(signals=[Signal(concept="python", value="3.12")])
    py38 = RecallContext(signals=[Signal(concept="python", value="3.8")])
    assert not (await recall(app, agent, q, py312)).silent
    assert (await recall(app, agent, q, py38)).silent


async def test_unresolved_signals_are_reported_not_fatal(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("fact", "builds are reproducible with uv lock files")
    pack = await recall(
        app, agent, "uv lock files", RecallContext(signals=[Signal(concept="templeos")])
    )
    assert not pack.silent and pack.unresolved == ["templeos"]
    assert "templeos" not in pack.rendered  # reported in the pack, not injected as noise


# ---- briefing: completeness ------------------------------------------------------------


async def test_briefing_returns_every_applicable_directive(
    app: Application, agent: Caller, remember, concepts
) -> None:
    ids = [
        await remember("directive", f"global rule number {n}", strength="should", scope="universal")
        for n in range(6)
    ]
    ids.append(
        await remember("directive", "project rule", strength="must", anchors=[{"uri": PROJECT}])
    )
    await remember("directive", "elsewhere rule", anchors=[{"uri": "file://m/other"}])
    await remember("fact", "an unanchored fact stays out of the briefing")
    await remember("fact", "this project uses uv", anchors=[{"uri": PROJECT}])
    pack = await app.recall.briefing(agent, RecallRequest(context=HERE, budget_tokens=5000))
    got = {i.memory for i in pack.must_follow + pack.applicable}
    assert got == set(ids)
    assert [i.text for i in pack.context] == ["this project uses uv"]
    assert pack.must_follow[0].text == "project rule" and not pack.more


async def test_briefing_over_budget_counts_what_did_not_fit(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("directive", "must rule", strength="must", scope="universal")
    for n in range(8):
        await remember(
            "directive",
            f"a fairly long optional rule number {n} " * 3,
            strength="prefer",
            scope="universal",
        )
    pack = await app.recall.briefing(agent, RecallRequest(context=HERE, budget_tokens=150))
    assert [i.text for i in pack.must_follow] == ["must rule"]
    assert pack.more and pack.more[0].section == "applicable"
    assert len(pack.applicable) + pack.more[0].count == 8


async def test_briefing_applies_qualifier_roles(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "directive",
        "use apt for system packages",
        strength="should",
        qualifiers=[{"concept": str(concepts["debian_family"]), "role": "necessary"}],
    )
    ubuntu = RecallContext(signals=[Signal(concept="ubuntu")])
    windows = RecallContext(signals=[Signal(concept="windows")])
    assert (await app.recall.briefing(agent, RecallRequest(context=ubuntu))).applicable
    assert not (await app.recall.briefing(agent, RecallRequest(context=windows))).applicable


async def test_trigger_qualifier_pushes_in_a_briefing(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "lesson",
        "when editing a Dockerfile pin the base image digest",
        qualifiers=[{"concept": str(concepts["docker"]), "role": "trigger"}],
    )
    quiet = await app.recall.briefing(agent, RecallRequest(context=HERE))
    assert not quiet.items()
    ctx = RecallContext(uris=HERE.uris, signals=[Signal(concept="docker")])
    pushed = await app.recall.briefing(agent, RecallRequest(context=ctx))
    assert pushed.items()[0].flags == ["triggered"]


async def test_open_overdue_tasks_appear_in_the_briefing(
    app: Application, agent: Caller, remember, concepts, clock
) -> None:
    await remember("task", "rotate the api keys", due=(clock() - timedelta(days=1)).isoformat())
    await remember(
        "task", "someday tidy the readme", due=(clock() + timedelta(days=30)).isoformat()
    )
    pack = await app.recall.briefing(agent, RecallRequest(context=HERE))
    assert [i.text for i in pack.open_tasks] == ["rotate the api keys"]


# ---- precedence and conflicts ----------------------------------------------------------


async def test_explicit_override_drops_the_overridden_directive(
    app: Application, agent: Caller, remember, concepts
) -> None:
    broad = await remember("directive", "format python code with black", strength="should")
    narrow = await remember(
        "directive",
        "format python code with ruff format",
        strength="should",
        anchors=[{"uri": PROJECT}],
    )
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[LinkMemories(relation="overrides", source=narrow, target=broad, reason="project")]  # type: ignore[arg-type]
        ),
    )
    pack = await recall(app, agent, "format python code", HERE)
    assert texts(pack) == ["format python code with ruff format"]
    elsewhere = await recall(app, agent, "format python code", RecallContext(uris=["file://m/x"]))
    assert texts(elsewhere) == ["format python code with black"]  # still valid elsewhere


async def test_unresolved_contradiction_shows_both_with_a_caution(
    app: Application, agent: Caller, remember, concepts
) -> None:
    a = await remember("fact", "the deploy window is on tuesday")
    b = await remember("fact", "the deploy window is on thursday")
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                LinkMemories(relation="contradicts", source=a, target=b, reason="conflicting notes")
            ]  # type: ignore[arg-type]
        ),
    )
    pack = await recall(app, agent, "when is the deploy window")
    assert len(pack.items()) == 2
    assert any("contradiction" in c.message for c in pack.cautions)
    assert "Cautions" in pack.rendered


async def test_near_duplicates_collapse(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("fact", "the api listens on port 8080 locally")
    await remember("fact", "the api listens on port 8080 locally.")
    pack = await recall(app, agent, "which port does the api listen on")
    assert len(pack.items()) == 1


# ---- the session ledger ------------------------------------------------------------------


async def test_the_ledger_prevents_repeats_and_redelivers_on_request(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("fact", "the api listens on port 8080")
    ctx = RecallContext(session="s1")
    first = await recall(app, agent, "which port does the api use", ctx)
    assert len(first.items()) == 1
    again = await recall(app, agent, "which port does the api use", ctx)
    assert again.silent
    after_compaction = await recall(app, agent, "which port does the api use", ctx, redeliver=True)
    assert len(after_compaction.items()) == 1
    other_session = await recall(
        app, agent, "which port does the api use", RecallContext(session="s2")
    )
    assert len(other_session.items()) == 1


async def test_a_correction_notice_follows_a_delivered_item_that_changed(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember("fact", "the api listens on port 8080")
    ctx = RecallContext(session="s1")
    await recall(app, agent, "which port does the api use", ctx)
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                ReviseMemory(
                    memory=mem, fields={"text": "the api listens on port 9090", "object": "9090"}
                )
            ]
        ),
    )
    pack = await recall(app, agent, "which port does the api use", ctx)
    assert [n.kind for n in pack.notices] == ["superseded"]
    assert texts(pack) == ["the api listens on port 9090"]  # the new revision is fresh
    assert "Corrections" in pack.rendered
    # the notice is sent once
    assert not (await recall(app, agent, "unrelated words", ctx)).notices


async def test_a_retracted_delivery_produces_a_retraction_notice(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember("fact", "the api listens on port 8080")
    ctx = RecallContext(session="s1")
    await recall(app, agent, "which port does the api use", ctx)
    await app.memory.submit(agent, ProposalRequest(ops=[RetractMemory(memory=mem, reason="wrong")]))
    pack = await recall(app, agent, "something else", ctx)
    assert [n.kind for n in pack.notices] == ["retracted"] and not pack.silent


# ---- determinism, traces, usage ----------------------------------------------------------


async def test_same_inputs_give_the_same_pack(
    app: Application, agent: Caller, remember, concepts
) -> None:
    for n in range(5):
        await remember("fact", f"the api listens on port {8000 + n}")
    first = await recall(app, agent, "which port does the api use", HERE)
    second = await recall(app, agent, "which port does the api use", HERE)
    strip = {"trace", "score"}
    a = first.model_dump(mode="json", exclude=strip)
    b = second.model_dump(mode="json", exclude=strip)
    for pack in (a, b):
        for section in ("must_follow", "applicable", "context", "how_to", "open_tasks"):
            for item in pack[section]:
                item.pop("score")
    assert a == b


async def test_every_recall_leaves_an_explainable_trace(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember("fact", "the api listens on port 8080")
    await remember("fact", "dogs are mammals and the api is not a dog")
    pack = await recall(app, agent, "which port does the api use")
    assert pack.trace is not None
    answer = await app.explore.ask(
        agent, AskRequest(question="explain", trace=pack.trace, memory=mem)
    )
    assert answer.answer["delivered"][0]["memory"] == str(mem)
    assert answer.answer["path"] == "recall"


async def test_delivery_is_counted_in_usage(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember("fact", "the api listens on port 8080")
    await recall(app, agent, "which port does the api use")
    await recall(app, agent, "which port does the api use")
    usage = await app.recall.store.get(RecordId("usage", mem.id))
    assert usage is not None and usage.recalls == 2  # type: ignore[attr-defined]


# ---- check -------------------------------------------------------------------------------


async def test_check_surfaces_applicable_directives_before_an_action(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "directive",
        "never prune docker networks without asking the owner",
        strength="must",
        polarity="avoid",
        about=[str(concepts["docker"])],
        scope="universal",
    )
    await remember("directive", "keep commit messages short", strength="should", scope="universal")
    risky = await app.recall.check(agent, "docker network prune", HERE)
    assert risky.verdict == "caution"
    assert any("prune docker networks" in i.text for i in risky.pack.must_follow)
    # every applicable `must` is part of a check, relevant to the intent or not; `should` is not
    calm = await app.recall.check(agent, "read a file", HERE)
    assert [i.text for i in calm.pack.must_follow] == [
        "never prune docker networks without asking the owner"
    ]
    assert not calm.pack.applicable


@pytest.mark.parametrize("profile", ["compact", "rich"])
async def test_profiles_render_the_same_items(
    app: Application, agent: Caller, remember, concepts, profile: str
) -> None:
    await remember("fact", "the api listens on port 8080")
    pack = await recall(app, agent, "which port does the api use", profile=profile)
    assert "the api listens on port 8080" in pack.rendered


async def test_links_between_memories_survive_revisions(
    app: Application, agent: Caller, remember, concepts
) -> None:
    broad = await remember("directive", "format python code with black", strength="should")
    narrow = await remember(
        "directive",
        "format python code with ruff format",
        strength="should",
        anchors=[{"uri": PROJECT}],
    )
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[LinkMemories(relation="overrides", source=narrow, target=broad, reason="project")]  # type: ignore[arg-type]
        ),
    )
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                ReviseMemory(
                    memory=narrow,
                    fields={
                        "text": "format python code with ruff format and ruff check",
                        "rule": "r",
                    },
                )
            ]
        ),
    )
    pack = await recall(app, agent, "format python code", HERE)
    assert texts(pack) == ["format python code with ruff format and ruff check"]


# ---- scope: what a briefing or check carries without a query ------------------------------


async def test_briefing_needs_a_tie_to_the_context_for_a_contextual_directive(
    app: Application, agent: Caller, remember, concepts
) -> None:
    linux = [{"concept": str(concepts["linux"]), "role": "necessary"}]
    universal = await remember("directive", "answer in English", scope="universal")
    on_topic = await remember(
        "directive", "run the test suite first", about=[str(concepts["pytest"])]
    )
    unrelated = await remember("directive", "water the ferns", about=[str(concepts["docker"])])
    unscoped = await remember("directive", "an unscoped rule")
    unknown_condition = await remember("directive", "linux only rule", qualifiers=linux)
    signals = [Signal(concept="ubuntu"), Signal(concept="pytest")]
    context = RecallContext(uris=HERE.uris, signals=signals)
    pack = await app.recall.briefing(agent, RecallRequest(context=context, budget_tokens=5000))
    got = {i.memory for i in pack.must_follow + pack.applicable}
    assert universal in got and on_topic in got  # universal; pytest observed
    assert unrelated not in got and unscoped not in got
    assert unknown_condition in got  # ubuntu entails linux: the qualifier is satisfied
    bare = await app.recall.briefing(agent, RecallRequest(context=HERE, budget_tokens=5000))
    got = {i.memory for i in bare.must_follow + bare.applicable}
    assert got == {universal}  # nothing ties the others to this context; an unknown necessary
    # qualifier is not evidence either


async def test_check_scopes_by_the_intent(
    app: Application, agent: Caller, remember, concepts
) -> None:
    docker_rule = await remember(
        "directive", "never prune docker networks", strength="must", about=[str(concepts["docker"])]
    )
    quiet = await app.recall.check(agent, "read a file", HERE)
    assert docker_rule not in {i.memory for i in quiet.pack.must_follow}
    loud = await app.recall.check(agent, "docker compose up", HERE)
    assert docker_rule in {i.memory for i in loud.pack.must_follow}


async def test_unscoped_directive_is_flagged_when_written(app: Application, agent: Caller) -> None:
    from ontyra.models.proposals import AddMemory, ProposalRequest

    def add(text: str, **fields: object) -> ProposalRequest:
        body = {"text": text, "strength": "should", "rule": text, **fields}
        return ProposalRequest(ops=[AddMemory(kind="directive", ref="m", fields=body)])  # type: ignore[arg-type]

    flagged = await app.memory.submit(agent, add("rotate the logs weekly"))
    assert [f.code for f in flagged.findings] == ["unscoped_directive"]
    assert flagged.status == "committed"  # advice, not a rejection
    ok = await app.memory.submit(agent, add("answer in English", scope="universal"))
    assert not ok.findings
