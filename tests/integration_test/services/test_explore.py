"""Path A verbs: search with facets, get with expansions, neighbors, ask, overview."""

from datetime import timedelta

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.proposals import LinkMemories, ProposalRequest, RetractMemory, ReviseMemory
from ontyra.models.recall import AskRequest, RecallContext, SearchRequest, Signal
from ontyra.services import Caller

PROJECT = "file://m/home/x/proj"


async def test_search_returns_hits_and_facets(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "directive", "run tests with pytest", strength="must", about=[str(concepts["pytest"])]
    )
    await remember("fact", "pytest is configured in pyproject", anchors=[{"uri": PROJECT}])
    await remember("fact", "the docker daemon is slow")
    res = await app.explore.search(agent, SearchRequest(query="pytest tests"))
    assert {h.kind for h in res.hits} == {"directive", "fact"}
    assert res.facets["kind"] == {"directive": 1, "fact": 1}
    assert res.facets["concept"] == {"pytest": 1}
    assert set(res.facets["anchor"]) == {"(everywhere)", PROJECT}
    assert any("get(" in a for a in res.affordances)


async def test_search_shows_non_applicable_items_marked_instead_of_hiding_them(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "fact",
        "apt's gcc is too old here",
        qualifiers=[{"concept": str(concepts["linux"]), "role": "necessary"}],
    )
    ctx = RecallContext(signals=[Signal(concept="windows")])
    res = await app.explore.search(agent, SearchRequest(query="apt gcc", context=ctx))
    assert [h.applies for h in res.hits] == ["no"]
    unknown = await app.explore.search(agent, SearchRequest(query="apt gcc"))
    assert [h.applies for h in unknown.hits] == ["unknown"]


async def test_search_filters_by_concept_with_ontology_expansion(
    app: Application, agent: Caller, remember, concepts
) -> None:
    py = await remember("fact", "fixtures live in conftest", about=[str(concepts["pytest"])])
    await remember("fact", "fixtures are also a thing in docker")
    res = await app.explore.search(
        agent, SearchRequest(query="fixtures", concept="testing framework")
    )
    assert [h.memory for h in res.hits] == [py]


async def test_get_expands_history_links_evidence_and_applicability(
    app: Application, agent: Caller, remember, concepts
) -> None:
    ep = await app.memory.record_episode(agent, session="s", role="user", content="we use uv here")
    a = await remember("fact", "this project uses uv", anchors=[{"uri": PROJECT}])
    b = await remember("fact", "this project uses pip")
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                ReviseMemory(
                    memory=a,
                    fields={"text": "this project uses uv exclusively", "object": "uv"},
                    evidence=[{"episode": ep, "quote": "we use uv"}],  # type: ignore[list-item]
                ),
                LinkMemories(relation="contradicts", source=b, target=a, reason="conflict"),
            ]
        ),
    )
    detail = await app.explore.get(
        agent, a, ["history", "links", "evidence", "applies"], RecallContext(uris=[f"{PROJECT}/x"])
    )
    assert detail.history is not None and len(detail.history.revisions) == 2
    assert {link["relation"] for link in detail.links or []} >= {"supersedes", "contradicts"}
    assert detail.evidence and detail.evidence[0]["excerpt"] == "we use uv here"
    assert detail.applies and detail.applies["applies"] and detail.applies["distance"] == 1
    plain = await app.explore.get(agent, a, [], RecallContext())
    assert plain.history is None and plain.links is None


async def test_neighbors_of_a_memory_and_a_concept(
    app: Application, agent: Caller, remember, concepts
) -> None:
    a = await remember("fact", "fixtures live in conftest", about=[str(concepts["pytest"])])
    b = await remember("decision", "use pytest-asyncio", about=[str(concepts["pytest"])])
    n = await app.explore.neighbors(agent, a)
    assert [c["label"] for c in n.concepts] == ["pytest"]
    assert [m["memory"] for m in n.memories] == [str(b)]
    c = await app.explore.neighbors(agent, concepts["pytest"])
    assert {m["memory"] for m in c.memories} == {str(a), str(b)}
    assert any(r["relation"] == "is_a" for r in c.concepts)


