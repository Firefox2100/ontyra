"""Qualifier evaluation: three-valued matching of a memory's conditions against the context
(plans/01 "Environment qualifiers"). Pure functions; the ontology supplies entailment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from packaging.version import InvalidVersion, Version

from ontyra.models import Model, RecordId
from ontyra.models.anchors import Qualifier, Scalar
from ontyra.models.ontology import Concept
from ontyra.services.reasoner import Ontology


class Tri(Enum):
    YES = "satisfied"
    NO = "unsatisfied"
    UNKNOWN = "unknown"


class Observation(Model):
    """One fact about the environment, already mapped to a concept."""

    concept: RecordId
    value: Scalar | None = None
    unit: str | None = None


_UNITS: dict[str, dict[str, float]] = {
    "memory": {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4},
    "duration": {"ms": 0.001, "s": 1, "sec": 1, "min": 60, "h": 3600, "d": 86400},
}


def known_unit(dimension: str, unit: str) -> bool:
    return unit.casefold() in _UNITS.get(dimension, {})


def _and(a: Tri, b: Tri) -> Tri:
    if Tri.NO in (a, b):
        return Tri.NO
    if Tri.UNKNOWN in (a, b):
        return Tri.UNKNOWN
    return Tri.YES


def _or(a: Tri, b: Tri) -> Tri:
    if Tri.YES in (a, b):
        return Tri.YES
    if Tri.UNKNOWN in (a, b):
        return Tri.UNKNOWN
    return Tri.NO


def parse_version(text: str, scheme: str | None) -> Any:
    """A comparable version, or raise ValueError. Schemes: semver, pep440, dotted, distro."""
    text = text.strip().lstrip("vV")
    if scheme in (None, "pep440", "semver"):
        try:
            return Version(text)
        except InvalidVersion:
            pass
    nums = re.findall(r"\d+", text)
    if not nums:
        raise ValueError(f"not a version: {text!r}")
    return tuple(int(n) for n in nums)


def _compare(a: Any, op: str, b: Any) -> bool:
    if op in ("=", "has"):
        return a == b
    if op == "!=":
        return a != b
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def _typed(value: Scalar, concept: Concept, unit: str | None) -> Any:
    """Normalise a value for comparison according to the concept's value type."""
    vt = concept.value_type
    if vt == "version":
        return parse_version(str(value), concept.version_scheme)
    if vt == "number":
        number = float(value)  # type: ignore[arg-type]
        if concept.dimension and unit:
            factor = _UNITS[concept.dimension].get(unit.casefold())
            if factor is None:
                raise ValueError(f"unknown unit {unit!r} for {concept.dimension}")
            number *= factor
        return number
    if vt == "bool":
        return value if isinstance(value, bool) else str(value).casefold() in ("true", "1", "yes")
    return str(value).casefold()


def _check_value(q: Qualifier, obs: Observation, concept: Concept) -> Tri:
    """Compare an observed value against a qualifier's expression (same or entailing concept)."""
    if obs.value is None or q.value is None:
        return Tri.UNKNOWN
    try:
        have = _typed(obs.value, concept, obs.unit)
        if q.op == "in":
            options = q.value if isinstance(q.value, list) else [q.value]
            return Tri.YES if any(have == _typed(o, concept, q.unit) for o in options) else Tri.NO
        if q.op == "between":
            if not isinstance(q.value, list) or len(q.value) != 2:
                return Tri.UNKNOWN
            lo, hi = (_typed(v, concept, q.unit) for v in q.value)
            return Tri.YES if _le(lo, have) and _le(have, hi) else Tri.NO
        want = _typed(q.value if not isinstance(q.value, list) else q.value[0], concept, q.unit)
        if isinstance(have, tuple) and isinstance(want, tuple):
            have, want = _pad(have, want)  # type: ignore[arg-type]
        return Tri.YES if _compare(have, q.op, want) else Tri.NO
    except (ValueError, TypeError):
        return Tri.UNKNOWN


def _le(a: Any, b: Any) -> bool:
    if isinstance(a, tuple) and isinstance(b, tuple):
        a, b = _pad(a, b)  # type: ignore[arg-type]
    return bool(a <= b)


def evaluate_one(q: Qualifier, observations: list[Observation], ontology: Ontology) -> Tri:
    """Three-valued result of one qualifier against the observations.

    satisfied: an observation establishes the concept (entailment) and any value expression
    holds; unsatisfied: the value fails, a `false` observation says the concept is absent, or
    an observed concept is declared disjoint with it; unknown: no signal.
    """
    concept = ontology.concepts.get(q.concept)
    for obs in observations:
        if not ontology.entails(obs.concept, q.concept):
            continue
        if q.op == "has" and q.value is None:
            return Tri.NO if obs.value is False else Tri.YES
        if concept is None:
            return Tri.UNKNOWN
        return _check_value(q, obs, concept)
    if q.op in ("has", "=", "in") and any(
        ontology.disjoint(o.concept, q.concept) for o in observations
    ):
        return Tri.NO
    return Tri.UNKNOWN


