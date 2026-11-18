"""Telling a restatement from an update, without a model."""

from __future__ import annotations

import pytest

from ontyra.services.dedupe import anchors_compatible, conflicting_values, is_update, values


def test_values_are_normalised() -> None:
    assert values("The budget is 450,000 EUR") == values("Budget: 450k euros")
    assert values("on 3rd March") == values("March 3")
    assert values("a seven-minute connection") == {"7"}
    assert values("Use pytest") == set()


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Tidewatch production runs PostgreSQL 14.", "Tidewatch production runs PostgreSQL 16."),
        (
            "Expense claims above 300 EUR need approval.",
            "Expense claims above 500 EUR need approval.",
        ),
        ("The refund window is 14 days.", "The refund window is 30 days."),
    ],
)
def test_a_changed_value_in_the_same_wording_is_an_update(a: str, b: str) -> None:
    assert conflicting_values(a, b)
    assert is_update(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # the same statement, one side only adds or spells out a number
        (
            "A seven-minute train connection was missed.",
            "Changing trains with 7 minutes failed twice.",
        ),
        (
            "Pixel gets one tablet at breakfast until 8 November.",
            "Until November 8th Pixel takes an antibiotic.",
        ),
        # no values at all
        ("Use pytest for tests.", "Tests are written with pytest."),
    ],
)
def test_a_restatement_is_not_an_update(a: str, b: str) -> None:
    assert not is_update(a, b)


def test_different_facts_with_different_numbers_are_not_an_update() -> None:
    a = "Main LocalAI listens on port 8081 with the cuda-12 image."
    b = "The second instance on port 8083 serves decision models only."
    assert conflicting_values(a, b)  # both have a value the other lacks...
    assert not is_update(a, b)  # ...but the rest of the wording differs


def test_anchors_apply_in_a_common_place() -> None:
    assert anchors_compatible([], ["file:///a"])  # unanchored applies everywhere
    assert anchors_compatible(["file:///a/b"], ["file:///a"])  # containment by path
    assert anchors_compatible(["file:///a"], ["file:///a"])
    assert not anchors_compatible(["file:///a/tidewatch"], ["file:///a/lanternkit"])
    assert not anchors_compatible(
        ["file:///a/tide"], ["file:///a/tidewatch"]
    )  # not a prefix of a segment
