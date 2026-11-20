"""Decision-model adapters: the request they send, the verdict they read, and how they fail."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ontyra.adapters import LangChainDecider, SystemOneDecider, build_decider
from ontyra.adapters.decider import clip
from ontyra.config import DecisionSettings
from ontyra.ports import MessageClassifier
from ontyra.ports.errors import DecisionError

SITUATION = "Working at: ssh://user@host\nRequest from the user: where do I install this?"
MEMORIES = ["Install under /local/software on the work servers", "Water the ferns on Sunday"]


class Server:
    """An httpx transport that records the request and answers with a fixed body."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def sent(self) -> dict[str, Any]:
        return json.loads(self.seen[0].content)


def decider(server: Server, **kw: Any) -> SystemOneDecider:
    return SystemOneDecider(
        "http://decisions.test/v1/", "kev-9b", api_key="k", transport=server.transport, **kw
    )


async def test_choice_asks_one_question_over_the_candidates_and_none() -> None:
    answers = {
        "pick": {
            "type": "choice",
            "choice": "m0",
            "probabilities": {"m0": 0.7, "m1": 0.1, "none": 0.2},
            "confidence": 0.5,
        }
    }
    server = Server(body={"answers": answers})
    verdict = await decider(server).judge(SITUATION, MEMORIES)
    assert verdict.scores == [0.7, 0.1] and verdict.none == 0.2 and verdict.model == "kev-9b"
    assert str(server.seen[0].url) == "http://decisions.test/v1/systemone"
    assert server.seen[0].headers["authorization"] == "Bearer k"
    sent = server.sent()
    assert sent["model"] == "kev-9b" and sent["state"] == SITUATION
    question = sent["questions"]["pick"]
    assert question["type"] == "choice"
    assert list(question["criteria"]) == ["m0", "m1", "none"]
    assert question["criteria"]["m0"] == MEMORIES[0]


async def test_noul_asks_one_yes_no_question_per_candidate_and_prefers_the_raw_number() -> None:
    answers = {
        "m0": {"type": "noul", "noul": 1.0, "noul_raw": 0.83},
        "m1": {"type": "noul", "noul": 0.04},
    }
    server = Server(body={"answers": answers})
    verdict = await decider(server, format="noul").judge(SITUATION, MEMORIES)
    assert verdict.scores == [0.83, 0.04] and verdict.none is None
    questions = server.sent()["questions"]
    assert set(questions) == {"m0", "m1"} and questions["m1"]["type"] == "noul"
    assert MEMORIES[1] in questions["m1"]["instructions"]


async def test_long_memories_are_clipped_to_one_line() -> None:
    server = Server(body={"answers": {"pick": {"probabilities": {}}}})
    long = "word " * 80 + "\nsecond line"
    await decider(server, memory_chars=40).judge(SITUATION, [long])
    sent = server.sent()["questions"]["pick"]["criteria"]["m0"]
    assert len(sent) <= 40 and "\n" not in sent and sent.endswith("…")
    assert clip("short", 40) == "short"


async def test_no_candidates_means_no_request() -> None:
    server = Server(body={"answers": {}})
    verdict = await decider(server).judge(SITUATION, [])
    assert verdict.scores == [] and not server.seen


async def test_a_candidate_the_model_did_not_score_is_none() -> None:
    server = Server(body={"answers": {"pick": {"probabilities": {"m0": 0.9}}}})
    verdict = await decider(server).judge(SITUATION, MEMORIES)
    assert verdict.scores == [0.9, None]


async def test_a_refusal_carries_the_servers_own_message() -> None:
    body = {"error": {"message": "the question and its options are too large to process"}}
    with pytest.raises(DecisionError, match="too large to process"):
        await decider(Server(status=400, body=body)).judge(SITUATION, MEMORIES)


async def test_an_unreachable_or_unreadable_server_is_a_decision_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    unreachable = SystemOneDecider(
        "http://decisions.test/v1", "kev-9b", transport=httpx.MockTransport(down)
    )
    with pytest.raises(DecisionError, match="could not be reached"):
        await unreachable.judge(SITUATION, MEMORIES)
    with pytest.raises(DecisionError, match="cannot be read"):
        await decider(Server(body={"nonsense": 1})).judge(SITUATION, MEMORIES)


def _fake_langchain(answers: dict[str, Any] | Exception) -> Any:
    from langchain_core.decisions import BaseDecisionModel, ClassifierResponse

    class Fake(BaseDecisionModel):
        model: str = "fake"
        seen: list[Any] = []

        @property
        def _provider(self) -> str:
            return "fake"

        def _classify(self, state: Any, questions: Any) -> ClassifierResponse:
            self.seen.append((state, questions))
            if isinstance(answers, Exception):
                raise answers
            return ClassifierResponse.model_validate({"model": "fake", "answers": answers})

    return Fake()


