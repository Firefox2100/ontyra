"""Actors and owners: who acts, and whose memories they act on."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime

from ontyra.models import RecordId
from ontyra.models.core import Actor, Owner
from ontyra.ports import Create, Store
from ontyra.services.context import ALL_PERMISSIONS, GLOBAL_OWNER, Caller

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

    async def ensure_actor(
        self, kind: str, name: str, harness: str | None = None, rid: RecordId | None = None
    ) -> Actor:
        """Idempotent by id (default: derived from the name). Trust comes from configuration by
        actor kind, never from the actor itself."""
        explicit = rid is not None
        rid = rid or RecordId("actor", _slug(name))
        existing = await self.store.get(rid)
        if isinstance(existing, Actor):
            # kind and trust are the caller's identity *now* (a credential, or the server's
            # configured kind), never what an older row of the same name says: otherwise a
            # client could pick up a human actor's kind by sending its name
            return existing.model_copy(update={"kind": kind, "trust": self.trust.get(kind, 0.5)})
        if explicit and await self.store.select("actor", {"name": name}, limit=1):
            name = f"{name}#{str(rid.id)[:6]}"  # a re-issued credential keeps its readable name
        actor = Actor(
            kind=kind,  # type: ignore[arg-type]
            name=name,
            harness=harness,
            trust=self.trust.get(kind, 0.5),
        )
        await self.store.apply([Create.of(rid, actor)])
        return actor.model_copy(update={"id": rid})

    async def caller(
        self,
        *,
        kind: str,
        name: str,
        harness: str | None = None,
        owner: RecordId | None = None,
        permissions: frozenset[str] | None = None,
        actor_id: RecordId | None = None,
    ) -> Caller:
        """Build a caller. Transports never take `owner` from a request: single-user mode leaves
        it unset (the global owner, with every permission), multi-user mode passes the account's."""
        actor = await self.ensure_actor(kind, name, harness, actor_id)
        if owner is None:
            return Caller(actor=actor, owner=await self.ensure_owner(), permissions=ALL_PERMISSIONS)
        return Caller(actor=actor, owner=owner, permissions=permissions or frozenset())
