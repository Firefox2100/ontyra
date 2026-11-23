"""Versions on disk: one directory each with a manifest, and an atomic pointer to the active one.

A version is the base model plus what was changed on top of it (a head file; later a LoRA
adapter). Versions are never deleted by the worker, so any of them can be activated again.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class Manifest:
    version: str
    base: str  # the configured model: a Hugging Face id or a directory
    architecture: str  # qwen3 | sequence
    created_at: str
    parent: str | None = None
    head: str | None = None  # file in the version directory that replaces the converted head
    adapter: str | None = None  # directory of a LoRA adapter (training)
    template: dict[str, str] = field(default_factory=lambda: {})
    metrics: dict[str, Any] = field(default_factory=lambda: {})
    note: str = ""


def _write_atomic(path: Path, text: str) -> None:
    """Write a file so a reader sees the old or the new content, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class VersionStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def _pointer(self) -> Path:
        return self.root / "active.json"

    def path(self, version: str) -> Path:
        return self.root / "versions" / version

    def list(self) -> list[Manifest]:
        found: list[Manifest] = []
        for manifest in sorted((self.root / "versions").glob("*/manifest.json")):
            found.append(Manifest(**json.loads(manifest.read_text("utf-8"))))
        return found

    def get(self, version: str) -> Manifest:
        return Manifest(**json.loads((self.path(version) / "manifest.json").read_text("utf-8")))

    def active_id(self) -> str | None:
        try:
            return json.loads(self._pointer.read_text("utf-8"))["version"]
        except (FileNotFoundError, KeyError, ValueError):
            return None

    def next_id(self) -> str:
        numbers = [int(m.version[1:]) for m in self.list() if m.version[1:].isdigit()]
        return f"v{max(numbers, default=0) + 1:04d}"

    def create(self, manifest: Manifest) -> Manifest:
        _write_atomic(
            self.path(manifest.version) / "manifest.json",
            json.dumps(asdict(manifest), indent=2, sort_keys=True),
        )
        return manifest

    def activate(self, version: str) -> None:
        self.get(version)  # must exist
        _write_atomic(self._pointer, json.dumps({"version": version}))

    def baseline(self, base: str, architecture: str) -> Manifest:
        """The unmodified version of `base` (what retraining starts from)."""
        for manifest in self.list():
            if (
                manifest.base == base
                and manifest.architecture == architecture
                and not (manifest.parent)
            ):
                return manifest
        raise KeyError(f"no baseline version of {base}")

    def ensure(self, base: str, architecture: str, template: dict[str, str]) -> Manifest:
        """The active version for `base`, creating the unmodified baseline when there is none.

        An active version of a different base is left on disk but not used: the operator changed
        the configured model, so serving starts from that model's own baseline.
        """
        active = self.active_id()
        if active is not None:
            current = self.get(active)
            if current.base == base and current.architecture == architecture:
                return current
        for manifest in reversed(self.list()):
            if (
                manifest.base == base
                and manifest.architecture == architecture
                and not (manifest.parent)
            ):
                self.activate(manifest.version)
                return manifest
        created = self.create(
            Manifest(
                version=self.next_id(),
                base=base,
                architecture=architecture,
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                template=template,
                note="baseline: the model as configured",
            )
        )
        self.activate(created.version)
        return created
