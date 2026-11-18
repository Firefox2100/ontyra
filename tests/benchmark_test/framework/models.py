"""Benchmark data: worlds (what is stored) and cases (what must come back), as JSON files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ontyra.models.ontology import (
    ConceptDefinition,
    RelationName,
    ResourceRequest,
    concept_slug,
)
from ontyra.models.recall import RecallContext

DATA = Path(__file__).resolve().parents[1] / "data"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelationSpec(Strict):
    source: str
    relation: RelationName
    target: str


class LinkSpec(Strict):
    relation: Literal["contradicts", "overrides", "refines"]
    target: str  # key of an earlier memory


KindLiteral = Literal["fact", "directive", "decision", "event", "procedure", "task", "lesson"]
TRAPS = (
    "superseded",  # replaced by a later memory: must not come back
    "retracted",  # declared wrong: must not come back
    "expired",  # past its expiry: must not come back
    "qualifier",  # applies only under a condition (OS, day type, version…)
    "anchor",  # applies only at a place (project, folder, host, room…)
    "overridden",  # a narrower rule overrides it in some scope
    "contradicted",  # an unresolved contradiction with another memory
    "opposite",  # nearly the same words, opposite or different guidance
    "homonym",  # shares a word with memories of another field
    "twin",  # same statement for a different subject (two projects, two journals…)
    "stale",  # a number or version that has since changed
)

_QUAL = re.compile(
    r"^(?P<role>necessary|sufficient|exclusion|prerequisite|trigger):(?P<concept>[a-z0-9_]+)"
    r"(?:\s*(?P<op>>=|<=|!=|=|<|>)\s*(?P<value>.+))?$"
)


class MemorySpec(Strict):
    """One memory, added in file order.

    Shorthands (merged into the kind's own `fields`; `fields` wins on a clash is an error):
    `about` concept slugs, `at` anchor URIs (`=uri` for an exact anchor), `q` qualifiers as
    `role:concept [op value [unit]]`, `strength`/`polarity` for directives. Defaults: a
    directive's `rule` is its text, a decision's `rationale` too, `steps` may be strings.
    """

    key: str
    kind: KindLiteral
    text: str
    fields: dict[str, Any] = {}
    about: list[str] = []
    at: list[str] = []
    q: list[str] = []
    strength: Literal["must", "should", "prefer"] | None = None
    polarity: Literal["do", "avoid"] | None = None
    scope: Literal["contextual", "universal"] = "contextual"
    supersedes: str | None = None  # key of an earlier memory this replaces
    retracted: bool = False  # added, then retracted (must never come back)
    links: list[LinkSpec] = []
    trap: list[str] = []  # which planted trap this memory is (see TRAPS), for the matrix
    # key of an earlier memory of the same kind that this one restates (a planted duplicate):
    # recall tolerates it wherever the original is allowed, and the consolidation scan must
    # put the two in one cluster
    duplicate_of: str | None = None
    note: str | None = None  # why it exists in the world
    domain: str = ""  # set by the loader: the part file's stem


def _qualifier(text: str, vtypes: dict[str, str]) -> dict[str, Any]:
    m = _QUAL.match(text.strip())
    if not m:
        raise ValueError(f"bad qualifier shorthand {text!r}")
    out: dict[str, Any] = {"concept": f"concept:{m['concept']}", "role": m["role"]}
    if m["op"]:
        out["op"] = m["op"]
        raw = m["value"].strip()
        number = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:\s*([A-Za-z%]+))?", raw)
        if vtypes.get(m["concept"]) == "number" and number:
            out["value"] = float(number[1]) if "." in number[1] else int(number[1])
            if number[2]:
                out["unit"] = number[2]
        else:
            out["value"] = raw
    return out


def memory_fields(mem: MemorySpec, vtypes: dict[str, str]) -> dict[str, Any]:
    """The `fields` of the add op: shorthands expanded, defaults applied."""
    out: dict[str, Any] = {}
    if mem.about:
        out["about"] = [f"concept:{c}" for c in mem.about]
    if mem.at:
        out["anchors"] = [
            {"uri": u[1:], "match": "exact"} if u.startswith("=") else {"uri": u} for u in mem.at
        ]
    if mem.q:
        out["qualifiers"] = [_qualifier(x, vtypes) for x in mem.q]
    if mem.strength:
        out["strength"] = mem.strength
    if mem.polarity:
        out["polarity"] = mem.polarity
    if mem.scope != "contextual":
        out["scope"] = mem.scope
    clash = set(out) & set(mem.fields)
    if clash:
        raise ValueError(f"{mem.key}: {sorted(clash)} given both as shorthand and in fields")
    out.update(mem.fields)
    if mem.kind == "directive":
        out.setdefault("strength", "should")
        out.setdefault("rule", mem.text)
    if mem.kind == "decision":
        out.setdefault("rationale", mem.text)
        out.setdefault("decided_at", "2026-06-01T09:00:00Z")
    if mem.kind == "procedure" and "steps" in out:
        out["steps"] = [{"text": x} if isinstance(x, str) else x for x in out["steps"]]
    if isinstance(out.get("subject"), str) and ":" not in out["subject"]:
        out["subject"] = f"concept:{out['subject']}"
    return out


class World(Strict):
    id: str
    description: str
    concepts: list[ConceptDefinition]
    relations: list[RelationSpec] = []
    resources: list[ResourceRequest] = []
    memories: list[MemorySpec]

    @property
    def concept_ids(self) -> set[str]:
        return {concept_slug(c.label) for c in self.concepts}

    @property
    def value_types(self) -> dict[str, str]:
        return {concept_slug(c.label): c.value_type for c in self.concepts}

    def fields_of(self, mem: MemorySpec) -> dict[str, Any]:
        return memory_fields(mem, self.value_types)


class Part(Strict):
    """One file of a world directory: a domain's concepts, relations, resources, memories."""

    concepts: list[ConceptDefinition] = []
    relations: list[RelationSpec] = []
    resources: list[ResourceRequest] = []
    memories: list[MemorySpec] = []


class Step(Strict):
    """One request and what must come of it."""

    mode: Literal["recall", "briefing", "check"] = "recall"
    query: str | None = None
    context: RecallContext = RecallContext()
    hints: list[str] = []
    include: list[str] = []  # memory keys that must be returned
    exclude: list[str] = []  # memory keys that must not be returned
    allow: list[str] = []  # returned without penalty (arguably relevant)
    allow_prefix: list[str] = []  # the same for every memory whose key starts with one of these
    silent: bool = False  # nothing may be returned
    strict: bool = False  # unexpected memories of the same domain count as false positives too
    top: int | None = None  # every `include` key must rank within the first `top`
    min_cautions: int = 0
    checks: dict[str, list[str]] = {}  # key -> substrings among its prerequisite checks
    caveats: dict[str, list[str]] = {}  # key -> substrings among its caveats
    verdict: Literal["clear", "caution"] | None = None  # `check` mode


class Case(Step):
    id: str
    world: str
    tags: list[str]  # reported per tag, never averaged across tags
    split: Literal["tune", "holdout"] = "tune"
    why: str
    then: list[Step] = []  # further requests in the same session


def load_world(world_id: str) -> World:
    """A world is a directory: `world.json` (id, description) plus one part file per domain.

    Parts are merged in file-name order (shared concepts first: `00-core.json`); a memory's
    `domain` is its part's file stem.
    """
    root = DATA / "worlds" / world_id
    head = json.loads((root / "world.json").read_text(encoding="utf-8"))
    merged: dict[str, Any] = {
        **head,
        "concepts": [],
        "relations": [],
        "resources": [],
        "memories": [],
    }
    for path in sorted(p for p in root.glob("*.json") if p.name != "world.json"):
        part = Part.model_validate_json(path.read_text(encoding="utf-8"))
        merged["concepts"] += [c.model_dump() for c in part.concepts]
        merged["relations"] += [r.model_dump() for r in part.relations]
        merged["resources"] += [r.model_dump() for r in part.resources]
        merged["memories"] += [{**m.model_dump(), "domain": path.stem} for m in part.memories]
    domains = {m["key"]: m["domain"] for m in merged["memories"]}
    for m in merged["memories"]:  # a duplicate belongs to the domain of what it restates
        if m["duplicate_of"] in domains:
            m["domain"] = domains[m["duplicate_of"]]
    return World.model_validate(merged)


def load_cases(suite: str) -> list[Case]:
    cases: list[Case] = []
    for path in sorted((DATA / "cases" / suite).glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            cases.append(Case.model_validate(raw))
    return cases


def world_ids() -> list[str]:
    return sorted(p.name for p in (DATA / "worlds").iterdir() if (p / "world.json").exists())
