"""The benchmark's data model: shorthand expansion and world loading.

`memory_fields` is the bridge between the compact JSON the worlds are written in and the memory
protocol: `about`, `at`, `q` and the kind-specific defaults become real fields, and a shorthand
that clashes with an explicit field is rejected rather than silently overridden.
"""

from __future__ import annotations

import pytest

from ontyra.calibration.models import (
    MemorySpec,
    World,
    _qualifier,
    load_world,
    memory_fields,
    world_ids,
)
from ontyra.models.ontology import ConceptDefinition, concept_slug


class TestQualifier:
    VTypes = {"budget": "number", "day": "string"}

    def test_a_number_becomes_an_int_with_its_unit(self) -> None:
        assert _qualifier("prerequisite:budget >= 500 EUR", self.VTypes) == {
            "concept": "concept:budget",
            "role": "prerequisite",
            "op": ">=",
            "value": 500,
            "unit": "EUR",
        }

    def test_a_decimal_becomes_a_float(self) -> None:
        got = _qualifier("sufficient:budget > 1.5", self.VTypes)
        assert got["value"] == 1.5
        assert "unit" not in got

    def test_negative_numbers_are_parsed(self) -> None:
        assert _qualifier("necessary:budget <= -3", self.VTypes)["value"] == -3

    def test_a_non_numeric_concept_keeps_the_text(self) -> None:
        got = _qualifier("trigger:day = weekday", self.VTypes)
        assert got["value"] == "weekday"
        assert "unit" not in got

    def test_a_number_shorthand_for_a_text_concept_stays_text(self) -> None:
        assert _qualifier("trigger:day = 14", self.VTypes)["value"] == "14"

    def test_no_operator_means_no_value(self) -> None:
        assert _qualifier("exclusion:budget", self.VTypes) == {
            "concept": "concept:budget",
            "role": "exclusion",
        }

    @pytest.mark.parametrize("shorthand", ["budget >= 5", "necessary:Budget >= 5", "necessary:"])
    def test_a_bad_shorthand_is_rejected(self, shorthand: str) -> None:
        with pytest.raises(ValueError, match="bad qualifier shorthand"):
            _qualifier(shorthand, self.VTypes)


class TestMemoryFields:
    def test_about_and_at_become_concept_ids_and_anchor_requests(self) -> None:
        mem = MemorySpec(
            key="m", kind="fact", text="t", about=["budget"], at=["memory://x", "=memory://y"]
        )
        assert memory_fields(mem, {}) == {
            "about": ["concept:budget"],
            "anchors": [{"uri": "memory://x"}, {"uri": "memory://y", "match": "exact"}],
        }

    def test_qualifiers_use_the_shorthand_expansion(self) -> None:
        mem = MemorySpec(key="m", kind="fact", text="t", q=["prerequisite:budget >= 5 EUR"])
        fields = memory_fields(mem, {"budget": "number"})
        assert fields["qualifiers"] == [
            {
                "concept": "concept:budget",
                "role": "prerequisite",
                "op": ">=",
                "value": 5,
                "unit": "EUR",
            }
        ]

    def test_strength_polarity_and_a_universal_scope_are_kept(self) -> None:
        mem = MemorySpec(
            key="m",
            kind="directive",
            text="t",
            strength="must",
            polarity="avoid",
            scope="universal",
        )
        fields = memory_fields(mem, {})
        assert fields["strength"] == "must"
        assert fields["polarity"] == "avoid"
        assert fields["scope"] == "universal"

    def test_directive_defaults_are_should_and_its_own_text(self) -> None:
        fields = memory_fields(MemorySpec(key="m", kind="directive", text="do not guess"), {})
        assert fields["strength"] == "should"
        assert fields["rule"] == "do not guess"

    def test_decision_defaults_are_its_text_and_a_fixed_date(self) -> None:
        fields = memory_fields(MemorySpec(key="m", kind="decision", text="use uv"), {})
        assert fields["rationale"] == "use uv"
        assert fields["decided_at"] == "2026-06-01T09:00:00Z"

    def test_procedure_steps_may_be_strings_and_dicts(self) -> None:
        mem = MemorySpec(
            key="m",
            kind="procedure",
            text="t",
            fields={"steps": ["first", {"text": "second"}, {"text": "third", "check": "ok"}]},
        )
        assert memory_fields(mem, {})["steps"] == [
            {"text": "first"},
            {"text": "second"},
            {"text": "third", "check": "ok"},
        ]

    def test_a_subject_without_a_scheme_gets_the_concept_prefix(self) -> None:
        mem = MemorySpec(key="m", kind="fact", text="t", fields={"subject": "project"})
        assert memory_fields(mem, {})["subject"] == "concept:project"

    def test_an_already_qualified_subject_is_left_alone(self) -> None:
        mem = MemorySpec(key="m", kind="fact", text="t", fields={"subject": "concept:project"})
        assert memory_fields(mem, {})["subject"] == "concept:project"

    def test_a_non_string_subject_is_left_alone(self) -> None:
        mem = MemorySpec(key="m", kind="fact", text="t", fields={"subject": ["a"]})
        assert memory_fields(mem, {})["subject"] == ["a"]

    def test_a_shorthand_that_clashes_with_fields_is_an_error(self) -> None:
        mem = MemorySpec(
            key="m", kind="fact", text="t", about=["budget"], fields={"about": ["concept:x"]}
        )
        with pytest.raises(ValueError, match="given both as shorthand and in fields"):
            memory_fields(mem, {})


def world() -> World:
    return World(
        id="w",
        description="a world",
        concepts=[
            ConceptDefinition(label="Budget", value_type="number"),
            ConceptDefinition(label="Project"),
        ],
        memories=[
            MemorySpec(
                key="m", kind="fact", text="t", about=["budget"], q=["necessary:budget = 5"]
            ),
        ],
    )


class TestWorld:
    def test_concept_ids_and_value_types_come_from_the_labels(self) -> None:
        w = world()
        assert w.concept_ids == {concept_slug("Budget"), concept_slug("Project")}
        assert w.value_types[concept_slug("Budget")] == "number"

    def test_fields_of_expands_a_memory_against_the_world_concepts(self) -> None:
        w = world()
        fields = w.fields_of(w.memories[0])
        assert fields["about"] == ["concept:budget"]
        assert fields["qualifiers"][0]["value"] == 5


class TestLoadWorld:
    def test_the_bundled_world_merges_its_parts_in_file_order(self) -> None:
        w = load_world("commons")
        assert w.id == "commons"
        assert w.memories
        assert all(m.domain for m in w.memories)  # every memory knows its part

    def test_a_duplicate_belongs_to_the_domain_of_what_it_restates(self) -> None:
        w = load_world("commons")
        by_key = {m.key: m for m in w.memories}
        for mem in w.memories:
            if mem.duplicate_of and mem.duplicate_of in by_key:
                assert mem.domain == by_key[mem.duplicate_of].domain

    def test_world_ids_lists_the_directories_that_carry_a_world(self) -> None:
        ids = world_ids()
        assert "commons" in ids
        assert ids == sorted(ids)
