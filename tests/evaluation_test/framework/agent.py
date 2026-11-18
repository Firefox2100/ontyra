"""A small agent loop over MCP tools: the model calls tools, the real server answers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluation_test.framework.llm import Chat, Message, ToolCall, ToolSpec

RESULT_CHARS = 3000


@dataclass
class Executed:
    call: ToolCall
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)  # the structured result, when there is one
    error: bool = False


@dataclass
class Trace:
    steps: list[list[Executed]] = field(default_factory=list)
    final: str = ""
    messages: list[Message] = field(default_factory=list)

    @property
    def calls(self) -> list[Executed]:
        return [e for step in self.steps for e in step]


def tool_specs(tools: list[dict[str, Any]], *, descriptions: bool = True) -> list[ToolSpec]:
    """MCP tool listings (`name`, `description`, `input_schema`) as chat tool specs."""
    return [
        {
            "name": t["name"],
            "description": t.get("description", "") if descriptions else "",
            "parameters": t.get("input_schema") or t.get("inputSchema") or {"type": "object"},
        }
        for t in tools
    ]


async def run_agent(
    chat: Chat,
    messages: list[Message],
    tools: list[ToolSpec],
    client: Any | None = None,
    *,
    max_steps: int = 4,
    force: str | None = None,
) -> Trace:
    """Let the model call tools until it answers in text or `max_steps` is reached.

    `client` is a `fastmcp.Client` on the real server; without one the first tool call ends
    the run (selection only, nothing is executed). `force` names a tool the first step must call.
    """
    trace = Trace(messages=list(messages))
    for step in range(max_steps):
        reply = await chat.reply(trace.messages, tools, force=force if step == 0 else None)
        trace.messages.append({"role": "assistant", "content": reply.text, "calls": reply.calls})
        if not reply.calls:
            trace.final = reply.text
            break
        done: list[Executed] = []
        for call in reply.calls:
            executed = await _execute(client, call) if client is not None else Executed(call)
            done.append(executed)
            trace.messages.append(
                {"role": "tool", "content": executed.text, "call_id": call.id or call.name}
            )
        trace.steps.append(done)
        if client is None:
            break
    return trace


async def _execute(client: Any, call: ToolCall) -> Executed:
    result = await client.call_tool(call.name, call.args, raise_on_error=False)
    text = "".join(getattr(c, "text", "") for c in result.content)[:RESULT_CHARS]
    data = result.structured_content if isinstance(result.structured_content, dict) else {}
    if not data:
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            data = {}
    return Executed(call, text=text, data=data, error=bool(result.is_error))


CONTRACT = Path(__file__).resolve().parents[3] / "integrations" / "contract"


def load_contract(profile: str) -> list[dict[str, Any]]:
    """The generated MCP tool listing of a profile (`lite` or `full`): what a client sees."""
    return json.loads((CONTRACT / f"mcp-tools-{profile}.json").read_text("utf-8"))
