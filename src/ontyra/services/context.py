"""Who is calling: resolved by the transport, passed explicitly to every service."""

from __future__ import annotations

from dataclasses import dataclass

from ontyra.models import RecordId
from ontyra.models.core import Actor

GLOBAL_OWNER = RecordId("owner", "global")


@dataclass(frozen=True)
class Caller:
    actor: Actor
    owner: RecordId = GLOBAL_OWNER

    @property
    def actor_id(self) -> RecordId:
        assert self.actor.id is not None
        return self.actor.id

    def visible_owners(self) -> list[RecordId]:
        """Owners whose memories this caller can see (own + global)."""
        return list(dict.fromkeys([self.owner, GLOBAL_OWNER]))
