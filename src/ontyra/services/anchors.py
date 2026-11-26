"""Resources and context resolution: from the URIs a caller observes to the chain of
resources that count as "here".
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from ontyra.models import RecordId, new_id
from ontyra.models.anchors import Anchor
from ontyra.models.ontology import Resource, ResourceRequest
from ontyra.models.proposals import ProposalRequest
from ontyra.models.uri import _clean_path, canonical_uri, uri_ancestors
from ontyra.ports import Create, Store
from ontyra.services import errors
from ontyra.services.ontology import OntologyService

# `file://D:/repo`: what a Windows path looks like when its drive letter lands in the host slot.
_DRIVE_AS_HOST = re.compile(r"^file://([A-Za-z]):(/.*)?$", re.IGNORECASE)


def qualify(uri: str, machine: str) -> str:
    """Give a `file:` URI the host of the machine that wrote it.

    A path anchor only means something relative to one machine, and only the caller knows which
    one it is. So a caller that says "I am `x-x99`" gets every path anchor recorded against it: a
    host copied from elsewhere (a Tailscale device name, another box's name) is replaced instead
    of stored, where it would never match a context and the memory would quietly go missing.
    """
    text = uri.strip()
    if text.partition(":")[0].lower() != "file":
        return uri
    host = machine.strip().lower() or "localhost"
    drive = _DRIVE_AS_HOST.match(text)
    if drive is not None:
        path = _clean_path(drive.group(2) or "/")
        return f"file://{host}/{drive.group(1).upper()}:{path if path else ''}"
    return f"file://{host}{_clean_path(urlsplit(text).path)}"


def anchored_to(body: ProposalRequest, machine: str) -> ProposalRequest:
    """The same proposal with every path anchor recorded against `machine`.

    Applied where a client declares which machine it runs on (an `X-Ontyra-Machine` header), so
    the host of a path anchor is decided by code rather than by whoever typed the URI.
    """
    ops: list[Any] = []
    for op in body.ops:
        fields = getattr(op, "fields", None)
        anchors = fields.get("anchors") if isinstance(fields, dict) else None
        if not isinstance(anchors, list) or not anchors:
            ops.append(op)
            continue
        rewritten = [
            Anchor(
                uri=qualify(str(a.get("uri", "") if isinstance(a, dict) else a.uri), machine),
                match=a.get("match", "subtree") if isinstance(a, dict) else a.match,
            )
            for a in anchors
        ]
        ops.append(op.model_copy(update={"fields": {**fields, "anchors": rewritten}}))
    return body.model_copy(update={"ops": ops})


@dataclass(frozen=True)
class Chain:
    """The resolved context: canonical URI → distance from the nearest observed resource."""

    distance: dict[str, int]
    concepts: list[RecordId]  # concepts the resources are instances of (become observations)

    def match(self, anchor: Anchor) -> int | None:
        """Distance if `anchor` applies in this context, else `None`. `exact` anchors only match
        a resource that is itself observed (distance 0)."""
        d = self.distance.get(anchor.uri)
        if d is None or (anchor.match == "exact" and d != 0):
            return None
        return d


class AnchorService:
    def __init__(
        self,
        store: Store,
        now: Callable[[], datetime],
        ontology: OntologyService,
        machine: str = "localhost",
    ) -> None:
        self.store = store
        self.now = now
        self.ontology = ontology
        self.machine = machine
        self._resources: dict[str, Resource] | None = None

    def canonical(self, uri: str) -> str:
        try:
            return canonical_uri(uri, self.machine)
        except ValueError as exc:
            raise errors.InvalidRequestError(str(exc)) from exc

    async def _registered(self) -> dict[str, Resource]:
        if self._resources is None:
            rows = await self.store.select("resource", limit=50_000)
            self._resources = {r.uri: r for r in rows if isinstance(r, Resource)}
        return self._resources

    async def register(self, request: ResourceRequest) -> Resource:
        uri = self.canonical(request.uri)
        if uri in await self._registered():
            raise errors.ConflictError(f"{uri} is already registered")
        concepts = [await self.ontology.resolve(c) for c in request.instance_of]
        resource = Resource(
            uri=uri,
            kind=request.kind,
            label=request.label,
            parents=[self.canonical(p) for p in request.parents],
            same_as=[self.canonical(s) for s in request.same_as],
            instance_of=concepts,
            created_at=self.now(),
        )
        await self.store.apply([Create.of(RecordId("resource", new_id()), resource)])
        self._resources = None
        return resource

    async def list(self, limit: int = 500) -> list[Resource]:
        return list((await self._registered()).values())[:limit]

    async def resolve(self, uris: Iterable[str]) -> Chain:
        """Union of the containment chains of the observed URIs, nearest first.

        Syntactic containment (path segments) plus registered `parents`; `same_as` links count
        as the same place (distance unchanged); `instance_of` concepts are collected.
        """
        registered = await self._registered()
        # reverse same_as so equivalence is symmetric
        equal: dict[str, set[str]] = {}
        for r in registered.values():
            for other in r.same_as:
                equal.setdefault(r.uri, set()).add(other)
                equal.setdefault(other, set()).add(r.uri)
        distance: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for raw in uris:
            queue.append((self.canonical(raw), 0))
        concepts: list[RecordId] = []
        while queue:
            uri, d = queue.popleft()
            if uri in distance and distance[uri] <= d:
                continue
            distance[uri] = d
            for step, anc in enumerate(uri_ancestors(uri)[1:], start=1):
                queue.append((anc, d + step))
            for other in equal.get(uri, ()):
                queue.append((other, d))
            res = registered.get(uri)
            if res is not None:
                queue.extend((p, d + 1) for p in res.parents)
                concepts.extend(c for c in res.instance_of if c not in concepts)
        return Chain(distance, concepts)
