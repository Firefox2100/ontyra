"""Dialogues: scripted conversations played against a world, one recall per user turn.

A real harness sends the user's latest message as the recall query. Many messages cannot be
understood alone ("yes, do it", "and what went wrong the last time we bumped it?"), so the
suite asks how the request should be built from the conversation so far. Each `strategy` builds
the query (and hints) differently; the same dialogues are played once per strategy and scored
turn by turn, so the strategies can be compared. Strategies use only what a hook could send
today (previous messages); `oracle` uses the hand-written `standalone` query of a turn and is
the upper bound.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from benchmark_test.framework.models import Case, Step, Strict
from benchmark_test.framework.score import SECTIONS, StepScore, score_step
from ontyra.app import Application
from ontyra.models.recall import RecallContext, RecallPack, RecallRequest
from ontyra.services import Caller
from ontyra.services.text import STOPWORDS, content_tokens

STRATEGIES = ("last", "users2", "users3", "window", "hints", "adaptive", "union", "oracle")
WINDOW_CHARS = 600
HINT_WORDS = 8


class Turn(Strict):
    """One user message, the assistant's reply to it, and what the recall for it must return.

    A turn without any expectation only adds to the history. `needs_history`: the message
    cannot be understood without earlier turns (a pronoun, an ellipsis, a bare "yes"); such a
    turn must carry a `standalone` query that says the same thing alone (used by `oracle`).
    """

    say: str = ""
    reply: str = ""
    mode: Literal["recall", "briefing", "check"] = "recall"
    context: RecallContext | None = None  # replaces the dialogue's context from this turn on
    redeliver: bool = False  # as after a compaction: ignore the session ledger
    needs_history: bool = False
    standalone: str | None = None
    include: list[str] = []
    exclude: list[str] = []
    allow: list[str] = []
    allow_prefix: list[str] = []
    silent: bool = False
    strict: bool = False
    top: int | None = None

    @property
    def scored(self) -> bool:
        return bool(self.include or self.exclude or self.silent)


class Dialogue(Strict):
    id: str
    world: str
    tags: list[str]  # realm (dev, office, research, daily, hobby) and conversation shapes
    why: str
    split: Literal["tune", "holdout"] = "tune"
    context: RecallContext = RecallContext()
    session: bool = False  # one session for the whole dialogue (enables the no-repeat ledger)
    turns: list[Turn]


def _words(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in re.findall(r"[\w]+", text.casefold()):
        if w not in STOPWORDS and len(w) > 2:
            seen.setdefault(w)
    return list(seen)


@dataclass
class Built:
    query: str | None
    hints: list[str]
    also: tuple[Built, ...] = ()  # further requests whose packs are merged with this one's


THIN_WORDS = 6  # a message with fewer content words than this is "thin"
BACK_REFERENCE = re.compile(
    r"\b(it|its|that|this|those|these|there|them|they|he|she|him|her|one|ones|same|again|"
    r"yes|yeah|ok|okay|sure|ahead|instead)\b|^(and|but|so|then|also)\b",
    re.IGNORECASE,
)


def is_thin(say: str) -> bool:
    """Short, or pointing back at something ("it", "that", "yes", "and the title?")."""
    return len(content_tokens(say)) < THIN_WORDS or bool(BACK_REFERENCE.search(say.strip()))


def build_request(strategy: str, history: list[tuple[str, str]], turn: Turn) -> Built:
    """The query and hints for `turn`, given the earlier `(say, reply)` pairs."""
    if turn.mode == "briefing":
        return Built(None, [])
    say = turn.say
    if strategy in ("last", "union"):  # union: this request first, `users2` is merged in later
        return Built(say, [])
    if strategy in ("users2", "users3"):
        n = int(strategy[-1]) - 1
        return Built(" ".join([*(s for s, _ in history[-n:]), say]), [])
    if strategy == "window":
        parts = [p for s, r in history[-2:] for p in (s, r) if p] + [say]
        return Built(" ".join(parts)[-WINDOW_CHARS:], [])
    if strategy == "hints":
        if not history:
            return Built(say, [])
        previous = " ".join(history[-1])
        own = set(_words(say))
        return Built(say, [w for w in _words(previous) if w not in own][:HINT_WORDS])
    if strategy == "adaptive":
        return build_request("users2" if is_thin(say) else "last", history, turn)
    if strategy == "oracle":
        return Built(turn.standalone or say, [])
    raise ValueError(f"unknown strategy {strategy!r}")


def merge_packs(packs: list[RecallPack]) -> RecallPack:
    """The first pack plus the items only later packs returned (a union of the results)."""
    merged = packs[0]
    seen = {str(i.memory) for sec in SECTIONS for i in getattr(merged, sec)}
    for other in packs[1:]:
        extra = {
            sec: [i for i in getattr(other, sec) if str(i.memory) not in seen] for sec in SECTIONS
        }
        merged = merged.model_copy(
            update={sec: [*getattr(merged, sec), *items] for sec, items in extra.items()}
        )
        seen |= {str(i.memory) for items in extra.values() for i in items}
    return merged


Builder = Callable[[list[tuple[str, str]], Turn, RecallContext], Awaitable[Built]]


async def play_dialogue(
    app: Application,
    caller: Caller,
    dialogue: Dialogue,
    strategy: str,
    keys: dict[str, str],
    domains: dict[str, str],
    universal: frozenset[str],
    twins: dict[str, str],
    builder: Builder | None = None,
) -> list[tuple[Case, StepScore]]:
    """Play every turn; returns one `(case, score)` per scored turn (id `dialogue#turn`).

    `builder` replaces the named strategy (a model that writes the query, for example); it gets
    the earlier `(say, reply)` pairs, the turn and the context.
    """
    session = f"{dialogue.id}:{strategy}" if dialogue.session else None
    context = dialogue.context
    history: list[tuple[str, str]] = []
    out: list[tuple[Case, StepScore]] = []
    for number, turn in enumerate(dialogue.turns, 1):
        context = turn.context or context
        built = (
            await builder(history, turn, context)
            if builder
            else build_request(strategy, history, turn)
        )
        sent = context.model_copy(update={"session": session})
        if turn.mode == "check":
            checked = await app.recall.check(caller, built.query or "", sent)
            pack, verdict = checked.pack, checked.verdict
        else:
            run = app.recall.recall if turn.mode == "recall" else app.recall.briefing
            queries = [built, *built.also]
            if strategy == "union" and turn.mode == "recall":
                queries = [
                    build_request("last", history, turn),
                    build_request("users2", history, turn),
                ]
            packs = [
                await run(
                    caller,
                    RecallRequest(
                        context=sent, query=q.query, hints=q.hints, redeliver=turn.redeliver
                    ),
                )
                for q in queries
            ]
            pack, verdict = merge_packs(packs), None
            built = queries[0]
        if turn.scored:
            step = Step(
                mode=turn.mode,
                query=built.query,
                context=sent,
                hints=built.hints,
                **turn.model_dump(
                    include={
                        "include",
                        "exclude",
                        "allow",
                        "allow_prefix",
                        "silent",
                        "strict",
                        "top",
                    }
                ),
            )
            case = Case(
                id=f"{dialogue.id}#{number}",
                world=dialogue.world,
                tags=[*dialogue.tags, "needs_history" if turn.needs_history else "standalone"],
                split=dialogue.split,
                why=dialogue.why,
            )
            out.append(
                (
                    case,
                    score_step(
                        step,
                        pack,
                        keys,
                        verdict=verdict,
                        domains=domains,
                        universal=universal,
                        twins=twins,
                    ),
                )
            )
        history.append((turn.say, turn.reply))
    return out


def load_dialogues() -> list[Dialogue]:
    import json

    from benchmark_test.framework.models import DATA

    found: list[Dialogue] = []
    for path in sorted((DATA / "cases" / "dialogue").glob("*.json")):
        found += [Dialogue.model_validate(raw) for raw in json.loads(path.read_text("utf-8"))]
    return found
