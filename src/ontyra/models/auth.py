"""Transport-visible models for accounts and credentials (never the token hash)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from ontyra.models.base import Model, RecordId
from ontyra.models.core import Credential, Permission, User


class UserCreate(Model):
    name: str
    permissions: list[Permission] = []


class UserUpdate(Model):
    permissions: list[Permission] | None = None
    disabled: bool | None = None


class UserView(Model):
    id: RecordId
    name: str
    owner: RecordId
    permissions: list[Permission]
    disabled: bool

    @classmethod
    def of(cls, user: User) -> UserView:
        assert user.id is not None
        return cls(
            id=user.id,
            name=user.name,
            owner=user.owner,
            permissions=user.permissions,
            disabled=user.disabled,
        )


class CredentialCreate(Model):
    name: str
    kind: Literal["human", "agent"] = "agent"
    harness: str | None = None


class CredentialView(Model):
    id: RecordId
    user: RecordId
    name: str
    kind: Literal["human", "agent"]
    harness: str | None
    token_hint: str  # the last characters of the token, to tell credentials apart
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None

    @classmethod
    def of(cls, cred: Credential) -> CredentialView:
        assert cred.id is not None
        return cls(
            id=cred.id,
            user=cred.user,
            name=cred.name,
            kind=cred.kind,
            harness=cred.harness,
            token_hint=cred.token_hint,
            created_at=cred.created_at,
            revoked_at=cred.revoked_at,
            last_used_at=cred.last_used_at,
        )


class IssuedCredential(Model):
    """The only time the token is ever shown."""

    credential: CredentialView
    token: str
