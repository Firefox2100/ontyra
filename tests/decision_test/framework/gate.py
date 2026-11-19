"""Task `gate`: does this message need the conversation so far, or is it chit-chat?

A harness sends the user's latest message as the recall query, but many messages cannot be
understood alone ("and who set that up?"), and a pleasantry needs no recall at all. A gate asks
the decision model, once per prompt, to sort the message into `standalone`, `refers_back` or
`filler`; the hook then widens the query with the previous user message, or skips the lookup.

Items are `data/gate.json` (hand-written, with a history) plus every turn of the benchmark's
dialogues (labelled `refers_back` when the turn has `needs_history`, otherwise `standalone`).
Measured: classification quality of each variant against two baselines (the `is_thin` regex the
`adaptive` strategy uses, and always guessing `standalone`), and, end to end, what the gate does
to recall when it chooses the query (`gate_builder`, played by the dialogue runner).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from benchmark_test.framework.dialogue import (
    Built,
    Dialogue,
    Turn,
    build_request,
    is_thin,
)
from decision_test.framework.client import Kev
from decision_test.framework.metrics import auc, best_threshold, confusion, prf, rounded
from ontyra.adapters.decider import clip
from ontyra.models.recall import RecallContext

DATA = Path(__file__).resolve().parents[1] / "data"
Label = Literal["standalone", "refers_back", "filler"]
LABELS: tuple[Label, ...] = ("standalone", "refers_back", "filler")


class Exchange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    say: str
    reply: str


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    realm: str
    label: Label
    history: list[Exchange]
    say: str
    why: str = ""


def load_items() -> list[Item]:
    return [Item.model_validate(raw) for raw in json.loads((DATA / "gate.json").read_text("utf-8"))]


def dialogue_items(dialogues: list[Dialogue]) -> list[Item]:
    """Every turn of the benchmark's dialogues, with the turns before it as history."""
    out: list[Item] = []
    for d in dialogues:
        realm = next(
            (t for t in d.tags if t in ("dev", "office", "research", "daily", "hobby")), "mixed"
        )
        for i, turn in enumerate(d.turns):
            if not turn.say or turn.mode != "recall":
                continue
            out.append(
                Item(
                    id=f"{d.id}#{i + 1}",
                    realm=realm,
                    label="refers_back" if turn.needs_history else "standalone",
                    history=[Exchange(say=t.say, reply=t.reply) for t in d.turns[:i] if t.say][-2:],
                    say=turn.say,
                )
            )
    return out


def state_of(history: list[tuple[str, str]], say: str, *, turns: int, chars: int = 200) -> str:
    lines = [
        f"Earlier in the conversation:\nUser: {clip(s, chars)}\nAssistant: {clip(r, chars)}"
        for s, r in history[-turns:]
        if s
    ]
    lines.append(f"User's new message: {say}")
    return (
        "\n".join(lines) if len(lines) > 1 else lines[0] + "\n(There is no earlier conversation.)"
    )


KIND3 = "What kind of message is the user's new message?"
OPTIONS3 = {
    "standalone": "A request or question that can be understood on its own, without the earlier conversation.",
    "refers_back": "It only makes sense with the earlier conversation: a pronoun, an ellipsis, 'that', 'it', 'and below that?', 'ok, go ahead'.",
    "filler": "A greeting, thanks or reaction that asks for nothing ('thanks', 'ok', 'lol').",
}
KIND2 = "Can the user's new message be understood without the earlier conversation?"
OPTIONS2 = {
    "standalone": "Yes: it names what it is about and can be answered on its own.",
    "refers_back": "No: it leans on the earlier conversation (a pronoun, an ellipsis, a bare yes).",
}
BACK = (
    "Does the user's new message depend on the earlier conversation to be understood "
    "(a pronoun, an ellipsis, 'that', 'it', 'yes')?"
)

Verdict = dict[str, float]  # label -> probability (missing labels are 0)
Classifier = Callable[[list[tuple[str, str]], str], Awaitable[Verdict]]


