"""The score layer (plans/25, phase 1): bounded, replayable corrections to recall's final score
from one owner's own feedback. No model weights are touched, so it works with any reranker, a
hosted one, or none.

The state is a pure function of the feedback log (the events that still count, in any order) and
the recall traces they cite: `build` recomputes it from scratch, so there is nothing to store,
roll back or migrate, and removing an event (a veto, a retraction) simply changes what the next
build sees. Two corrections:

* a *prior* per memory: its decayed, trust-weighted net verdicts, squashed and capped;
* an *online blend*: a logistic fit of "was it relevant?" over the lane scores the traces kept
  (`FEATURES`) on top of the plain final score. The plain score enters with a fixed gain and the
  other coefficients start at zero, so an empty fit *is* the plain ranking; they are pulled back
  towards it (`l2`), clipped to `max_shift`, and the result is mixed in with a weight that grows
  with the evidence up to `max_mix`. No source may weigh more than `source_cap` of the fit.

A fixed share of the judged recalls (`is_canary`, a hash of the trace id: stable and needing no
storage) is never trained on and carries no prior. After each build the layer must rank those
recalls at least as well as the plain ranking, or it stays off (`reverted`).
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ontyra.models.base import RecordId
from ontyra.models.feedback import LayerState, LearningSettings
from ontyra.models.search import RecallTrace

FEATURES = ("lexical", "dense", "concept", "rerank", "decision")
GAIN = 6.0  # how strongly the plain final score drives the fitted one (a fixed, not fitted, term)
STEPS = 300
RATE = 1.0


@dataclass(frozen=True)
class Verdict:
    """One feedback event as the layer sees it."""

    memory: str
    relevance: float  # 1 relevant, 0 not
    weight: float  # trust of its source
    age_days: float
    source: str
    group: str | None = None  # the recall (trace) it judged, when known


@dataclass(frozen=True)
class Observation:
    """A judged candidate with the features its recall kept."""

    group: str
    memory: str
    source: str
    features: tuple[float, ...]
    base: float  # the plain final score the engine computed
    relevance: float
    weight: float


@dataclass
class Layer:
    weights: tuple[float, ...] = (0.0,) * len(FEATURES)
    bias: float = -GAIN / 2
    mix: float = 0.0
    priors: dict[str, float] = field(default_factory=lambda: {})
    active: bool = False

    def score(self, memory: str, features: Sequence[float], base: float) -> float:
        """The corrected final score of one candidate (the plain one when the layer is off)."""
        if not self.active:
            return base
        p = _sigmoid(
            self.bias
            + GAIN * base
            + sum(w * x for w, x in zip(self.weights, features, strict=True))
        )
        return (1 - self.mix) * base + self.mix * p + self.priors.get(memory, 0.0)


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def is_canary(group: str, fraction: float) -> bool:
    """Is this recall held out of training? Stable per trace, uniform, no state."""
    if fraction <= 0:
        return False
    h = int.from_bytes(hashlib.sha1(group.encode()).digest()[:4], "big") / 2**32
    return h < fraction


def vector(row: dict[str, Any]) -> tuple[float, ...]:
    """The features of a trace's candidate row (a lane that did not score counts as zero)."""
    return tuple(float(row.get(name) or 0.0) for name in FEATURES)


def priors(verdicts: Sequence[Verdict], cfg: LearningSettings) -> dict[str, float]:
    """Per-memory prior: decayed net verdicts, squashed to `max_prior`."""
    net: dict[str, float] = defaultdict(float)
    for v in verdicts:
        decay = 0.5 ** (v.age_days / cfg.half_life_days) if cfg.half_life_days > 0 else 1.0
        net[v.memory] += (2.0 * v.relevance - 1.0) * v.weight * decay
    return {m: cfg.max_prior * math.tanh(x / cfg.prior_scale) for m, x in net.items() if x}


def cap_shares(sources: Sequence[str], weights: Sequence[float], cap: float) -> list[float]:
    """Weights with no source above `cap` of the total (only when several sources contributed:
    one person's feedback is not diluted by itself)."""
    w = list(weights)
    if len(set(sources)) < 2 or cap >= 1.0:
        return w
    for _ in range(200):
        total = sum(w)
        share: dict[str, float] = defaultdict(float)
        for source, x in zip(sources, w, strict=True):
            share[source] += x
        over = {s: t for s, t in share.items() if t > cap * total * (1 + 1e-9)}
        if not over:
            break
        for i, source in enumerate(sources):
            if source in over:
                w[i] *= cap * total / over[source]
    return w


def capped(observations: Sequence[Observation], cap: float) -> list[float]:
    """Example weights of the observations with no source above `cap` of the total."""
    return cap_shares([o.source for o in observations], [o.weight for o in observations], cap)


def fit(
    observations: Sequence[Observation], cfg: LearningSettings
) -> tuple[tuple[float, ...], float]:
    """Fitted (coefficients, bias): batch gradient descent from the plain ranking, deterministic
    and independent of the order of the observations."""
    obs = sorted(observations, key=lambda o: (o.group, o.memory, o.source))
    w = [0.0] * len(FEATURES)
    b0 = -GAIN / 2
    b = b0
    weights = capped(obs, cfg.source_cap)
    total = sum(weights) or 1.0
    for _ in range(STEPS):
        gw = [0.0] * len(FEATURES)
        gb = 0.0
        for o, sw in zip(obs, weights, strict=True):
            z = b + GAIN * o.base + sum(c * x for c, x in zip(w, o.features, strict=True))
            err = (_sigmoid(z) - o.relevance) * sw / total
            gb += err
            for i, x in enumerate(o.features):
                gw[i] += err * x
        for i in range(len(w)):
            w[i] -= RATE * (gw[i] + cfg.l2 * w[i] / max(1.0, len(obs)))
            w[i] = max(-cfg.max_shift, min(cfg.max_shift, w[i]))
        b -= RATE * gb
        b = max(b0 - cfg.max_shift, min(b0 + cfg.max_shift, b))
    return tuple(w), b


