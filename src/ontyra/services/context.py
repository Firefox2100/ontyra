"""Who is calling: resolved by the transport, passed explicitly to every service."""

from __future__ import annotations

from dataclasses import dataclass

from ontyra.models import RecordId
from ontyra.models.core import Actor

GLOBAL_OWNER = RecordId("owner", "global")

ALL_PERMISSIONS: frozenset[str] = frozenset({"global_write", "admin"})


@dataclass(frozen=True)
class Caller:
    """The authenticated caller. `owner` comes from the account (or is the global owner when
    multi-user mode is off); it is never taken from a request parameter."""

    actor: Actor
    owner: RecordId = GLOBAL_OWNER
    permissions: frozenset[str] = frozenset()

    @property
    def actor_id(self) -> RecordId:
        assert self.actor.id is not None
        return self.actor.id

    def visible_owners(self) -> list[RecordId]:
        """Owners whose memories this caller can see (own + global)."""
        return list(dict.fromkeys([self.owner, GLOBAL_OWNER]))

    def can_write(self, owner: RecordId) -> bool:
        """May this caller change memories of `owner`? Its own, or the global memory with the
        `global_write` permission."""
        return owner == self.owner or (owner == GLOBAL_OWNER and "global_write" in self.permissions)

    def can_review(self, owner: RecordId) -> bool:
        """Only a human credential reviews, and only what it could write itself."""
        return self.actor.kind == "human" and self.can_write(owner)

    def has(self, permission: str) -> bool:
        return permission in self.permissions
