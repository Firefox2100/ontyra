from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from ontyra_ingest.config import Settings
from ontyra_ingest.daemon import Daemon
from ontyra_ingest.llm import Llm


class FakeDaemon:
    """An in-memory stand-in for the daemon's REST API, behind an httpx mock transport."""

    def __init__(self) -> None:
        self.concepts: dict[str, str] = {}  # label.casefold() -> id
        self.proposals: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.decision = 201
        self.reject_containing: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/concepts" and method == "GET":
            term = request.url.params.get("term", "").casefold()
            hit = self.concepts.get(term)
            return httpx.Response(200, json=[{"id": hit}] if hit else [])
        if path == "/v1/concepts" and method == "POST":
            label = json.loads(request.content)["label"]
            cid = "concept:" + "_".join(label.casefold().split())
            self.concepts[label.casefold()] = cid
            return httpx.Response(200, json={"id": cid})
        if path == "/v1/proposals":
            body = json.loads(request.content)
            if self.reject_containing and self.reject_containing in json.dumps(body):
                return httpx.Response(
                    422, json={"proposal": "proposal:x", "findings": [{"code": "bad_source"}]}
                )
            self.proposals.append(body)
            return httpx.Response(
                self.decision, json={"proposal": f"proposal:{len(self.proposals)}"}
            )
        if path == "/v1/sources/check":
            self.checks.append(json.loads(request.content))
            return httpx.Response(200, json={"tasks": 2})
        return httpx.Response(404, json={"detail": path})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path / "state",
        chunk_chars=600,
        overlap_chars=0,
        concepts="existing",
        llm_retries=0,
        url="http://daemon",
    )


@pytest.fixture
def fake_daemon() -> FakeDaemon:
    return FakeDaemon()


@pytest.fixture
def daemon(settings: Settings, fake_daemon: FakeDaemon) -> Daemon:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(fake_daemon.handle), base_url="http://daemon"
    )
    return Daemon(settings, client)


@pytest.fixture
def make_llm(settings: Settings) -> Callable[[Callable[[str, str], Any]], Llm]:
    """An Llm answering from a function of (system, user) -> JSON-able value."""

    def build(answer: Callable[[str, str], Any]) -> Llm:
        def handler(request: httpx.Request) -> httpx.Response:
            messages = json.loads(request.content)["messages"]
            content = json.dumps(answer(messages[0]["content"], messages[1]["content"]))
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://llm")
        return Llm(settings, client)

    return build
