"""A small client for any OpenAI-compatible chat endpoint that answers in JSON."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from ontyra_ingest.config import Settings

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


class LlmError(Exception):
    pass


_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def _loads(text: str) -> Any:
    """`json.loads`, retried with stray backslashes (LaTeX, `\\_`) escaped."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_BAD_ESCAPE.sub(r"\\\\", text))


def parse_json(content: str) -> Any:
    """The JSON in a model's answer, tolerating reasoning blocks and code fences."""
    text = _FENCE.sub("", _THINK.sub("", content).strip()).strip()
    try:
        return _loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start < 0 or end <= start:
            raise LlmError("the model's answer holds no JSON") from None
        try:
            return _loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LlmError(f"the model's answer is not valid JSON: {exc}") from exc


class Llm:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        headers: dict[str, str] = {}
        if settings.llm_key:
            headers["Authorization"] = f"Bearer {settings.llm_key.get_secret_value()}"
        self._client = client or httpx.AsyncClient(
            base_url=settings.llm_url.rstrip("/"), headers=headers, timeout=settings.llm_timeout
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def json(self, system: str, user: str) -> Any:
        body: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            **self._settings.llm_extra,
        }
        last: Exception | None = None
        for attempt in range(self._settings.llm_retries + 1):
            try:
                response = await self._client.post("/chat/completions", json=body)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"] or ""
                return parse_json(content)
            except (httpx.HTTPError, LlmError, KeyError, IndexError, ValueError) as exc:
                last = exc
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
        raise LlmError(f"the model gave no usable answer: {last}")
