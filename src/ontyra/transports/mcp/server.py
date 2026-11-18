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
from ontyra.models.consolidation import ScanReport, ScanRequest
from ontyra.models.core import Proposal, TaskKind
from ontyra.models.feedback import FeedbackRequest, FeedbackView
from ontyra.models.ontology import ConceptView
from ontyra.models.ontology_change import ChangeRequest, ChangeResult, OntologyChange
from ontyra.models.proposals import LiteProposalRequest, ProposalRequest, ProposalResult
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
from ontyra.models.tasks import DetectReport, ResolveRequest, TaskResult, TaskView
from ontyra.services import Caller, errors
from ontyra.services.views import MemoryDetail, MemoryView, Neighbors
from ontyra.transports.auth import resolve_caller
from ontyra.transports.rest.deps import Services

Profile = Literal["lite", "full"]

INSTRUCTIONS = """\
Ontyra is long-term memory. Memories may already be in your context (a briefing at session
start, recall on each prompt): use them, and do not recall again for the same thing. With none
in your context, call `recall` with the user's message and where you are working before you
start a task. Injected memory is matched on the user's literal words only: when the message is
short or refers back ("yes, do it", "and the other server", "send it to them", "what about that
one"), assume it missed the point and call `recall` yourself with a standalone `query` that
names the subject. When the message stands on its own, do not recall again. Call `check` before
an action with side effects (delete, push, deploy), not before writing memory. Pleasantries and
small talk need no memory tool.

When the user states something durable (a rule, preference, decision, fact, lesson), write it
with `remember`: one `add` op per statement, no recall needed first. Store only settled
statements: not speculation ("maybe", "someday", "not sure"), not a passing remark or a
mechanical edit. A proposal is committed or held for review: tell the user it is saved only
when the result says `committed`. Memories returned by `recall` are advice about this user and
project: follow directives marked `must`.
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
        "clears the relevance bar. `rendered` is ready to read. Call before starting a task "
        "unless relevant memory is already in your context. When the message depends on earlier "
        'turns, make `query` standalone: name the subject instead of "it" or "that".',
    )
    async def recall(request: RecallRequest) -> RecallPack:
        return await run(lambda s, c: s.recall.recall(c, request))

    @mcp.tool(
        title="Check before acting",
        annotations=READ,
        description="Before an action with side effects (delete, push, deploy, migrate): the "
        "directives, prerequisites and lessons that apply to this intent, plus every applicable "
        "`must` directive. Not needed before writing memory.",
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

    async def remember(proposal: Any) -> ProposalResult:
        return await run(lambda s, c: s.memory.submit(c, proposal))

    # the profile decides which ops the schema offers (annotations are strings here)
    remember.__annotations__["proposal"] = (
        ProposalRequest if profile == "full" else LiteProposalRequest
    )
    mcp.tool(
        title="Remember (propose a memory change)",
        annotations=PROPOSE,
        description="Store, change or retire a memory by sending a proposal of ops. To store "
        "something new use op `add` (a `kind` and its `fields`; the schema lists them). "
        "`revise`, `supersede` and `retract` (and `link`, `annotate` in the full profile) act on "
        "memories that already exist: use ids from recall or an earlier result, never an "
        "invented one. Cite what the "
        'user said as `evidence: [{"quote": ...}]`. The proposal is validated, then committed or '
        "held for review: say it is saved only when `status` is `committed` (`pending` means a "
        "human must approve). Read the findings: a duplicate warning usually means supersede; "
        "an unscoped warning means `revise` that memory, not add it again. Side effects: a "
        "proposal is recorded; if the policy commits it, memories are added or superseded or "
        "retracted (history is kept). Memories go to your own space; `space: global` writes to "
        "the shared memory and needs permission.",
    )(remember)

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
            title="Find duplicate memories",
            annotations=READ,
            description="Read-only scan for memories that say the same thing (clusters, oldest "
            "first) and for restatements with a changed value (updates). Nothing is changed: "
            "resolve a cluster with a `supersede` or `retract` proposal via `remember`.",
        )
        async def find_duplicates(request: ScanRequest) -> ScanReport:
            return await run(lambda s, c: s.consolidation.scan(c, request))

        @mcp.tool(
            title="Give feedback on a memory",
            annotations=PROPOSE,
            description="Say that a recalled memory was `helpful`, `applied`, `irrelevant`, "
            "`missed` (it should have come back), `outdated` or `wrong`. Side effect: the verdict "
            "is stored. `outdated` and `wrong` also queue a maintenance task for a human or agent "
            "to retract, revise or confirm the memory; the memory itself is not changed.",
        )
        async def feedback(request: FeedbackRequest) -> FeedbackView:
            return await run(lambda s, c: s.feedback.record(c, request))

        @mcp.tool(
            title="Open maintenance tasks",
            annotations=READ,
            description="Maintenance work waiting for judgment: duplicate clusters and updates "
            "to confirm, each with a card (what to look at, the choices). Claim one with "
            "`claim_task`.",
        )
        async def pending_tasks(
            kind: list[TaskKind] | None = None,
            limit: Annotated[int, Field(ge=1, le=200)] = 50,
        ) -> list[TaskView]:
            return await run(lambda s, c: s.tasks.list(c, kinds=kind or [], limit=limit))

        @mcp.tool(
            title="Look for maintenance work",
            annotations=PROPOSE,
            description="Scan for duplicates and updates and queue each finding once as a task. "
            "Side effect: task records are created (and tasks whose memories changed are "
            "closed); no memory is changed.",
        )
        async def detect_tasks() -> DetectReport:
            return await run(lambda s, c: s.tasks.detect(c))

        @mcp.tool(
            title="Claim a maintenance task",
            annotations=PROPOSE,
            description="Take a pending task so others do not. Side effect: the task is marked "
            "claimed by you.",
        )
        async def claim_task(task: str) -> TaskView:
            return await run(
                lambda s, c: s.tasks.claim(c, RecordId("task", task.removeprefix("task:")))
            )

        @mcp.tool(
            title="Complete a maintenance task",
            annotations=PROPOSE,
            description="Finish a claimed task. Resolve it first with an ordinary proposal "
            "(`remember`: supersede the duplicates), then complete with decision `merge` or "
            "`supersede` and that proposal's id; `distinct` says the memories differ (not "
            "raised again); `defer` parks it. Side effect: the task is closed.",
        )
        async def complete_task(task: str, result: TaskResult) -> TaskView:
            return await run(
                lambda s, c: s.tasks.complete(
                    c, RecordId("task", task.removeprefix("task:")), result
                )
            )

        @mcp.tool(
            title="Resolve a maintenance task",
            annotations=PROPOSE,
            description="Do what the task's card proposes: `merge` (the oldest memory replaces "
            "the other members of a duplicate cluster) or `supersede` (the newer memory of an "
            "update replaces the older). Side effect: a `supersede` proposal is submitted as "
            "you, so it commits or waits for review under the usual policy (memories are "
            "replaced, history is kept), then the task is completed citing it.",
        )
        async def resolve_task(task: str, request: ResolveRequest) -> TaskView:
            return await run(
                lambda s, c: s.tasks.resolve(
                    c, RecordId("task", task.removeprefix("task:")), request.decision
                )
            )

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
            "supersede or retract memories. Only a human credential may review: call it only "
            "when the user explicitly asks you to approve that proposal, never for your own.",
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
