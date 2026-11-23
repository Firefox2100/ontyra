"""The live tier: a small, bounded edit of the head from feedback, replayed from the snapshot.

The served head is the active version's own head plus a delta. The delta is a pure function of
(the version's head, the examples it is given): every update refits it from zero, so removing an
example (a veto, a retraction) simply gives a fit without it, and nothing accumulates. The
backbone is never touched, so the last hidden states of the judged texts are computed once and
cached: refitting the head is a small logistic regression on them.

Bounds: the delta's norm may not exceed `max_distance` of the head's own norm; it is pulled
towards zero (`l2`); and after fitting, a trusted set of requests (the gate) is scored with and
without it: if the edit ranks them worse than the tolerance allows it is not applied.
"""

from __future__ import annotations

import hashlib
import importlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ontyra.worker.model import TorchScorer
from ontyra.worker.training import Example, class_weights, mrr_and_accuracy

CACHE_ROWS = 20000


@dataclass
class LiveOptions:
    steps: int = 200
    learning_rate: float = 0.05
    l2: float = 1.0  # pull of the delta towards zero, relative to the data term
    max_distance: float = 0.25  # largest norm of the delta, relative to the head's norm
    tolerance: float = 0.01  # what the edit may lose on the gate requests and still apply
    seed: int = 13


def key(version: str, query: str, text: str) -> str:
    return hashlib.sha1(f"{version}\0{query}\0{text}".encode()).hexdigest()


class Features:
    """Hidden states of (query, text) pairs for the active version, computed once."""

    def __init__(self) -> None:
        self._rows: OrderedDict[str, Any] = OrderedDict()

    def __len__(self) -> int:
        return len(self._rows)

    def of(self, scorer: TorchScorer, pairs: Sequence[tuple[str, str]]) -> Any:
        torch: Any = importlib.import_module("torch")
        version = scorer.manifest.version
        missing = [p for p in dict.fromkeys(pairs) if key(version, *p) not in self._rows]
        if missing:
            got = scorer.hidden(missing)
            for pair, row in zip(missing, got, strict=True):
                self._rows[key(version, *pair)] = row
            while len(self._rows) > CACHE_ROWS:
                self._rows.popitem(last=False)
        for pair in pairs:
            self._rows.move_to_end(key(version, *pair))
        return torch.stack([self._rows[key(version, *p)] for p in pairs])


def fit(
    hidden: Any,
    labels: Sequence[float],
    weights: Sequence[float],
    head: Any,
    bias: Any,
    options: LiveOptions,
) -> tuple[Any, Any, float]:
    """Delta (weight, bias) and its final loss: weighted logistic loss on the hidden states plus
    an l2 pull to zero, norm-bounded after every step. Full batch, so it is deterministic."""
    torch: Any = importlib.import_module("torch")
    torch.manual_seed(options.seed)
    y = torch.tensor(list(labels), dtype=torch.float32)
    w = torch.tensor(list(weights), dtype=torch.float32)
    w = w / w.sum()
    dw = torch.zeros_like(head, requires_grad=True)
    db = torch.zeros((), requires_grad=True)
    optimizer = torch.optim.Adam([dw, db], lr=options.learning_rate)
    limit = options.max_distance * float(head.norm())
    loss = torch.zeros(())
    for _ in range(options.steps):
        logits = hidden @ (head + dw) + bias + db
        data = (
            torch.nn.functional.binary_cross_entropy_with_logits(logits, y, reduction="none") * w
        ).sum()
        loss = data + options.l2 * 0.5 * (dw.pow(2).sum() + db.pow(2)) / max(
            1.0, float(head.norm()) ** 2
        )
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            size = float((dw.pow(2).sum() + db.pow(2)).sqrt())
            if size > limit > 0:
                dw.mul_(limit / size)
                db.mul_(limit / size)
    return dw.detach(), db.detach(), float(loss.detach())


def update(
    scorer: TorchScorer,
    features: Features,
    examples: Sequence[Example],
    gate: Sequence[dict[str, Any]],
    options: LiveOptions,
) -> dict[str, Any]:
    """Refit the live edit from the snapshot head and the given examples, and serve it unless the
    gate says it ranks the trusted requests worse. Returns what happened."""
    torch: Any = importlib.import_module("torch")
    if scorer.manifest.architecture != "qwen3":
        raise ValueError("a live head edit is supported for Qwen3-style rerankers")
    scorer.load()
    head, bias = (t.detach().cpu().float() for t in scorer.snapshot())
    if not examples:
        scorer.set_live(None, None)
        return {"applied": False, "reverted": False, "distance": 0.0, "examples": 0, "loss": None}
    hidden = features.of(scorer, [(e.query, e.text) for e in examples])
    dw, db, loss = fit(
        hidden,
        [e.relevance for e in examples],
        class_weights(examples),
        head,
        bias,
        options,
    )
    result: dict[str, Any] = {"examples": len(examples), "loss": round(loss, 4)}
    if gate:
        pairs = [(g["query"], c["text"]) for g in gate for c in g["candidates"]]
        h = features.of(scorer, pairs)
        logit_of = {p: float(x) for p, x in zip(pairs, (h @ head + bias).tolist(), strict=True)}
        edited = {
            p: float(x) for p, x in zip(pairs, (h @ (head + dw) + bias + db).tolist(), strict=True)
        }

        def lookup(
            table: dict[tuple[str, str], float],
        ) -> Callable[[Sequence[tuple[str, str]]], list[float]]:
            def scores(ps: Sequence[tuple[str, str]]) -> list[float]:
                return [table[p] for p in ps]

            return scores

        before = mrr_and_accuracy(gate, lookup(logit_of))
        after = mrr_and_accuracy(gate, lookup(edited))
        result |= {"gate_before": before, "gate_after": after}
        if any(after[k] < before[k] - options.tolerance for k in ("mrr", "pairwise")):
            scorer.set_live(None, None)
            return result | {"applied": False, "reverted": True, "distance": 0.0}
    scorer.set_live(dw, db)
    distance = float(torch.sqrt(dw.pow(2).sum() + db.pow(2)) / head.norm())
    return result | {"applied": True, "reverted": False, "distance": round(distance, 4)}
