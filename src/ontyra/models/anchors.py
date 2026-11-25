"""Where and under which circumstances a memory applies: anchors and qualifiers.

Both are embedded in the (immutable) revision, so a revision's applicability is versioned with
its text and written atomically with it.
"""

from __future__ import annotations

from typing import Literal

from ontyra.models.base import ConceptRef, Model

QualifierRole = Literal["necessary", "sufficient", "exclusion", "prerequisite", "trigger"]
QualifierOp = Literal["has", "=", "!=", "<", "<=", ">", ">=", "in", "between"]
Scalar = str | float | bool


class Anchor(Model):
    """A resource (by canonical URI) the memory is scoped to."""

    uri: str
    match: Literal["subtree", "exact"] = "subtree"


class Qualifier(Model):
    """An ontology-backed condition with a role and an optional typed value expression.

    `group`: qualifiers of one role in the same group are AND-ed, different groups OR-ed.
    """

    concept: ConceptRef
    role: QualifierRole
    op: QualifierOp = "has"
    value: Scalar | list[str] | list[float] | None = None
    unit: str | None = None
    group: int = 0
