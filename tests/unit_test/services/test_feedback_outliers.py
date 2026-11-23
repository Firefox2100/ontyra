from datetime import UTC, datetime, timedelta

from ontyra.models import RecordId
from ontyra.models.feedback import Feedback
from ontyra.services.feedback_review import rank_outliers

T0 = datetime(2026, 1, 1, tzinfo=UTC)
OWNER, MEM = RecordId("owner", "o"), RecordId("memory", "m")


def event(
    n: int,
    relevance: float,
    *,
    source: str = "a",
    query: str = "q",
    minutes: int = 0,
    owner: RecordId = OWNER,
    memory: RecordId = MEM,
) -> Feedback:
    return Feedback(
        id=RecordId("feedback", f"f{n}"),
        owner=owner,
        memory=memory,
        query=query,
        label="applied" if relevance else "irrelevant",
        relevance=relevance,
        source=RecordId("actor", source),
        source_kind="agent",
        weight=1.0,
        created_at=T0 + timedelta(minutes=minutes),
    )


def test_a_confident_disagreement_ranks_above_agreement() -> None:
    agree, clash = event(1, 1.0), event(2, 0.0, query="other")
    assert agree.id is not None and clash.id is not None
    scores = {agree.id: 0.9, clash.id: 0.95}
    rows = rank_outliers([agree, clash], scores, {}, {})
    assert rows[0][0] is clash and rows[0][3] == ["disagrees"]
    assert rows[1][3] == []


def test_without_lane_scores_only_the_other_signals_count() -> None:
    a, b = event(1, 1.0), event(2, 0.0, query="x", source="b")
    history = {(OWNER, MEM, "x"): [b, event(3, 1.0, query="x", source="b")]}
    rows = rank_outliers([a, b], {}, history, {})
    assert rows[0][0] is b and rows[0][3] == ["flipped"] and rows[0][2] is None


def test_other_owners_disagreeing_and_bursts_are_flagged() -> None:
    mine = event(1, 1.0)
    theirs = event(2, 0.0, owner=RecordId("owner", "x"))
    burst = [
        event(
            10 + i,
            1.0,
            source="spam",
            query=f"q{i}",
            minutes=i % 5,
            memory=RecordId("memory", f"z{i}"),
        )
        for i in range(20)
    ]
    rows = {r[0].id: r for r in rank_outliers([mine, *burst], {}, {}, {MEM: [theirs]})}
    assert rows[mine.id][3] == ["others_disagree"]
    assert all("burst" in rows[b.id][3] for b in burst)
