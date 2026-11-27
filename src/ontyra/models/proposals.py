"""The vocabulary of proposals: operations, findings and results (transport-visible models)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from ontyra.models.base import EpisodeRef, MemoryRef, Model, RecordId
from ontyra.models.kinds import KindName, Scope, fields_guide
from ontyra.models.policy import Decision
from ontyra.models.sources import SourceRef

ReasonCode = Literal[
    "corrected", "outdated", "refined", "merged", "resolved_contradiction", "source_retracted"
]


class Evidence(Model):
    """What a memory comes from: words that were said, or an external source it cites.

    Words that were said: give a `quote` and the daemon looks it up among your recent episodes (it
    must occur there literally, case and spacing aside); nothing is stored when it does not. Give an
    `episode` id as well to name the exact message; the quote must then occur in that one.

    A source: give `source` (a uri, and ideally a locator and a short literal quote). The daemon
    stores the reference with the memory; it does not read the source or require that it exists.
    """

    quote: str | None = Field(
        default=None, description="Words from the conversation, copied literally (a sentence)."
    )
    episode: EpisodeRef | None = Field(
        default=None, description="Optional: the episode id, when you know it."
    )
    source: SourceRef | None = Field(
        default=None, description="An external source it comes from (instead of a quote)."
    )

    @model_validator(mode="after")
    def _something_to_go_on(self) -> Evidence:
        said = self.episode is not None or bool(self.quote and self.quote.strip())
        if self.source is not None and said:
            raise ValueError("evidence is either words that were said or a source, not both")
        if self.source is None and not said:
            raise ValueError("evidence needs a quote, an episode or a source")
        return self


class AddMemory(Model):
    """Create a NEW memory (item + first revision). This is how anything gets stored: pick the
    `kind` and give its `fields`. Say where it applies (`scope: universal`, or `anchors` /
    `about`), or a directive is flagged as unscoped."""

    op: Literal["add"] = "add"
    ref: str | None = None  # alias other ops in this proposal can point at
    kind: KindName
    fields: dict[str, Any] = Field(
        description="`text` (the whole statement, in one sentence) plus the kind's own fields, "
        "required first, optional in brackets:\n"
        + fields_guide()
        + "\nEvery kind also takes: scope (contextual|universal|topical|reference), anchors, "
        "about (concept ids), qualifiers, expires_at, confidence. Do not repeat `kind` inside "
        "`fields`."
    )
    valid_from: datetime | None = None
    evidence: list[Evidence] = []
    derived_from: list[str | RecordId] = []  # memories (or refs) this was derived from


class ReviseMemory(Model):
    """New revision of an existing memory; `fields` overlay the current revision."""

    op: Literal["revise"] = "revise"
    memory: MemoryRef
    fields: dict[str, Any]
    reason_code: ReasonCode = "refined"
    reason: str | None = None
    evidence: list[Evidence] = []


class SupersedeMemory(Model):
    """`new` (a `ref` from this proposal, or an existing memory id) replaces `target`."""

    op: Literal["supersede"] = "supersede"
    new: str | RecordId
    target: MemoryRef
    reason_code: ReasonCode
    reason: str | None = None


class RetractMemory(Model):
    """Declare a memory wrong/polluted. Dependents derived from it are flagged stale."""

    op: Literal["retract"] = "retract"
    memory: MemoryRef
    reason: str


class LinkMemories(Model):
    """Relate two EXISTING memories (ids from recall, or `ref`s of this proposal). It stores
    nothing new: use `add` for that."""

    op: Literal["link"] = "link"
    relation: Literal["contradicts", "overrides", "refines", "promoted_to"]
    source: str | RecordId  # `ref` or memory id
    target: str | RecordId
    reason: str | None = None


class Annotate(Model):
    """Attach a note to an EXISTING memory (`target` is its id). It stores nothing new: use
    `add` for that."""

    op: Literal["annotate"] = "annotate"
    target: RecordId
    kind: Literal["comment", "tag", "flag"] = "comment"
    text: Annotated[str, Field(max_length=5000)]


Op = Annotated[
    AddMemory | ReviseMemory | SupersedeMemory | RetractMemory | LinkMemories | Annotate,
    Field(discriminator="op"),
]


class ProposalRequest(Model):
    ops: Annotated[list[Op], Field(min_length=1, max_length=100)]
    # Where new memories go: the caller's own memory (default) or the shared global memory,
    # which needs the `global_write` permission. Owners are never named: they come from the
    # authenticated account. With multi-user mode off there is only the global memory.
    space: Literal["own", "global"] = "own"
    idempotency_key: Annotated[str | None, Field(max_length=200)] = None
    note: Annotated[str | None, Field(max_length=2000)] = None
    # `auto`: values that look like credentials are masked before storing, and a warning finding
    # names what was masked. `off`: store them as sent — for a false positive (a test fixture, a
    # documented example), not for a real key.
    redaction: Literal["auto", "off"] = "auto"


class LiteProposalRequest(ProposalRequest):
    """The proposal of the `lite` MCP profile: only the ops an agent needs to write and correct
    memory (`add`, `revise`, `supersede`, `retract`). `link` and `annotate` are left out: a weak
    tool-caller reaches for `annotate` as the easy way to "note" something and stores nothing."""

    ops: Annotated[  # type: ignore[assignment]
        list[
            Annotated[
                AddMemory | ReviseMemory | SupersedeMemory | RetractMemory,
                Field(discriminator="op"),
            ]
        ],
        Field(min_length=1, max_length=100),
    ]


class Finding(Model):
    """One validation observation. `error` blocks the proposal; others are advice."""

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    op: int | None = None  # index into the request's ops
    data: dict[str, Any] = {}


class ProposalResult(Model):
    proposal: RecordId
    status: Literal["pending", "changes_requested", "committed", "rejected", "withdrawn"]
    decision: Decision
    reason: str | None = None
    findings: list[Finding] = []
    created: dict[str, RecordId] = {}  # op `ref` (or "op<N>") -> new memory id
    duplicate_of_idempotency_key: bool = False
    version: int | None = None  # the proposal's version after this operation
    rejected_ops: list[int] = []  # op numbers (of the version reviewed) that were not committed


class CorrectionRequest(Model):
    """Correct one memory in a single call: `fields` overlay its current revision, so send only
    what changes. The result is an ordinary proposal — validated, then committed or held."""

    fields: dict[str, Any] = Field(
        min_length=1, description="Only what changes: `text`, and any of the kind's own fields."
    )
    reason_code: ReasonCode = "corrected"
    reason: Annotated[str | None, Field(max_length=2000)] = None


class ForgetRequest(Model):
    """Retire memories: one by id, or every active memory anchored under a path.

    Nothing is deleted: each target gets a `retract` op, so the reason and the history stay."""

    memory: MemoryRef | None = None
    uri: Annotated[str | None, Field(min_length=3)] = None  # subtree: forget what applies here
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    limit: Annotated[int, Field(ge=1, le=100)] = 50

    @model_validator(mode="after")
    def _one_target(self) -> ForgetRequest:
        if (self.memory is None) == (self.uri is None):
            raise ValueError("give exactly one of `memory` or `uri`")
        return self


class ForgetResult(Model):
    proposals: list[RecordId] = []  # the one proposal that carries every retraction (all or none)
    forgotten: list[RecordId] = []
    status: Literal["pending", "changes_requested", "committed", "rejected", "withdrawn"] = (
        "pending"
    )
    findings: list[Finding] = []


class AmendRequest(Model):
    """Replace the ops of a pending proposal: a new version, validated again."""

    ops: Annotated[list[Op], Field(min_length=1)]
    why: str = Field(description="What you changed and why (kept with the older version)")
    expect_version: int | None = Field(
        default=None, description="The version you based this on; refused when it moved on"
    )


class OverlayRequest(Model):
    """The same fields on every `add` and `revise` of several waiting proposals (a bulk edit)."""

    proposals: list[RecordId]
    scope: Scope | None = None
    expires_at: str | None = None
    anchor: str | None = None  # a subtree anchor added to each
    about: list[str] = []  # concept labels or ids added to each


class WithdrawRequest(Model):
    reason: str


class RequestChanges(Model):
    """Send a proposal back to its submitter: it leaves the approve queue until amended."""

    comment: str
    op_comments: dict[int, str] = {}  # op number (0-based, of the version reviewed) -> comment
    expect_version: int | None = None


class ApproveRequest(Model):
    ops: list[int] | None = Field(
        default=None,
        description="Op numbers to commit (what they need is included); default all. The rest "
        "are rejected with `reason`.",
    )
    reason: str | None = None  # why the others were rejected (required for a partial approval)
    expect_version: int | None = None


class RejectRequest(Model):
    reason: str
    ops: list[int] | None = Field(
        default=None,
        description="Op numbers to reject (what needs them goes too); default the whole proposal. "
        "The rest stay pending as a new version.",
    )
    expect_version: int | None = None
