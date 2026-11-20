"""Ports for external model adapters. Implementations live in `ontyra.adapters`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ontyra.models.decision import Judgement
from ontyra.models.feedback import GateVerdict, RerankExample, TrainOptions, TrainResult


class Embedder(Protocol):
    """Turns text into vectors. `name` identifies model + settings (stored with each vector)."""

    @property
    def name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class Decider(Protocol):
    """A decision model: given a situation and candidate memories it answers, in one pass and
    with probabilities (no generated text), which of them matter. Optional; the answer is a
    signal blended into the ranking, never the last word. Raises `DecisionError`."""

    @property
    def name(self) -> str: ...

    async def judge(self, situation: str, memories: Sequence[str]) -> Judgement: ...


@runtime_checkable
class MessageClassifier(Protocol):
    """A model that says how likely a message is pure filler (a greeting, thanks or reaction that
    asks for nothing): a probability, in one pass, no generated text. Optional and advice only:
    a lookup is skipped for filler, never for a message that might ask for something. Raises
    `DecisionError`."""

    @property
    def name(self) -> str: ...

    async def filler(self, text: str) -> float: ...


@runtime_checkable
class StatementComparer(Protocol):
    """A model that says whether two memories state the same thing (only worded differently;
    different values or different facts mean no): a probability, in one pass, no generated
    text. Optional, and advice only. Raises `DecisionError`."""

    @property
    def name(self) -> str: ...

    async def same(self, a: str, b: str) -> float: ...


class Reranker(Protocol):
    """Scores candidate texts against a query (higher is more relevant), in input order."""

    async def rerank(self, query: str, texts: Sequence[str]) -> list[float]: ...


@runtime_checkable
class TrainableReranker(Reranker, Protocol):
    """A reranker that can be retrained from labelled pairs (hosted rerank APIs cannot).

    Training is explicit and user-triggered; versions are kept so a result can be activated or
    rolled back. Implementations decide whether a trained version passes their own gate.
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def versions(self) -> list[str]: ...

    async def train(
        self, examples: Sequence[RerankExample], options: TrainOptions
    ) -> TrainResult: ...

    async def activate(self, version: str) -> None: ...


@runtime_checkable
class PromotionGate(Protocol):
    """A check a retrained version must pass to stay active (for example: the benchmark does
    not regress). It runs with the candidate already active; if it fails the service restores
    `previous`. Feedback is small and noisy, so a version that fits it but breaks known cases
    must never reach production."""

    async def check(
        self, reranker: TrainableReranker, candidate: str, previous: str
    ) -> GateVerdict: ...
