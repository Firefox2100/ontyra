"""Settings for the episode log (`ONTYRA_EPISODES__*`)."""

from __future__ import annotations

from ontyra.models.base import Model


class EpisodeSettings(Model):
    """How episodes are matched as evidence and how long unused ones are kept.

    An agent cites what was said with a `quote`; the daemon finds the quote among the caller's
    recent episodes (`match_window_hours`, newest first, at most `match_limit` examined). An
    episode that no memory cites is deleted after `retention_days` (0 keeps everything); the
    sweep runs at most every `prune_interval_minutes`, piggybacked on episode writes.
    """

    match_window_hours: int = 72
    match_limit: int = 500
    retention_days: int = 14
    prune_interval_minutes: int = 60
