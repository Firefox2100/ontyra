import pytest

from ontyra.app import Application
from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, RelationRequest, ResourceRequest
from ontyra.models.proposals import AddMemory, ProposalRequest
from ontyra.services import Caller, errors


async def test_defining_a_concept_derives_a_readable_id(
    app: Application, human: Caller, concepts: dict[str, RecordId]
) -> None:
    view = await app.ontology.get(human, concepts["ubuntu"])
    assert str(view.id) == "concept:ubuntu" and view.scheme == "qualifier"
    assert [(r.relation, r.label) for r in view.relations if r.direction == "out"] == [
        ("is_a", "Debian family")
    ]


async def test_duplicate_label_conflicts(
    app: Application, human: Caller, concepts: dict[str, RecordId]
) -> None:
    with pytest.raises(errors.ConflictError):
        await app.ontology.define(human, ConceptDefinition(label="LINUX"))


async def test_version_and_enum_concepts_need_their_details(
    app: Application, human: Caller
) -> None:
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology.define(human, ConceptDefinition(label="x", value_type="version"))
    with pytest.raises(errors.InvalidRequestError):
        await app.ontology.define(human, ConceptDefinition(label="y", value_type="enum"))


async def test_lookup_by_alias_and_unknown_is_not_found(
    app: Application, human: Caller, concepts: dict[str, RecordId]
) -> None:
    found = await app.ontology.lookup(human, "PY.TEST")
    assert [str(v.id) for v in found] == ["concept:pytest"]
    assert await app.ontology.lookup(human, "nonexistent") == []
    with pytest.raises(errors.NotFoundError):
        await app.ontology.resolve("nonexistent")


async def test_relate_rejects_cycles_and_is_idempotent(
    app: Application, human: Caller, concepts: dict[str, RecordId]
) -> None:
    with pytest.raises(errors.ConflictError, match="cycle"):
        await app.ontology.relate(
            human, RelationRequest(source="linux", relation="is_a", target="ubuntu")
        )
    again = RelationRequest(source="ubuntu", relation="is_a", target="debian family")
    first = await app.ontology.relate(human, again)
    second = await app.ontology.relate(human, again)
    assert len(first.relations) == len(second.relations)


async def test_deprecation_points_at_the_successor(
    app: Application, human: Caller, concepts: dict[str, RecordId]
) -> None:
    await app.ontology.deprecate(human, concepts["unittest"], concepts["pytest"])
    onto = await app.ontology.ontology()
    assert onto.resolve(concepts["unittest"]) == concepts["pytest"]


async def test_memories_about_a_deprecated_concept_warn(
    app: Application, agent: Caller, human: Caller, concepts: dict[str, RecordId]
) -> None:
    await app.ontology.deprecate(human, concepts["unittest"], concepts["pytest"])
    result = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="decision",
                    fields={
                        "text": "stay on unittest",
                        "chosen": "unittest",
                        "rationale": "legacy",
                        "decided_at": "2026-01-01T00:00:00Z",
                        "about": [str(concepts["unittest"])],
                    },
                )
            ]
        ),
    )
    assert result.status == "committed"
    assert [f.code for f in result.findings if f.severity == "warning"] == ["deprecated_concept"]


# ---- validation of links, qualifiers and anchors -----------------------------------------


async def _try(app: Application, caller: Caller, **fields: object) -> list[str]:
    result = await app.memory.submit(
        caller,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="fact",
                    fields={
                        "text": "t",
                        "subject": "concept:linux",
                        "predicate": "p",
                        "object": "o",
                        **fields,
                    },
                )
            ]
        ),
    )
    return [f.code for f in result.findings if f.severity == "error"]


async def test_unknown_concept_links_are_rejected(
    app: Application, agent: Caller, concepts: dict[str, RecordId]
) -> None:
    assert await _try(app, agent, about=["concept:nope"]) == ["unknown_concept"]
    bad = [{"concept": "concept:nope", "role": "necessary"}]
    assert await _try(app, agent, qualifiers=bad) == ["unknown_concept"]


async def test_qualifier_expressions_must_fit_the_concept(
    app: Application, agent: Caller, concepts: dict[str, RecordId]
) -> None:
    ok = [{"concept": "concept:python", "role": "necessary", "op": ">=", "value": "3.9"}]
    assert await _try(app, agent, qualifiers=ok) == []
    bad_op = [{"concept": "concept:linux", "role": "necessary", "op": ">=", "value": "1"}]
    assert await _try(app, agent, qualifiers=bad_op) == ["bad_qualifier"]
    bad_unit = [
        {"concept": "concept:ram", "role": "necessary", "op": ">=", "value": 8, "unit": "furlong"}
    ]
    assert await _try(app, agent, qualifiers=bad_unit) == ["bad_qualifier"]


async def test_anchor_uris_are_canonicalised_and_validated(
    app: Application, agent: Caller, concepts: dict[str, RecordId]
) -> None:
    assert await _try(app, agent, anchors=[{"uri": "not a uri"}]) == ["bad_anchor"]
    result = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="fact",
                    ref="m",
                    fields={
                        "text": "t",
                        "subject": "concept:linux",
                        "predicate": "p",
                        "object": "o",
                        "anchors": [
                            {"uri": "SSH://someone@Somewhere.COM/"},
                            {"uri": "ssh://someone@somewhere.com"},
                        ],
                    },
                )
            ]
        ),
    )
    view = await app.memory.get_memory(agent, result.created["m"])
    assert view.content["anchors"] == [{"uri": "ssh://someone@somewhere.com", "match": "subtree"}]


# ---- resources and context resolution ----------------------------------------------------


async def test_chain_nearest_first_and_siblings_excluded(app: Application) -> None:
    chain = await app.anchors.resolve(["file://m/home/x/proj/src/a.py"])
    assert chain.distance["file://m/home/x/proj/src/a.py"] == 0
    assert chain.distance["file://m/home/x/proj"] == 2
    assert "file://m/home/x/proj2" not in chain.distance


async def test_registered_parents_same_as_and_instance_of(
    app: Application, concepts: dict[str, RecordId]
) -> None:
    await app.anchors.register(
        ResourceRequest(
            uri="file://m/home/x/proj", kind="project", parents=["git+https://github.com/org/repo"]
        )
    )
    await app.anchors.register(
        ResourceRequest(
            uri="git+https://github.com/org/repo", kind="repo", same_as=["file://laptop/src/repo"]
        )
    )
    await app.anchors.register(
        ResourceRequest(uri="ssh://someone@somewhere.com", instance_of=["docker"])
    )
    chain = await app.anchors.resolve(["file://m/home/x/proj/a.py", "ssh://someone@somewhere.com"])
    assert chain.distance["git+https://github.com/org/repo"] == 2  # a.py → proj → repo
    assert chain.distance["file://laptop/src/repo"] == 2  # same_as: same place
    assert concepts["docker"] in chain.concepts


async def test_duplicate_registration_conflicts(app: Application) -> None:
    await app.anchors.register(ResourceRequest(uri="host:somewhere.com", kind="host"))
    with pytest.raises(errors.ConflictError):
        await app.anchors.register(ResourceRequest(uri="HOST:Somewhere.com"))
