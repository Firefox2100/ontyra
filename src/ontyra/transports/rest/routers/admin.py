from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from ontyra.models import RecordId
from ontyra.models.auth import (
    CredentialCreate,
    CredentialView,
    IssuedCredential,
    UserCreate,
    UserUpdate,
    UserView,
)
from ontyra.transports.rest.deps import AdminDep, ServicesDep

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", summary="List accounts (multi-user mode, administrators)")
async def list_users(_: AdminDep, services: ServicesDep) -> list[UserView]:
    return [UserView.of(u) for u in await services.auth.list_users()]


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Create an account")
async def create_user(body: UserCreate, _: AdminDep, services: ServicesDep) -> UserView:
    return UserView.of(await services.auth.create_user(body.name, list(body.permissions)))


@router.patch("/users/{name}", summary="Change an account's permissions or disable it")
async def update_user(name: str, body: UserUpdate, _: AdminDep, services: ServicesDep) -> UserView:
    user = await services.auth.update_user(
        name,
        permissions=list(body.permissions) if body.permissions is not None else None,
        disabled=body.disabled,
    )
    return UserView.of(user)


@router.get("/credentials", summary="List credentials (never the tokens)")
async def list_credentials(
    _: AdminDep,
    services: ServicesDep,
    user: Annotated[str | None, Query()] = None,
) -> list[CredentialView]:
    return [CredentialView.of(c) for c in await services.auth.list_credentials(user)]


@router.post(
    "/users/{name}/credentials",
    status_code=status.HTTP_201_CREATED,
    summary="Issue a credential to an account",
    description="One account can hold several: one for the person, one per agent harness. "
    "The token is in this response only; it is stored as a hash and cannot be shown again.",
)
async def issue_credential(
    name: str, body: CredentialCreate, _: AdminDep, services: ServicesDep
) -> IssuedCredential:
    cred, token = await services.auth.issue_credential(name, body.name, body.kind, body.harness)
    return IssuedCredential(credential=CredentialView.of(cred), token=token)


@router.post("/credentials/{credential_id}/revoke", summary="Revoke a credential")
async def revoke_credential(
    credential_id: str, _: AdminDep, services: ServicesDep
) -> CredentialView:
    cred = await services.auth.revoke_credential(RecordId("credential", credential_id))
    return CredentialView.of(cred)
