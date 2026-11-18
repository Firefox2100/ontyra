"""A chat model behind a tiny protocol, so the suites run against a real model (LangChain) or
a scripted one (offline tests). Messages are plain dicts:

    {"role": "system"|"user"|"assistant"|"tool", "content": str,
     "calls": [ToolCall]   # assistant messages that called tools
     "call_id": str}       # tool messages: the call they answer
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]
ToolSpec = dict[str, Any]  # {"name", "description", "parameters"} (a JSON schema)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str = ""


@dataclass
class Reply:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)


class Chat(Protocol):
    name: str

    async def reply(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, force: str | None = None
    ) -> Reply: ...


class LangChainChat:
    """`langchain_openai.ChatOpenAI` against an OpenAI-compatible server (e.g. LocalAI)."""

    def __init__(
        self,
        *,
        url: str,
        model: str,
        api_key: str = "none",
        temperature: float = 0.0,
        max_tokens: int = 6000,
        timeout: float = 300.0,
        thinking: bool = False,
    ) -> None:
        import httpx
        from langchain_openai import ChatOpenAI

        self.name = model
        self._model = ChatOpenAI(
            model=model,
            base_url=url,
            api_key=api_key,  # type: ignore[arg-type]
            temperature=temperature,
            max_completion_tokens=max_tokens,
            timeout=timeout,
            max_retries=1,
            # langchain-openai caches one default client for every instance; each test has
            # its own event loop, so a shared client breaks with "Event loop is closed"
            http_async_client=httpx.AsyncClient(timeout=timeout),
            extra_body=_thinking_options(thinking),
        )

    async def reply(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, force: str | None = None
    ) -> Reply:
        model: Any = self._model
        if tools:
            model = model.bind_tools(
                [{"type": "function", "function": t} for t in tools],
                tool_choice=force if force else "auto",
            )
        result = await model.ainvoke([_to_langchain(m) for m in messages])
        calls = [
            ToolCall(name=c["name"], args=c["args"], id=c.get("id") or "")
            for c in (result.tool_calls or [])
        ]
        return Reply(text=clean(_text(result.content)), calls=calls)


def _thinking_options(thinking: bool) -> dict[str, Any]:
    """Request fields that switch reasoning on or off.

    LocalAI's llama.cpp backend ignores `chat_template_kwargs.enable_thinking` (checked against
    qwen3.6: the answer still came with a reasoning block); `reasoning_effort: "none"` is what
    turns it off. The kwarg stays for servers (vLLM) that read it instead.
    """
    body: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": thinking}}
    if not thinking:
        body["reasoning_effort"] = "none"
    return body


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


def clean(text: str) -> str:
    """The answer without a reasoning block."""
    return _THINK.sub("", text).strip()


def _to_langchain(m: Message) -> Any:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    role, content = m["role"], m.get("content", "")
    if role == "system":
        return SystemMessage(content=content)
    if role == "user":
        return HumanMessage(content=content)
    if role == "tool":
        return ToolMessage(content=content, tool_call_id=m["call_id"])
    calls = [{"name": c.name, "args": c.args, "id": c.id} for c in m.get("calls", [])]
    return AIMessage(content=content, tool_calls=calls)


class ScriptedChat:
    """Replies from a function of the conversation; for offline tests of the runners."""

    name = "scripted"

    def __init__(self, script: Callable[[list[Message], list[ToolSpec] | None, str | None], Reply]):
        self._script = script
        self.seen: list[list[Message]] = []

    async def reply(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, force: str | None = None
    ) -> Reply:
        self.seen.append(list(messages))
        return self._script(messages, tools, force)
