"""Deterministic ontology reasoning over an in-memory snapshot (plans/11, "Reasoner v1").

Pure: no I/O. Built from concepts and typed relations; answers entailment, disjointness,
replacement and recall-expansion questions. All traversals are cycle-safe. The interface is
narrow on purpose, so a rule engine could replace the internals later.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ontyra.models import RecordId
from ontyra.models.ontology import Concept

BROADER = ("is_a", "instance_of")  # "X is a kind of Y": entailment goes upward along these
PARTWISE = ("part_of",)

# recall expansion weights (config could expose these later)
W_DESCENDANT = 0.8
W_ANCESTOR = 0.6
W_PART = 0.6
W_ALTERNATIVE = 0.5
W_REPLACED = 0.9


@dataclass(frozen=True)
class Mention:
    concept: RecordId
    phrase: str


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[\w.+#-]+", text.casefold()))


class Ontology:
    def __init__(
        self,
        concepts: Mapping[RecordId, Concept],
        relations: Iterable[tuple[RecordId, str, RecordId]],
    ) -> None:
        self.concepts = dict(concepts)
        self._out: dict[str, dict[RecordId, set[RecordId]]] = {}
        self._in: dict[str, dict[RecordId, set[RecordId]]] = {}
        for src, rel, dst in relations:
            self._out.setdefault(rel, {}).setdefault(src, set()).add(dst)
            self._in.setdefault(rel, {}).setdefault(dst, set()).add(src)
        for cid, c in self.concepts.items():  # the field is the same fact as the relation
            if c.replaced_by is not None:
                self._out.setdefault("replaced_by", {}).setdefault(cid, set()).add(c.replaced_by)
                self._in.setdefault("replaced_by", {}).setdefault(c.replaced_by, set()).add(cid)
        self._phrases: dict[str, set[RecordId]] = {}
        for cid, c in self.concepts.items():
            for phrase in (c.label, *c.aliases, str(cid.id).replace("_", " ")):
                key = _norm(phrase)
                if key:
                    self._phrases.setdefault(key, set()).add(cid)
        self._max_words = max((len(p.split()) for p in self._phrases), default=1)

    # ---- graph walks --------------------------------------------------------

    def _walk(
        self, start: RecordId, adj: Mapping[RecordId, set[RecordId]], max_depth: int = 12
    ) -> dict[RecordId, int]:
        """Nodes reachable from `start` (excluded) with their shortest distance."""
        seen: dict[RecordId, int] = {start: 0}
        queue: deque[RecordId] = deque([start])
        while queue:
            node = queue.popleft()
            if seen[node] >= max_depth:
                continue
            for nxt in sorted(adj.get(node, ()), key=str):
                if nxt not in seen:
                    seen[nxt] = seen[node] + 1
                    queue.append(nxt)
        del seen[start]
        return seen

    def _union(self, rels: tuple[str, ...], *, up: bool) -> dict[RecordId, set[RecordId]]:
        adj: dict[RecordId, set[RecordId]] = {}
        for rel in rels:
            for node, others in (self._out if up else self._in).get(rel, {}).items():
                adj.setdefault(node, set()).update(others)
        return adj

    def ancestors(self, concept: RecordId, rels: tuple[str, ...] = BROADER) -> dict[RecordId, int]:
        """Everything `concept` is a (transitive) kind of, with distances."""
        return self._walk(concept, self._union(rels, up=True))

    def descendants(
        self, concept: RecordId, rels: tuple[str, ...] = BROADER
    ) -> dict[RecordId, int]:
        return self._walk(concept, self._union(rels, up=False))

    def equivalents(self, concept: RecordId) -> set[RecordId]:
        """`same_as` equivalence class (symmetric and transitive), including `concept`."""
        adj: dict[RecordId, set[RecordId]] = {}
        for a, others in self._out.get("same_as", {}).items():
            for b in others:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        return {concept, *self._walk(concept, adj)}

    def resolve(self, concept: RecordId) -> RecordId:
        """Follow `replaced_by` to the current concept (cycle-safe)."""
        seen = {concept}
        cur = concept
        while True:
            nxt = sorted(self._out.get("replaced_by", {}).get(cur, ()), key=str)
            if not nxt or nxt[0] in seen:
                return cur
            cur = nxt[0]
            seen.add(cur)

    def successors(self, concept: RecordId) -> list[RecordId]:
        """What a split concept became (empty when it was not split)."""
        return sorted(self._out.get("split_into", {}).get(concept, ()), key=str)

    def live(self, concept: RecordId) -> tuple[str, list[RecordId]]:
        """How a concept may be used by a new writer: `("ok", [it])`, `("redirect", [successor])`
        for a one-to-one replacement, `("split", [successors])` or `("gone", [])`."""
        c = self.concepts.get(concept)
        if c is None or c.status != "deprecated":
            return "ok", [concept]
        target = self.resolve(concept)
        if target != concept and self.concepts[target].status != "deprecated":
            return "redirect", [target]
        if self.successors(concept):
            return "split", self.successors(concept)
        return "gone", []

    # ---- entailment ---------------------------------------------------------

    def _up_closure(self, concept: RecordId) -> set[RecordId]:
        """The concept, its equivalents, and everything they are a kind of."""
        out: set[RecordId] = set()
        for c in self.equivalents(self.resolve(concept)):
            out.add(c)
            for a in self.ancestors(c):
                out.update(self.equivalents(a))
        return out

    def entails(self, observed: RecordId, required: RecordId) -> bool:
        """Does observing `observed` establish `required`? (`ubuntu` entails `linux`)"""
        return self.resolve(required) in {self.resolve(c) for c in self._up_closure(observed)}

    def disjoint(self, observed: RecordId, required: RecordId) -> bool:
        """Does observing `observed` positively rule out `required`?

        True when some kind-of-closure member of one is declared `disjoint_with` (or
        `alternative_to`) some kind-of-closure member of the other.
        """
        left = self._up_closure(observed)
        right = self._up_closure(required)
        for rel in ("disjoint_with", "alternative_to"):
            pairs = self._out.get(rel, {})
            for a in left:
                if pairs.get(a, set()) & right:
                    return True
            for b in right:
                if pairs.get(b, set()) & left:
                    return True
        return False

    # ---- recall expansion ---------------------------------------------------

    def expand(self, concept: RecordId) -> dict[RecordId, tuple[float, str]]:
        """Concepts whose memories are relevant when `concept` is mentioned: id → (weight,
        why). The concept itself and its equivalents weigh 1.0."""
        out: dict[RecordId, tuple[float, str]] = {}

        def put(c: RecordId, w: float, why: str) -> None:
            if c not in out or out[c][0] < w:
                out[c] = (w, why)

        start = self.resolve(concept)
        for eq in self.equivalents(start):
            put(eq, 1.0, "mentioned" if eq == concept else "same_as/replaced")
        put(concept, 1.0, "mentioned")
        for old in self._in.get("replaced_by", {}).get(start, ()):
            put(old, W_REPLACED, f"{old.id} was replaced by {start.id}")
        for old in self._in.get("split_into", {}).get(start, ()):  # what it was split from
            put(old, W_REPLACED, f"{start.id} was split out of {old.id}")
        for new in self._out.get("split_into", {}).get(start, ()):  # what a split became
            put(new, W_REPLACED, f"{start.id} was split into {new.id}")
        for d, dist in self.descendants(start).items():
            put(d, W_DESCENDANT**dist, f"{d.id} is a kind of {start.id}")
        for a, dist in self.ancestors(start).items():
            put(a, W_ANCESTOR**dist, f"{start.id} is a kind of {a.id}")
        for part in self.descendants(start, PARTWISE):
            put(part, W_PART, f"{part.id} is part of {start.id}")
        for alt in self._out.get("alternative_to", {}).get(start, ()):
            put(alt, W_ALTERNATIVE, f"{alt.id} is an alternative to {start.id}")
        for alt in self._in.get("alternative_to", {}).get(start, ()):
            put(alt, W_ALTERNATIVE, f"{alt.id} is an alternative to {start.id}")
        return out

    # ---- mentions -----------------------------------------------------------

    def find(self, term: str) -> list[RecordId]:
        """Concepts whose label or alias equals `term` (normalised), best match first."""
        return sorted(self._phrases.get(_norm(term), ()), key=str)

    def mentions(self, text: str) -> list[Mention]:
        """Concepts mentioned in free text by label/alias (longest phrase first, no overlap)."""
        words = _norm(text).split()
        taken = [False] * len(words)
        found: list[Mention] = []
        for n in range(min(self._max_words, len(words)), 0, -1):
            for i in range(len(words) - n + 1):
                if any(taken[i : i + n]):
                    continue
                phrase = " ".join(words[i : i + n])
                ids = self._phrases.get(phrase)
                if not ids:
                    continue
                for cid in sorted(ids, key=str):
                    found.append(Mention(cid, phrase))
                for j in range(i, i + n):
                    taken[j] = True
        return found

    def neighbours(self, concept: RecordId) -> list[tuple[str, str, RecordId]]:
        """All direct relations as (direction, relation, other)."""
        out: list[tuple[str, str, RecordId]] = []
        for rel, adj in sorted(self._out.items()):
            out += [("out", rel, o) for o in sorted(adj.get(concept, ()), key=str)]
        for rel, adj in sorted(self._in.items()):
            out += [("in", rel, o) for o in sorted(adj.get(concept, ()), key=str)]
        return out
