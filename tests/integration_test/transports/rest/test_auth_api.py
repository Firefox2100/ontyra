"""Authentication over HTTP: the single-user token and multi-user accounts."""

import functools
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from ontyra.app import create_rest_app
from ontyra.config import Settings

TOKEN = "single-user-token-0123456789"
ADMIN = "admin-token-for-tests-0123456789"
PROPOSAL: dict[str, Any] = {
    "ops": [
        {
            "op": "add",
            "kind": "directive",
            "fields": {"text": "x", "strength": "must", "rule": "x", "scope": "universal"},
        }
    ]
}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def daemon(tmp_path: Path, **auth: Any) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    settings = Settings(_env_file=None, auth=auth)  # type: ignore[call-arg, arg-type]
    settings.db.url = f"surrealkv://{tmp_path}/auth"
    settings.db.namespace = settings.db.database = "auth"
    settings.policy.default = "auto_commit"
    settings.mcp.enabled = True
    settings.ui.enabled = True
    app = create_rest_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield app, client


Pair = tuple[Any, httpx.AsyncClient]


def running(**auth: Any) -> Callable[[Callable[[Pair], Awaitable[None]]], Callable[..., Any]]:
    """Run the test body against a started daemon. (The MCP app's lifespan holds an anyio task
    group, which must start and stop in the same task: so not in a pytest fixture.)"""

    def decorate(test: Callable[[Pair], Awaitable[None]]) -> Callable[..., Any]:
        @functools.wraps(test)
        async def wrapper(tmp_path: Path) -> None:
            async with daemon(tmp_path, **auth) as pair:
                await test(pair)

        wrapper.__signature__ = inspect.signature(lambda tmp_path: None)  # type: ignore[attr-defined]
        return wrapper

    return decorate


single = running(token=TOKEN)
multi = running(multi_user=True, admin_token=ADMIN)


@single
async def test_a_single_user_token_is_required_when_configured(pair: Pair) -> None:
    _, c = pair
    assert (await c.get("/health")).status_code == 200  # probes stay open
    assert (await c.get("/openapi.json")).status_code == 200
    denied = await c.get("/v1/memories")
    assert denied.status_code == 401 and denied.headers["www-authenticate"] == "Bearer"
    assert (
        await c.get("/v1/memories", headers=bearer("wrong-wrong-wrong-1234"))
    ).status_code == 401
    assert (await c.get("/v1/memories", headers=bearer(TOKEN))).status_code == 200
    posted = await c.post("/v1/proposals", json=PROPOSAL, headers=bearer(TOKEN))
    assert posted.status_code == 201
    assert (await c.post("/v1/proposals", json=PROPOSAL)).status_code == 401


@single
async def test_the_single_user_token_guards_mcp_too(pair: Pair) -> None:
    app, _ = pair

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)  # type: ignore[arg-type]

    anonymous = StreamableHttpTransport("http://test/mcp/", httpx_client_factory=factory)  # type: ignore[arg-type]
    with pytest.raises(Exception):  # noqa: B017 - the handshake itself is refused
        async with Client(anonymous) as client:
            await client.list_tools()
    ok = StreamableHttpTransport(
        "http://test/mcp/",
        headers=bearer(TOKEN),
        httpx_client_factory=factory,  # type: ignore[arg-type]
    )
    async with Client(ok) as client:
        assert "remember" in {t.name for t in await client.list_tools()}


@single
async def test_accounts_do_not_exist_in_single_user_mode(pair: Pair) -> None:
    _, c = pair
    human = {**bearer(TOKEN), "X-Ontyra-Actor-Kind": "human"}
    assert (await c.get("/v1/admin/users", headers=human)).status_code == 404


@multi
async def test_multi_user_needs_a_credential_for_everything(pair: Pair) -> None:
    _, c = pair
    assert (await c.get("/health")).status_code == 200
    assert (await c.get("/v1/memories")).status_code == 401
    assert (await c.get("/v1/memories", headers=bearer("ont_unknown"))).status_code == 401
    assert (await c.get("/v1/memories", headers=bearer(ADMIN))).status_code == 200