async def test_ask_applies_here_and_prerequisites(
    app: Application, agent: Caller, remember, concepts
) -> None:
    mem = await remember(
        "procedure",
        "create the dev env with conda",
        anchors=[{"uri": PROJECT}],
        qualifiers=[{"concept": str(concepts["conda"]), "role": "prerequisite"}],
        preconditions=["network access"],
    )
    here = RecallContext(uris=[f"{PROJECT}/a.py"])
    yes = await app.explore.ask(
        agent, AskRequest(question="applies_here", memory=mem, context=here)
    )
    assert yes.answer["applies"] and "applies here" in yes.summary
    no = await app.explore.ask(
        agent,
        AskRequest(
            question="applies_here", memory=mem, context=RecallContext(uris=["file://m/other"])
        ),
    )
    assert not no.answer["applies"] and "anchored elsewhere" in no.summary
    pre = await app.explore.ask(
        agent, AskRequest(question="prerequisites_of", memory=mem, context=here)
    )
    assert pre.answer["preconditions"] == ["network access"]
    assert pre.answer["checks"] == ["conda"] and "2 prerequisite" in pre.summary


async def test_ask_absence_states_its_search_envelope(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("fact", "the api listens on port 8080")
    ctx = RecallContext(uris=[f"{PROJECT}/a.py"], signals=[Signal(concept="ubuntu")])
    gone = await app.explore.ask(
        agent, AskRequest(question="absence", topic="kubernetes cluster", context=ctx)
    )
    assert (
        gone.answer["found"] == [] and "no live memory about 'kubernetes cluster'" in gone.summary
    )
    env = gone.answer["envelope"]
    assert f"{PROJECT}/a.py" in env["anchors"] and env["observed"] == ["Ubuntu"]
    present = await app.explore.ask(agent, AskRequest(question="absence", topic="api port"))
    assert present.answer["found"]


async def test_ask_conflicts_and_changes_since_and_supersession_chain(
    app: Application, agent: Caller, remember, concepts, clock
) -> None:
    since = clock() + timedelta(seconds=1)
    a = await remember("fact", "the deploy window is on tuesday")
    b = await remember("fact", "the deploy window is on thursday")
    gone = await remember("fact", "an obsolete note")
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                LinkMemories(relation="contradicts", source=a, target=b, reason="notes differ"),
                RetractMemory(memory=gone, reason="obsolete"),
            ]
        ),
    )
    conflicts = await app.explore.ask(agent, AskRequest(question="conflicts"))
    assert len(conflicts.answer["contradictions"]) == 1
    changes = await app.explore.ask(agent, AskRequest(question="changes_since", since=since))
    assert len(changes.answer["added_or_revised"]) == 2
    assert [c["status"] for c in changes.answer["superseded_or_retracted"]] == ["retracted"]
    chain = await app.explore.ask(agent, AskRequest(question="supersession_chain", memory=a))
    assert len(chain.answer["revisions"]) == 1


async def test_ask_validates_its_arguments(app: Application, agent: Caller) -> None:
    import pytest

    from ontyra.services import errors

    for q in ("applies_here", "supersession_chain", "prerequisites_of"):
        with pytest.raises(errors.InvalidRequestError):
            await app.explore.ask(agent, AskRequest(question=q))  # type: ignore[arg-type]
    with pytest.raises(errors.InvalidRequestError):
        await app.explore.ask(agent, AskRequest(question="changes_since"))
    with pytest.raises(errors.InvalidRequestError):
        await app.explore.ask(agent, AskRequest(question="explain"))
    with pytest.raises(errors.NotFoundError):
        await app.explore.ask(
            agent, AskRequest(question="explain", trace=RecordId("recall_trace", "nope"))
        )


async def test_overview_orients_the_agent(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember("directive", "use pytest", strength="must", about=[str(concepts["pytest"])])
    await remember("fact", "the api listens on port 8080")
    ov = await app.explore.overview(
        agent,
        RecallContext(uris=[f"{PROJECT}/a.py"], signals=[Signal(concept="python", value="3.12")]),
    )
    assert ov.counts == {"directive": 1, "fact": 1}
    assert ov.concepts == ["pytest (1)"]
    assert ov.anchor_chain[0] == f"{PROJECT}/a.py" and ov.qualifiers_observed == ["Python=3.12"]
    assert any(v.startswith("recall(") for v in ov.verbs) and len(ov.recent) == 2
