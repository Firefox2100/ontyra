"""REST application structure and error translation, without a database."""

import importlib
import pkgutil
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import APIRouter

from ontyra.models.proposals import Finding
from ontyra.services import errors
from ontyra.transports.rest import create_app, routers


class _Memory:
    """A stub memory service whose methods raise whatever the test configures."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def get_memory(self, *_: Any, **__: Any) -> None:
        raise self.exc

    async def get_proposal(self, *_: Any, **__: Any) -> None:
        raise self.exc


class _Actors:
    async def caller(self, **_: Any) -> Any:
        from ontyra.models.core import Actor
        from ontyra.services import Caller

        return Caller(actor=Actor(id=None, kind="agent", name="t"))  # type: ignore[arg-type]

    async def ensure_owner(self, *_: Any) -> Any:
        return None


class _Auth:
    required = False

    async def caller(self, principal: Any, **_: Any) -> Any:
        return await _Actors().caller()


def _client(exc: Exception) -> httpx.AsyncClient:
    services = SimpleNamespace(actors=_Actors(), auth=_Auth(), memory=_Memory(exc))
    app = create_app(services)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_every_module_in_routers_defines_one_router_and_is_mounted() -> None:
    mounted = {id(r) for r in [*routers.VERSIONED, *routers.UNVERSIONED]}
    names = [m.name for m in pkgutil.iter_modules(routers.__path__)]
    assert names, "no routers found"
    for name in names:
        module = importlib.import_module(f"{routers.__name__}.{name}")
        found = [v for v in vars(module).values() if isinstance(v, APIRouter)]
        assert len(found) == 1, f"{name}.py must define exactly one APIRouter"
        assert id(module.router) in mounted, f"{name}.py router is not mounted"


def test_openapi_schema_builds_and_lists_the_public_surface() -> None:
    app = create_app(SimpleNamespace())  # type: ignore[arg-type]
    paths = app.openapi()["paths"]
    for path in [
        "/health",
        "/v1/episodes",
        "/v1/proposals",
        "/v1/proposals/{proposal_id}",
        "/v1/proposals/{proposal_id}/approve",
        "/v1/proposals/{proposal_id}/reject",
        "/v1/memories",
        "/v1/memories/{memory_id}",
        "/v1/memories/{memory_id}/history",
    ]:
        assert path in paths, path


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (errors.NotFoundError("gone"), 404),
        (errors.ConflictError("retry"), 409),
        (errors.ProposalStateError("not pending"), 409),
    ],
)
async def test_service_errors_become_http_errors(exc: Exception, status: int) -> None:
    async with _client(exc) as client:
        r = await client.get("/v1/memories/abc")
    assert r.status_code == status and r.json()["detail"] == str(exc)


async def test_invalid_proposal_error_carries_the_findings() -> None:
    finding = Finding(severity="error", code="invalid_field", message="strength: bad", op=0)
    async with _client(errors.InvalidProposalError([finding])) as client:
        r = await client.get("/v1/proposals/abc")
    assert r.status_code == 422
    assert r.json()["findings"][0]["code"] == "invalid_field"
