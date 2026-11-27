from ontyra.models import RecordId
from ontyra.ports import ExpectFields, Update
from ontyra.storage.repository import compile_writes


def test_field_guard_precedes_update_and_uses_bound_values() -> None:
    task = RecordId("task", "one")
    statements, params = compile_writes(
        [
            ExpectFields(task, {"status": "pending", "claimed_by": None}),
            Update(task, {"status": "claimed"}),
        ]
    )
    assert statements[0].startswith("LET $g0 = (SELECT * FROM ONLY $i0)")
    assert "$g0.status != $e0_0" in statements[0]
    assert "$g0.claimed_by != $e0_1" in statements[0]
    assert statements[1] == "UPDATE $i1 MERGE $c1"
    assert params["i0"] == task
    assert params["e0_0"] == "pending" and params["e0_1"] is None
