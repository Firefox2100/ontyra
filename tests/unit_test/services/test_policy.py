import pytest

from ontyra.models import PolicyConfig, PolicyRule
from ontyra.models.core import Actor
from ontyra.models.proposals import Finding
from ontyra.services import Caller, PolicyEngine
from ontyra.services.policy import OpSummary


def caller(kind: str = "agent", trust: float = 0.6) -> Caller:
    return Caller(actor=Actor(kind=kind, name="x", trust=trust))  # type: ignore[arg-type]


ADD_FACT = [OpSummary("add", "fact")]
ADD_DIRECTIVE = [OpSummary("add", "directive")]


def test_errors_always_reject_regardless_of_rules() -> None:
    engine = PolicyEngine(PolicyConfig(default="auto_commit"))
    err = Finding(severity="error", code="x", message="bad")
    assert engine.decide(caller(), ADD_FACT, [err])[0] == "reject"


def test_safe_default_is_review() -> None:
    assert PolicyEngine(PolicyConfig()).decide(caller(), ADD_FACT, [])[0] == "review"


def test_first_matching_rule_wins() -> None:
    engine = PolicyEngine(
        PolicyConfig(
            rules=[
                PolicyRule(kinds=["fact"], decision="auto_commit"),
                PolicyRule(kinds=["fact"], decision="reject"),
            ]
        )
    )
    assert engine.decide(caller(), ADD_FACT, []) == ("auto_commit", "policy rule 0")


@pytest.mark.parametrize(
    ("rule", "who", "ops", "expected"),
    [
        (PolicyRule(min_trust=0.7, decision="auto_commit"), caller(trust=0.6), ADD_FACT, "review"),
        (
            PolicyRule(min_trust=0.5, decision="auto_commit"),
            caller(trust=0.6),
            ADD_FACT,
            "auto_commit",
        ),
        (
            PolicyRule(actor_kinds=["human"], decision="auto_commit"),
            caller("agent"),
            ADD_FACT,
            "review",
        ),
        (
            PolicyRule(actor_kinds=["human"], decision="auto_commit"),
            caller("human"),
            ADD_FACT,
            "auto_commit",
        ),
        (PolicyRule(kinds=["fact"], decision="auto_commit"), caller(), ADD_DIRECTIVE, "review"),
        (
            PolicyRule(ops=["add"], decision="auto_commit"),
            caller(),
            [OpSummary("retract", "fact")],
            "review",
        ),
        (
            PolicyRule(ops=["add", "annotate"], decision="auto_commit"),
            caller(),
            [OpSummary("annotate")],
            "auto_commit",
        ),
    ],
)
def test_rule_matching(rule: PolicyRule, who: Caller, ops: list[OpSummary], expected: str) -> None:
    assert PolicyEngine(PolicyConfig(rules=[rule])).decide(who, ops, [])[0] == expected


def test_a_rule_must_fit_every_op_in_the_proposal() -> None:
    engine = PolicyEngine(PolicyConfig(rules=[PolicyRule(kinds=["fact"], decision="auto_commit")]))
    assert engine.decide(caller(), [*ADD_FACT, *ADD_DIRECTIVE], [])[0] == "review"


def test_kindless_ops_do_not_break_a_kind_rule() -> None:
    engine = PolicyEngine(PolicyConfig(rules=[PolicyRule(kinds=["fact"], decision="auto_commit")]))
    assert engine.decide(caller(), [*ADD_FACT, OpSummary("annotate")], [])[0] == "auto_commit"
