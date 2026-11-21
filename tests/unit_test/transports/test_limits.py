"""The request body cap and the length caps on request models, without a database."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from ontyra.models.proposals import AddMemory, ProposalRequest
from ontyra.models.recall import RecallRequest, SearchRequest
from ontyra.transports.limits import BodyLimitMiddleware


async def echo(scope: Any, receive: Any, send: Any) -> None:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(len(body)).encode()})


def client(limit: int) -> httpx.AsyncClient:
    app = BodyLimitMiddleware(echo, limit)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_a_body_within_the_cap_passes() -> None:
    async with client(100) as c:
        reply = await c.post("/", content=b"x" * 100)
    assert reply.status_code == 200 and reply.text == "100"


async def test_a_declared_oversize_body_is_refused_before_it_is_read() -> None:
    async with client(100) as c:
        reply = await c.post("/", content=b"x" * 101)
    assert reply.status_code == 413


async def test_a_streamed_body_without_a_length_is_cut_off() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"x" * 10

    async with client(100) as c:
        reply = await c.post("/", content=chunks())
    assert reply.status_code == 413


async def test_zero_turns_the_cap_off() -> None:
    async with client(0) as c:
        reply = await c.post("/", content=b"x" * 10_000)
    assert reply.status_code == 200


def test_request_models_cap_what_a_caller_can_send() -> None:
    with pytest.raises(ValidationError):
        RecallRequest(query="x" * 20_001)
    with pytest.raises(ValidationError):
        RecallRequest(hints=["k"] * 51)
    with pytest.raises(ValidationError):
        RecallRequest(budget_tokens=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="q", limit=10_000)
    op = AddMemory(kind="fact", ref="m", fields={"text": "x"})
    with pytest.raises(ValidationError):
        ProposalRequest(ops=[op] * 101)
    assert ProposalRequest(ops=[op] * 100).ops
