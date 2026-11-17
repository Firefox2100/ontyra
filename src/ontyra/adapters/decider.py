"""Decision-model adapters: a System One style API (hosted or local, e.g. LocalAI with a Kev
model) and any LangChain decision model (`langchain_core.decisions.BaseDecisionModel`)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

from ontyra.models.decision import Judgement
from ontyra.ports.errors import DecisionError

CHOICE = "Which stored memory matters most for the situation?"
NONE = "None of these memories applies to the situation."
NOUL = "Does this stored memory apply to the situation?"
NONE_KEY = "none"

Format = Literal["choice", "noul"]


def clip(text: str, chars: int) -> str:
    """One line, at most `chars` characters: a decision model's request has a size limit."""
    one = " ".join(text.split())
    return one if len(one) <= chars else one[: max(1, chars - 1)].rstrip() + "…"


class SystemOneDecider:
    """`POST {url}/systemone` with `{model, state, questions}`: the situation is the state; with
    `format="choice"` there is one question over all candidates (plus "none"), with `noul` one
    yes/no question per candidate. One request, one forward pass, no generated text."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str | None = None,
        format: Format = "choice",
        memory_chars: int = 100,
        timeout: float = 30.0,
        transport: Any = None,
    ) -> None:
        self._url = url.rstrip("/") + "/systemone"
        self._model = model
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self._format = format
        self._chars = memory_chars
        self._timeout = timeout
        self._transport = transport  # tests inject an httpx transport

    @property
    def name(self) -> str:
        return f"systemone:{self._model}"

    def questions(self, memories: Sequence[str]) -> dict[str, Any]:
        texts = [clip(m, self._chars) for m in memories]
        if self._format == "noul":
            return {
                f"m{i}": {"type": "noul", "instructions": f"{NOUL} Memory: {t}"}
                for i, t in enumerate(texts)
            }
        options = {f"m{i}": t for i, t in enumerate(texts)}
        options[NONE_KEY] = NONE
        return {"pick": {"type": "choice", "instructions": CHOICE, "criteria": options}}

    async def judge(self, situation: str, memories: Sequence[str]) -> Judgement:
        import httpx

        if not memories:
            return Judgement(scores=[], model=self._model)
        body = {"model": self._model, "state": situation, "questions": self.questions(memories)}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(self._url, headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            raise DecisionError(f"{self._url} could not be reached: {exc!r}") from exc
        if response.status_code >= 400:
            raise DecisionError(f"{self._url} answered {response.status_code}: {_said(response)}")
        try:
            return self._read(response.json()["answers"], len(memories))
        except (KeyError, TypeError, ValueError) as exc:
            raise DecisionError(f"{self._url} gave an answer that cannot be read: {exc!r}") from exc

    def _read(self, answers: dict[str, Any], n: int) -> Judgement:
        if self._format == "noul":
            scores = [
                _number(answers[f"m{i}"].get("noul_raw", answers[f"m{i}"]["noul"]))
                for i in range(n)
            ]
            return Judgement(scores=scores, model=self._model)
        probabilities = answers["pick"]["probabilities"]
        return Judgement(
            scores=[_number(probabilities.get(f"m{i}")) for i in range(n)],
            none=_number(probabilities.get(NONE_KEY)),
            model=self._model,
        )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"invalid probability: {value!r}")
    return number


def _said(response: Any) -> str:
    try:
        error = response.json().get("error", {})
        return str(error.get("message") or error)[:300]
    except ValueError:
        return str(response.text)[:300]


class LangChainDecider:
    """Wraps a LangChain decision model (`langchain_core.decisions.BaseDecisionModel`, such as
    `langchain_openai.decisions.OpenAIClassifier` or a System One client): the same questions as
    `SystemOneDecider`, asked through the model's own `ainvoke`."""

    def __init__(
        self, model: Any, *, name: str, format: Format = "choice", memory_chars: int = 100
    ) -> None:
        self._model = model
        self._name = name
        self._format = format
        self._chars = memory_chars

    @property
    def name(self) -> str:
        return self._name

    async def judge(self, situation: str, memories: Sequence[str]) -> Judgement:
        from langchain_core.decisions import Choice, Noul

        if not memories:
            return Judgement(scores=[], model=self._name)
        texts = [clip(m, self._chars) for m in memories]
        if self._format == "noul":
            questions: dict[str, Any] = {
                f"m{i}": Noul(instructions=f"{NOUL} Memory: {t}") for i, t in enumerate(texts)
            }
        else:
            options: dict[str, Any] = {f"m{i}": t for i, t in enumerate(texts)}
            options[NONE_KEY] = NONE
            questions = {"pick": Choice(instructions=CHOICE, criteria=options)}
        try:
            response = await self._model.ainvoke({"state": situation, "questions": questions})
            if self._format == "noul":
                scores = [_number(response.nouls[f"m{i}"].noul) for i in range(len(texts))]
                return Judgement(scores=scores, model=self._name)
            probabilities = response.choices["pick"].probabilities
        except Exception as exc:  # the integration's own errors: any of them means "no verdict"
            raise DecisionError(f"{self._name} could not decide: {exc!r}") from exc
        try:
            return Judgement(
                scores=[_number(probabilities.get(f"m{i}")) for i in range(len(texts))],
                none=_number(probabilities.get(NONE_KEY)),
                model=self._name,
            )
        except ValueError as exc:
            raise DecisionError(f"{self._name} gave invalid probabilities: {exc!r}") from exc
