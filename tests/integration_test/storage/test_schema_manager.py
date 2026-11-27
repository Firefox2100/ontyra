"""Generated schema applied to a real SurrealDB: acceptance, immutability, drift."""

import pytest

from ontyra.models import RecordId, registry
from ontyra.models.core import Actor, RevState, Supersedes
from ontyra.models.kinds import ProcedureRev
from ontyra.storage import Database, SchemaManager
from ontyra.storage.codec import to_content
from ontyra.storage.errors import (
    ImmutableViolationError,
    SchemaViolationError,
    UniqueViolationError,
)


async def _create(db: Database, rid: RecordId, model: object) -> None:
    await db.query("CREATE $id CONTENT $c", {"id": rid, "c": to_content(model)})  # type: ignore[arg-type]


async def test_apply_creates_every_registered_table(
    database: Database, schema: SchemaManager
) -> None:
    tables = set((await database.info_db())["tables"])
    assert set(registry.tables) <= tables


async def test_apply_is_idempotent_and_keeps_data(
    database: Database, schema: SchemaManager, seeded: RecordId
) -> None:
    await schema.apply()
    await schema.apply()
    assert (await database.query("SELECT * FROM memory"))[0][0]["kind"] == "directive"


async def test_every_kind_round_trips_through_the_database(
    database: Database, seeded: RecordId, revisions: dict[str, object]
) -> None:
    for kind, rev in revisions.items():
        spec = registry.kinds[kind]
        await _create(database, RecordId(spec.table, f"{kind}1"), rev)
        row = (await database.query("SELECT * FROM $id", {"id": RecordId(spec.table, f"{kind}1")}))[
            0
        ][0]
        back = spec.model.model_validate(row)
        assert back.id == RecordId(spec.table, f"{kind}1")
        assert back.model_dump(exclude={"id"}, exclude_none=True) == rev.model_dump(  # type: ignore[attr-defined]
            exclude={"id"}, exclude_none=True
        )


async def test_nested_steps_survive_the_round_trip(
    database: Database, seeded: RecordId, revisions: dict[str, object]
) -> None:
    rev = revisions["procedure"]
    assert isinstance(rev, ProcedureRev)
    await _create(database, RecordId("procedure_rev", "p"), rev)
    row = (await database.query("SELECT * FROM procedure_rev:p"))[0][0]
    assert [s["text"] for s in row["steps"]] == ["tag", "push"]


async def test_database_rejects_bad_data_that_bypasses_pydantic(
    database: Database, seeded: RecordId, revisions: dict[str, object]
) -> None:
    good = to_content(revisions["directive"])  # type: ignore[arg-type]
    for patch, why in [
        ({"strength": "always"}, "literal"),
        ({"memory": RecordId("actor", "claude")}, "link target"),
        ({"bogus": 1}, "unknown field"),
        ({"seq": "one"}, "type"),
    ]:
        with pytest.raises(SchemaViolationError, match=r"."):
            await database.query(
                "CREATE type::record('directive_rev', $k) CONTENT $c",
                {"k": f"bad-{why}".replace(" ", "-"), "c": {**good, **patch}},
            )


async def test_database_fills_defaults_for_raw_writes(
    database: Database, seeded: RecordId, now
) -> None:
    await database.query(
        "CREATE directive_rev:raw CONTENT $c",
        {
            "c": {
                "memory": seeded,
                "seq": 1,
                "text": "t",
                "recorded_at": now,
                "proposed_by": RecordId("actor", "claude"),
                "strength": "should",
                "rule": "r",
            }
        },
    )
    row = (await database.query("SELECT * FROM directive_rev:raw"))[0][0]
    assert row["confidence"] == 1.0 and row["polarity"] == "do"


async def test_revisions_cannot_be_updated_or_deleted(
    database: Database, seeded: RecordId, revisions: dict[str, object]
) -> None:
    rid = RecordId("directive_rev", "d1")
    await _create(database, rid, revisions["directive"])
    with pytest.raises(ImmutableViolationError):
        await database.query("UPDATE $id SET rule = 'changed'", {"id": rid})
    with pytest.raises(ImmutableViolationError):
        await database.query("DELETE $id", {"id": rid})
    assert (await database.query("SELECT rule FROM $id", {"id": rid}))[0][0]["rule"] == revisions[
        "directive"
    ].rule  # type: ignore[attr-defined]


async def test_rev_state_and_memory_pointer_are_mutable(
    database: Database, seeded: RecordId, revisions: dict[str, object], now
) -> None:
    rid = RecordId("directive_rev", "d1")
    await _create(database, rid, revisions["directive"])
    await _create(
        database,
        RecordId("rev_state", "s1"),
        RevState(rev=rid, state="proposed", valid_from=now),
    )
    await database.query("UPDATE rev_state:s1 SET state = 'committed'")
    await database.query("UPDATE $m SET current = $r", {"m": seeded, "r": rid})
    assert (await database.query("SELECT state FROM rev_state:s1"))[0][0]["state"] == "committed"
    assert (await database.query("SELECT current FROM $m", {"m": seeded}))[0][0][
        "current"
    ] is not None


