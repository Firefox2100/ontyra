"""Commit policy: decides whether a validated proposal commits, waits for review, or fails."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ontyra.models import Decision, PolicyConfig, PolicyRule
from ontyra.models.proposals import Finding
from ontyra.services.context import Caller


@dataclass(frozen=True)
class OpSummary:
    op: str
    kind: str | None = None  # memory kind touched, when the op has one
    sourced: bool = False  # the op cites an external source with a literal quote


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def decide(
        self, caller: Caller, ops: Sequence[OpSummary], findings: Sequence[Finding]
    ) -> tuple[Decision, str]:
        if any(f.severity == "error" for f in findings):
            return "reject", "validation errors"
        for i, rule in enumerate(self.config.rules):
            if self._matches(rule, caller, ops):
                return rule.decision, f"policy rule {i}"
        return self.config.default, "policy default"

    @staticmethod
    def _matches(rule: PolicyRule, caller: Caller, ops: Sequence[OpSummary]) -> bool:
        if caller.actor.trust < rule.min_trust:
            return False
        if rule.actor_kinds is not None and caller.actor.kind not in rule.actor_kinds:
            return False
        for op in ops:
            if rule.ops is not None and op.op not in rule.ops:
                return False
            if rule.kinds is not None and op.kind is not None and op.kind not in rule.kinds:
                return False
            if rule.sourced is not None and op.sourced != rule.sourced:
                return False
        return True
