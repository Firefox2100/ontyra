"""Load a world into an application and play a case against it."""

from __future__ import annotations

import time

from benchmark_test.framework.models import Case, Step, World
from benchmark_test.framework.probe import Probe
from benchmark_test.framework.score import StepScore, score_step
from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import RelationRequest
from ontyra.models.proposals import (
    AddMemory,
    LinkMemories,
    ProposalRequest,
    RetractMemory,
    SupersedeMemory,
)
from ontyra.models.recall import RecallRequest
from ontyra.services import Caller


async def load_world(
    app: Application, curator: Caller, writer: Caller, world: World
) -> dict[str, str]:
    """Create the ontology, resources and memories; returns `memory id -> world key`."""
    for concept in world.concepts:
        await app.ontology.define(curator, concept)
    for rel in world.relations:
        await app.ontology.relate(
            curator, RelationRequest(source=rel.source, relation=rel.relation, target=rel.target)
        )
    for resource in world.resources:
        await app.anchors.register(resource)

    ids: dict[str, RecordId] = {}
    for mem in world.memories:
        ops: list[AddMemory | SupersedeMemory | LinkMemories] = [
            AddMemory(kind=mem.kind, ref=mem.key, fields={"text": mem.text, **world.fields_of(mem)})
        ]
        if mem.supersedes:
            ops.append(
                SupersedeMemory(new=mem.key, target=ids[mem.supersedes], reason_code="outdated")
            )
        ops += [
            LinkMemories(relation=link.relation, source=mem.key, target=ids[link.target])
            for link in mem.links
        ]
        result = await app.memory.submit(writer, ProposalRequest(ops=ops))  # type: ignore[arg-type]
        assert result.status == "committed", (mem.key, result.findings)
        ids[mem.key] = result.created[mem.key]
        if mem.retracted:
            gone = await app.memory.submit(
                writer,
                ProposalRequest(ops=[RetractMemory(memory=ids[mem.key], reason="planted error")]),
            )
            assert gone.status == "committed", (mem.key, gone.findings)
    await app.indexer.backfill()
    return {str(v): k for k, v in ids.items()}


async def play(
    app: Application,
    caller: Caller,
    case: Case,
    keys: dict[str, str],
    domains: dict[str, str] | None = None,
    probe: Probe | None = None,
    universal: frozenset[str] = frozenset(),
    twins: dict[str, str] | None = None,
) -> list[StepScore]:
    """Run the case's first request and its `then` requests (one session when it has any)."""
    session = case.id if case.then else None
    scores: list[StepScore] = []
    for step in [case, *case.then]:
        scores.append(
            await _one(app, caller, step, keys, session, domains, probe, universal, twins)
        )
    return scores


async def _one(
    app: Application,
    caller: Caller,
    step: Step,
    keys: dict[str, str],
    session: str | None,
    domains: dict[str, str] | None,
    probe: Probe | None,
    universal: frozenset[str] = frozenset(),
    twins: dict[str, str] | None = None,
) -> StepScore:
    context = step.context.model_copy(update={"session": session or step.context.session})
    rows = await probe.rows(step, session) if probe is not None else None
    started = time.perf_counter()
    verdict: str | None = None
    if step.mode == "check":
        checked = await app.recall.check(caller, step.query or "", context)
        pack, verdict = checked.pack, checked.verdict
    else:
        request = RecallRequest(context=context, query=step.query, hints=step.hints)
        run = app.recall.recall if step.mode == "recall" else app.recall.briefing
        pack = await run(caller, request)
    ms = (time.perf_counter() - started) * 1000
    scored = score_step(
        step,
        pack,
        keys,
        verdict=verdict,
        ms=ms,
        domains=domains,
        universal=universal,
        twins=twins,
    )
    scored.rows = rows
    return scored