def classifiers(kev: Kev, turns: int = 1) -> dict[str, Classifier]:
    async def choice3(history: list[tuple[str, str]], say: str) -> Verdict:
        return await kev.choice(state_of(history, say, turns=turns), KIND3, OPTIONS3)

    async def choice2(history: list[tuple[str, str]], say: str) -> Verdict:
        p = await kev.choice(state_of(history, say, turns=turns), KIND2, OPTIONS2)
        return {**p, "filler": 0.0}

    async def noul(history: list[tuple[str, str]], say: str) -> Verdict:
        back = await kev.yes(state_of(history, say, turns=turns), BACK)
        return {"refers_back": back, "standalone": 1 - back, "filler": 0.0}

    return {f"choice3-h{turns}": choice3, f"choice2-h{turns}": choice2, f"noul-h{turns}": noul}


def regex_verdict(history: list[tuple[str, str]], say: str) -> Verdict:
    """The heuristic the `adaptive` strategy uses, as a baseline."""
    thin = is_thin(say) and bool(history)
    return {"refers_back": float(thin), "standalone": float(not thin), "filler": 0.0}


def predicted(verdict: Verdict) -> str:
    return max(LABELS, key=lambda k: (verdict.get(k, 0.0), k == "standalone"))


def score(items: list[Item], verdicts: list[Verdict], *, threshold: float = 0.5) -> dict[str, Any]:
    actual = [i.label for i in items]
    back = [v.get("refers_back", 0.0) for v in verdicts]
    is_back = [a == "refers_back" for a in actual]
    guesses = [predicted(v) for v in verdicts]
    at = prf([b >= threshold for b in back], is_back)
    best = best_threshold(back, is_back)
    filler = [v.get("filler", 0.0) for v in verdicts]
    return {
        "items": len(items),
        "accuracy": rounded(sum(g == a for g, a in zip(guesses, actual, strict=True)) / len(items)),
        "auc_refers_back": rounded(
            auc(
                [b for b, x in zip(back, is_back, strict=True) if x],
                [b for b, x in zip(back, is_back, strict=True) if not x],
            )
        ),
        "at_threshold": {"threshold": threshold, **{k: rounded(float(v)) for k, v in at.items()}},
        "best_cutoff": {k: rounded(v) for k, v in best.items()},
        "auc_filler": rounded(
            auc(
                [f for f, a in zip(filler, actual, strict=True) if a == "filler"],
                [f for f, a in zip(filler, actual, strict=True) if a != "filler"],
            )
        ),
        "confusion": confusion(guesses, actual),
        "by_realm": {
            r: rounded(
                sum(g == a for g, a, i in zip(guesses, actual, items, strict=True) if i.realm == r)
                / sum(i.realm == r for i in items)
            )
            for r in sorted({i.realm for i in items})
        },
        "wrong": [
            {"id": i.id, "say": i.say, "label": i.label, "guess": g, "p_back": rounded(b, 3)}
            for i, g, b in zip(items, guesses, back, strict=True)
            if g != i.label
        ],
    }


def gate_builder(
    classify: Classifier,
    *,
    widen: Literal["users2", "union"] = "users2",
    threshold: float = 0.5,
    cache: dict[tuple[tuple[tuple[str, str], ...], str], Verdict] | None = None,
) -> Callable[[list[tuple[str, str]], Turn, RecallContext], Awaitable[Built]]:
    """A query builder for the dialogue runner: when the classifier says the message refers
    back, the query is widened with the previous user message (`users2`) or both are searched
    and merged (`union`: this message alone, then widened); anything else is sent as it is.
    Verdicts are cached by (history, message), so two strategies share the model's answers."""
    memo = {} if cache is None else cache

    async def build(history: list[tuple[str, str]], turn: Turn, context: RecallContext) -> Built:
        if turn.mode != "recall" or not history:
            return build_request("last", history, turn)
        key = (tuple(history[-2:]), turn.say)
        if key not in memo:
            memo[key] = await classify(history, turn.say)
        if memo[key].get("refers_back", 0.0) < threshold:
            return build_request("last", history, turn)
        wide = build_request("users2", history, turn)
        return wide if widen == "users2" else Built(turn.say, [], also=(wide,))

    return build
