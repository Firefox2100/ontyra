"""What a run remembers on disk, so an interrupted or repeated run costs nothing it already paid.

Per document: the model's answers per chunk and which proposals were accepted. Across documents:
the content hash last read at each source URI, to notice that a source changed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def short(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]


class RunState:
    """One document under one configuration."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"chunks": {}, "submitted": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    @property
    def chunks(self) -> dict[str, Any]:
        return self.data["chunks"]

    @property
    def submitted(self) -> dict[str, str]:
        return self.data["submitted"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run(self, key: str) -> RunState:
        return RunState(self.root / "runs" / f"{key}.json")

    def _sources(self) -> dict[str, str]:
        path = self.root / "sources.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def last_hash(self, uri: str) -> str | None:
        return self._sources().get(uri)

    def remember_hash(self, uri: str, content_hash: str) -> None:
        sources = self._sources()
        sources[uri] = content_hash
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

    def rejected_log(self, key: str) -> Path:
        return self.root / "rejected" / f"{key}.jsonl"
