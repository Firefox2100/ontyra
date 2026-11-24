"""Knowledge from sources: evidence that cites a source, vetting, source checks, recall (plans/26)."""

from __future__ import annotations

import pytest

from ontyra.app import Application
from ontyra.models import PolicyConfig, PolicyRule
from ontyra.models.proposals import AddMemory, Evidence, ProposalRequest, ReviseMemory
from ontyra.models.recall import RecallContext, RecallRequest, RecallSettings
from ontyra.models.sources import SourceCheckRequest, SourceRef
from ontyra.services import Caller
from ontyra.services.errors import PermissionDeniedError

PAPER = "https://Example.org/papers/gene-x/?id=7#sec3"
CANON = "https://example.org/papers/gene-x?id=7"
TEXT = "Gene X is relevant to the Y pathway, which regulates Z"


def cite(uri: str = PAPER, **kw: object) -> Evidence:
    return Evidence(source=SourceRef(uri=uri, locator="section 3", quote="X acts on Y", **kw))  # type: ignore[arg-type]


async def knowledge(
    app: Application, caller: Caller, evidence: list[Evidence] | None = None, text: str = TEXT
):
    return await app.memory.submit(
        caller,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="fact",
                    ref="m",
                    fields={
                        "text": text,
                        "subject": "concept:linux",
                        "predicate": "relates_to",
                        "object": "pathway Y",
                        "scope": "universal",
                    },
                    evidence=evidence if evidence is not None else [cite()],
                )
            ]
        ),
    )


async def test_a_memory_cites_a_source_it_may_not_have_and_it_is_kept_canonical(
    app: Application, agent: Caller
) -> None:
    result = await knowledge(app, agent, [cite("file:///no/such/place/paper.pdf")])
    assert result.status == "committed"
    detail = await app.explore.get(agent, result.created["m"], ["evidence"], RecallContext())  # type: ignore[arg-type]
    assert detail.evidence is not None
    shown = detail.evidence[0]
    assert shown["source"].startswith("file://") and shown["locator"] == "section 3"
    assert shown["quote"] == "X acts on Y"
    web = await knowledge(app, agent, [cite()], text="Another statement of the same paper")
    memory = await app.memory.get_memory(agent, web.created["m"])  # type: ignore[arg-type]
    rev = await app.recall.store.get(memory.revision)
    assert rev.sources[0].uri == CANON  # fragment dropped, query kept, host lowercased  # type: ignore[union-attr]


async def test_evidence_is_words_or_a_source_never_both_and_the_uri_must_be_one(
    app: Application, agent: Caller
) -> None:
    with pytest.raises(ValueError, match="not both"):
        Evidence(quote="said it", source=SourceRef(uri="file:///a"))
    with pytest.raises(ValueError, match="needs a quote"):
        Evidence()
    bad = await knowledge(app, agent, [cite("not a uri")])
    assert bad.status == "rejected" and "bad_source" in {f.code for f in bad.findings}
    reserved = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="fact",
                    fields={
                        "text": "t",
                        "subject": "concept:linux",
                        "predicate": "p",
                        "object": "o",
                        "sources": [{"uri": "file:///a"}],
                    },
                )
            ]
        ),
    )
    assert reserved.status == "rejected" and "reserved_field" in {f.code for f in reserved.findings}


async def test_a_revision_can_add_a_source_and_does_not_repeat_one(
    app: Application, agent: Caller
) -> None:
    memory = (await knowledge(app, agent)).created["m"]
    again = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                ReviseMemory(
                    memory=memory,
                    fields={"confidence": 0.8},
                    evidence=[cite(), cite("doi:10.1000/xyz")],
                )
            ]
        ),
    )
    assert again.status == "committed"
    view = await app.memory.get_memory(agent, memory)
    rev = await app.recall.store.get(view.revision)
    assert [s.uri for s in rev.sources] == [CANON, "doi:10.1000/xyz"]  # type: ignore[union-attr]


async def test_recall_shows_the_source_and_marks_what_no_human_vetted(
    app: Application, agent: Caller, human: Caller
) -> None:
    await knowledge(app, agent)
    await app.indexer.backfill()
    pack = await app.recall.recall(
        human, RecallRequest(query="gene X pathway Y", profile="compact")
    )
    item = next(i for i in pack.items() if "Gene X" in i.text)
    assert item.sources[0].uri == CANON and item.vetting == "auto"
    assert f"(source: {CANON} section 3)" in (pack.rendered or "")
    assert "[unvetted]" in (pack.rendered or "")


async def test_a_human_writes_vetted_and_may_vet_what_policy_committed(
    app: Application, agent: Caller, human: Caller
) -> None:
    mine = (await knowledge(app, human, text="A statement a person wrote")).created["m"]
    theirs = (await knowledge(app, agent)).created["m"]
    pending = await app.sources.unvetted(human)
    assert [p["memory"] for p in pending] == [str(theirs)] and str(mine) not in {
        p["memory"] for p in pending
    }
    with pytest.raises(PermissionDeniedError):
        await app.sources.vet(agent, theirs)
    await app.sources.vet(human, theirs)
    assert await app.sources.unvetted(human) == []


