"""Consistency checks over benchmark data (no database, no model)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from benchmark_test.framework.models import TRAPS, Case, MemorySpec, World
from ontyra.models.ontology import concept_slug
from ontyra.models.uri import canonical_uri
from ontyra.services.text import coverage


def _concept_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith("concept:") else []
    if isinstance(value, list):
        return [r for v in value for r in _concept_refs(v)]
    if isinstance(value, dict):
        return [r for v in value.values() for r in _concept_refs(v)]
    return []


def world_problems(world: World) -> list[str]:
    problems: list[str] = []
    known = world.concept_ids
    names = known | {concept_slug(a) for c in world.concepts for a in c.aliases}
    labels = [concept_slug(c.label) for c in world.concepts]
    problems += [
        f"duplicate concept {c}" for c in sorted({c for c in labels if labels.count(c) > 1})
    ]
    for rel in world.relations:
        for end in (rel.source, rel.target):
            if concept_slug(end) not in names:
                problems.append(
                    f"relation {rel.source} {rel.relation} {rel.target}: unknown {end!r}"
                )
    seen: set[str] = set()
    for mem in world.memories:
        if mem.key in seen:
            problems.append(f"duplicate memory key {mem.key}")
        for ref in [mem.supersedes, *[link.target for link in mem.links]]:
            if ref and ref not in seen:
                problems.append(f"{mem.key}: refers to {ref!r}, which is not an earlier memory")
        try:
            resolved = world.fields_of(mem)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        for ref in _concept_refs(resolved):
            if ref.removeprefix("concept:") not in known:
                problems.append(f"{mem.key}: unknown concept {ref!r}")
        for anchor in resolved.get("anchors", []):
            try:
                canonical_uri(anchor["uri"])
            except ValueError as exc:
                problems.append(f"{mem.key}: {exc}")
        problems += [f"{mem.key}: unknown trap {t!r}" for t in mem.trap if t not in TRAPS]
        seen.add(mem.key)
    return problems


def case_problems(cases: list[Case], worlds: dict[str, World]) -> list[str]:
    problems: list[str] = []
    ids = [c.id for c in cases]
    problems += [f"duplicate case id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    for case in cases:
        world = worlds.get(case.world)
        if world is None:
            problems.append(f"{case.id}: unknown world {case.world!r}")
            continue
        keys = {m.key for m in world.memories}
        for step in [case, *case.then]:
            named = [*step.include, *step.exclude, *step.allow, *step.checks, *step.caveats]
            problems += [f"{case.id}: unknown memory key {k!r}" for k in named if k not in keys]
            for prefix in step.allow_prefix:
                if not any(k.startswith(prefix) for k in keys):
                    problems.append(f"{case.id}: allow_prefix {prefix!r} matches no memory")
            both = set(step.include) & set(step.exclude)
            problems += [f"{case.id}: {k!r} is both included and excluded" for k in sorted(both)]
            if step.silent and step.include:
                problems.append(f"{case.id}: silent but includes keys")
            if step.mode == "check" and not step.query:
                problems.append(f"{case.id}: a check needs a query")
            if step.mode == "recall" and not step.query:
                problems.append(f"{case.id}: a recall needs a query")
            for concept in (s.concept for s in step.context.signals):
                if concept_slug(concept) not in world.concept_ids | {
                    concept_slug(a) for c in world.concepts for a in c.aliases
                }:
                    problems.append(f"{case.id}: unknown signal concept {concept!r}")
        if "paraphrase" in case.tags:
            texts = {m.key: memory_text(m) for m in world.memories}
            for key in case.include:
                shared = coverage(case.query or "", texts[key])
                if shared > PARAPHRASE_MAX_OVERLAP:
                    problems.append(
                        f"{case.id}: a paraphrase must not reuse the memory's words "
                        f"({shared:.2f} of the query's words appear in {key})"
                    )
    return problems


PARAPHRASE_MAX_OVERLAP = 0.2  # share of the query's content words found in the memory


_STRUCTURAL = {
    "about",
    "anchors",
    "qualifiers",
    "strength",
    "polarity",
    "decided_at",
    "occurred_at",
    "due",
    "state",
    "expires_at",
    "subject",
    "alternatives",
    "participants",
}


def _leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [x for v in value for x in _leaves(v)]
    if isinstance(value, dict):
        return [x for v in value.values() for x in _leaves(v)]
    return []


def memory_text(mem: MemorySpec) -> str:
    """Approximately what lexical search sees: the text and the kind's own free-text fields."""
    own = {k: v for k, v in mem.fields.items() if k not in _STRUCTURAL}
    return " ".join([mem.text, *_leaves(own)])


def overlap(case: Case, world: World) -> float | None:
    """The smallest share of the query's words found in any expected memory (None: no query)."""
    texts = {m.key: memory_text(m) for m in world.memories}
    shares = [coverage(case.query or "", texts[k]) for k in case.include if k in texts]
    return min(shares) if shares and case.query else None


def matrix(world: World) -> dict[str, dict[str, int]]:
    """Memories per domain group (the part-file prefix) and kind."""
    out: dict[str, dict[str, int]] = {}
    for mem in world.memories:
        group = mem.domain.split("-")[0]
        row = out.setdefault(group, {})
        row[mem.kind] = row.get(mem.kind, 0) + 1
    return out


def trap_counts(world: World) -> dict[str, int]:
    counts: dict[str, int] = {}
    for mem in world.memories:
        for trap in mem.trap:
            counts[trap] = counts.get(trap, 0) + 1
    return counts


def coverage_report(cases: list[Case], world: World) -> dict[str, list[str]]:
    """Memories no case includes, excludes or allows (dead weight), and tags by case count."""
    used: set[str] = set()
    tags: dict[str, int] = defaultdict(int)
    for case in cases:
        if case.world != world.id:
            continue
        for step in [case, *case.then]:
            used |= {*step.include, *step.exclude, *step.allow}
            used |= {m.key for m in world.memories if m.key.startswith(tuple(step.allow_prefix))}
        for tag in case.tags:
            tags[tag] += 1
    return {
        "unused": [m.key for m in world.memories if m.key not in used],
        "thin_tags": [t for t, n in sorted(tags.items()) if n < 2],
    }
