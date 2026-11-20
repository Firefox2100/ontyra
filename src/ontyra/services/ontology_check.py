"""A read-only health report on the ontology (plans/23). Nothing is fixed automatically."""

from __future__ import annotations

from collections import defaultdict

from ontyra.models import RecordId
from ontyra.models.ontology import Concept, OntologyCheck, OntologyFinding
from ontyra.models.ontology_change import OntologyChange
from ontyra.services.context import Caller
from ontyra.services.ontology import MAX_CONCEPTS, OntologyService

HIERARCHIES = ("is_a", "part_of", "instance_of")


class OntologyChecker:
    def __init__(self, ontology: OntologyService) -> None:
        self.o = ontology

    async def check(self, caller: Caller) -> OntologyCheck:
        store = self.o.store
        concepts = {c.id: c for c in (await self._concepts()) if c.id is not None}
        edges = [e for e in await store.edges("related", limit=200_000) if not e.get("ended_at")]
        recent: dict[RecordId, RecordId] = {}
        for ch in await store.select(
            "ontology_change", limit=2000, order_by="created_at", descending=True
        ):
            if isinstance(ch, OntologyChange) and ch.id is not None and ch.status == "applied":
                for t in ch.touched or []:
                    recent.setdefault(t, ch.id)
        findings: list[OntologyFinding] = []

        def add(severity: str, code: str, message: str, *ids: RecordId) -> None:
            findings.append(
                OntologyFinding(
                    severity=severity,  # type: ignore[arg-type]
                    code=code,
                    message=message,
                    concepts=list(ids),
                    change=next((recent[i] for i in ids if i in recent), None),
                )
            )

        # edges to concepts that do not exist
        for e in edges:
            for end in (e["in"], e["out"]):
                if end not in concepts:
                    add(
                        "error",
                        "dangling_edge",
                        f"a {e['relation']} relation names {end}, which does not exist",
                        end,
                    )
        # cycles in the hierarchies
        for rel in HIERARCHIES:
            graph: dict[RecordId, list[RecordId]] = defaultdict(list)
            for e in edges:
                if e["relation"] == rel:
                    graph[e["in"]].append(e["out"])
            for cycle in _cycles(graph):
                add("error", "cycle", f"{rel} loops: " + " -> ".join(str(c) for c in cycle), *cycle)
        # replacement pointers
        for cid, c in concepts.items():
            if c.replaced_by is not None and c.replaced_by not in concepts:
                add(
                    "error",
                    "missing_successor",
                    f"{cid} is replaced by {c.replaced_by}, which does not exist",
                    cid,
                )
            seen = {cid}
            cur = c.replaced_by
            while cur is not None and cur in concepts:
                if cur in seen:
                    add("error", "replacement_loop", f"the replacements of {cid} loop", cid)
                    break
                seen.add(cur)
                cur = concepts[cur].replaced_by
        for e in edges:
            if (
                e["relation"] == "split_into"
                and e["out"] in concepts
                and concepts[e["in"]].status == "active"
            ):
                add(
                    "warning",
                    "split_but_active",
                    f"{e['in']} was split but is not deprecated",
                    e["in"],
                )
        # one name, two concepts
        names: dict[str, list[RecordId]] = defaultdict(list)
        for cid, c in concepts.items():
            for name in {c.label.casefold().strip(), *(a.casefold().strip() for a in c.aliases)}:
                names[name].append(cid)
        for name, ids in names.items():
            # a merge hands the tombstone's names to its successor on purpose
            live = [i for i in ids if concepts[i].status == "active"]
            if len(live) > 1 or (len(ids) > 1 and not live):
                add(
                    "warning",
                    "shared_name",
                    f"{name!r} names {', '.join(str(i) for i in ids)}",
                    *ids,
                )
        # deprecated concepts live memories still use; and index rows a maintenance step can tidy
        related = {e["in"] for e in edges} | {e["out"] for e in edges}
        for cid, c in concepts.items():
            about = len(await self.o.store.docs(caller.visible_owners(), about=[cid], limit=5000))
            if c.status == "deprecated" and about:
                succ = f"; its successor is {c.replaced_by}" if c.replaced_by else ""
                add(
                    "info",
                    "deprecated_in_use",
                    f"{about} live memory(ies) are still about deprecated {cid}{succ} (allowed: "
                    "they are updated when revised)",
                    cid,
                )
            elif c.status == "active" and c.scheme == "domain" and not about and cid not in related:
                add("info", "unused", f"{cid} has no relations and no memories", cid)
        order = {"error": 0, "warning": 1, "info": 2}
        findings.sort(key=lambda f: (order[f.severity], f.code, str(f.concepts[:1])))
        return OntologyCheck(concepts=len(concepts), live_edges=len(edges), findings=findings)

    async def _concepts(self) -> list[Concept]:
        return [
            c
            for c in await self.o.store.select("concept", limit=MAX_CONCEPTS)
            if isinstance(c, Concept)
        ]


def _cycles(graph: dict[RecordId, list[RecordId]]) -> list[list[RecordId]]:
    """One representative loop per strongly connected knot (depth-first search)."""
    found: list[list[RecordId]] = []
    state: dict[RecordId, int] = {}
    stack: list[RecordId] = []

    def visit(node: RecordId) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, ()):
            if state.get(nxt, 0) == 0:
                visit(nxt)
            elif state[nxt] == 1:
                found.append([*stack[stack.index(nxt) :], nxt])
        stack.pop()
        state[node] = 2

    for n in list(graph):
        if state.get(n, 0) == 0:
            visit(n)
    return found
