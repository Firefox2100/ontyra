"""The worker's own administration: versions, retraining and switching the active version."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from ontyra.worker import live, training
from ontyra.worker.engine import Engine
from ontyra.worker.model import HEAD_FILE, TorchScorer
from ontyra.worker.probe import Capabilities
from ontyra.worker.versions import Manifest, VersionStore


class TrainingRefused(Exception):
    """The worker cannot or will not train now (the reason is the message)."""


class Admin:
    def __init__(
        self, store: VersionStore, scorer: TorchScorer, engine: Engine, caps: Capabilities
    ) -> None:
        self.store = store
        self.scorer = scorer
        self.engine = engine
        self.caps = caps
        self.features = live.Features()

    @property
    def manifest(self) -> Manifest:
        return self.scorer.manifest

    def versions(self) -> dict[str, Any]:
        return {
            "active": self.manifest.version,
            "versions": [asdict(m) for m in self.store.list() if m.base == self.manifest.base],
        }

    async def train(
        self,
        examples: Sequence[training.Example],
        gate: Sequence[dict[str, Any]],
        options: training.Options,
    ) -> dict[str, Any]:
        """Retrain from the baseline of the configured model; the result is a new version, not
        activated. Serving answers busy meanwhile (callers fall back to their plain ranking)."""
        if not self.caps.train:
            raise TrainingRefused(self.caps.reasons.get("train", "training is not available"))
        active = self.manifest
        baseline = self.store.baseline(active.base, active.architecture)
        version = self.store.next_id()
        out = self.store.path(version)
        async with self.engine.lease():

            def work() -> dict[str, Any]:
                try:
                    if active.version != baseline.version:  # start from the base, not the last
                        self.scorer.release()
                        self.scorer.manifest = baseline
                    return training.train(self.scorer, examples, gate, options, out)
                finally:
                    self.scorer.release()  # the next request loads the active version again
                    self.scorer.manifest = active

            metrics = await self.engine.run_exclusive(work)
        manifest = self.store.create(
            Manifest(
                version=version,
                base=active.base,
                architecture=active.architecture,
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                parent=baseline.version,
                head=HEAD_FILE,
                adapter=None if options.head_only else "adapter",
                template=baseline.template,
                metrics=metrics,
                note=f"{len(examples)} examples",
            )
        )
        return {"version": manifest.version, "metrics": metrics}

    def live_status(self) -> dict[str, Any]:
        return {
            "enabled": self.caps.live_edit,
            "version": self.manifest.version,
            "distance": round(self.scorer.live_distance(), 4),
            "cached_rows": len(self.features),
            "reason": self.caps.reasons.get("live_edit", ""),
        }

    async def live_update(
        self,
        examples: Sequence[training.Example],
        gate: Sequence[dict[str, Any]],
        options: live.LiveOptions,
    ) -> dict[str, Any]:
        """Refit the live head edit from the snapshot and these examples (a replay), and serve it
        unless it ranks the trusted requests worse. Serving carries on in between."""
        if not self.caps.live_edit:
            raise TrainingRefused(self.caps.reasons.get("live_edit", "live edits are off"))
        if self.engine.leased:
            raise TrainingRefused("the model is busy")
        scorer, features = self.scorer, self.features
        return await self.engine.run_exclusive(
            lambda: live.update(scorer, features, examples, gate, options)
        )

    async def live_reset(self) -> dict[str, Any]:
        """Drop the live edit: the version's own head serves again."""
        scorer = self.scorer
        await self.engine.run_exclusive(lambda: scorer.set_live(None, None))
        return self.live_status()

    async def activate(self, version: str) -> Manifest:
        target = self.store.get(version)
        if target.base != self.manifest.base:
            raise KeyError(f"version {version} belongs to another model")
        async with self.engine.lease():

            def swap() -> None:
                self.scorer.release()
                self.scorer.manifest = target
                self.store.activate(version)
                self.scorer.load()

            await self.engine.run_exclusive(swap)
        return target
