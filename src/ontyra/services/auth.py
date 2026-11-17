"""Authentication and accounts.

Single-user mode: one optional shared token; the caller is the global owner. Multi-user mode:
accounts (`user`) own their memories and hold credentials (`credential`): one per thing that
acts for them (the person, their Codex, their Claude Code...), each with its own token and actor
kind. Tokens are random, shown once, and stored only as a SHA-256 hash.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ontyra.models import RecordId
from ontyra.models.core import Credential, User
from ontyra.ports import Create, Store, Update
from ontyra.services import errors
from ontyra.services.actors import ActorService
from ontyra.services.context import Caller

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ADMIN = "admin"
BOOTSTRAP = "bootstrap"
TOKEN_PREFIX = "ont_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Who authenticated. In single-user mode both fields are `None`."""

    user: User | None = None
    credential: Credential | None = None


class AuthService:
    def __init__(
        self,
        store: Store,
        actors: ActorService,
        now: Callable[[], datetime],
        *,
        multi_user: bool = False,
        token: str | None = None,
        admin_token: str | None = None,
        single_user_kind: str = "human",
    ) -> None:
        self.store = store
        self.actors = actors
        self.now = now
        self.multi_user = multi_user
        self.token = token
        self.admin_token = admin_token
        self.single_user_kind = single_user_kind

    @property
    def required(self) -> bool:
        """Must requests carry a credential?"""
        return self.multi_user or self.token is not None

    # ---- bootstrap ------------------------------------------------------------

    async def bootstrap(self) -> None:
        """Multi-user mode: make sure the `admin` account and its bootstrap credential exist and
        match the configured token (rotate it by changing the setting)."""
        if not self.multi_user or not self.admin_token:
            return
        user = await self._user(ADMIN)
        if user is None:
            user = await self._create_user(ADMIN, ["admin", "global_write"])
        digest = hash_token(self.admin_token)
        assert user.id is not None
        existing = [
            c
            for c in await self.store.select("credential", {"user": user.id}, limit=50)
            if isinstance(c, Credential) and c.name == BOOTSTRAP
        ]
        now = self.now()
        if existing and existing[0].id is not None:
            if existing[0].token_hash != digest or existing[0].revoked_at is not None:
                await self.store.apply(
                    [
                        Update(
                            existing[0].id,
                            {
                                "token_hash": digest,
                                "token_hint": self.admin_token[-4:],
                                "revoked_at": None,
                            },
                        )
                    ]
                )
            return
        await self._create_credential(user, BOOTSTRAP, "human", None, digest, self.admin_token, now)

    # ---- authenticating ---------------------------------------------------------

    async def authenticate(self, token: str | None) -> Principal | None:
        """The principal for a presented token, or `None`. Never raises on a bad token."""
        if not token:
            return None
        if not self.multi_user:
            ok = self.token is not None and hmac.compare_digest(token, self.token)
            return Principal() if ok else None
        found = await self.store.select("credential", {"token_hash": hash_token(token)}, limit=1)
        if not found or not isinstance(found[0], Credential) or found[0].id is None:
            return None
        cred = found[0]
        if cred.id is None:
            return None
        cred_id = cred.id
        if cred.revoked_at is not None:
            return None
        user = await self.store.get(cred.user)
        if not isinstance(user, User) or user.disabled:
            return None
        now = self.now()
        if cred.last_used_at is None or now - cred.last_used_at > timedelta(minutes=1):
            await self.store.apply([Update(cred_id, {"last_used_at": now})])
        return Principal(user=user, credential=cred)

    async def refresh(self, principal: Principal) -> Principal | None:
        """Revalidate a UI session so revocation, disablement and permission changes are live."""
        if not self.multi_user:
            return principal
        if principal.credential is None or principal.credential.id is None:
            return None
        cred = await self.store.get(principal.credential.id)
        if not isinstance(cred, Credential) or cred.revoked_at is not None:
            return None
        user = await self.store.get(cred.user)
        if not isinstance(user, User) or user.disabled:
            return None
        return Principal(user=user, credential=cred)

    async def caller(
        self, principal: Principal, *, label: str, kind: str, harness: str | None
    ) -> Caller:
        """The caller for a principal. Open mode uses self-declared labels. A configured shared
        token binds every caller to the server-configured kind. Multi-user identity comes from
        the stored credential; request identity headers are ignored."""
        if principal.user is None or principal.credential is None:
            if self.token is not None:
                kind = self.single_user_kind
            return await self.actors.caller(kind=kind, name=label, harness=harness)
        user, cred = principal.user, principal.credential
        assert cred.id is not None
        permissions = frozenset(user.permissions)
        if cred.kind != "human":
            permissions -= {"admin"}
        return await self.actors.caller(
            kind=cred.kind,
            name=f"{user.name}.{cred.name}",
            harness=cred.harness,
            owner=user.owner,
            permissions=permissions,
            actor_id=RecordId("actor", cred.id.id),
        )

    # ---- accounts (admin) ---------------------------------------------------------

    def _multi(self) -> None:
        if not self.multi_user:
            raise errors.NotFoundError("accounts exist only in multi-user mode")

    async def list_users(self) -> list[User]:
        self._multi()
        return [u for u in await self.store.select("user", limit=500) if isinstance(u, User)]

    async def list_credentials(self, user: str | None = None) -> list[Credential]:
        self._multi()
        where = {"user": (await self._require_user(user)).id} if user else None
        rows = await self.store.select("credential", where, limit=1000)
        return [c for c in rows if isinstance(c, Credential)]

    async def create_user(self, name: str, permissions: list[str]) -> User:
        self._multi()
        if name == ADMIN or await self._user(name) is not None:
            raise errors.ConflictError(f"user {name!r} already exists")
        return await self._create_user(name, permissions)

    async def update_user(
        self, name: str, *, permissions: list[str] | None = None, disabled: bool | None = None
    ) -> User:
        self._multi()
        user = await self._require_user(name)
        assert user.id is not None
        patch: dict[str, object] = {}
        if permissions is not None:
            patch["permissions"] = self._permissions(permissions)
        if disabled is not None:
            patch["disabled"] = disabled
        if patch:
            await self.store.apply([Update(user.id, patch)])
        return await self._require_user(name)

    async def issue_credential(
        self, user: str, name: str, kind: str, harness: str | None = None
    ) -> tuple[Credential, str]:
        """Create a credential. The token is returned here and never again."""
        self._multi()
        account = await self._require_user(user)
        self._check_name(name)
        if kind not in ("human", "agent"):
            raise errors.InvalidRequestError("kind must be 'human' or 'agent'")
        assert account.id is not None
        for c in await self.store.select("credential", {"user": account.id}, limit=500):
            if isinstance(c, Credential) and c.name == name and c.revoked_at is None:
                raise errors.ConflictError(f"{user} already has an active credential {name!r}")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        cred = await self._create_credential(
            account, name, kind, harness, hash_token(token), token, self.now()
        )
        return cred, token

    async def revoke_credential(self, credential: RecordId) -> Credential:
        self._multi()
        cred = await self.store.get(credential)
        if not isinstance(cred, Credential) or cred.id is None:
            raise errors.NotFoundError(f"{credential} does not exist")
        await self.store.apply([Update(cred.id, {"revoked_at": self.now()})])
        revoked = await self.store.get(credential)
        assert isinstance(revoked, Credential)
        return revoked

    # ---- internals ------------------------------------------------------------------

    def _check_name(self, name: str) -> None:
        if not NAME.match(name) or name == "global":
            raise errors.InvalidRequestError(
                "names are 1-32 characters: a-z, 0-9, '-', '_', starting with a letter or digit"
            )

    def _permissions(self, permissions: list[str]) -> list[str]:
        bad = sorted(set(permissions) - {"global_write", "admin"})
        if bad:
            raise errors.InvalidRequestError(f"unknown permission {bad}")
        return sorted(set(permissions))

    async def _user(self, name: str) -> User | None:
        found = await self.store.select("user", {"name": name}, limit=1)
        return found[0] if found and isinstance(found[0], User) else None

    async def _require_user(self, name: str | None) -> User:
        user = await self._user(name or "")
        if user is None:
            raise errors.NotFoundError(f"user {name!r} does not exist")
        return user

    async def _create_user(self, name: str, permissions: list[str]) -> User:
        if name != ADMIN:
            self._check_name(name)
        owner = await self.actors.ensure_owner("user", name)
        user = User(
            name=name,
            owner=owner,
            permissions=self._permissions(permissions),  # type: ignore[arg-type]
            created_at=self.now(),
        )
        rid = RecordId("user", name)
        await self.store.apply([Create.of(rid, user)])
        return user.model_copy(update={"id": rid})

    async def _create_credential(
        self,
        user: User,
        name: str,
        kind: str,
        harness: str | None,
        digest: str,
        token: str,
        now: datetime,
    ) -> Credential:
        assert user.id is not None
        cred = Credential(
            user=user.id,
            name=name,
            kind=kind,  # type: ignore[arg-type]
            harness=harness,
            token_hash=digest,
            token_hint=token[-4:],
            created_at=now,
        )
        rid = RecordId("credential", secrets.token_hex(10))
        await self.store.apply([Create.of(rid, cred)])
        return cred.model_copy(update={"id": rid})
