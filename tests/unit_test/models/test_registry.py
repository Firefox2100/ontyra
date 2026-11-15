import typing

import pytest

from ontyra.models import LifecyclePolicy, Record, Registry, registry
from ontyra.models.core import Memory
from ontyra.models.kinds import KindName

KINDS = ["fact", "directive", "decision", "event", "procedure", "task", "lesson"]


class _A(Record):
    x: int = 0


def test_register_table(fresh_registry: Registry) -> None:
    fresh_registry.table("a", immutable=True)(_A)
    spec = fresh_registry.tables["a"]
    assert spec.model is _A and spec.immutable and spec.edge is None


def test_duplicate_table_is_an_error(fresh_registry: Registry) -> None:
    fresh_registry.table("a")(_A)
    with pytest.raises(ValueError, match="already registered"):
        fresh_registry.table("a")(_A)


def test_kind_creates_an_immutable_rev_table(fresh_registry: Registry) -> None:
    fresh_registry.kind("note", LifecyclePolicy(relevance_decay=True))(_A)
    assert fresh_registry.kinds["note"].table == "note_rev"
    assert fresh_registry.tables["note_rev"].immutable
    assert fresh_registry.rev_tables() == ("note_rev",)


def test_wildcard_expansion(fresh_registry: Registry) -> None:
    fresh_registry.kind("a")(_A)
    fresh_registry.kind("b")(_A)
    assert fresh_registry.expand(("*_rev", "episode")) == ("a_rev", "b_rev", "episode")
    assert fresh_registry.expand(("x", "x")) == ("x",)


def test_default_registry_has_the_seven_kinds() -> None:
    assert list(registry.kinds) == KINDS
    assert registry.rev_tables() == tuple(f"{k}_rev" for k in KINDS)


def test_kind_literal_matches_registry() -> None:
    """`KindName` is spelled out for type checkers; it must not drift from the registry."""
    assert list(typing.get_args(KindName)) == list(registry.kinds)
    assert list(typing.get_args(Memory.model_fields["kind"].annotation)) == list(registry.kinds)


def test_core_tables_and_edges_are_registered() -> None:
    expected = {
        "actor", "owner", "episode", "memory", "rev_state",
        "supersedes", "contradicts", "overrides", "refines", "derived_from", "promoted_to",
    }  # fmt: skip
    assert expected <= set(registry.tables)
    assert all(
        registry.tables[e].edge
        for e in expected - {"actor", "owner", "episode", "memory", "rev_state"}
    )
