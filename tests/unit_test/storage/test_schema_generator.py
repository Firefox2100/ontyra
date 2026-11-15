from datetime import datetime
from typing import Annotated, Any, Literal

import pytest

from ontyra.models import Indexed, Model, Record, RecordId, Ref, Registry, registry
from ontyra.storage.schema import SchemaGenerator


class _Inner(Model):
    text: str
    optional: bool = False


@pytest.fixture
def gen() -> SchemaGenerator:
    return SchemaGenerator(registry)


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (str, "string"),
        (int, "int"),
        (float, "float"),
        (bool, "bool"),
        (datetime, "datetime"),
        (str | None, "option<string>"),
        (list[str], "array<string>"),
        (list[str] | None, "option<array<string>>"),
        (Literal["a", "b"], "'a' | 'b'"),
        (Literal["it's"], "'it\\'s'"),
        (dict[str, Any], "object"),
        (_Inner, "object"),
        (list[_Inner], "array<object>"),
        (Any, "any"),
        (Ref("actor"), "record<actor>"),
        (Ref("concept", "resource") | None, "option<record<concept|resource>>"),
        (str | Ref("concept"), "string | record<concept>"),
        (list[Ref("episode")], "array<record<episode>>"),  # type: ignore[misc]
        (RecordId, "record"),
    ],
)
def test_surreal_type_mapping(gen: SchemaGenerator, annotation: Any, expected: str) -> None:
    assert gen.surreal_type(annotation)[0] == expected


def test_dict_is_flexible_but_nested_models_are_not(gen: SchemaGenerator) -> None:
    assert gen.surreal_type(dict[str, Any])[1] is True
    assert gen.surreal_type(_Inner)[1] is False
    assert gen.surreal_type(list[dict[str, Any]])[1] is True


def test_wildcard_ref_expands_to_registered_revision_tables(gen: SchemaGenerator) -> None:
    t, _ = gen.surreal_type(Ref("*_rev"))
    assert t == "record<" + "|".join(registry.rev_tables()) + ">"


def test_unmappable_type_is_an_error(gen: SchemaGenerator) -> None:
    with pytest.raises(TypeError):
        gen.surreal_type(set[complex])


class _Doc(Record):
    name: Annotated[str, Indexed(unique=True)]
    tag: Annotated[str | None, Indexed()] = None
    items: list[_Inner] = []
    meta: dict[str, Any] = {}
    level: Literal["lo", "hi"] = "lo"
    count: int = 3
    flag: bool = False


def _doc_registry(*, immutable: bool = False) -> Registry:
    r = Registry()
    r.table("doc", immutable=immutable)(_Doc)
    return r


def test_table_ddl_fields_defaults_nested_and_indexes() -> None:
    stmts = SchemaGenerator(_doc_registry()).ddl()
    assert stmts[0] == "DEFINE TABLE OVERWRITE doc SCHEMAFULL"
    assert "DEFINE FIELD OVERWRITE name ON doc TYPE string" in stmts
    assert "DEFINE FIELD OVERWRITE tag ON doc TYPE option<string>" in stmts
    assert "DEFINE FIELD OVERWRITE items ON doc TYPE array<object> DEFAULT []" in stmts
    assert "DEFINE FIELD OVERWRITE items[*].text ON doc TYPE string" in stmts
    assert "DEFINE FIELD OVERWRITE items[*].optional ON doc TYPE bool DEFAULT false" in stmts
    assert "DEFINE FIELD OVERWRITE meta ON doc TYPE object FLEXIBLE DEFAULT {}" in stmts
    assert "DEFINE FIELD OVERWRITE level ON doc TYPE 'lo' | 'hi' DEFAULT 'lo'" in stmts
    assert "DEFINE FIELD OVERWRITE count ON doc TYPE int DEFAULT 3" in stmts
    assert "DEFINE INDEX OVERWRITE doc_name_idx ON doc FIELDS name UNIQUE" in stmts
    assert "DEFINE INDEX OVERWRITE doc_tag_idx ON doc FIELDS tag" in stmts
    assert not any("DEFINE FIELD OVERWRITE id " in s for s in stmts)
    assert not any("READONLY" in s or "DEFINE EVENT" in s for s in stmts)


def test_immutable_table_gets_readonly_fields_and_delete_guard() -> None:
    stmts = SchemaGenerator(_doc_registry(immutable=True)).ddl()
    fields = [s for s in stmts if s.startswith("DEFINE FIELD")]
    assert fields and all(s.endswith("READONLY") for s in fields)
    guard = [s for s in stmts if s.startswith("DEFINE EVENT")]
    assert len(guard) == 1 and '$event = "DELETE"' in guard[0] and "THROW" in guard[0]


def test_edge_table_endpoints_are_expanded_from_the_registry() -> None:
    stmts = SchemaGenerator(registry).table_ddl(registry.tables["supersedes"])
    revs = "|".join(registry.rev_tables())
    assert stmts[0] == (
        f"DEFINE TABLE OVERWRITE supersedes TYPE RELATION IN {revs} OUT {revs} ENFORCED SCHEMAFULL"
    )
    assert any("reason_code ON supersedes TYPE 'corrected' |" in s for s in stmts)


def test_full_ddl_covers_every_registered_table_and_is_well_formed(gen: SchemaGenerator) -> None:
    stmts = gen.ddl()
    for name in registry.tables:
        assert f"DEFINE TABLE OVERWRITE {name} " in " ".join(stmts)
    assert all(s.startswith("DEFINE ") for s in stmts)
    assert all(";" not in s for s in stmts), "statements are joined by the caller"


def test_adding_a_kind_widens_every_revision_link() -> None:
    r = Registry()
    r.kind("note")(_Doc)  # type: ignore[arg-type]
    r.table("memory")(_Doc)
    # the wildcard in an edge expands against *this* registry
    r.table("e", edge=(("*_rev",), ("*_rev",)))(_Doc)
    first = SchemaGenerator(r).table_ddl(r.tables["e"])[0]
    r.kind("other")(_Doc)  # type: ignore[arg-type]
    second = SchemaGenerator(r).table_ddl(r.tables["e"])[0]
    assert "note_rev" in first and "other_rev" not in first
    assert "other_rev" in second


def test_plan_drift_detects_stale_tables_and_fields_only(gen: SchemaGenerator) -> None:
    r = _doc_registry()
    g = SchemaGenerator(r)
    fields = {
        "name": "...", "tag": "...", "items": "...", "items.*": "...", "items.*.text": "...",
        "items.*.optional": "...", "meta": "...", "level": "...", "count": "...", "flag": "...",
    }  # fmt: skip
    assert g.plan_drift({"tables": {"doc": ""}}, {"doc": {"fields": fields}}) == []
    stale = g.plan_drift(
        {"tables": {"doc": "", "gone": ""}}, {"doc": {"fields": {**fields, "old": "..."}}}
    )
    assert sorted(stale) == ["REMOVE FIELD `old` ON doc", "REMOVE TABLE gone"]
