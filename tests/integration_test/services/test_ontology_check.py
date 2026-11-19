"""The ontology health report, on an ontology broken by direct writes (the service refuses to)."""

from __future__ import annotations

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, Related
from ontyra.ports import Delete, Relate
from ontyra.services import Caller


async def test_a_healthy_ontology_reports_only_information(
    app: Application, human: Caller, concepts
) -> None:
    report = await app.ontology_check.check(human)
    assert report.concepts >= 3
    assert not [f for f in report.findings if f.severity in ("error", "warning")]


async def test_the_check_finds_loops_shared_names_and_deprecated_use(
    app: Application, human: Caller, remember
) -> None:
    ids = {}
    for label in ("Aa", "Bb", "Cc", "Old", "New", "Twin", "Ghost"):
        ids[label] = (await app.ontology.define(human, ConceptDefinition(label=label))).id
    edge = Related(relation="is_a", by=human.actor_id, at=app.ontology.now()).model_dump(
        mode="python", exclude={"id"}
    )
    store = app.ontology.store
    await store.apply(
        [
            Relate("related", ids["Aa"], ids["Bb"], edge),
            Relate("related", ids["Bb"], ids["Cc"], edge),
            Relate("related", ids["Cc"], ids["Aa"], edge),  # a loop
            Relate("related", ids["Aa"], ids["Ghost"], edge),
        ]
    )
    await store.apply([Delete(ids["Ghost"])])  # the database removes its edges with it
    app.ontology.invalidate()
    await remember("fact", "a statement about the old thing", about=[str(ids["Old"])])
    await app.ontology.deprecate(human, ids["Old"], ids["New"])
    from ontyra.models.ontology import Concept

    twin = await store.get(ids["Twin"])
    assert isinstance(twin, Concept)
    await store.apply([_set_aliases(ids["Aa"], ["twin"])])  # a second concept now says "twin"
    app.ontology.invalidate()
    report = await app.ontology_check.check(human)
    codes = {f.code for f in report.findings}
    assert {"cycle", "shared_name", "deprecated_in_use"} <= codes
    loop = next(f for f in report.findings if f.code == "cycle")
    assert {ids["Aa"], ids["Bb"], ids["Cc"]} <= set(loop.concepts)
    assert report.findings[0].severity == "error"  # errors first


def _set_aliases(concept: RecordId, aliases: list[str]):  # type: ignore[no-untyped-def]
    from ontyra.ports import Update

    return Update(concept, {"aliases": aliases})
