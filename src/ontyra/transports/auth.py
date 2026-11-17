"""Authentication at the edge: one ASGI middleware for every route (REST, MCP, console), and
the translation from the authenticated principal to a service `Caller`.

A request is authenticated with `Authorization: Bearer <token>`. `/health`, the API docs and the
console's own sign-in (cookie sessions, see `transports/web`) are open. Without a required
credential (single-user mode, no token) everything is open, as before.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from typing import Any

from ontyra.services import AuthService, Caller, Principal, errors

SCOPE_KEY = "ontyra.principal"
OPEN_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
CONSOLE_PREFIX = "/ui"

Scope = MutableMapping[str, Any]


def bearer_token(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "bearer" and token.strip() else None
    return None


class AuthMiddleware:
    """Pure ASGI (no response buffering, safe for streaming MCP)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        services = getattr(scope["app"].state, "services", None)
        auth: AuthService | None = getattr(services, "auth", None)
        console = path == CONSOLE_PREFIX or path.startswith(CONSOLE_PREFIX + "/")
        if auth is None or not auth.required or path in OPEN_PATHS or console:
            await self.app(scope, receive, send)
            return
        principal = await auth.authenticate(bearer_token(scope))
        if principal is None:
            await self._deny(scope, send)
            return
        scope[SCOPE_KEY] = principal
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope: Scope, send: Any) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": "authentication required"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


async def resolve_caller(
    auth: AuthService, scope: Scope, *, label: str, kind: str, harness: str | None
) -> Caller:
    """The service caller for this request. Fails closed: if a credential is required and the
    middleware did not attach one, nobody is let in as the global owner."""
    principal = scope.get(SCOPE_KEY)
    if principal is None:
        if auth.required:
            raise errors.AuthenticationError("authentication required")
        principal = Principal()
    return await auth.caller(principal, label=label, kind=kind, harness=harness)