@multi
async def test_the_admin_issues_accounts_and_credentials_and_isolation_follows(pair: Pair) -> None:
    _, c = pair
    admin = bearer(ADMIN)
    assert (
        await c.post("/v1/admin/users", json={"name": "alice"}, headers=admin)
    ).status_code == 201
    assert (await c.post("/v1/admin/users", json={"name": "bob"}, headers=admin)).status_code == 201
    issued: dict[str, str] = {}
    for user, cred, kind in [
        ("alice", "me", "human"),
        ("alice", "codex", "agent"),
        ("bob", "me", "human"),
    ]:
        r = await c.post(
            f"/v1/admin/users/{user}/credentials", json={"name": cred, "kind": kind}, headers=admin
        )
        assert r.status_code == 201
        issued[f"{user}.{cred}"] = r.json()["token"]
    listing = (await c.get("/v1/admin/credentials", headers=admin)).text
    assert not any(t in listing for t in issued.values())  # tokens are shown once

    # alice's human credential commits at once? the test policy is auto_commit for everyone
    alice, bob = bearer(issued["alice.me"]), bearer(issued["bob.me"])
    assert (await c.post("/v1/proposals", json=PROPOSAL, headers=alice)).status_code == 201
    assert len((await c.get("/v1/memories", headers=alice)).json()) == 1
    assert (await c.get("/v1/memories", headers=bob)).json() == []
    # an owner or kind header changes nothing: the account decides
    sneaky = {
        **bob,
        "X-Ontyra-Owner": "alice",
        "X-Ontyra-Actor-Kind": "human",
        "X-Ontyra-Actor": "alice",
    }
    assert (await c.get("/v1/memories", headers=sneaky)).json() == []
    # the second credential of alice shares her memory but acts as an agent
    codex = bearer(issued["alice.codex"])
    assert len((await c.get("/v1/memories", headers=codex)).json()) == 1
    # a revoked credential is refused at once
    cid = (await c.get("/v1/admin/credentials", params={"user": "bob"}, headers=admin)).json()[0][
        "id"
    ]
    assert (
        await c.post(f"/v1/admin/credentials/{cid.split(':')[1]}/revoke", headers=admin)
    ).status_code == 200
    assert (await c.get("/v1/memories", headers=bob)).status_code == 401


@multi
async def test_only_an_admin_manages_accounts_and_only_global_writers_change_the_ontology(
    pair: Pair,
) -> None:
    _, c = pair
    admin = bearer(ADMIN)
    await c.post("/v1/admin/users", json={"name": "carol"}, headers=admin)
    token = (
        await c.post(
            "/v1/admin/users/carol/credentials", json={"name": "me", "kind": "human"}, headers=admin
        )
    ).json()["token"]
    carol = bearer(token)
    assert (await c.get("/v1/admin/users", headers=carol)).status_code == 403
    assert (await c.post("/v1/admin/users", json={"name": "x"}, headers=carol)).status_code == 403
    assert (await c.post("/v1/concepts", json={"label": "thing"}, headers=carol)).status_code == 403
    assert (await c.post("/v1/concepts", json={"label": "thing"}, headers=admin)).status_code == 201
    assert (await c.post("/v1/reranker/train", headers=carol)).status_code == 403
    await c.patch("/v1/admin/users/carol", json={"permissions": ["global_write"]}, headers=admin)
    assert (await c.post("/v1/concepts", json={"label": "other"}, headers=carol)).status_code == 201


@multi
async def test_mcp_resolves_the_account_from_the_credential(pair: Pair) -> None:
    app, c = pair
    admin = bearer(ADMIN)
    await c.post("/v1/admin/users", json={"name": "dave"}, headers=admin)
    token = (
        await c.post(
            "/v1/admin/users/dave/credentials",
            json={"name": "claude", "kind": "agent"},
            headers=admin,
        )
    ).json()["token"]

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)  # type: ignore[arg-type]

    transport = StreamableHttpTransport(
        "http://test/mcp/",
        # these label headers must be ignored in multi-user mode
        headers={**bearer(token), "x-ontyra-actor-kind": "human", "x-ontyra-actor": "root"},
        httpx_client_factory=factory,  # type: ignore[arg-type]
    )
    async with Client(transport) as client:
        written = await client.call_tool("remember", {"proposal": PROPOSAL})
        assert written.data.status == "committed"  # the test policy commits everything
        denied = await client.call_tool(
            "remember", {"proposal": {**PROPOSAL, "space": "global"}}, raise_on_error=False
        )
        assert denied.data.status == "rejected"
    mine = (await c.get("/v1/memories", headers=bearer(token))).json()
    assert [m["owner"] for m in mine] == ["owner:dave"]
    with pytest.raises(Exception):  # noqa: B017 - refused at the handshake
        async with Client(
            StreamableHttpTransport("http://test/mcp/", httpx_client_factory=factory)  # type: ignore[arg-type]
        ) as anonymous:
            await anonymous.list_tools()


@multi
async def test_the_console_signs_in_with_a_human_credential_only(pair: Pair) -> None:
    _, c = pair
    admin = bearer(ADMIN)
    await c.post("/v1/admin/users", json={"name": "erin"}, headers=admin)
    agent_token = (
        await c.post(
            "/v1/admin/users/erin/credentials", json={"name": "bot", "kind": "agent"}, headers=admin
        )
    ).json()["token"]
    page = await c.get("/ui/review")
    assert page.status_code == 303 and page.headers["location"] == "/ui/login"
    assert (await c.get("/ui/login")).status_code == 200
    assert (await c.post("/ui/login", data={"token": "nope"})).status_code == 401
    assert (await c.post("/ui/login", data={"token": agent_token})).status_code == 401
    ok = await c.post("/ui/login", data={"token": ADMIN})
    assert ok.status_code == 303 and "ontyra_session" in ok.headers["set-cookie"]
    assert "httponly" in ok.headers["set-cookie"].lower()
    assert (await c.get("/ui/review")).status_code == 200  # the client keeps the cookie
    await c.post("/ui/logout")
    assert (await c.get("/ui/review")).status_code == 303
