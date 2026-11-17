"""What a decision model says about a request (the port's result type). No I/O."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, FiniteFloat

from ontyra.models.base import Model

Probability = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]


class Judgement(Model):
    """A decision model's verdict on candidate memories for one situation.

    `scores[i]` is the probability that candidate `i` is the one that matters (a `choice`
    answer: the probabilities of the candidates and of `none` add up to about 1) or that it
    applies (a `noul` answer: independent probabilities); `None` when the model did not score
    it. `none` is the probability that nothing applies, when the format offers that outcome.
    """

    scores: list[Probability | None]
    none: Probability | None = None
    model: str | None = None
