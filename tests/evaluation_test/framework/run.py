"""Runners: play one case against a chat model and score the outcome."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation_test.framework.agent import Trace, run_agent
from evaluation_test.framework.llm import Chat, Message, ToolSpec
from evaluation_test.framework.models import (
    DecisionCase,
    PresentationCase,
    ToolCase,
    WriteCase,
)
from evaluation_test.framework.score import (
    DecisionScore,
    ToolScore,
    WriteScore,
    check_text,
    score_decision,
    score_tool_call,
    score_write,
)
from ontyra.models import RecordId
from ontyra.models.consolidation import DuplicateCluster, Member, PairScore, UpdateCandidate
from ontyra.services.feedback import feedback_card
from ontyra.services.tasks import CHOICES, duplicate_card, expiry_card, update_card
from ontyra.transports.mcp.server import INSTRUCTIONS

SKILL = Path(__file__).resolve().parents[3] / "integrations/skills/ontyra-memory/SKILL.md"
ASSISTANT = "You are an AI assistant working with a user. You can use the tools provided.\n"
HOST = "devbox"


def system_prompt(cwd: str | None = None, extra: str = "") -> str:
    parts = [ASSISTANT, INSTRUCTIONS.strip()]
    if cwd:
        parts.append(f"The current working directory is {cwd} on host {HOST}.")
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def skill_text() -> str:
    """The shared agent skill without its front matter."""
    text = SKILL.read_text("utf-8")
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


def skill_section(heading: str) -> str:
    """One `## heading` section of the skill."""
    body = skill_text()
    match = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", body, re.DOTALL | re.MULTILINE)
    assert match, f"no section {heading!r} in the skill"
    return match.group(0).strip()


# ---- tool selection -------------------------------------------------------------------------


async def run_tool_case(
    chat: Chat, case: ToolCase, specs: list[ToolSpec], schemas: dict[str, dict[str, Any]]
) -> tuple[ToolScore, Trace]:
    messages: list[Message] = [
        {"role": "system", "content": system_prompt(case.cwd)},
        {"role": "user", "content": case.say},
    ]
    trace = await run_agent(chat, messages, specs, client=None)
    calls = [e.call for e in trace.calls]
    return score_tool_call(case, calls, schemas), trace


# ---- writing --------------------------------------------------------------------------------


async def run_write_case(
    chat: Chat, case: WriteCase, specs: list[ToolSpec], client: Any
) -> tuple[WriteScore, Trace]:
    transcript = "\n".join(f"{role}: {text}" for role, text in case.conversation)
    note = f"Guidance for using memory:\n{skill_text()}"
    if len(case.conversation) > 1:
        content = (
            f"(Earlier in the conversation)\n{transcript}\n\n(Respond to the last user line; "
            "use the memory tools only if something durable was said.)"
        )
    else:
        content = case.conversation[0][1]
    messages: list[Message] = [
        {"role": "system", "content": system_prompt(case.cwd, note)},
        {"role": "user", "content": content},
    ]
    trace = await run_agent(chat, messages, specs, client, max_steps=5)
    return score_write(case, trace.calls), trace


# ---- presentation ---------------------------------------------------------------------------


def presentation_messages(case: PresentationCase, rendered: str | None) -> list[Message]:
    system = (
        "You are an AI coding and research assistant. Answer the user's request directly and "
        "concretely (steps, commands or code where they help); keep it short."
    )
    if rendered:
        system += f"\n\n{rendered}"
    return [{"role": "system", "content": system}, {"role": "user", "content": case.task}]


async def run_presentation_case(
    chat: Chat, case: PresentationCase, rendered: str | None
) -> tuple[list[str], str]:
    reply = await chat.reply(presentation_messages(case, rendered))
    return check_text(reply.text, case.must, case.avoid), reply.text


# ---- maintenance decisions ------------------------------------------------------------------

KIND_OF = {"fact", "directive", "decision", "event", "procedure", "task", "lesson"}


def _member(n: int, text: str, kind: str) -> Member:
    return Member(
        owner=RecordId("owner", "global"),
        memory=RecordId("memory", f"m{n}"),
        revision=RecordId(f"{kind}_rev", f"r{n}"),
        kind=kind,  # type: ignore[arg-type]
        text=text,
        created_at=datetime(2026, 1, n, tzinfo=UTC),
    )


def build_card(case: DecisionCase, texts: dict[str, tuple[str, str]]) -> dict[str, Any]:
    """The card the daemon would queue for this finding. `texts`: world key -> (text, kind)."""
    members: list[Member] = []
    for n, spec in enumerate(case.members, 1):
        if spec.key:
            text, kind = texts[spec.key]
        else:
            assert spec.text is not None
            text, kind = spec.text, spec.kind
        members.append(_member(n, text, kind))
    if case.finding == "duplicates":
        pairs = [
            PairScore(a=a.memory, b=b.memory, cosine=0.86, judged_by="cosine")
            for i, a in enumerate(members)
            for b in members[i + 1 :]
        ]
        return duplicate_card(
            DuplicateCluster(members=members, pairs=pairs, keep=members[0].memory)
        )
    if case.finding == "update":
        return update_card(UpdateCandidate(older=members[0], newer=members[1], cosine=0.88))
    only = members[0]
    if case.finding == "feedback":
        label, _, said = case.report.partition(":")
        return feedback_card(
            memory=only.memory,
            revision=only.revision,
            kind=only.kind,
            text=only.text,
            label=label.strip() or "wrong",
            report={
                "label": label.strip(),
                "query": "(a recalled memory)",
                "by": "claude",
                "note": said.strip() or None,
            },
        )
    return expiry_card(
        memory=only.memory,
        kind=only.kind,
        text=only.text,
        revision=only.revision,
        expires_at=datetime(2026, 11, 20, tzinfo=UTC),
        expired=case.expired,
    )


def decision_messages(card: dict[str, Any]) -> list[Message]:
    system = (
        "You review maintenance tasks for an agent memory system, as a careful reviewer would.\n\n"
        f"{skill_section('Maintenance tasks (full MCP profile, or `/v1/tasks` over REST)')}\n\n"
        "Decide this one task. Answer with a single JSON object and nothing else: "
        '{"decision": "<one of the card\'s choices>", "note": "<one sentence: why>"}'
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "```json\n" + json.dumps(card, indent=1, default=str) + "\n```",
        },
    ]


async def run_decision_case(
    chat: Chat, case: DecisionCase, card: dict[str, Any]
) -> tuple[DecisionScore, str]:
    reply = await chat.reply(decision_messages(card))
    kind = {"duplicates": "consolidate", "update": "review_contradiction"}.get(
        case.finding, "review_feedback" if case.finding == "feedback" else "expire_check"
    )
    return score_decision(case, reply.text, list(CHOICES[kind])), reply.text  # type: ignore[index]
