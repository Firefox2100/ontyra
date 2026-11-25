"""Review helpers: which ops depend on which, and what an op subset needs.

An op depends on another when it names the `ref` the other gives to a new memory (`supersede.new`,
`link.source|target`, `derived_from`). Approving an op therefore needs the ops it depends on, and
rejecting an op takes everything that needs it with it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ontyra.services import errors


def refs(ops: list[dict[str, Any]]) -> dict[str, int]:
    """`ref` -> number of the op that defines it (only `add` ops define refs)."""
    return {
        str(op["ref"]): i for i, op in enumerate(ops) if op.get("op") == "add" and op.get("ref")
    }


def _mentioned(op: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("new", "target", "source", "memory"):
        value = op.get(key)
        if isinstance(value, str):
            found.append(value)
    found += [v for v in op.get("derived_from", []) if isinstance(v, str)]
    return found


def depends_on(ops: list[dict[str, Any]]) -> dict[int, set[int]]:
    """For each op number, the numbers of the ops whose `ref` it uses."""
    known = refs(ops)
    return {
        i: {known[m] for m in _mentioned(op) if m in known and known[m] != i}
        for i, op in enumerate(ops)
    }


def needs(ops: list[dict[str, Any]], chosen: Iterable[int]) -> set[int]:
    """`chosen` plus everything it depends on, transitively."""
    graph = depends_on(ops)
    out: set[int] = set()
    todo = list(chosen)
    while todo:
        i = todo.pop()
        if i in out:
            continue
        out.add(i)
        todo += graph.get(i, ())
    return out


def needed_by(ops: list[dict[str, Any]], chosen: Iterable[int]) -> set[int]:
    """`chosen` plus every op that depends on it, transitively."""
    graph = depends_on(ops)
    out = set(chosen)
    grew = True
    while grew:
        grew = False
        for i, deps in graph.items():
            if i not in out and deps & out:
                out.add(i)
                grew = True
    return out


def check_numbers(ops: list[dict[str, Any]], numbers: Iterable[int]) -> list[int]:
    wanted = sorted(set(numbers))
    bad = [n for n in wanted if not 0 <= n < len(ops)]
    if bad:
        raise errors.InvalidRequestError(
            f"no such op number(s) {bad}: the proposal has {len(ops)} op(s), numbered from 0"
        )
    return wanted


def summary(op: dict[str, Any]) -> str:
    """One line for a decision record or a comment."""
    fields: dict[str, Any] = op.get("fields") or {}
    text = fields.get("text") if op.get("op") == "add" else None
    target = op.get("memory") or op.get("target")
    return f"{op.get('op')} {text or target or ''}".strip()[:120]