async def test_recall_can_be_told_to_use_only_vetted_memories(
    make_app, agent: Caller, human: Caller
) -> None:
    app = await make_app(recall=RecallSettings(min_vetting="vetted"))
    agent = await app.actors.caller(kind="agent", name="claude")
    human = await app.actors.caller(kind="human", name="owner")
    unvetted = (await knowledge(app, agent)).created["m"]
    await knowledge(app, human, text="Gene X also matters for pathway Y, a person says")
    await app.indexer.backfill()

    def ids(pack):  # type: ignore[no-untyped-def]
        return {i.memory for i in pack.items()}

    assert unvetted not in ids(
        await app.recall.recall(human, RecallRequest(query="gene X pathway Y"))
    )
    await app.sources.vet(human, unvetted)
    assert unvetted in ids(await app.recall.recall(human, RecallRequest(query="gene X pathway Y")))


async def test_a_changed_source_opens_one_review_per_memory_and_recall_says_so(
    app: Application, agent: Caller, human: Caller
) -> None:
    first = (await knowledge(app, agent)).created["m"]
    second = (await knowledge(app, agent, text="Gene X is expressed in tissue Q")).created["m"]
    await knowledge(app, agent, [cite("file:///elsewhere.md")], text="Unrelated claim from a file")
    await app.indexer.backfill()
    ok = await app.sources.check(agent, SourceCheckRequest(uri=PAPER, state="ok"))
    assert (ok.memories, ok.tasks) == (2, 0)
    changed = await app.sources.check(
        agent, SourceCheckRequest(uri=PAPER, state="changed", note="section 3 was rewritten")
    )
    assert (changed.uri, changed.memories, changed.tasks) == (CANON, 2, 2)
    again = await app.sources.check(agent, SourceCheckRequest(uri=PAPER, state="missing"))
    assert again.tasks == 0  # raised already for these revisions
    tasks = await app.tasks.list(human, kinds=["review_source"])
    assert {str(t.target) for t in tasks} == {str(first), str(second)}
    card = tasks[0].card
    assert card["finding"] == "source" and card["source"] == CANON and card["cited"][0]["quote"]
    pack = await app.recall.recall(human, RecallRequest(query="gene X pathway Y"))
    flagged = next(i for i in pack.items() if i.memory == first)
    assert "source_missing" in flagged.flags and any(
        "reported missing" in c for c in flagged.caveats
    )
    cited = await app.sources.citing(human, PAPER)
    assert cited["check"]["state"] == "missing" and len(cited["memories"]) == 2


async def test_a_policy_rule_can_auto_commit_only_what_cites_a_quoted_source(
    make_app,
) -> None:
    rule = PolicyRule(actor_kinds=["agent"], kinds=["fact"], sourced=True, decision="auto_commit")
    app = await make_app(PolicyConfig(default="review", rules=[rule]))
    agent = await app.actors.caller(kind="agent", name="claude")
    sourced = await knowledge(app, agent)
    assert sourced.status == "committed"
    no_quote = await knowledge(
        app, agent, [Evidence(source=SourceRef(uri="file:///a.md"))], text="No quote given"
    )
    assert no_quote.status == "pending"
    bare = await knowledge(app, agent, [], text="No source at all")
    assert bare.status == "pending"


async def test_tiers_and_corroboration_lift_a_memory_and_an_old_reading_is_flagged(
    make_app, agent: Caller, human: Caller
) -> None:
    from datetime import UTC, datetime, timedelta

    from ontyra.models.recall import SourceTier

    app = await make_app(
        PolicyConfig(default="auto_commit"),
        recall=RecallSettings(
            source_tiers=[SourceTier(match="doi:10.", tier="journal", weight=1.2)],
            source_stale_days=30,
        ),
    )
    agent = await app.actors.caller(kind="agent", name="claude")
    long_ago = datetime.now(UTC) - timedelta(days=90)
    await knowledge(
        app,
        agent,
        [cite("doi:10.1000/a", retrieved_at=long_ago), cite("file:///x/b.md")],
        text="Gene X drives pathway Y in tissue Q",
    )
    await knowledge(app, agent, [cite("file:///x/c.md")], text="Gene X drives pathway Y in cells")
    await app.indexer.backfill()
    pack = await app.recall.recall(
        agent, RecallRequest(query="gene X drives pathway Y", profile="rich")
    )
    texts = [i.text for i in pack.knowledge]
    assert texts[0] == "Gene X drives pathway Y in tissue Q" and len(texts) == 2
    top = pack.knowledge[0]
    assert "source tier journal" in top.why and "2 independent sources" in top.why
    assert any("days ago" in c for c in top.caveats), (top.caveats, top.sources)
    assert not pack.context and "Knowledge:" in pack.rendered