def _group(results: list[Tri]) -> Tri:
    out = Tri.YES
    for r in results:
        out = _and(out, r)
    return out


def _any_group(by_group: dict[int, list[Tri]]) -> Tri:
    out = Tri.NO
    for results in by_group.values():
        out = _or(out, _group(results))
    return out


@dataclass
class QualifierOutcome:
    """What the qualifiers mean for one memory in this context (the role x result table)."""

    include: bool = True
    demote: bool = False  # `necessary` unknown: keep, but with a caveat
    boost: bool = False  # `sufficient` satisfied
    triggered: bool = False
    matched: int = 0  # satisfied qualifiers (specificity)
    caveats: list[str] = field(default_factory=lambda: [])
    checks: list[str] = field(default_factory=lambda: [])  # prerequisite follow-ups
    reasons: list[str] = field(default_factory=lambda: [])


def apply_roles(
    qualifiers: list[Qualifier], observations: list[Observation], ontology: Ontology
) -> QualifierOutcome:
    out = QualifierOutcome()

    def label(q: Qualifier) -> str:
        c = ontology.concepts.get(q.concept)
        name = c.label if c else str(q.concept.id)
        if q.op == "has" and q.value is None:
            return name
        shown = q.value if not isinstance(q.value, list) else "-".join(map(str, q.value))
        return f"{name} {q.op} {shown}{' ' + q.unit if q.unit else ''}"

    by_role: dict[str, dict[int, list[Tri]]] = {}
    names: dict[str, list[str]] = {}
    for q in qualifiers:
        r = evaluate_one(q, observations, ontology)
        if r is Tri.YES:
            out.matched += 1
        by_role.setdefault(q.role, {}).setdefault(q.group, []).append(r)
        names.setdefault(q.role, []).append(label(q))
        if q.role == "prerequisite":
            if r is Tri.NO:
                out.caveats.append(f"prerequisite not met: {label(q)}")
                out.checks.append(label(q))
            elif r is Tri.UNKNOWN:
                out.caveats.append(f"verify prerequisite: {label(q)}")
                out.checks.append(label(q))
    necessary = by_role.get("necessary")
    if necessary:
        res = _any_group(necessary)
        if res is Tri.NO:
            out.include = False
            out.reasons.append(f"excluded: requires {' / '.join(names['necessary'])}")
        elif res is Tri.UNKNOWN:
            out.demote = True
            out.caveats.append(f"only if {' / '.join(names['necessary'])}")
    exclusion = by_role.get("exclusion")
    if exclusion and _any_group(exclusion) is Tri.YES:
        out.include = False
        out.reasons.append(f"excluded: not applicable when {' / '.join(names['exclusion'])}")
    sufficient = by_role.get("sufficient")
    if sufficient and _any_group(sufficient) is Tri.YES:
        out.boost = True
    trigger = by_role.get("trigger")
    if trigger and _any_group(trigger) is Tri.YES:
        out.triggered = True
    return out


def qualifier_problems(q: Qualifier, concept: Concept) -> list[str]:
    """Why a qualifier's expression does not fit its concept's value type (empty if it does)."""
    out: list[str] = []
    vt = concept.value_type
    if q.op in ("<", "<=", ">", ">=", "between") and vt not in ("version", "number"):
        out.append(f"operator {q.op} needs a version or number concept; {concept.label} is {vt}")
    if q.op != "has" and q.value is None:
        out.append(f"operator {q.op} needs a value")
    if q.op == "between" and not (isinstance(q.value, list) and len(q.value) == 2):
        out.append("between needs [low, high]")
    if q.op == "in" and q.value is not None and not isinstance(q.value, list):
        out.append("in needs a list of values")
    if q.unit and (concept.dimension is None or not known_unit(concept.dimension, q.unit)):
        out.append(f"unit {q.unit!r} does not fit {concept.label}")
    if vt == "enum" and q.value is not None and q.op in ("=", "!=", "in"):
        values = q.value if isinstance(q.value, list) else [q.value]
        bad = [v for v in values if str(v) not in concept.values]
        if bad:
            out.append(f"{bad} not among {concept.label}'s values {concept.values}")
    if vt == "version":
        for v in q.value if isinstance(q.value, list) else [q.value]:
            if v is None:
                continue
            try:
                parse_version(str(v), concept.version_scheme)
            except ValueError:
                out.append(f"{v!r} is not a valid {concept.version_scheme or 'dotted'} version")
    return out