async def test_a_langchain_decision_model_is_asked_the_same_questions() -> None:
    answers = {
        "pick": {
            "type": "choice",
            "choice": "m1",
            "probabilities": {"m0": 0.2, "m1": 0.6, "none": 0.2},
            "confidence": 0.4,
        }
    }
    model = _fake_langchain(answers)
    verdict = await LangChainDecider(model, name="fake").judge(SITUATION, MEMORIES)
    assert verdict.scores == [0.2, 0.6] and verdict.none == 0.2
    state, questions = model.seen[0]
    assert state == SITUATION and list(questions["pick"].criteria) == ["m0", "m1", "none"]


async def test_a_langchain_noul_format_reads_each_probability() -> None:
    answers = {"m0": {"type": "noul", "noul": 0.9}, "m1": {"type": "noul", "noul": 0.1}}
    model = _fake_langchain(answers)
    verdict = await LangChainDecider(model, name="fake", format="noul").judge(SITUATION, MEMORIES)
    assert verdict.scores == [0.9, 0.1]


async def test_a_failing_langchain_model_is_a_decision_error() -> None:
    model = _fake_langchain(RuntimeError("quota exceeded"))
    with pytest.raises(DecisionError, match="quota exceeded"):
        await LangChainDecider(model, name="fake").judge(SITUATION, MEMORIES)


def test_the_factory_builds_each_provider() -> None:
    assert build_decider(DecisionSettings()) is None
    built = build_decider(DecisionSettings(provider="systemone", url="http://x/v1", model="kev"))
    assert isinstance(built, SystemOneDecider) and built.name == "systemone:kev"
    with pytest.raises(ValueError, match="URL"):
        build_decider(DecisionSettings(provider="systemone"))
    with pytest.raises(ValueError, match="class_path"):
        build_decider(DecisionSettings(provider="langchain"))


def _same(score: float | None = 0.9) -> Server:
    return Server(body={"answers": {"same": {"type": "noul", "noul": score}}})


async def test_same_asks_one_yes_no_question_about_both_memories() -> None:
    server = _same(0.93)
    score = await decider(server).same(
        "Use uv for dependencies.", "Dependencies are managed with uv."
    )
    assert score == 0.93
    sent = server.sent()
    assert sent["state"] == (
        "Memory A: Use uv for dependencies.\nMemory B: Dependencies are managed with uv."
    )
    question = sent["questions"]["same"]
    assert question["type"] == "noul"
    assert "same thing" in question["instructions"]
    assert str(server.seen[0].url).endswith("/v1/systemone")


async def test_same_cuts_each_memory_to_compare_chars() -> None:
    server = _same()
    await decider(server, compare_chars=20).same("x " * 100, "y")
    memory_a = server.sent()["state"].split("\n")[0]
    assert len(memory_a.removeprefix("Memory A: ")) <= 20


@pytest.mark.parametrize(
    "server",
    [
        Server(status=503, body={"error": {"message": "busy"}}),
        Server(body={"answers": {}}),
        Server(body={"answers": {"same": {"type": "noul", "noul": 7}}}),
        Server(body={"answers": {"same": {"type": "noul"}}}),
    ],
)
async def test_same_fails_with_a_decision_error(server: Server) -> None:
    with pytest.raises(DecisionError):
        await decider(server).same("a", "b")


async def test_the_filler_question_is_asked_without_history_and_reads_one_probability() -> None:
    answers = {
        "kind": {
            "type": "choice",
            "choice": "filler",
            "probabilities": {"standalone": 0.05, "refers_back": 0.05, "filler": 0.9},
            "confidence": 0.8,
        }
    }
    server = Server(body={"answers": answers})
    assert await decider(server).filler("thanks!") == 0.9
    sent = server.sent()
    assert "thanks!" in sent["state"] and "no earlier conversation" in sent["state"]
    assert set(sent["questions"]["kind"]["criteria"]) == {"standalone", "refers_back", "filler"}
    assert isinstance(decider(server), MessageClassifier)


async def test_a_filler_answer_that_cannot_be_read_is_a_decision_error() -> None:
    with pytest.raises(DecisionError):
        await decider(Server(body={"answers": {}})).filler("ok")
    with pytest.raises(DecisionError):
        await decider(Server(status=500, body={"error": {"message": "down"}})).filler("ok")


async def test_a_langchain_model_answers_the_filler_question_too() -> None:
    answers = {
        "kind": {
            "type": "choice",
            "choice": "filler",
            "probabilities": {"standalone": 0.1, "filler": 0.8},
            "confidence": 0.7,
        }
    }
    model = _fake_langchain(answers)
    assert await LangChainDecider(model, name="fake").filler("lol") == 0.8
    assert "lol" in model.seen[0][0]
