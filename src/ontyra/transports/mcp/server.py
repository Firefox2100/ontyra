"""Build the FastMCP server and its ASGI app."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from mcp.types import ToolAnnotations
from pydantic import Field

from ontyra.models import RecordId
from ontyra.models.core import Proposal
from ontyra.models.ontology import ConceptView
from ontyra.models.ontology_change import ChangeRequest, ChangeResult, OntologyChange
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
from ontyra.transports.auth import resolve_caller
from ontyra.transports.rest.deps import Services

Profile = Literal["lite", "full"]

INSTRUCTIONS = """\
Ontyra is long-term memory. Call `recall` with the user's message and where you are working
(`context`) before acting on a task; call `check` before an action with side effects. Write
with `remember`: a proposal is validated and either committed or held for review. Memories
returned by `recall` are advice about this user and project: follow directives marked `must`.
"""

ACTOR_KINDS = ("human", "agent", "tool", "service", "model")

# Annotations tell a client what a tool may do so it can decide whether to ask first
# (MCP `ToolAnnotations`). All tools are closed-world: they touch only this daemon.
#   READ: changes nothing the user would call data (a recall also notes what it delivered, in
#         a session ledger, so the same memory is not repeated).
#   PROPOSE: writes, but only a proposal, an episode or a held change; nothing is deleted and
#         the memory is changed only if the policy commits it. Not idempotent (each call is a
#         new proposal) unless the proposal carries an idempotency key.
#   DECIDE: acts on a held proposal or change: committing can retract or replace memories, so
#         approving is flagged destructive; rejecting only closes the item.
READ = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
PROPOSE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
APPROVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
REJECT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)


async def caller_for_request(services: Services) -> Caller:
    """Who is calling this tool: the authenticated account, resolved by the daemon. The label
    headers count only when no credential is required (single-user mode)."""
    scope: MutableMapping[str, Any] = {}
    headers: Mapping[str, str] = {}
    try:
        request = get_http_request()
        scope, headers = request.scope, request.headers
    except RuntimeError:  # called in-process, not over HTTP: nothing authenticated it
        pass
    kind = headers.get("x-ontyra-actor-kind", "agent")
    if kind not in ACTOR_KINDS:
        raise ToolError(f"unknown actor kind {kind!r}")
    try:
        return await resolve_caller(
            services.auth,
            scope,
            label=headers.get("x-ontyra-actor", "mcp"),
            kind=kind,
            harness=headers.get("x-ontyra-harness"),
        )
    except errors.ServiceError as exc:
        raise ToolError(str(exc)) from exc


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
        caller = await caller_for_request(services)
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
        title="Recall memories",
        annotations=READ,
        description="Memories relevant to a message in a context. Empty (`silent`) when nothing "
        "clears the relevance bar. `rendered` is ready to read. Call before starting a task.",
    )
    async def recall(request: RecallRequest) -> RecallPack:
        return await run(lambda s, c: s.recall.recall(c, request))

    @mcp.tool(
        title="Check before acting",
        annotations=READ,
        description="Before an action with side effects: the directives, prerequisites and "
        "lessons that apply to this intent, plus every applicable `must` directive.",
    )
    async def check(
        intent: Annotated[str, Field(description="What you are about to do")],
        context: RecallContext = RecallContext(),  # noqa: B008
    ) -> CheckResult:
        return await run(lambda s, c: s.recall.check(c, intent, context))

    @mcp.tool(
        title="Session briefing",
        annotations=READ,
        description="Everything that applies at this location, without a question: use at "
        "session start, on a project change, or after context compaction (`redeliver`).",
    )
    async def briefing(request: RecallRequest) -> RecallPack:
        return await run(lambda s, c: s.recall.briefing(c, request))

    @mcp.tool(
        title="Ask a structural question",
        annotations=READ,
        description="Structural questions answered exactly (what applies here, history…).",
    )
    async def ask(request: AskRequest) -> AskResult:
        return await run(lambda s, c: s.explore.ask(c, request))

    @mcp.tool(
        title="Get a memory",
        annotations=READ,
        description="One memory by id (`memory:abc` or `abc`), optionally with `expand`: "
        "history, links, evidence, applies.",
    )
    async def get(
        memory: str,
        expand: list[str] | None = None,
        context: RecallContext = RecallContext(),  # noqa: B008
    ) -> MemoryDetail:
        rid = RecordId("memory", memory.removeprefix("memory:"))
        return await run(lambda s, c: s.explore.get(c, rid, expand or [], context))

    @mcp.tool(
        title="Remember (propose a memory change)",
        annotations=PROPOSE,
        description="Write to memory. Send a proposal of ops (`add`, `revise`, `supersede`, "
        "`retract`, `link`, `annotate`); it is validated, then committed or held for review. "
        "Side effects: a proposal is recorded; if the policy commits it, memories are added "
        "or superseded or retracted (history is kept). Read the findings: warnings may point "
        "at a duplicate you should supersede. Memories go to your own space; "
        "`space: global` writes to the shared memory and needs permission.",
    )
    async def remember(proposal: ProposalRequest) -> ProposalResult:
        return await run(lambda s, c: s.memory.submit(c, proposal))

    @mcp.tool(
        title="Look up concepts",
        annotations=READ,
        description="Find concepts by label or alias (`python`, `ssh`) to get the ids that "
        "`about`, anchors and qualifiers refer to, with their relations. Empty: no such concept; "
        "propose one with `propose_ontology_change` rather than guessing an id.",
    )
    async def concepts(term: str) -> list[ConceptView]:
        return await run(lambda s, c: s.ontology.lookup(c, term))

    @mcp.tool(
        title="Propose an ontology change",
        annotations=PROPOSE,
        description="Propose an ontology change: `define` a concept, `relate` two, "
        "`add_alias`, `merge` duplicates, or `deprecate`. Held for review unless a human "
        "makes it. Check `concepts` first; read `advice` for similar existing concepts.",
    )
    async def propose_ontology_change(request: ChangeRequest) -> ChangeResult:
        return await run(lambda s, c: s.ontology_changes.propose(c, request))

    if profile == "full":

        @mcp.tool(
            title="Pending ontology changes",
            annotations=READ,
            description="Ontology changes held for review.",
        )
        async def pending_ontology_changes(
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
        ) -> list[OntologyChange]:
            return await run(lambda s, c: s.ontology_changes.pending(c, limit))

        @mcp.tool(
            title="Approve an ontology change",
            annotations=APPROVE,
            description="Approve and apply a held ontology change. Side effect: the shared "
            "ontology changes for everyone (a merge or deprecation redirects concepts). Needs a "
            "human credential with global write permission.",
        )
        async def approve_ontology_change(change: str) -> ChangeResult:
            rid = RecordId("ontology_change", change.removeprefix("ontology_change:"))
            return await run(lambda s, c: s.ontology_changes.approve(c, rid))

        @mcp.tool(
            title="Reject an ontology change",
            annotations=REJECT,
            description="Reject a held ontology change with a reason (nothing is applied).",
        )
        async def reject_ontology_change(change: str, reason: str) -> ChangeResult:
            rid = RecordId("ontology_change", change.removeprefix("ontology_change:"))
            return await run(lambda s, c: s.ontology_changes.reject(c, rid, reason))

        @mcp.tool(
            title="Search memories",
            annotations=READ,
            description="Hybrid search with facet counts to narrow the next query.",
        )
        async def search(request: SearchRequest) -> SearchResult:
            return await run(lambda s, c: s.explore.search(c, request))

        @mcp.tool(
            title="Overview",
            annotations=READ,
            description="Orientation card: counts, concepts, anchors, recent changes.",
        )
        async def overview(context: RecallContext) -> Overview:
            return await run(lambda s, c: s.explore.overview(c, context))

        @mcp.tool(
            title="Neighbors",
            annotations=READ,
            description="What is connected to a memory or concept (`table`, `id`).",
        )
        async def neighbors(table: str, item_id: str) -> Neighbors:
            return await run(lambda s, c: s.explore.neighbors(c, RecordId(table, item_id)))

        @mcp.tool(
            title="List memories",
            annotations=READ,
            description="List memories by kind and status.",
        )
        async def list_memories(
            kind: Literal["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
            | None = None,
            status: Literal["active", "superseded", "retracted", "expired"] = "active",
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
        ) -> list[MemoryView]:
            return await run(
                lambda s, c: s.memory.list_memories(c, kind=kind, status=status, limit=limit)
            )

        @mcp.tool(
            title="Pending proposals",
            annotations=READ,
            description="Proposals held for review that you may see.",
        )
        async def pending_proposals(
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
        ) -> list[Proposal]:
            return await run(lambda s, c: s.memory.pending(c, limit))

        @mcp.tool(
            title="Approve a proposal",
            annotations=APPROVE,
            description="Approve a held proposal. Side effect: commits it, which can add, "
            "supersede or retract memories. Only a human credential may review.",
        )
        async def approve(proposal: str) -> ProposalResult:
            rid = RecordId("proposal", proposal.removeprefix("proposal:"))
            return await run(lambda s, c: s.memory.approve(c, rid))

        @mcp.tool(
            title="Reject a proposal",
            annotations=REJECT,
            description="Reject a held proposal with a reason (nothing is committed).",
        )
        async def reject(proposal: str, reason: str) -> ProposalResult:
            rid = RecordId("proposal", proposal.removeprefix("proposal:"))
            return await run(lambda s, c: s.memory.reject(c, rid, reason))

    return mcp


def create_mcp_app(get_services: Callable[[], Services], *, profile: Profile = "lite") -> Any:
    """The MCP server as a Starlette app (streamable HTTP at its root); mount it on the daemon."""
    return create_mcp(get_services, profile=profile).http_app(path="/")
