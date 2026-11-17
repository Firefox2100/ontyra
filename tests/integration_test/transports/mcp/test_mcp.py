"""The MCP tools over the real services (in-memory MCP client, no sockets)."""

from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from ontyra.app import Application, create_rest_app
from ontyra.config import Settings
from ontyra.transports.mcp import create_mcp

PROPOSAL = {
    "proposal": {
        "ops": [
            {
                "op": "add",
                "ref": "a",
                "kind": "fact",
                "fields": {
                    "text": "the staging database is reset every night",
                    "subject": "concept:ontyra",
                    "predicate": "is",
                    "object": "reset",
                },
            }
        ]
    }
}


def server(app: Application, profile: str = "lite") -> Client:  # type: ignore[type-arg]
    return Client(create_mcp(lambda: app, profile=profile))  # type: ignore[arg-type]


async def test_lite_profile_lists_a_handful_of_tools(app: Application) -> None:
    async with server(app) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {
        "recall",
        "check",
        "briefing",
        "ask",
        "get",
        "remember",
        "concepts",
        "propose_ontology_change",
    }


async def test_full_profile_adds_browsing_and_review(app: Application) -> None:
    async with server(app, "full") as client:
        names = {t.name for t in await client.list_tools()}
    assert {"search", "overview", "neighbors", "list_memories", "approve", "reject"} <= names
    assert {"recall", "remember"} <= names


async def test_remember_then_recall_round_trip(app: Application) -> None:
    async with server(app) as client:
        written = await client.call_tool("remember", PROPOSAL)
        assert written.data.status == "committed"
        pack = await client.call_tool(
            "recall", {"request": {"query": "when is the staging database reset"}}
        )
    assert "reset every night" in pack.data.rendered


async def test_a_service_error_becomes_a_tool_error(app: Application) -> None:
    async with server(app) as client:
        with pytest.raises(ToolError, match=r"not found|no such|memory"):
            await client.call_tool("get", {"memory": "memory:missing"})


async def test_a_rejected_proposal_returns_its_findings(app: Application) -> None:
    bad = {"proposal": {"ops": [{"op": "retract", "memory": "memory:nothing", "reason": "x"}]}}
    async with server(app) as client:
        result = await client.call_tool("remember", bad)
    assert result.data.status == "rejected"
    assert any(f.severity == "error" for f in result.data.findings)


async def test_the_daemon_serves_mcp_when_enabled(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = f"surrealkv://{tmp_path}/mcp"
    settings.db.namespace = settings.db.database = "mcp"
    settings.policy.default = "auto_commit"
    settings.mcp.enabled = True
    app = create_rest_app(settings)

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            **kwargs,  # type: ignore[arg-type]
        )

    async with app.router.lifespan_context(app):
        transport = StreamableHttpTransport(
            "http://test/mcp/",
            headers={"x-ontyra-actor": "claude", "x-ontyra-harness": "claude-code"},
            httpx_client_factory=factory,  # type: ignore[arg-type]
        )
        async with Client(transport) as client:
            assert (await client.call_tool("remember", PROPOSAL)).data.status == "committed"
        # the REST side sees the same store
        async with factory(base_url="http://test") as rest:
            memories = (await rest.get("/v1/memories")).json()
    assert len(memories) == 1
