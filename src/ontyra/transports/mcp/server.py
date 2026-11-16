"""Build the FastMCP server and its ASGI app."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from pydantic import Field

from ontyra.models import RecordId
from ontyra.models.core import Proposal
from ontyra.models.proposals import ProposalRequest, ProposalResult
from ontyra.models.recall import (
    AskRequest,
    AskResult,
    CheckResult,
    Overview,
    RecallContext,
    RecallPack,
    RecallRequest,
    SearchRequest,
    SearchResult,
)
from ontyra.services import Caller, errors
from ontyra.services.views import MemoryDetail, MemoryView, Neighbors
from ontyra.transports.rest.deps import Services

Profile = Literal["lite", "full"]

INSTRUCTIONS = """\
Ontyra is long-term memory. Call `recall` with the user's message and where you are working
(`context`) before acting on a task; call `check` before an action with side effects. Write
with `remember`: a proposal is validated and either committed or held for review. Memories
returned by `recall` are advice about this user and project: follow directives marked `must`.
"""

HEADERS = {"x-ontyra-actor", "x-ontyra-actor-kind", "x-ontyra-harness", "x-ontyra-owner"}
ACTOR_KINDS = ("human", "agent", "tool", "service", "model")


async def caller_from_headers(services: Services, headers: dict[str, str]) -> Caller:
    """Same identification as the REST transport; a missing header takes the default."""
    owner_name = headers.get("x-ontyra-owner")
    owner = await services.actors.ensure_owner("user", owner_name) if owner_name else None
    kind = headers.get("x-ontyra-actor-kind", "agent")
    if kind not in ACTOR_KINDS:
        raise ToolError(f"unknown actor kind {kind!r}")
    return await services.actors.caller(
        kind=kind,
        name=headers.get("x-ontyra-actor", "mcp"),
        harness=headers.get("x-ontyra-harness"),
        owner=owner,
    )


def _message(exc: Exception) -> str:
    if isinstance(exc, errors.InvalidProposalError):
        return f"{exc}; findings: " + "; ".join(f"{f.code}: {f.message}" for f in exc.findings)
    return str(exc)


def create_mcp(
    get_services: Callable[[], Services], *, profile: Profile = "lite", name: str = "ontyra"
) -> FastMCP:
    """The MCP server. `get_services` is called per request (services exist after startup)."""
    mcp = FastMCP(name, instructions=INSTRUCTIONS)

    async def run[T](action: Callable[[Services, Caller], Awaitable[T]]) -> T:
        services = get_services()
        caller = await caller_from_headers(services, get_http_headers(include=HEADERS))
        try:
            return await action(services, caller)
        except (
            errors.NotFoundError,
            errors.ConflictError,
            errors.InvalidRequestError,
            errors.ProposalStateError,
            errors.InvalidProposalError,
        ) as exc:
            raise ToolError(_message(exc)) from exc

    @mcp.tool(
        description="Memories relevant to a message in a context. Empty (`silent`) when nothing "
        "clears the relevance bar. `rendered` is ready to read. Call before starting a task."
    )
    async def recall(request: RecallRequest) -> RecallPack:
        return await run(lambda s, c: s.recall.recall(c, request))

    @mcp.tool(
        description="Before an action with side effects: the directives, prerequisites and "
        "lessons that apply to this intent, plus every applicable `must` directive."
    )
    async def check(
        intent: Annotated[str, Field(description="What you are about to do")],
        context: RecallContext = RecallContext(),  # noqa: B008
    ) -> CheckResult:
        return await run(lambda s, c: s.recall.check(c, intent, context))

    @mcp.tool(
        description="Everything that applies at this location, without a question: use at "
        "session start, on a project change, or after context compaction (`redeliver`)."
    )
    async def briefing(request: RecallRequest) -> RecallPack:
        return await run(lambda s, c: s.recall.briefing(c, request))

    @mcp.tool(description="Structural questions answered exactly (what applies here, history…).")
    async def ask(request: AskRequest) -> AskResult:
        return await run(lambda s, c: s.explore.ask(c, request))

    @mcp.tool(
        description="One memory by id (`memory:abc` or `abc`), optionally with `expand`: "
        "history, links, evidence, applies."
    )
    async def get(
        memory: str,
        expand: list[str] | None = None,
        context: RecallContext = RecallContext(),  # noqa: B008
    ) -> MemoryDetail:
        rid = RecordId("memory", memory.removeprefix("memory:"))
        return await run(lambda s, c: s.explore.get(c, rid, expand or [], context))

    @mcp.tool(
        description="Write to memory. Send a proposal of ops (`add`, `revise`, `supersede`, "
        "`retract`, `link`, `annotate`); it is validated, then committed or held for review. "
        "Read the findings: warnings may point at a duplicate you should supersede."
    )
    async def remember(proposal: ProposalRequest) -> ProposalResult:
        return await run(lambda s, c: s.memory.submit(c, proposal))

    if profile == "full":

        @mcp.tool(description="Hybrid search with facet counts to narrow the next query.")
        async def search(request: SearchRequest) -> SearchResult:
            return await run(lambda s, c: s.explore.search(c, request))

        @mcp.tool(description="Orientation card: counts, concepts, anchors, recent changes.")
        async def overview(context: RecallContext) -> Overview:
            return await run(lambda s, c: s.explore.overview(c, context))

        @mcp.tool(description="What is connected to a memory or concept (`table`, `id`).")
        async def neighbors(table: str, item_id: str) -> Neighbors:
            return await run(lambda s, c: s.explore.neighbors(c, RecordId(table, item_id)))

        @mcp.tool(description="List memories by kind and status.")
        async def list_memories(
            kind: Literal["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
            | None = None,
            status: Literal["active", "superseded", "retracted", "expired"] = "active",
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
        ) -> list[MemoryView]:
            return await run(
                lambda s, c: s.memory.list_memories(c, kind=kind, status=status, limit=limit)
            )

        @mcp.tool(description="Proposals held for review.")
        async def pending_proposals(
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
        ) -> list[Proposal]:
            return await run(lambda s, c: s.memory.pending(limit))

        @mcp.tool(description="Approve a held proposal (only a human reviewer's call).")
        async def approve(proposal: str) -> ProposalResult:
            rid = RecordId("proposal", proposal.removeprefix("proposal:"))
            return await run(lambda s, c: s.memory.approve(c, rid))

        @mcp.tool(description="Reject a held proposal with a reason.")
        async def reject(proposal: str, reason: str) -> ProposalResult:
            rid = RecordId("proposal", proposal.removeprefix("proposal:"))
            return await run(lambda s, c: s.memory.reject(c, rid, reason))

    return mcp


def create_mcp_app(get_services: Callable[[], Services], *, profile: Profile = "lite") -> Any:
    """The MCP server as a Starlette app (streamable HTTP at its root); mount it on the daemon."""
    return create_mcp(get_services, profile=profile).http_app(path="/")
