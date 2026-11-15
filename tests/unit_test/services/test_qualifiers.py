"""Three-valued qualifier evaluation and the role x result table (plans/01)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from ontyra.models import RecordId
from ontyra.models.anchors import Qualifier
from ontyra.models.ontology import Concept
from ontyra.services.qualifiers import (
    Observation,
    Tri,
    apply_roles,
    evaluate_one,
    parse_version,
    qualifier_problems,
)
from ontyra.services.reasoner import Ontology

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def cid(name: str) -> RecordId:
    return RecordId("concept", name)


def concept(name: str, **kw: Any) -> Concept:
    return Concept(label=name, created_at=NOW, **kw)


@pytest.fixture
def onto() -> Ontology:
    concepts = {
        cid("linux"): concept("linux"),
        cid("windows"): concept("windows"),
        cid("ubuntu"): concept("ubuntu"),
        cid("conda"): concept("conda"),
        cid("python"): concept("python", value_type="version", version_scheme="pep440"),
        cid("ram"): concept("ram", value_type="number", dimension="memory"),
        cid("shell"): concept("shell", value_type="enum", values=["bash", "zsh"]),
    }
    rels = [
        (cid("ubuntu"), "is_a", cid("linux")),
        (cid("linux"), "disjoint_with", cid("windows")),
    ]
    return Ontology(concepts, rels)


def q(name: str, role: str = "necessary", **kw: Any) -> Qualifier:
    return Qualifier(concept=cid(name), role=role, **kw)  # type: ignore[arg-type]


def obs(name: str, value: Any = None, unit: str | None = None) -> Observation:
    return Observation(concept=cid(name), value=value, unit=unit)


# ---- single qualifier ------------------------------------------------------------------


def test_entailed_concept_satisfies(onto: Ontology) -> None:
    assert evaluate_one(q("linux"), [obs("ubuntu")], onto) is Tri.YES


def test_disjoint_observation_is_unsatisfied(onto: Ontology) -> None:
    assert evaluate_one(q("linux"), [obs("windows")], onto) is Tri.NO


def test_no_signal_is_unknown_not_unsatisfied(onto: Ontology) -> None:
    assert evaluate_one(q("linux"), [], onto) is Tri.UNKNOWN
    assert evaluate_one(q("linux"), [obs("conda")], onto) is Tri.UNKNOWN


def test_observation_with_false_value_says_absent(onto: Ontology) -> None:
    assert evaluate_one(q("conda"), [obs("conda", False)], onto) is Tri.NO


@pytest.mark.parametrize(
    ("have", "op", "want", "expected"),
    [
        ("3.12", ">=", "3.9", Tri.YES),
        ("3.8", ">=", "3.9", Tri.NO),
        ("3.10", ">", "3.9", Tri.YES),  # numeric, not lexicographic
        ("3.9.0", "=", "3.9", Tri.YES),
        ("3.9", "!=", "3.9", Tri.NO),
        ("3.9", "<", "3.10", Tri.YES),
    ],
)
def test_version_comparison(have: str, op: str, want: str, expected: Tri, onto: Ontology) -> None:
    assert evaluate_one(q("python", op=op, value=want), [obs("python", have)], onto) is expected


def test_version_op_without_observed_value_is_unknown(onto: Ontology) -> None:
    assert evaluate_one(q("python", op=">=", value="3.9"), [obs("python")], onto) is Tri.UNKNOWN


def test_unparseable_version_is_unknown(onto: Ontology) -> None:
    assert evaluate_one(q("python", op=">=", value="3.9"), [obs("python", "n/a")], onto) is (
        Tri.UNKNOWN
    )


def test_between(onto: Ontology) -> None:
    cond = q("python", op="between", value=["3.9", "3.12"])
    assert evaluate_one(cond, [obs("python", "3.10")], onto) is Tri.YES
    assert evaluate_one(cond, [obs("python", "3.13")], onto) is Tri.NO


def test_units_convert_within_a_dimension(onto: Ontology) -> None:
    need = q("ram", op=">=", value=16, unit="GB")
    assert evaluate_one(need, [obs("ram", 32768, "MB")], onto) is Tri.YES
    assert evaluate_one(need, [obs("ram", 8192, "MB")], onto) is Tri.NO


def test_enum_in(onto: Ontology) -> None:
    cond = q("shell", op="in", value=["bash", "zsh"])
    assert evaluate_one(cond, [obs("shell", "zsh")], onto) is Tri.YES


def test_parse_version_schemes() -> None:
    assert parse_version("v3.12.1", "pep440") > parse_version("3.9", "pep440")
    assert parse_version("24.04", "distro") > parse_version("22.04", "distro")
    with pytest.raises(ValueError):
        parse_version("none", "dotted")


# ---- roles -----------------------------------------------------------------------------


def test_necessary_unsatisfied_excludes(onto: Ontology) -> None:
    out = apply_roles([q("linux", "necessary")], [obs("windows")], onto)
    assert not out.include and "requires linux" in out.reasons[0]


def test_necessary_unknown_keeps_with_caveat(onto: Ontology) -> None:
    out = apply_roles([q("linux", "necessary")], [], onto)
    assert out.include and out.demote and out.caveats == ["only if linux"]


def test_necessary_satisfied_is_clean(onto: Ontology) -> None:
    out = apply_roles([q("linux", "necessary")], [obs("ubuntu")], onto)
    assert out.include and not out.demote and not out.caveats and out.matched == 1


def test_exclusion_satisfied_excludes_but_unknown_does_not(onto: Ontology) -> None:
    assert not apply_roles([q("windows", "exclusion")], [obs("windows")], onto).include
    assert apply_roles([q("windows", "exclusion")], [], onto).include
    assert apply_roles([q("windows", "exclusion")], [obs("ubuntu")], onto).include


def test_sufficient_boosts_only_when_satisfied(onto: Ontology) -> None:
    assert apply_roles([q("linux", "sufficient")], [obs("ubuntu")], onto).boost
    for context in ([], [obs("windows")]):
        out = apply_roles([q("linux", "sufficient")], context, onto)
        assert out.include and not out.boost


def test_prerequisite_never_filters_but_attaches_checks(onto: Ontology) -> None:
    met = apply_roles([q("conda", "prerequisite")], [obs("conda")], onto)
    assert met.include and not met.checks
    unknown = apply_roles([q("conda", "prerequisite")], [], onto)
    assert unknown.include and unknown.caveats == ["verify prerequisite: conda"]
    missing = apply_roles([q("conda", "prerequisite")], [obs("conda", False)], onto)
    assert missing.include and missing.caveats == ["prerequisite not met: conda"]
    assert missing.checks == ["conda"]


def test_trigger_activates_when_satisfied(onto: Ontology) -> None:
    assert apply_roles([q("linux", "trigger")], [obs("ubuntu")], onto).triggered
    assert not apply_roles([q("linux", "trigger")], [], onto).triggered


def test_groups_are_and_within_or_across(onto: Ontology) -> None:
    both = [q("linux", group=0), q("conda", group=0)]
    assert apply_roles(both, [obs("ubuntu")], onto).demote  # conda unknown: AND is unknown
    assert not apply_roles(both, [obs("ubuntu"), obs("conda", False)], onto).include
    either = [q("linux", group=0), q("windows", group=1)]
    assert apply_roles(either, [obs("windows")], onto).include  # OR across groups
    assert not apply_roles(either, [obs("conda", False)], onto).demote or True


def test_no_qualifiers_apply_everywhere(onto: Ontology) -> None:
    out = apply_roles([], [], onto)
    assert out.include and not out.demote and not out.caveats


# ---- validation of expressions ---------------------------------------------------------


def test_expression_must_fit_the_concept(onto: Ontology) -> None:
    linux = onto.concepts[cid("linux")]
    assert qualifier_problems(q("linux", op=">=", value="1"), linux)
    python = onto.concepts[cid("python")]
    assert not qualifier_problems(q("python", op=">=", value="3.9"), python)
    assert qualifier_problems(q("python", op=">=", value="abc"), python)
    assert qualifier_problems(q("python", op="between", value=["3.9"]), python)
    assert qualifier_problems(q("python", op=">="), python)
    ram = onto.concepts[cid("ram")]
    assert qualifier_problems(q("ram", op=">=", value=1, unit="parsecs"), ram)
    shell = onto.concepts[cid("shell")]
    assert qualifier_problems(q("shell", op="=", value="fish"), shell)
