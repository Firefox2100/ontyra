"""Resources and context resolution: from the URIs a caller observes to the chain of
resources that count as "here" (plans/01 "Context resolution", plans/12).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from ontyra.models import RecordId, new_id
from ontyra.models.anchors import Anchor
from ontyra.models.ontology import Resource, ResourceRequest
from ontyra.models.uri import canonical_uri, uri_ancestors
from ontyra.ports import Create, Store
from ontyra.services import errors
from ontyra.services.ontology import OntologyService


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