def pairwise(groups: dict[str, list[tuple[float, float]]]) -> tuple[float, int]:
    """Mean over recalls of the share of (relevant, not relevant) pairs the scores order right
    (ties count half), and how many recalls had both kinds."""
    accuracies: list[float] = []
    for rows in groups.values():
        good = [s for s, r in rows if r >= 0.5]
        bad = [s for s, r in rows if r < 0.5]
        if not good or not bad:
            continue
        wins = sum(1.0 if g > n else 0.5 if g == n else 0.0 for g in good for n in bad)
        accuracies.append(wins / (len(good) * len(bad)))
    return (sum(accuracies) / len(accuracies) if accuracies else 0.0), len(accuracies)


def build(
    verdicts: Sequence[Verdict], observations: Sequence[Observation], cfg: LearningSettings
) -> tuple[LayerState, Layer]:
    """The layer for one owner, from scratch, and the account of what it did."""
    if not cfg.enabled:
        return LayerState(enabled=False, status="off", reason="the score layer is off"), Layer()
    frac = cfg.canary_fraction
    train = [o for o in observations if not is_canary(o.group, frac)]
    canary = [o for o in observations if is_canary(o.group, frac)]
    training_verdicts = [v for v in verdicts if v.group is None or not is_canary(v.group, frac)]
    prior = priors(training_verdicts, cfg)
    held: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for o in canary:
        held[o.group].append((o.base, o.relevance))
    base_acc, judged = pairwise(held)
    state = LayerState(
        enabled=True,
        status="learning",
        examples=len(train),
        groups=len({o.group for o in train}),
        canary_groups=judged,
        priors=len(prior),
    )
    if len(train) < cfg.min_examples:
        state.reason = f"{len(train)} of {cfg.min_examples} judged candidates collected"
        return state, Layer()
    if judged < cfg.canary_min_groups:
        state.reason = f"{judged} of {cfg.canary_min_groups} canary recalls collected"
        return state, Layer()
    weights, bias = fit(train, cfg)
    mix = cfg.max_mix * len(train) / (len(train) + cfg.mix_half)
    layer = Layer(weights, bias, mix, prior, active=True)
    after: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for o in canary:
        after[o.group].append((layer.score(o.memory, o.features, o.base), o.relevance))
    layer_acc, _ = pairwise(after)
    state.canary_base, state.canary_layer = round(base_acc, 4), round(layer_acc, 4)
    state.mix = round(mix, 4)
    state.coefficients = {n: round(c, 4) for n, c in zip(FEATURES, weights, strict=True)} | {
        "bias": round(bias, 4)
    }
    if layer_acc < base_acc - cfg.canary_tolerance:
        state.status = "reverted"
        state.reason = "the canary recalls rank worse with the layer; it stays off"
        return state, Layer()
    state.status = "active"
    return state, layer


class ScoreLayer:
    """The layer per owner, built from stored feedback and cached until feedback changes."""

    def __init__(
        self,
        store: Any,
        events: Callable[[RecordId], Any],
        cfg: LearningSettings,
        now: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.events = events  # owner -> awaitable list of that owner's active Feedback rows
        self.cfg = cfg
        self.now = now
        self._cache: dict[RecordId, tuple[datetime, LayerState, Layer]] = {}

    def invalidate(self, owner: RecordId | None = None) -> None:
        if owner is None:
            self._cache.clear()
        else:
            self._cache.pop(owner, None)

    async def state(self, owner: RecordId) -> tuple[LayerState, Layer]:
        if not self.cfg.enabled:
            return LayerState(enabled=False, status="off", reason="the score layer is off"), Layer()
        now = self.now()
        hit = self._cache.get(owner)
        if hit is not None and (now - hit[0]).total_seconds() < self.cfg.cache_seconds:
            return hit[1], hit[2]
        verdicts, observations = await self._collect(owner, now)
        state, layer = build(verdicts, observations, self.cfg)
        self._cache[owner] = (now, state, layer)
        return state, layer

    async def rescore(
        self, owner: RecordId, items: Sequence[tuple[str, Sequence[float], float]]
    ) -> list[float] | None:
        """Corrected final scores for `(memory, features, plain score)`, or None when the layer
        is not active for this owner (the plain scores stand)."""
        _, layer = await self.state(owner)
        if not layer.active:
            return None
        return [layer.score(m, f, s) for m, f, s in items]

    async def _collect(
        self, owner: RecordId, now: datetime
    ) -> tuple[list[Verdict], list[Observation]]:
        rows = await self.events(owner)
        traces = await self.store.get_many(list({f.trace for f in rows if f.trace}))
        verdicts: list[Verdict] = []
        observations: list[Observation] = []
        for f in rows:
            trace = traces.get(f.trace) if f.trace else None
            group = str(f.trace) if f.trace else None
            source = str(f.source)
            verdicts.append(
                Verdict(
                    str(f.memory),
                    f.relevance,
                    f.weight,
                    max(0.0, (now - f.created_at).total_seconds() / 86400),
                    source,
                    group,
                )
            )
            if not isinstance(trace, RecallTrace) or f.revision is None or group is None:
                continue
            for row in (trace.scoring or {}).get("candidates", []):
                if row.get("rev") == str(f.revision):
                    base = row.get("base", row.get("final")) or 0.0
                    observations.append(
                        Observation(
                            group,
                            str(f.memory),
                            source,
                            vector(row),
                            float(base),
                            f.relevance,
                            f.weight,
                        )
                    )
                    break
        return verdicts, observations
