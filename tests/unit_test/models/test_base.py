from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from ontyra.models import Model, RecordId, Ref


class TestRecordId:
    def test_parses_string(self) -> None:
        rid = RecordId.parse("fact_rev:abc")
        assert (rid.table, rid.id) == ("fact_rev", "abc")
        assert str(rid) == "fact_rev:abc"

    def test_strips_surreal_quoting(self) -> None:
        assert RecordId.parse("t:⟨a-b⟩").id == "a-b"

    def test_parses_sdk_like_object(self) -> None:
        sdk = SimpleNamespace(table_name="actor", id="claude")
        assert RecordId.parse(sdk) == RecordId("actor", "claude")

    def test_parses_the_real_sdk_record_id(self) -> None:
        from surrealdb import RecordID

        assert RecordId.parse(RecordID("actor", "claude")) == RecordId("actor", "claude")
        assert RecordId.parse(RecordID("t", 7)) == RecordId("t", 7)

    def test_parses_dict(self) -> None:
        assert RecordId.parse({"table": "a", "id": 1}) == RecordId("a", 1)

    @pytest.mark.parametrize("bad", ["nocolon", "t:", 3, None, {"table": "x"}, object()])
    def test_rejects_garbage(self, bad: object) -> None:
        with pytest.raises(ValueError):
            RecordId.parse(bad)

    def test_equality_and_hash(self) -> None:
        assert RecordId("a", "1") == RecordId("a", "1")
        assert RecordId("a", "1") != RecordId("a", "2")
        assert len({RecordId("a", "1"), RecordId("a", "1")}) == 1

    def test_json_serialises_to_string_and_python_keeps_object(self) -> None:
        ta = TypeAdapter(RecordId)
        rid = RecordId("a", "1")
        assert ta.dump_python(rid) is rid
        assert ta.dump_json(rid) == b'"a:1"'


class _Holder(Model):
    memory: Ref("memory")  # type: ignore[valid-type]
    rev: Ref("*_rev")  # type: ignore[valid-type]


class TestRef:
    def test_accepts_matching_table_from_string(self) -> None:
        h = _Holder(memory="memory:m1", rev="fact_rev:r1")  # type: ignore[arg-type]
        assert h.memory == RecordId("memory", "m1")

    def test_rejects_wrong_table(self) -> None:
        with pytest.raises(ValidationError, match="must link to"):
            _Holder(memory="actor:x", rev="fact_rev:r1")  # type: ignore[arg-type]

    def test_wildcard_accepts_every_revision_table_and_nothing_else(self) -> None:
        for table in ("fact_rev", "directive_rev", "lesson_rev"):
            _Holder(memory="memory:m", rev=f"{table}:r")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            _Holder(memory="memory:m", rev="actor:r")  # type: ignore[arg-type]


def test_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _Holder(memory="memory:m", rev="fact_rev:r", bogus=1)  # type: ignore[call-arg, arg-type]
