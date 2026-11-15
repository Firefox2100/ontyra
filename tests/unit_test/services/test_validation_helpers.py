import pytest

from ontyra.models import RecordId
from ontyra.services.validation import rev_state_id, similarity


def test_rev_state_shares_the_revision_id() -> None:
    assert rev_state_id(RecordId("fact_rev", "abc")) == RecordId("rev_state", "abc")


@pytest.mark.parametrize(
    ("a", "b", "low", "high"),
    [
        ("Use pytest instead of unittest", "use pytest instead of unittest", 1.0, 1.0),
        ("Use pytest instead of unittest", "always use pytest for new tests", 0.2, 0.6),
        ("serial ports live in /dev", "use pytest instead of unittest", 0.0, 0.0),
        ("", "anything", 0.0, 0.0),
    ],
)
def test_similarity_ranges(a: str, b: str, low: float, high: float) -> None:
    assert low <= similarity(a, b) <= high
