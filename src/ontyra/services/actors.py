"""Actors and owners: who acts, and whose memories they act on."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime

from ontyra.models import RecordId
from ontyra.models.core import Actor, Owner
from ontyra.ports import Create, Store
from ontyra.services.context import GLOBAL_OWNER, Caller

DEFAULT_TRUST: dict[str, float] = {
    "human": 1.0,
    "service": 0.8,
    "agent": 0.6,
    "tool": 0.5,
    "model": 0.4,
}


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.casefold()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive an id from {name!r}")
    return slug


class ActorService:
    def __init__(
        self,
        store: Store,
        now: Callable[[], datetime],
        trust: Mapping[str, float] | None = None,
    ) -> None:
        self.store = store
        self.now = now
        self.trust = {**DEFAULT_TRUST, **(trust or {})}

    async def ensure_owner(self, kind: str = "global", name: str = "global") -> RecordId:
        rid = (
            GLOBAL_OWNER if (kind, name) == ("global", "global") else RecordId("owner", _slug(name))
        )
        if await self.store.get(rid) is None:
            await self.store.apply([Create.of(rid, Owner(kind=kind, name=name))])  # type: ignore[arg-type]
        return rid

    async def ensure_actor(self, kind: str, name: str, harness: str | None = None) -> Actor:
        """Idempotent by name. Trust comes from configuration by actor kind, never from the
        actor itself."""
        rid = RecordId("actor", _slug(name))
        existing = await self.store.get(rid)
        if isinstance(existing, Actor):
            return existing
        actor = Actor(
            kind=kind,  # type: ignore[arg-type]
            name=name,
            harness=harness,
            trust=self.trust.get(kind, 0.5),
        )
        await self.store.apply([Create.of(rid, actor)])
        return actor.model_copy(update={"id": rid})

    async def caller(
        self, *, kind: str, name: str, harness: str | None = None, owner: RecordId | None = None
    ) -> Caller:
        actor = await self.ensure_actor(kind, name, harness)
        return Caller(actor=actor, owner=owner or await self.ensure_owner())