async def test_rev_state_only_links_to_revision_tables(
    database: Database, seeded: RecordId, now
) -> None:
    with pytest.raises(SchemaViolationError):
        await database.query(
            "CREATE rev_state:x CONTENT $c",
            {"c": {"rev": RecordId("actor", "claude"), "state": "proposed", "valid_from": now}},
        )


async def test_unique_index_on_actor_name(database: Database, seeded: RecordId) -> None:
    with pytest.raises(UniqueViolationError):
        await _create(database, RecordId("actor", "other"), Actor(kind="agent", name="claude"))


async def test_supersedes_edge_links_across_kinds_carries_a_reason_and_is_immutable(
    database: Database, seeded: RecordId, revisions: dict[str, object], now
) -> None:
    await _create(database, RecordId("fact_rev", "f1"), revisions["fact"])
    await _create(database, RecordId("directive_rev", "d1"), revisions["directive"])
    edge = Supersedes(
        reason_code="refined", reason="narrowed", by=RecordId("actor", "claude"), at=now
    )
    await database.query(
        "RELATE $a->supersedes->$b CONTENT $c",
        {
            "a": RecordId("directive_rev", "d1"),
            "b": RecordId("fact_rev", "f1"),
            "c": to_content(edge),
        },
    )
    row = (await database.query("SELECT ->supersedes.reason_code AS rc FROM directive_rev:d1"))[0][
        0
    ]
    assert row["rc"] == ["refined"]
    with pytest.raises(ImmutableViolationError):
        await database.query("UPDATE supersedes SET reason = 'edited'")
    with pytest.raises(SchemaViolationError):  # an actor is not a revision
        await database.query(
            "RELATE $a->supersedes->$b CONTENT $c",
            {
                "a": RecordId("actor", "claude"),
                "b": RecordId("fact_rev", "f1"),
                "c": to_content(edge),
            },
        )


async def test_sync_removes_stale_tables_and_fields_but_keeps_data(
    database: Database, schema: SchemaManager, seeded: RecordId, revisions: dict[str, object]
) -> None:
    await _create(database, RecordId("fact_rev", "f1"), revisions["fact"])
    await database.query(
        "DEFINE FIELD stale_note ON fact_rev TYPE option<string>; DEFINE TABLE gone SCHEMALESS"
    )
    assert sorted(await schema.drift()) == [
        "REMOVE FIELD `stale_note` ON fact_rev",
        "REMOVE TABLE gone",
    ]
    removed = await schema.sync()
    assert len(removed) == 2 and await schema.drift() == []
    assert (await database.query("SELECT count() FROM fact_rev GROUP ALL"))[0][0]["count"] == 1


async def test_wipe_removes_all_registered_tables(
    database: Database, schema: SchemaManager
) -> None:
    await schema.wipe()
    assert set(registry.tables).isdisjoint((await database.info_db())["tables"])
    await schema.apply()  # and the schema can be recreated from scratch
    assert set(registry.tables) <= set((await database.info_db())["tables"])


async def test_a_field_added_to_a_table_with_rows_migrates_them(
    database: Database, schema: SchemaManager, seeded: RecordId
) -> None:
    await database.query("REMOVE FIELD trust ON actor")
    await database.query("REMOVE FIELD meta ON actor")
    assert (await database.query("SELECT * FROM actor"))[0]
    await schema.apply()
    row = (await database.query("SELECT * FROM actor"))[0][0]
    assert row["trust"] is not None and "meta" in row
    await schema.apply()


async def test_a_required_field_nothing_can_fill_is_added_to_an_empty_table(
    database: Database, schema: SchemaManager
) -> None:
    """`source_check.owner` is a record link: no value can be invented for existing rows, so a
    table that has rows needs a hand-written migration, and one that has none just gets the field."""
    await database.query("REMOVE FIELD owner ON source_check")
    await schema.apply()
    assert "owner" in (await database.info_table("source_check"))["fields"]


async def test_a_required_field_nothing_can_fill_is_refused_when_rows_exist(
    database: Database, schema: SchemaManager, seeded: RecordId
) -> None:
    del seeded  # the actor that checked_by points at
    await database.query("REMOVE FIELD owner ON source_check")
    await database.query(
        "CREATE source_check:old SET uri = 'file://m/a', state = 'ok', "
        "checked_at = time::now(), checked_by = actor:claude"
    )
    with pytest.raises(ValueError, match=r"source_check\.owner"):
        await schema.apply()
    assert (await database.query("SELECT count() FROM source_check GROUP ALL"))[0][0]["count"] == 1
