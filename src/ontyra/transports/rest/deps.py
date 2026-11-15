"""FastAPI dependencies: what route handlers receive through `Depends`."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from fastapi import Depends, Header, Request

from ontyra.services import (
    ActorService,
    AnchorService,
    Caller,
    ExplorationService,
    MemoryService,
    OntologyService,
    RecallService,
)


class Services(Protocol):
    """The service objects the REST transport needs (the composition root provides them)."""

    @property
    def actors(self) -> ActorService: ...

    @property
    def memory(self) -> MemoryService: ...

    @property
    def ontology(self) -> OntologyService: ...

    @property
    def anchors(self) -> AnchorService: ...

    @property
    def recall(self) -> RecallService: ...

    @property
    def explore(self) -> ExplorationService: ...


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_memory_service(services: ServicesDep) -> MemoryService:
    return services.memory


MemoryServiceDep = Annotated[MemoryService, Depends(get_memory_service)]


def get_ontology_service(services: ServicesDep) -> OntologyService:
    return services.ontology


OntologyServiceDep = Annotated[OntologyService, Depends(get_ontology_service)]


def get_anchor_service(services: ServicesDep) -> AnchorService:
    return services.anchors


AnchorServiceDep = Annotated[AnchorService, Depends(get_anchor_service)]


def get_recall_service(services: ServicesDep) -> RecallService:
    return services.recall


RecallServiceDep = Annotated[RecallService, Depends(get_recall_service)]


def get_exploration_service(services: ServicesDep) -> ExplorationService:
    return services.explore


ExplorationServiceDep = Annotated[ExplorationService, Depends(get_exploration_service)]


async def get_caller(
    services: ServicesDep,
    x_ontyra_actor: Annotated[
        str, Header(description="Name of the acting agent/person. Registered on first use.")
    ] = "anonymous",
    x_ontyra_actor_kind: Annotated[
        Literal["human", "agent", "tool", "service", "model"],
        Header(description="Kind of actor; trust is configured per kind."),
    ] = "agent",
    x_ontyra_harness: Annotated[
        str | None, Header(description="Harness the actor runs in, e.g. `claude-code`.")
    ] = None,
    x_ontyra_owner: Annotated[
        str | None, Header(description="Owner scope name; omitted means the global owner.")
    ] = None,
) -> Caller:
    """Identify the caller from headers. There is no authentication yet: these headers are
    trusted as sent, so run the daemon on localhost only."""
    owner = await services.actors.ensure_owner("user", x_ontyra_owner) if x_ontyra_owner else None
    return await services.actors.caller(
        kind=x_ontyra_actor_kind, name=x_ontyra_actor, harness=x_ontyra_harness, owner=owner
    )


CallerDep = Annotated[Caller, Depends(get_caller)]
