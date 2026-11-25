"""The daemon, over REST. Nothing else in this package knows its routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ontyra_ingest.config import Settings


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


@dataclass
class Outcome:
    """What the daemon did with one proposal."""

    status: str  # committed | held | rejected | failed
    proposal: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    detail: str = ""


class Daemon:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        headers = {
            "X-Ontyra-Actor": settings.actor,
            "X-Ontyra-Actor-Kind": "agent",
            "X-Ontyra-Harness": "ontyra-ingest",
        }
        if settings.token:
            headers["Authorization"] = f"Bearer {settings.token.get_secret_value()}"
        self._space = settings.space
        self._client = client or httpx.AsyncClient(
            base_url=settings.url.rstrip("/"), headers=headers, timeout=60.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"cannot reach the daemon: {exc}") from exc

    async def health(self) -> None:
        response = await self._call("GET", "/health")
        if response.status_code != 200:
            raise ApiError(response.status_code, "the daemon is not healthy")

    async def find_concept(self, term: str) -> str | None:
        response = await self._call("GET", "/v1/concepts", params={"term": term})
        if response.status_code == 422:  # ambiguous term: not a safe subject
            return None
        _ok(response)
        found = response.json()
        return str(found[0]["id"]) if len(found) == 1 else None

    async def define_concept(self, label: str, is_a: list[str], definition: str | None) -> str:
        body: dict[str, Any] = {"label": label, "is_a": is_a, "value_type": "bool"}
        if definition:
            body["definition"] = definition
        response = await self._call("POST", "/v1/concepts", json=body)
        _ok(response)
        return str(response.json()["id"])

    async def submit(self, ops: list[dict[str, Any]], key: str, note: str) -> Outcome:
        body = {"ops": ops, "space": self._space, "idempotency_key": key, "note": note}
        response = await self._call("POST", "/v1/proposals", json=body)
        if response.status_code not in (201, 202, 422):
            return Outcome("failed", detail=_detail(response))
        data = response.json()
        status = {201: "committed", 202: "held", 422: "rejected"}[response.status_code]
        return Outcome(status, data.get("proposal"), data.get("findings") or [])

    async def check_source(self, uri: str, state: str, content_hash: str | None) -> int:
        """Report what was found at a source; returns the number of review tasks opened."""
        body: dict[str, Any] = {"uri": uri, "state": state}
        if content_hash:
            body["content_hash"] = content_hash
        response = await self._call("POST", "/v1/sources/check", json=body)
        _ok(response)
        return int(response.json().get("tasks", 0))


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text[:300]


def _ok(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise ApiError(response.status_code, _detail(response))
