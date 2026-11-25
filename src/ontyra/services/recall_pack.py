"""Packing and rendering a recall pack: sections, token budget, `more` counts. Pure functions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ontyra.models.recall import Caution, More, RecallItem, RecallPack, Section

SECTIONS: tuple[Section, ...] = (
    "must_follow",
    "applicable",
    "context",
    "knowledge",
    "how_to",
    "open_tasks",
)
TITLES: dict[str, str] = {
    "must_follow": "Must follow",
    "applicable": "Applies here",
    "context": "Context",
    "knowledge": "Knowledge",
    "how_to": "How to",
    "open_tasks": "Open tasks",
}
OVERHEAD_PER_SECTION = 8


def section_of(item: RecallItem) -> Section:
    if item.kind == "directive":
        return "must_follow" if item.strength == "must" else "applicable"
    if item.kind in ("procedure", "lesson"):
        return "how_to"
    if item.kind == "task":
        return "open_tasks"
    if item.sources:
        return "knowledge"
    return "context"


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def render_item(item: RecallItem, profile: Literal["compact", "rich"]) -> str:
    head = item.text
    if profile == "compact" and item.kind == "directive":
        # the short imperative; the full statement and rationale stay one `get` away
        rule = item.detail.get("rule")
        if isinstance(rule, str) and rule.strip():
            head = rule.strip()
    if (
        item.kind == "directive"
        and item.polarity == "avoid"
        and not head.lower().startswith(("do not", "don't", "never", "avoid"))
    ):
        head = f"Avoid: {head}"
    cited = ""
    if item.sources:
        first = item.sources[0]
        extra = f" +{len(item.sources) - 1} more" if len(item.sources) > 1 else ""
        cited = f" (source: {first.uri}{' ' + first.locator if first.locator else ''}{extra})"
    if profile == "compact":
        tail = f" ({'; '.join(item.caveats)})" if item.caveats else ""
        unvetted = " [unvetted]" if item.vetting == "auto" else ""
        return f"- {head}{tail}{cited}{unvetted} [{item.memory}]"
    meta = [str(item.memory)]
    if item.anchor:
        meta.append(f"anchor {item.anchor}")
    meta.extend(item.why[:2])
    if item.vetting == "auto":
        meta.append("unvetted")
    if item.flags:
        meta.append("flags: " + ", ".join(item.flags))
    lines = [f"- {head}{cited}  ({' · '.join(meta)})"]
    lines += [f"  ! {c}" for c in item.caveats]
    return "\n".join(lines)


def render_pack(pack: RecallPack, profile: Literal["compact", "rich"]) -> str:
    out: list[str] = []
    for section in SECTIONS:
        items: list[RecallItem] = getattr(pack, section)
        if items:
            out.append(f"{TITLES[section]}:")
            out += [render_item(i, profile) for i in items]
    if pack.cautions:
        out.append("Cautions:")
        out += [f"- {c.message}" for c in pack.cautions]
    if pack.notices:
        out.append("Corrections:")
        out += [f"- {n.message}" for n in pack.notices]
    if pack.more:
        out.append("More available:")
        out += [f"- {m.count} more {m.section.replace('_', ' ')}: {m.hint}" for m in pack.more]
    return "\n".join(out)


def build_pack(
    path: Literal["recall", "briefing", "check"],
    ranked: Sequence[RecallItem],
    *,
    budget_tokens: int,
    profile: Literal["compact", "rich"],
    cautions: Sequence[Caution] = (),
    knowledge_budget_tokens: int = 0,
) -> RecallPack:
    """Fill sections in priority order within the budget; count what did not fit. Knowledge
    (what cites a source) is packed within its own budget, so it neither crowds out the rest nor
    is crowded out; with none, it shares the main one."""
    pack = RecallPack(path=path, silent=not ranked, cautions=list(cautions))
    grouped: dict[Section, list[RecallItem]] = {s: [] for s in SECTIONS}
    for item in ranked:
        grouped[section_of(item)].append(item)
    used = 0
    used_knowledge = 0
    skipped: dict[str, int] = {}
    for section in SECTIONS:
        taken: list[RecallItem] = []
        for n, item in enumerate(grouped[section]):
            cost = estimate_tokens(render_item(item, profile))
            if not taken:
                cost += OVERHEAD_PER_SECTION
            own = section == "knowledge" and knowledge_budget_tokens > 0
            spent, limit = (
                (used_knowledge, knowledge_budget_tokens) if own else (used, budget_tokens)
            )
            if spent + cost > limit and (taken or spent > 0):
                skipped[section] = len(grouped[section]) - n
                break
            taken.append(item)
            if own:
                used_knowledge += cost
            else:
                used += cost
        setattr(pack, section, taken)
    pack.more = [
        More(
            section=s,
            count=c,
            hint="ask for a briefing/list of this kind, or search with a narrower query",
        )
        for s, c in skipped.items()
    ]
    pack.silent = not pack.items()
    pack.rendered = render_pack(pack, profile)
    pack.tokens = estimate_tokens(pack.rendered) if pack.rendered else 0
    return pack
