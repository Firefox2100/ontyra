"""Commit policy configuration: who may commit what without review."""

from __future__ import annotations

from typing import Literal

from ontyra.models.base import Model

Decision = Literal["auto_commit", "review", "reject"]


class PolicyRule(Model):
    """Matches a proposal when *every* op satisfies the listed constraints (unset = any)."""

    kinds: list[str] | None = None  # memory kinds touched by add/revise ops
    ops: list[str] | None = None  # op types: add, revise, supersede, retract, link, annotate
    actor_kinds: list[str] | None = None
    min_trust: float = 0.0
    decision: Decision


class PolicyConfig(Model):
    """First matching rule wins; otherwise `default`. Safe default: everything is reviewed."""

    default: Decision = "review"
    rules: list[PolicyRule] = []
