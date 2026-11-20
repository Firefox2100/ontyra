"""Regressions found by the first full code review (pure logic)."""

from __future__ import annotations

from datetime import UTC, datetime

from ontyra.calibration import search
from ontyra.calibration.ranking import Row
from ontyra.models import RecordId
from ontyra.models.ontology import Concept
from ontyra.services.dedupe import is_update, values
from ontyra.services.reasoner import Ontology

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def concept(label: str, *aliases: str, status: str = "active") -> Concept:
    return Concept(label=label, aliases=list(aliases), status=status, created_at=NOW)  # type: ignore[arg-type]


def test_thousands_separators_are_removed_however_many_groups() -> None:
    assert values("limit is 1,000,000 requests") == values("limit is 1000000 requests")
    assert not is_update("limit is 1,000,000 requests a day", "limit is 1000000 requests a day")


def test_a_concept_at_the_end_of_a_sentence_is_still_mentioned() -> None:
    cid = RecordId("concept", "ubuntu")
    onto = Ontology({cid: concept("Ubuntu")}, [])
    assert [m.concept for m in onto.mentions("we run Ubuntu.")] == [cid]
    assert [m.concept for m in onto.mentions("it is ubuntu 24.04 here")] == [cid]  # inner dots stay
    assert onto.find("Ubuntu.") == [cid]


def test_a_merged_name_belongs_to_the_survivor_not_the_tombstone() -> None:
    keep, gone = RecordId("concept", "ubuntu"), RecordId("concept", "ubuntu_linux")
    onto = Ontology(
        {
            keep: concept("Ubuntu", "Ubuntu Linux", "Ubuntu LTS"),
            gone: concept("Ubuntu Linux", "Ubuntu LTS", status="deprecated"),
        },
        [],
    )
    assert onto.find("ubuntu lts") == [keep]
    assert [m.concept for m in onto.mentions("on Ubuntu LTS today")] == [keep]
    only_old = Ontology({gone: concept("Old thing", status="deprecated")}, [])
    assert only_old.find("old thing") == [gone]  # nothing else claims it


def row(key: str, cos: float) -> Row:
    return Row(key, None, cos, None, 0.0, True, None, None)


def test_a_relevant_memory_that_was_never_retrieved_cannot_pass() -> None:
    req = search.Request(
        id="r", split="tune", silent=False, pool=[row("a", 0.9)], relevant=set(), negative=set(),
        best_cos=0.9, missing=True,
    )  # fmt: skip
    assert not search.passes(req, search.Setting(0.0, "min", 0.5, 0.0))
    empty = search.Request(
        id="r", split="tune", silent=False, pool=[row("a", 0.9)], relevant=set(), negative=set(),
        best_cos=0.9,
    )  # fmt: skip
    assert not search.passes(empty, search.Setting(0.0, "min", 0.5, 0.0))


def test_a_valued_observation_decides_before_a_bare_one() -> None:
    from ontyra.models.anchors import Qualifier
    from ontyra.services.qualifiers import Observation, Tri, evaluate_one

    ubuntu = RecordId("concept", "ubuntu")
    c = Concept(label="Ubuntu", value_type="version", version_scheme="dotted", created_at=NOW)
    onto = Ontology({ubuntu: c}, [])
    q = Qualifier(concept=ubuntu, role="necessary", op=">=", value="22.04")
    bare = Observation(concept=ubuntu)  # a resource that is an instance of ubuntu
    valued = Observation(concept=ubuntu, value="24.04")
    assert evaluate_one(q, [bare, valued], onto) is Tri.YES  # order does not matter
    assert evaluate_one(q, [valued, bare], onto) is Tri.YES
    assert evaluate_one(q, [bare], onto) is Tri.UNKNOWN
    has = Qualifier(concept=ubuntu, role="necessary", op="has")
    assert evaluate_one(has, [bare, Observation(concept=ubuntu, value=False)], onto) is Tri.NO


def test_equal_cosines_do_not_zero_the_dense_score() -> None:
    from ontyra.calibration.search import fused

    only = [row("a", 0.8)]
    assert fused(only, 0.0, "mean")["a"] == 1.0  # a lone candidate is fully dense, not zero
    assert fused([row("a", 0.8), row("b", 0.8)], 0.0, "min") == {"a": 1.0, "b": 1.0}
