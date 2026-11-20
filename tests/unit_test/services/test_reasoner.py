from datetime import UTC, datetime

import pytest

from ontyra.models import RecordId
from ontyra.models.ontology import Concept
from ontyra.services.reasoner import Ontology

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def cid(name: str) -> RecordId:
    return RecordId("concept", name)


def build(
    relations: list[tuple[str, str, str]],
    aliases: dict[str, list[str]] | None = None,
    replaced: dict[str, str] | None = None,
) -> Ontology:
    names = {n for a, _, b in relations for n in (a, b)} | set(aliases or {}) | set(replaced or {})
    names |= set((replaced or {}).values())
    concepts = {
        cid(n): Concept(
            label=n.replace("_", " "),
            aliases=(aliases or {}).get(n, []),
            replaced_by=cid(replaced[n]) if replaced and n in replaced else None,
            created_at=NOW,
        )
        for n in names
    }
    return Ontology(concepts, [(cid(a), r, cid(b)) for a, r, b in relations])


@pytest.fixture
def onto() -> Ontology:
    return build(
        [
            ("ubuntu_24_04", "is_a", "ubuntu"),
            ("ubuntu", "is_a", "debian_family"),
            ("debian_family", "is_a", "linux"),
            ("linux", "disjoint_with", "windows"),
            ("pytest", "is_a", "testing_framework"),
            ("unittest", "alternative_to", "pytest"),
            ("apt", "part_of", "debian_family"),
        ],
        aliases={"pytest": ["py.test"], "linux": ["gnu/linux"]},
    )


def test_entailment_goes_up_the_is_a_chain(onto: Ontology) -> None:
    assert onto.entails(cid("ubuntu_24_04"), cid("linux"))
    assert not onto.entails(cid("linux"), cid("ubuntu_24_04"))


def test_disjointness_is_inherited_by_subconcepts(onto: Ontology) -> None:
    assert onto.disjoint(cid("ubuntu_24_04"), cid("windows"))
    assert onto.disjoint(cid("windows"), cid("ubuntu"))
    assert not onto.disjoint(cid("ubuntu"), cid("linux"))


def test_alternatives_rule_each_other_out(onto: Ontology) -> None:
    assert onto.disjoint(cid("unittest"), cid("pytest"))


def test_cycles_terminate() -> None:
    onto = build([("a", "is_a", "b"), ("b", "is_a", "c"), ("c", "is_a", "a")])
    assert set(onto.ancestors(cid("a"))) == {cid("b"), cid("c")}
    assert onto.entails(cid("a"), cid("c"))


def test_replaced_by_chain_resolves_to_the_current_concept() -> None:
    onto = build([], replaced={"py2": "py3", "py3": "py4"})
    assert onto.resolve(cid("py2")) == cid("py4")
    assert onto.entails(cid("py4"), cid("py2"))  # the old name still refers to it


def test_replaced_by_cycle_does_not_loop() -> None:
    onto = build([], replaced={"a": "b", "b": "a"})
    assert onto.resolve(cid("a")) in {cid("a"), cid("b")}


def test_same_as_is_symmetric_and_transitive() -> None:
    onto = build([("a", "same_as", "b"), ("c", "same_as", "b")])
    assert onto.equivalents(cid("a")) == {cid("a"), cid("b"), cid("c")}


def test_expansion_weights_decay_with_distance(onto: Ontology) -> None:
    expanded = onto.expand(cid("debian_family"))
    assert expanded[cid("debian_family")][0] == 1.0
    assert expanded[cid("ubuntu")][0] > expanded[cid("ubuntu_24_04")][0] > 0
    assert expanded[cid("linux")][0] < expanded[cid("ubuntu")][0]
    assert expanded[cid("apt")][0] > 0


def test_expansion_includes_alternatives(onto: Ontology) -> None:
    assert cid("unittest") in onto.expand(cid("pytest"))


def test_mentions_use_labels_and_aliases_longest_first(onto: Ontology) -> None:
    found = onto.mentions("how do I run py.test on Ubuntu 24 04 with testing framework tools")
    assert {m.concept for m in found} >= {
        cid("pytest"),
        cid("ubuntu_24_04"),
        cid("testing_framework"),
    }
    assert [m.phrase for m in onto.mentions("GNU/Linux")] == ["gnu linux"] or onto.mentions("linux")


def test_find_is_case_insensitive(onto: Ontology) -> None:
    assert onto.find("PyTest") == [cid("pytest")]
    assert onto.find("nothing") == []


def test_plurals_fold_onto_the_singular() -> None:
    o = build([("vegetarian", "is_a", "dietary_preference")])
    assert o.find("dietary preferences") == [cid("dietary_preference")]
    assert [m.concept for m in o.mentions("which dietary preferences apply")] == [
        cid("dietary_preference")
    ]
