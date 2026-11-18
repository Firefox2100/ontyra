"""Case data of the model-driven suites (JSON files under `data/cases/<area>/`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ontyra.models.recall import RecallContext

DATA = Path(__file__).resolve().parents[1] / "data"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCase(Strict):
    """A request and the tool call it should lead to (selection and arguments)."""

    id: str
    tags: list[str]
    why: str
    say: str
    cwd: str | None = None  # the working directory the agent is told about
    expect: str | None = None  # the tool that must be called first; None: none (or an `accept`)
    accept: list[str] = []  # other first calls that are fine
    forbid: list[str] = []  # tools that must not be called at all
    contains: list[str] = []  # every one must appear (case-insensitive) in the call's arguments
    contains_any: list[str] = []  # at least one must appear
    split: Literal["tune", "holdout"] = "tune"


class WriteCase(Strict):
    """A conversation in which something durable may or may not be said; the model has the
    `remember` and `concepts` tools, the real server answers, and the result is judged."""

    id: str
    tags: list[str]
    why: str
    conversation: list[tuple[Literal["user", "assistant"], str]]
    cwd: str | None = None
    should_write: bool
    kinds: list[str] = []  # acceptable kinds of the memory added
    count: int = 1  # memories to add (one per statement)
    contains: list[str] = []  # in the proposal's arguments, case-insensitive
    split: Literal["tune", "holdout"] = "tune"


class PresentationCase(Strict):
    """A real recall pack is rendered into the model's context, then the model does a task:
    the answer is checked with patterns. Played with the pack rendered `compact`, `rich`, and
    not at all (the baseline: does the memory change the answer?)."""

    id: str
    tags: list[str]
    why: str
    world: str = "commons"
    mode: Literal["recall", "briefing", "check"] = "recall"
    query: str
    context: RecallContext = RecallContext()
    include: list[str]  # world keys the pack must contain (checked offline, not by the model)
    task: str  # what the user asks (usually the same as `query`)
    must: list[str] = []  # regular expressions that must match the answer
    avoid: list[str] = []  # regular expressions that must not
    split: Literal["tune", "holdout"] = "tune"


class CardMember(Strict):
    key: str | None = None  # a memory of the world: its text and kind
    text: str | None = None
    kind: str = "fact"


class DecisionCase(Strict):
    """A maintenance task card (built by the daemon's own card functions); the model decides."""

    id: str
    tags: list[str]
    why: str
    finding: Literal["duplicates", "update", "feedback", "expiry"]
    members: list[CardMember]  # duplicates/update: two or more (oldest first); else one
    report: str = ""  # feedback: what the reporter said
    expired: bool = False  # expiry: already past
    accept: list[str]  # decisions that are right
    note_needed: bool = False  # distinct/keep must say why
    split: Literal["tune", "holdout"] = "tune"


def _load[T: BaseModel](area: str, model: type[T]) -> list[T]:
    out: list[T] = []
    for path in sorted((DATA / "cases" / area).glob("*.json")):
        out += [model.model_validate(raw) for raw in json.loads(path.read_text("utf-8"))]
    return out


def load_tool_cases() -> list[ToolCase]:
    return _load("tools", ToolCase)


def load_write_cases() -> list[WriteCase]:
    return _load("write", WriteCase)


def load_presentation_cases() -> list[PresentationCase]:
    return _load("presentation", PresentationCase)


def load_decision_cases() -> list[DecisionCase]:
    return _load("decisions", DecisionCase)
