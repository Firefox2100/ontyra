"""Decision-model adapters: a System One style API (hosted or local, e.g. LocalAI with a Kev
model) and any LangChain decision model (`langchain_core.decisions.BaseDecisionModel`)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any, Literal

from ontyra.models.decision import Judgement
from ontyra.ports.errors import DecisionError

CHOICE = "Which stored memory matters most for the situation?"
NONE = "None of these memories applies to the situation."
NOUL = "Does this stored memory apply to the situation?"
NONE_KEY = "none"
SAME = (
    "Do memory A and memory B state the same thing, only worded differently? "
    "Different values or different facts mean no."
)

MESSAGE_KIND = "What kind of message is the user's new message?"
CREDENTIAL = (
    "Does this string look like a credential (an API key, a token, a password, a private key)? "
    "A path, a host name, an identifier or a checksum of public data is not."
)
CREDENTIAL_LIKELY = 0.7
SECRET_CHARS = 2000
SECRET_TOKENS = re.compile(r"[A-Za-z0-9_+/.=:\-]{16,}")
MESSAGE_OPTIONS = {
    "standalone": "A request or question that can be understood on its own, without the earlier "
    "conversation.",
    "refers_back": "It only makes sense with the earlier conversation: a pronoun, an ellipsis, "
    "'that', 'it', 'and below that?', 'ok, go ahead'.",
    "filler": "A greeting, thanks or reaction that asks for nothing ('thanks', 'ok', 'lol').",
}
NO_HISTORY = "(There is no earlier conversation.)"
FILLER_CHARS = 300

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
        compare_chars: int = 300,
        timeout: float = 30.0,
        transport: Any = None,
    ) -> None:
        self._url = url.rstrip("/") + "/systemone"
        self._model = model
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self._format = format
        self._chars = memory_chars
        self._compare_chars = compare_chars
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

    async def same(self, a: str, b: str) -> float:
        import httpx

        state = (
            f"Memory A: {clip(a, self._compare_chars)}\nMemory B: {clip(b, self._compare_chars)}"
        )
        body = {
            "model": self._model,
            "state": state,
            "questions": {"same": {"type": "noul", "instructions": SAME}},
        }
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
            answer = response.json()["answers"]["same"]
            score = _number(answer.get("noul_raw", answer["noul"]))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise DecisionError(f"{self._url} gave an answer that cannot be read: {exc!r}") from exc
        if score is None:
            raise DecisionError(f"{self._url} gave no score")
        return score

    async def filler(self, text: str) -> float:
        import httpx

        body = {
            "model": self._model,
            "state": f"User's new message: {clip(text, FILLER_CHARS)}\n{NO_HISTORY}",
            "questions": {
                "kind": {
                    "type": "choice",
                    "instructions": MESSAGE_KIND,
                    "criteria": MESSAGE_OPTIONS,
                }
            },
        }
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
            score = _number(response.json()["answers"]["kind"]["probabilities"].get("filler"))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise DecisionError(f"{self._url} gave an answer that cannot be read: {exc!r}") from exc
        if score is None:
            raise DecisionError(f"{self._url} gave no score")
        return score

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
        self,
        model: Any,
        *,
        name: str,
        format: Format = "choice",
        memory_chars: int = 100,
        compare_chars: int = 300,
    ) -> None:
        self._model = model
        self._name = name
        self._format = format
        self._chars = memory_chars
        self._compare_chars = compare_chars

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

    async def filler(self, text: str) -> float:
        from langchain_core.decisions import Choice

        state = f"User's new message: {clip(text, FILLER_CHARS)}\n{NO_HISTORY}"
        try:
            response = await self._model.ainvoke(
                {
                    "state": state,
                    "questions": {
                        "kind": Choice(instructions=MESSAGE_KIND, criteria=dict(MESSAGE_OPTIONS))
                    },
                }
            )
            score = _number(response.choices["kind"].probabilities.get("filler"))
        except Exception as exc:  # the integration's own errors: any of them means "no verdict"
            raise DecisionError(f"{self._name} could not decide: {exc!r}") from exc
        if score is None:
            raise DecisionError(f"{self._name} gave no score")
        return score

    async def same(self, a: str, b: str) -> float:
        from langchain_core.decisions import Noul

        state = (
            f"Memory A: {clip(a, self._compare_chars)}\nMemory B: {clip(b, self._compare_chars)}"
        )
        try:
            response = await self._model.ainvoke(
                {"state": state, "questions": {"same": Noul(instructions=SAME)}}
            )
            score = _number(response.nouls["same"].noul)
        except Exception as exc:  # the integration's own errors: any of them means "no verdict"
            raise DecisionError(f"{self._name} could not decide: {exc!r}") from exc
        if score is None:
            raise DecisionError(f"{self._name} gave no score")
        return score

    async def secrets(self, text: str) -> list[str]:
        """Which long tokens in `text` look like credentials: the decider's half of redaction.

        The patterns in `services/secrets.py` know key shapes; they cannot know that "the string
        we use for the billing portal is hunter2-admin" hides a password. Asking is a choice
        question per token, so one call covers the message and the answers are probabilities.
        """
        from langchain_core.decisions import Choice

        tokens = [t for t in SECRET_TOKENS.findall(clip(text, SECRET_CHARS)) if not _ordinary(t)]
        tokens = list(dict.fromkeys(tokens))[:6]
        if not tokens:
            return []
        questions: dict[str, Any] = {
            f"t{i}": Choice(
                instructions=CREDENTIAL,
                criteria={"credential": t, "ordinary": "an ordinary value: a path, a name, an id"},
            )
            for i, t in enumerate(tokens)
        }
        try:
            response = await self._model.ainvoke(
                {"state": clip(text, SECRET_CHARS), "questions": questions}
            )
            probabilities = [response.choices[f"t{i}"].probabilities for i in range(len(tokens))]
        except Exception as exc:  # the integration's own errors: any of them means "no verdict"
            raise DecisionError(f"{self._name} could not decide: {exc!r}") from exc
        return [
            token
            for token, p in zip(tokens, probabilities, strict=True)
            if (_number(p.get("credential")) or 0.0) >= CREDENTIAL_LIKELY
        ]


def _ordinary(token: str) -> bool:
    """A path, a host or a URL is not a credential, however long it is."""
    lowered = token.lower()
    return "/" in token or "\\" in token or lowered.startswith(("http", "file:", "doi:"))
