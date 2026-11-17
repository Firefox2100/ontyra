"""FastAPI dependencies: what route handlers receive through `Depends`."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from fastapi import Depends, Header, Request

from ontyra.services import (
    ActorService,
    AnchorService,
    AuthService,
    Caller,
    ExplorationService,
    FeedbackService,
    MemoryService,
    OntologyChangeService,
    OntologyService,
    RecallService,
    RerankTrainingService,
    errors,
)
from ontyra.services.context import GLOBAL_OWNER
from ontyra.transports.auth import resolve_caller


class Services(Protocol):
    """The service objects the REST transport needs (the composition root provides them)."""

    @property
    def auth(self) -> AuthService: ...

    @property
    def actors(self) -> ActorService: ...

    @property
    def memory(self) -> MemoryService: ...

    @property
    def ontology(self) -> OntologyService: ...

    @property
    def ontology_changes(self) -> OntologyChangeService: ...

    @property
    def anchors(self) -> AnchorService: ...

    @property
    def recall(self) -> RecallService: ...

    @property
    def explore(self) -> ExplorationService: ...

    @property
    def feedback(self) -> FeedbackService: ...

    @property
    def reranking(self) -> RerankTrainingService: ...


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


def get_ontology_change_service(services: ServicesDep) -> OntologyChangeService:
    return services.ontology_changes


OntologyChangeServiceDep = Annotated[OntologyChangeService, Depends(get_ontology_change_service)]


def get_anchor_service(services: ServicesDep) -> AnchorService:
    return services.anchors


AnchorServiceDep = Annotated[AnchorService, Depends(get_anchor_service)]


def get_recall_service(services: ServicesDep) -> RecallService:
    return services.recall


RecallServiceDep = Annotated[RecallService, Depends(get_recall_service)]


def get_exploration_service(services: ServicesDep) -> ExplorationService:
    return services.explore


ExplorationServiceDep = Annotated[ExplorationService, Depends(get_exploration_service)]


def get_feedback_service(services: ServicesDep) -> FeedbackService:
    return services.feedback


FeedbackServiceDep = Annotated[FeedbackService, Depends(get_feedback_service)]


def get_reranking_service(services: ServicesDep) -> RerankTrainingService:
    return services.reranking


RerankingServiceDep = Annotated[RerankTrainingService, Depends(get_reranking_service)]


async def get_caller(
    request: Request,
    services: ServicesDep,
    x_ontyra_actor: Annotated[
        str,
        Header(
            description="A name for the caller. Only a label: used when no account system "
            "is active (single-user mode); ignored when accounts are on."
        ),
    ] = "anonymous",
    x_ontyra_actor_kind: Annotated[
        Literal["human", "agent", "tool", "service", "model"],
        Header(description="Declared kind. Single-user mode only; accounts set it."),
    ] = "agent",
    x_ontyra_harness: Annotated[
        str | None, Header(description="Harness the actor runs in, e.g. `claude-code`.")
    ] = None,
) -> Caller:
    """The caller. With a required credential (an `Authorization: Bearer` token, checked by the
    middleware) the account decides the owner, kind and permissions; the headers above are then
    ignored. Without one (single-user, no token) they are trusted labels and the owner is global.
    The owner is never a request parameter."""
    return await resolve_caller(
        services.auth,
        request.scope,
        label=x_ontyra_actor,
        kind=x_ontyra_actor_kind,
        harness=x_ontyra_harness,
    )


CallerDep = Annotated[Caller, Depends(get_caller)]


async def require_global_write(caller: CallerDep) -> Caller:
    if not caller.can_write(GLOBAL_OWNER):
        raise errors.PermissionDeniedError("this needs permission to write the global memory")
    return caller


GlobalWriterDep = Annotated[Caller, Depends(require_global_write)]


async def require_admin(caller: CallerDep) -> Caller:
    if caller.actor.kind != "human" or not caller.has("admin"):
        raise errors.PermissionDeniedError("this needs an administrator's human credential")
    return caller


AdminDep = Annotated[Caller, Depends(require_admin)]
