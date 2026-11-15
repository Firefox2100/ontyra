from datetime import UTC, datetime

from surrealdb import RecordID

from ontyra.models import RecordId
from ontyra.models.core import Actor
from ontyra.storage.codec import encode, to_content


def test_encode_converts_record_ids_recursively() -> None:
    out = encode({"a": RecordId("t", "1"), "b": [RecordId("u", 2), {"c": RecordId("v", "3")}]})
    assert out["a"] == RecordID("t", "1")
    assert out["b"][0] == RecordID("u", 2)
    assert out["b"][1]["c"] == RecordID("v", "3")


def test_encode_leaves_plain_values_alone() -> None:
    now = datetime.now(UTC)
    assert encode({"n": 1, "s": "x", "d": now, "none": None}) == {
        "n": 1,
        "s": "x",
        "d": now,
        "none": None,
    }


def test_to_content_drops_id_and_none_fields() -> None:
    actor = Actor(id=RecordId("actor", "claude"), kind="agent", name="claude")
    content = to_content(actor)
    assert "id" not in content and "harness" not in content
    assert content["name"] == "claude" and content["trust"] == 0.5


def test_encode_accepts_models() -> None:
    out = encode(Actor(kind="agent", name="a"))
    assert out["kind"] == "agent"
