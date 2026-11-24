"""How a cited source changes a memory's standing in recall: the tier of its best
source (a deployment's judgement of a URI, never the agent's claim), corroboration by several
distinct sources, and the age of what was read. Pure functions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ontyra.models.recall import SourceTier
from ontyra.models.sources import SourceRef

MAX_CORROBORATING = 3  # sources beyond the first that still add to the score


def tier_of(uri: str, tiers: Sequence[SourceTier]) -> SourceTier | None:
    """The first configured tier whose `match` is a prefix of the (canonical) URI."""
    return next((t for t in tiers if uri.startswith(t.match)), None)


def corroboration(sources: Sequence[SourceRef]) -> int:
    """How many distinct sources the memory cites."""
    return len({s.uri for s in sources})


def source_factor(
    sources: Sequence[SourceRef], tiers: Sequence[SourceTier], boost: float
) -> tuple[float, str | None]:
    """The multiplier on the score and the name of the tier it came from (the best source
    wins: one reliable citation is enough). No source: neutral."""
    if not sources:
        return 1.0, None
    found = [(t.weight, t.tier) for s in sources if (t := tier_of(s.uri, tiers)) is not None]
    weight, name = max(found, default=(1.0, None))
    extra = min(max(corroboration(sources) - 1, 0), MAX_CORROBORATING)
    return weight * (1.0 + boost * extra), name


def read_age_days(sources: Sequence[SourceRef], now: datetime) -> int | None:
    """Days since the most recent reading of any cited source, when known."""
    read = [s.retrieved_at for s in sources if s.retrieved_at is not None]
    return max(0, (now - max(read)).days) if read else None
