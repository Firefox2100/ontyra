"""Time-ordered record ids (sortable, collision-resistant, URL/SurrealQL safe)."""

from __future__ import annotations

import secrets
import time


def new_id() -> str:
    """12 hex chars of millisecond time + 10 random hex chars."""
    return f"{int(time.time() * 1000):012x}{secrets.token_hex(5)}"
