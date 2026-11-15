"""`Database` against a real SurrealDB, in embedded and server mode."""

import pytest

from ontyra.storage import Database
from ontyra.storage.errors import (
    SchemaViolationError,
    StorageError,
    TransactionAbortedError,
    UniqueViolationError,
)


async def test_capabilities_match_the_mode(database: Database) -> None:
    caps = database.capabilities
    assert caps.mode in ("embedded", "ws")
    assert caps.requires_auth is (caps.mode == "ws")
    assert caps.exclusive_access is (caps.mode == "embedded")
    assert caps.live_queries is (caps.mode == "ws")


async def test_server_version_is_reported(database: Database) -> None:
    assert (await database.server_version()).startswith("surrealdb-4")


async def test_query_returns_one_result_per_statement(database: Database) -> None:
    res = await database.query("RETURN 1; RETURN 2")
    assert res == [1, 2]


async def test_run_atomic_commits_all_statements(database: Database) -> None:
    await database.query("DEFINE TABLE t SCHEMALESS")
    await database.run_atomic(["CREATE t:1 SET n=1", "CREATE t:2 SET n=2"])
    assert (await database.query("SELECT count() FROM t GROUP ALL"))[0][0]["count"] == 2


async def test_run_atomic_rolls_everything_back_on_failure(database: Database) -> None:
    await database.query("DEFINE TABLE t SCHEMALESS")
    with pytest.raises(StorageError):
        await database.run_atomic(["CREATE t:1 SET n=1", "THROW 'boom'", "CREATE t:2 SET n=2"])
    counts = (await database.query("SELECT count() FROM t GROUP ALL"))[0]
    assert sum(row["count"] for row in counts) == 0


async def test_run_atomic_accepts_variables(database: Database) -> None:
    await database.query("DEFINE TABLE t SCHEMALESS")
    await database.run_atomic(["CREATE t:1 SET n=$n"], {"n": 7})
    assert (await database.query("SELECT n FROM t:1"))[0][0]["n"] == 7


async def test_failed_batch_is_reported_as_a_storage_error(database: Database) -> None:
    with pytest.raises(StorageError) as info:
        await database.run_atomic(["THROW 'nope'"])
    assert isinstance(info.value, (TransactionAbortedError, StorageError))


async def test_schema_and_unique_violations_map_to_storage_errors(database: Database) -> None:
    await database.query(
        "DEFINE TABLE u SCHEMAFULL; DEFINE FIELD n ON u TYPE int; DEFINE FIELD k ON u TYPE string;"
        "DEFINE INDEX u_k ON u FIELDS k UNIQUE; CREATE u:1 SET n=1, k='a'"
    )
    with pytest.raises(SchemaViolationError):
        await database.query("CREATE u:2 SET n='not an int', k='b'")
    with pytest.raises(UniqueViolationError):
        await database.query("CREATE u:3 SET n=1, k='a'")


async def test_embedded_rejects_a_second_open_of_the_same_path(
    tmp_path: pytest.TempPathFactory,
) -> None:
    url = f"surrealkv://{tmp_path}/db"  # type: ignore[str-bytes-safe]
    first = await Database.connect(url, namespace="n", database="d")
    try:
        with pytest.raises(StorageError):
            await Database.connect(url, namespace="n", database="d")
    finally:
        await first.close()


async def test_embedded_file_data_persists_across_reopen(tmp_path: pytest.TempPathFactory) -> None:
    url = f"surrealkv://{tmp_path}/db"  # type: ignore[str-bytes-safe]
    db = await Database.connect(url, namespace="n", database="d")
    await db.query("DEFINE TABLE t SCHEMALESS; CREATE t:1 SET a=1")
    await db.close()
    db = await Database.connect(url, namespace="n", database="d")
    try:
        assert len((await db.query("SELECT * FROM t"))[0]) == 1
    finally:
        await db.close()
