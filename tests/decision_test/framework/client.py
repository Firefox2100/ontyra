"""A thin client of the System One API (`POST {url}/systemone`) that records latency.

The production adapters (`ontyra.adapters.decider`) are what the daemon uses and are measured as
they are; this client exists for the questions the daemon does not ask yet (the gate), and to
time every request. One forward pass, no generated text: it answers with probabilities.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from ontyra.ports import DecisionError

T = TypeVar("T")


class Kev:
    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        concurrency: int = 4,
        transport: Any = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._transport = transport
        self._slots = asyncio.Semaphore(concurrency)
        self.seconds: list[float] = []  # latency of every request made

    async def timed(self, call: Callable[[], Awaitable[T]]) -> T:
        """Run any request (a production adapter's, for one) in a slot, recording its latency."""
        async with self._slots:
            began = time.perf_counter()
            try:
                return await call()
            finally:
                self.seconds.append(time.perf_counter() - began)

    async def ask(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        body = {"model": self.model, "state": state, "questions": questions}

        async def post() -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                return await client.post(f"{self.url}/systemone", headers=self._headers, json=body)

        try:
            response = await self.timed(post)
        except httpx.HTTPError as exc:
            raise DecisionError(f"decision model unreachable: {exc!r}") from exc
        if response.status_code >= 400:
            raise DecisionError(f"decision model answered {response.status_code}")
        try:
            return response.json()["answers"]
        except (KeyError, ValueError) as exc:
            raise DecisionError(f"decision model gave no answers: {exc!r}") from exc

    async def choice(
        self, state: str, instructions: str, options: dict[str, str]
    ) -> dict[str, float]:
        """Probability of each option (they add up to about 1)."""
        answers = await self.ask(
            state, {"pick": {"type": "choice", "instructions": instructions, "criteria": options}}
        )
        try:
            probabilities = answers["pick"]["probabilities"]
            return {key: float(probabilities[key]) for key in options}
        except (KeyError, TypeError, ValueError) as exc:
            raise DecisionError(f"unreadable choice answer: {exc!r}") from exc

    async def yes(self, state: str, question: str) -> float:
        """Probability of "yes" to one yes/no question."""
        answers = await self.ask(state, {"q": {"type": "noul", "instructions": question}})
        try:
            answer = answers["q"]
            return float(answer.get("noul_raw", answer["noul"]))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise DecisionError(f"unreadable yes/no answer: {exc!r}") from exc
