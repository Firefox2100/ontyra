"""Play a case against a loaded world (`ontyra.calibration.world.populate` loads one)."""

from __future__ import annotations

import time

from benchmark_test.framework.score import StepScore, score_step
from ontyra.app import Application
from ontyra.calibration.models import Case, Step
from ontyra.calibration.probe import Probe
from ontyra.models.recall import RecallRequest
from ontyra.services import Caller


async def play(
    app: Application,
    caller: Caller,
    case: Case,
    keys: dict[str, str],
    domains: dict[str, str] | None = None,
    probe: Probe | None = None,
    universal: frozenset[str] = frozenset(),
    twins: dict[str, str] | None = None,
) -> list[StepScore]:
    """Run the case's first request and its `then` requests (one session when it has any)."""
    session = case.id if case.then else None
    scores: list[StepScore] = []
    for step in [case, *case.then]:
        scores.append(
            await _one(app, caller, step, keys, session, domains, probe, universal, twins)
        )
    return scores


async def _one(
    app: Application,
    caller: Caller,
    step: Step,
    keys: dict[str, str],
    session: str | None,
    domains: dict[str, str] | None,
    probe: Probe | None,
    universal: frozenset[str] = frozenset(),
    twins: dict[str, str] | None = None,
) -> StepScore:
    context = step.context.model_copy(update={"session": session or step.context.session})
    rows = await probe.rows(step, session) if probe is not None else None
    started = time.perf_counter()
    verdict: str | None = None
    if step.mode == "check":
        checked = await app.recall.check(caller, step.query or "", context)
        pack, verdict = checked.pack, checked.verdict
    else:
        request = RecallRequest(context=context, query=step.query, hints=step.hints)
        run = app.recall.recall if step.mode == "recall" else app.recall.briefing
        pack = await run(caller, request)
    ms = (time.perf_counter() - started) * 1000
    scored = score_step(
        step,
        pack,
        keys,
        verdict=verdict,
        ms=ms,
        domains=domains,
        universal=universal,
        twins=twins,
    )
    scored.rows = rows
    return scored
