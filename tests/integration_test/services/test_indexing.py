"""The derived index: kept in step with commits, rebuildable, embeddings backfilled."""

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.proposals import ProposalRequest, RetractMemory, ReviseMemory, SupersedeMemory
from ontyra.models.search import Embedding, SearchDoc
from ontyra.services import Caller
from ontyra.services.indexing import doc_id, embedding_id, search_text_of

OWNERS = [RecordId("owner", "global")]


async def live_docs(app: Application) -> list[SearchDoc]:
    return await app.recall.store.docs(OWNERS)


async def test_commit_indexes_text_anchors_and_concepts(
    app: Application, agent: Caller, remember, concepts
) -> None:
    await remember(
        "directive",
        "use pytest",
        strength="must",
        rationale="consistency",
        anchors=[{"uri": "file://m/home/x/proj"}],
        about=[str(concepts["pytest"])],
    )
    (doc,) = await live_docs(app)
    assert doc.anchors == ["file://m/home/x/proj"] and doc.about == [concepts["pytest"]]
    assert "use pytest" in doc.text and "consistency" in doc.text  # kind-specific fields indexed


async def test_embeddings_follow_the_commit(app: Application, agent: Caller, remember) -> None:
    await remember("fact", "the api listens on port 8080")
    (doc,) = await live_docs(app)
    emb = await app.recall.store.get(embedding_id(doc.rev))
    assert isinstance(emb, Embedding) and emb.model == "hash-256" and len(emb.vector) == 256


async def test_revision_replaces_the_index_entry(app: Application, agent: Caller, remember) -> None:
    mem = await remember("fact", "the api listens on port 8080")
    (before,) = await live_docs(app)
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[ReviseMemory(memory=mem, fields={"text": "port 9090", "object": "9090"})]
        ),
    )
    (after,) = await live_docs(app)
    assert after.rev != before.rev and "9090" in after.text
    assert await app.recall.store.get(embedding_id(before.rev)) is None


async def test_supersede_and_retract_remove_index_entries(
    app: Application, agent: Caller, remember
) -> None:
    old = await remember("fact", "alpha")
    new = await remember("fact", "beta")
    gone = await remember("fact", "gamma")
    await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                SupersedeMemory(new=new, target=old, reason_code="outdated"),  # type: ignore[arg-type]
                RetractMemory(memory=gone, reason="wrong"),
            ]
        ),
    )
    assert [d.text.split(".")[0] for d in await live_docs(app)] == ["beta"]


async def test_rebuild_recreates_the_index_from_revisions(
    app: Application, agent: Caller, remember
) -> None:
    await remember("fact", "alpha")
    await remember("fact", "beta")
    store = app.recall.store
    for d in await live_docs(app):
        from ontyra.ports import Delete

        await store.apply([Delete(doc_id(d.rev)), Delete(embedding_id(d.rev))])
    assert await live_docs(app) == []
    assert await app.indexer.rebuild_docs() == 2
    assert len(await live_docs(app)) == 2
    assert await app.indexer.backfill() == 2  # vectors come back too
    assert await store.unembedded("hash-256") == []


async def test_a_failing_embedder_does_not_block_commits(
    app: Application, agent: Caller, remember
) -> None:
    class Broken:
        name, dimension = "broken", 256

        async def embed_documents(self, texts):  # type: ignore[no-untyped-def]
            raise RuntimeError("model endpoint down")

        async def embed_query(self, text):  # type: ignore[no-untyped-def]
            raise RuntimeError("model endpoint down")

    app.indexer.embedder = Broken()  # type: ignore[assignment]
    mem = await remember("fact", "committed anyway")
    assert (await app.memory.get_memory(agent, mem)).text == "committed anyway"
    (doc,) = await live_docs(app)
    assert await app.recall.store.get(embedding_id(doc.rev)) is None
    assert [d.rev for d in await app.recall.store.unembedded("hash-256")] == [doc.rev]


def test_search_text_covers_kind_fields() -> None:
    from datetime import UTC, datetime

    from ontyra.models.kinds import ProcedureRev, Step

    rev = ProcedureRev(
        memory=RecordId("memory", "m"),
        seq=1,
        text="deploy",
        recorded_at=datetime.now(UTC),
        proposed_by=RecordId("actor", "a"),
        trigger="on release",
        steps=[Step(text="tag it")],
        preconditions=["green ci"],
    )
    assert search_text_of(rev) == "deploy. on release. tag it. green ci"
