from __future__ import annotations

from fastapi import APIRouter, status

from ontyra.models.ontology import Resource, ResourceRequest
from ontyra.transports.rest.deps import AnchorServiceDep

router = APIRouter(prefix="/resources", tags=["anchors"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Register a resource",
    description="Adds explicit containment (`parents`), equivalence (`same_as`) and concept "
    "membership (`instance_of`) to a URI. Paths below a registered resource need no "
    "registration: containment by path segment is automatic.",
)
async def register(request: ResourceRequest, service: AnchorServiceDep) -> Resource:
    return await service.register(request)


@router.get("", summary="List registered resources")
async def list_resources(service: AnchorServiceDep) -> list[Resource]:
    return await service.list()
