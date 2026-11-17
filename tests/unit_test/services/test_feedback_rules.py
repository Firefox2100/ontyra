"""The feedback conflict resolver and query normalisation (pure logic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ontyra.models import RecordId
from ontyra.models.feedback import Feedback
from ontyra.services.feedback import normalise_query, resolve

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def event(weight: float, minutes: int, relevance: float = 1.0) -> Feedback:
    return Feedback(
        owner=RecordId("owner", "global"),
        memory=RecordId("memory", "m"),
        query="q",
        label="applied" if relevance else "irrelevant",
        relevance=relevance,
        source=RecordId("actor", "a"),
        source_kind="agent",
        weight=weight,
        created_at=T0 + timedelta(minutes=minutes),
    )


def test_the_newer_event_wins_a_tie_in_trust() -> None:
    wins, losers = resolve(event(0.8, 5), [event(0.8, 0)])
    assert wins and len(losers) == 1


def test_a_more_trusted_older_verdict_beats_a_newer_agent_verdict() -> None:
    human = event(1.0, 0, relevance=0.0)
    wins, losers = resolve(event(0.8, 10, relevance=1.0), [human])
    assert not wins and losers == []


def test_a_new_event_replaces_every_weaker_one() -> None:
    wins, losers = resolve(event(1.0, 20), [event(0.5, 0), event(0.8, 5)])
    assert wins and len(losers) == 2


def test_it_loses_if_any_existing_event_is_more_trusted() -> None:
    wins, _ = resolve(event(0.8, 20), [event(0.5, 0), event(1.0, 5)])
    assert not wins


def test_queries_are_compared_without_case_or_spacing() -> None:
    assert normalise_query("  How  do I\tCut a Release ") == "how do i cut a release"
