"""The MCP tools over the real services (in-memory MCP client, no sockets)."""

import json
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
        "feedback",
        "consult",
        "correct",
        "forget",
        "check_source",
        "pending_proposals",
        "amend",
        "concepts",
        "propose_ontology_change",
    }


async def test_full_profile_adds_browsing_and_review(app: Application) -> None:
    async with server(app, "full") as client:
        names = {t.name for t in await client.list_tools()}
    assert {
        "search",
        "overview",
        "neighbors",
        "list_memories",
        "approve",
        "reject",
        "withdraw",
        "request_changes",
    } <= names
    assert {"recall", "remember"} <= names


async def test_remember_then_recall_round_trip(app: Application) -> None:
    async with server(app) as client:
        written = await client.call_tool("remember", PROPOSAL)
        assert written.data.status == "committed"
        pack = await client.call_tool(
            "recall", {"request": {"query": "when is the staging database reset"}}
        )
    assert "reset every night" in pack.data.rendered


async def test_the_lite_remember_offers_only_the_ops_an_agent_needs(app: Application) -> None:
    async with server(app) as lite, server(app, "full") as full:
        lite_ops = next(t for t in await lite.list_tools() if t.name == "remember")
        full_ops = next(t for t in await full.list_tools() if t.name == "remember")
    assert '"annotate"' not in json.dumps(lite_ops.input_schema)
    assert '"annotate"' in json.dumps(full_ops.input_schema)
    async with server(app) as client:
        with pytest.raises(ToolError):  # not in the lite schema, so the server refuses it
            await client.call_tool(
                "remember",
                {"proposal": {"ops": [{"op": "annotate", "target": "memory:x", "text": "t"}]}},
            )


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


async def test_every_tool_declares_what_it_may_do(app: Application) -> None:
    """Clients derive "ask first?" from these hints, so none may be missing."""
    async with server(app, "full") as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name, tool in tools.items():
        hints = tool.annotations
        assert hints is not None and tool.title, f"{name} lacks annotations or a title"
        assert hints.open_world_hint is False  # only this daemon is touched
        assert hints.read_only_hint is not None and hints.destructive_hint is not None
    reads = {
        "recall", "check", "briefing", "ask", "get", "concepts", "search", "overview",
        "neighbors", "list_memories", "pending_proposals", "pending_ontology_changes", "find_duplicates", "feedback_outliers", "pending_tasks",
        "preview_ontology_change", "concept_history", "ontology_history", "check_ontology", "consult",
    }  # fmt: skip
    for name in reads:
        assert tools[name].annotations.read_only_hint is True, name  # type: ignore[union-attr]
        assert tools[name].annotations.idempotent_hint is True, name  # type: ignore[union-attr]
    for name in set(tools) - reads:
        assert tools[name].annotations.read_only_hint is False, name  # type: ignore[union-attr]
    # committing can retract or replace memories; a proposal or a rejection destroys nothing
    assert tools["approve"].annotations.destructive_hint is True  # type: ignore[union-attr]
    assert tools["approve_ontology_change"].annotations.destructive_hint is True  # type: ignore[union-attr]
    for name in (
        "remember", "propose_ontology_change", "reject", "reject_ontology_change",
        "detect_tasks", "claim_task", "complete_task", "resolve_task", "feedback", "check_source",
        "amend_ontology_change", "withdraw_ontology_change", "amend", "withdraw", "request_changes",
    ):  # fmt: skip
        assert tools[name].annotations.destructive_hint is False, name  # type: ignore[union-attr]
