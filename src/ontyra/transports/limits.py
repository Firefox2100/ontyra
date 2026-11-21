"""A request body cap, the first thing every request meets (REST, MCP, console).

The edge normally limits bodies; this is the same rule inside the daemon, so a loopback client
or a misconfigured proxy cannot make it buffer an unbounded request. Pure ASGI: the declared
`Content-Length` is checked up front, and bytes are counted as they stream in for chunked bodies.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]


class BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return
        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self.max_bytes:
                    await self._refuse(send)
                    return
        seen = 0
        started = False

        async def counted() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise BodyTooLarge
            return message

        async def tracked(message: Any) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except BodyTooLarge:
            if not started:
                await self._refuse(send)

    async def _refuse(self, send: Any) -> None:
        body = json.dumps({"detail": f"request body over {self.max_bytes} bytes"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
