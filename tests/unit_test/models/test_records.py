from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ontyra.models import RecordId
from ontyra.models.kinds import DirectiveRev, LessonRev, ProcedureRev, Step

NOW = datetime(2026, 11, 15, tzinfo=UTC)
BASE = {
    "memory": RecordId("memory", "m1"),
    "seq": 1,
    "text": "t",
    "recorded_at": NOW,
    "proposed_by": RecordId("actor", "claude"),
}


def test_directive_defaults() -> None:
    d = DirectiveRev(**BASE, strength="must", rule="use pytest")  # type: ignore[arg-type]
    assert d.polarity == "do" and d.confidence == 1.0 and d.expires_at is None and d.id is None


def test_directive_rejects_unknown_strength() -> None:
    with pytest.raises(ValidationError):
        DirectiveRev(**BASE, strength="always", rule="r")  # type: ignore[arg-type]


def test_revision_links_are_checked_in_python_too() -> None:
    bad = {**BASE, "memory": RecordId("actor", "x")}
    with pytest.raises(ValidationError, match="must link to"):
        DirectiveRev(**bad, strength="must", rule="r")  # type: ignore[arg-type]


def test_nested_steps_and_mutable_defaults_are_per_instance() -> None:
    a = ProcedureRev(**BASE, trigger="release", steps=[Step(text="tag")])  # type: ignore[arg-type]
    b = ProcedureRev(**BASE, trigger="release", steps=[Step(text="tag")])  # type: ignore[arg-type]
    a.preconditions.append("clean tree")
    assert b.preconditions == []


def test_lesson_evidence_links_to_episodes() -> None:
    ok = LessonRev(**BASE, situation="s", insight="i", evidence=["episode:e1"])  # type: ignore[arg-type, list-item]
    assert ok.evidence == [RecordId("episode", "e1")]
    with pytest.raises(ValidationError):
        LessonRev(**BASE, situation="s", insight="i", evidence=["actor:a"])  # type: ignore[arg-type, list-item]
