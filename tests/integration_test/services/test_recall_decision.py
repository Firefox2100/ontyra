"""The decision model inside recall: blended into the fused ranking when it answers, absent when
it is off, skipped when it fails, and never asked when recall would stay silent anyway."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ontyra.app import Application
from ontyra.config import DecisionSettings
from ontyra.models import RecordId
from ontyra.models.decision import Judgement
from ontyra.models.recall import RecallContext, RecallRequest, RecallSettings, Signal
from ontyra.ports.errors import DecisionError
from ontyra.services import Caller

TEXTS = {
    "a": "run the tests with pytest before every commit",
    "b": "run the tests in the staging cluster before every release",
    "c": "tests are slow on the shared runner",
}


class FakeDecider:
    """A decision model that loves one text (probability 0.9) and records what it was asked."""

    name = "fake"

    def __init__(self, loves: str = "", fail: bool = False) -> None:
        self.loves, self.fail = loves, fail
        self.calls: list[tuple[str, list[str]]] = []

    async def judge(self, situation: str, memories: Sequence[str]) -> Judgement:
        self.calls.append((situation, list(memories)))
        if self.fail:
            raise DecisionError("the model is down")
        return Judgement(
            scores=[0.9 if self.loves in m else 0.05 for m in memories], none=0.1, model="fake"
        )


@pytest.fixture
async def seeded(make_app, agent: Caller) -> tuple[Application, dict[str, RecordId]]:
    app = await make_app(recall=RecallSettings(engine="fusion", fusion_margin=0.5))
    ids: dict[str, RecordId] = {}
    from ontyra.models.proposals import AddMemory, ProposalRequest

    for key, text in TEXTS.items():
        result = await app.memory.submit(
            agent,
            ProposalRequest(
                ops=[
                    AddMemory(
                        kind="directive",
                        ref="m",
                        fields={
                            "text": text,
                            "strength": "should",
                            "rule": text,
                            "scope": "universal",
                        },
                    )
                ]
            ),
        )
        ids[key] = result.created["m"]
    await app.indexer.backfill()
    return app, ids


def returned(pack) -> list[RecordId]:  # type: ignore[no-untyped-def]
    items = pack.must_follow + pack.applicable + pack.context + pack.how_to
    return [i.memory for i in sorted(items, key=lambda i: -i.score)]


async def test_without_a_decision_model_nothing_changes(seeded, agent: Caller) -> None:
    app, _ = seeded
    assert app.recall.decider is None  # type: ignore[attr-defined]
    pack = await app.recall.recall(agent, RecallRequest(query="run the tests"))
    assert not any("decision" in why for i in pack.applicable + pack.must_follow for why in i.why)


async def test_the_decision_model_reorders_the_result(seeded, agent: Caller) -> None:
    app, ids = seeded
    plain = returned(await app.recall.recall(agent, RecallRequest(query="run the tests")))
    assert plain and plain[0] != ids["b"]
    fake = FakeDecider(loves="staging cluster")
    app.recall.decider = fake  # type: ignore[attr-defined]
    pack = await app.recall.recall(agent, RecallRequest(query="run the tests"))
    assert returned(pack)[0] == ids["b"]
    best = max(pack.must_follow + pack.applicable, key=lambda i: i.score)
    assert any("decision 0.90" in why for why in best.why)


async def test_its_margin_decides_how_many_are_kept(seeded, agent: Caller) -> None:
    app, ids = seeded
    app.recall.decider = FakeDecider(loves="staging cluster")  # type: ignore[attr-defined]
    app.recall.cfg.decision_weight = 1.0  # the model alone: 0.9 against 0.05
    app.recall.cfg.decision_margin = 0.2
    pack = await app.recall.recall(agent, RecallRequest(query="run the tests"))
    assert returned(pack) == [ids["b"]]
    app.recall.cfg.decision_margin = 0.95
    wide = await app.recall.recall(agent, RecallRequest(query="run the tests"))
    assert len(returned(wide)) > 1


async def test_the_situation_it_sees_names_the_place_the_conditions_and_the_request(
    seeded, agent: Caller
) -> None:
    app, _ = seeded
    fake = FakeDecider()
    app.recall.decider = fake  # type: ignore[attr-defined]
    context = RecallContext(
        uris=["file:///home/u/proj"], signals=[Signal(concept="python", value="3.12")]
    )
    await app.recall.recall(agent, RecallRequest(query="run the tests", context=context))
    situation, memories = fake.calls[0]
    assert "Working at: file:///home/u/proj" in situation
    assert "Known conditions: python = 3.12" in situation
    assert "Request from the user: run the tests" in situation
    assert set(memories) <= {t for t in TEXTS.values()} or all(
        any(t in m for t in TEXTS.values()) for m in memories
    )


async def test_a_failing_decision_model_leaves_the_deterministic_result(
    seeded, agent: Caller
) -> None:
    app, _ = seeded
    plain = returned(await app.recall.recall(agent, RecallRequest(query="run the tests")))
    fake = FakeDecider(fail=True)
    app.recall.decider = fake  # type: ignore[attr-defined]
    again = returned(await app.recall.recall(agent, RecallRequest(query="run the tests")))
    assert fake.calls and again == plain


async def test_a_request_can_opt_out(seeded, agent: Caller) -> None:
    app, _ = seeded
    fake = FakeDecider()
    app.recall.decider = fake  # type: ignore[attr-defined]
    await app.recall.recall(agent, RecallRequest(query="run the tests", decide=False))
    assert not fake.calls


async def test_it_is_not_asked_when_recall_stays_silent(make_app, agent: Caller) -> None:
    app = await make_app(recall=RecallSettings(engine="fusion", abstain_cosine=0.99))
    fake = FakeDecider()
    app.recall.decider = fake  # type: ignore[attr-defined]
    pack = await app.recall.recall(agent, RecallRequest(query="zzzz qqqq"))
    assert pack.silent and not fake.calls


async def test_only_the_top_candidates_are_sent(seeded, agent: Caller) -> None:
    app, _ = seeded
    fake = FakeDecider()
    app.recall.decider = fake  # type: ignore[attr-defined]
    app.recall.cfg.decision_candidates = 2
    await app.recall.recall(agent, RecallRequest(query="run the tests"))
    assert len(fake.calls[0][1]) == 2


async def test_a_decision_model_needs_the_fusion_engine(make_app) -> None:
    configured = DecisionSettings(provider="systemone", url="http://x/v1", model="kev")
    with pytest.raises(ValueError, match="ENGINE=fusion"):
        await make_app(decision=configured)
    app = await make_app(recall=RecallSettings(engine="fusion"), decision=configured)
    assert app.recall.decider is not None  # type: ignore[attr-defined]


class FillerDecider(FakeDecider):
    """A decision model that also says whether a message is a pleasantry."""

    def __init__(self, filler: float = 0.0, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self.p, self.asked = filler, []

    async def filler(self, text: str) -> float:
        self.asked.append(text)
        if self.fail:
            raise DecisionError("down")
        return self.p


async def gated(seeded, agent, decider, query, **request):  # type: ignore[no-untyped-def]
    app, _ = seeded
    app.recall.decider = decider
    app.recall.cfg = app.recall.cfg.model_copy(update={"filler_gate": True})
    return await app.recall.recall(agent, RecallRequest(query=query, **request))


async def test_a_pleasantry_skips_the_lookup_and_the_judging(seeded, agent: Caller) -> None:
    decider = FillerDecider(filler=0.9)
    pack = await gated(seeded, agent, decider, "thanks!")
    assert pack.silent and decider.asked == ["thanks!"] and decider.calls == []


async def test_the_gate_only_asks_when_it_is_on_and_the_message_is_short(
    seeded, agent: Caller
) -> None:
    app, _ = seeded
    decider = FillerDecider(filler=0.9)
    app.recall.decider = decider  # the gate is off by default
    assert not (await app.recall.recall(agent, RecallRequest(query="run the tests"))).silent
    assert decider.asked == []
    long = "thanks " + "so much " * 10 + "run the tests"
    assert not (await gated(seeded, agent, decider, long)).silent and decider.asked == []


async def test_a_message_below_the_threshold_or_a_failing_model_is_recalled_as_usual(
    seeded, agent: Caller
) -> None:
    low = FillerDecider(filler=0.3, loves="pytest")
    assert not (await gated(seeded, agent, low, "run the tests")).silent
    broken = FillerDecider(fail=True)
    assert not (await gated(seeded, agent, broken, "run the tests")).silent
    skip = FillerDecider(filler=0.99)
    assert not (await gated(seeded, agent, skip, "run the tests", decide=False)).silent
    assert skip.asked == []


async def test_a_pleasantry_still_delivers_a_satisfied_trigger(make_app) -> None:
    from ontyra.models.ontology import ConceptDefinition
    from ontyra.models.proposals import AddMemory, ProposalRequest

    app: Application = await make_app(recall=RecallSettings(engine="fusion", filler_gate=True))
    caller = await app.actors.caller(kind="human", name="owner")
    await app.ontology.define(caller, ConceptDefinition(label="Docker"))
    text = "pin the base image digest when you touch a Dockerfile"
    done = await app.memory.submit(
        caller,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="lesson",
                    fields={
                        "text": text,
                        "situation": text,
                        "insight": text,
                        "qualifiers": [{"concept": "concept:docker", "role": "trigger"}],
                    },
                )
            ]
        ),
    )
    assert done.status == "committed", done.findings
    await app.indexer.backfill()
    decider = FillerDecider(filler=0.95)
    app.recall.decider = decider  # type: ignore[attr-defined]
    ctx = RecallContext(signals=[Signal(concept="docker")])
    pack = await app.recall.recall(caller, RecallRequest(query="thanks!", context=ctx))
    assert decider.asked == ["thanks!"] and decider.calls == []  # no lookup, no judging
    assert pack.items() and pack.items()[0].flags == ["triggered"]
