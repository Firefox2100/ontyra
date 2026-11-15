"""Recall, exploration, ontology and resource endpoints over HTTP."""

from typing import Any

import httpx

AGENT = {"X-Ontyra-Actor": "claude", "X-Ontyra-Actor-Kind": "agent"}
HUMAN = {"X-Ontyra-Actor": "owner", "X-Ontyra-Actor-Kind": "human"}
PROJECT = "file://m/home/x/proj"


async def propose(
    client: httpx.AsyncClient, kind: str, text: str, ref: str = "m", **fields: Any
) -> str:
    body = {"text": text, **fields}
    r = await client.post(
        "/v1/proposals",
        json={"ops": [{"op": "add", "ref": ref, "kind": kind, "fields": body}]},
        headers=AGENT,
    )
    assert r.status_code == 201, r.text
    return r.json()["created"][ref]


async def test_ontology_endpoints(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/v1/concepts", json={"label": "Linux", "aliases": ["gnu/linux"]}, headers=HUMAN
    )
    assert r.status_code == 201 and r.json()["id"] == "concept:linux"
    r = await client.post(
        "/v1/concepts", json={"label": "Ubuntu", "is_a": ["linux"]}, headers=HUMAN
    )
    assert r.json()["relations"][0]["relation"] == "is_a"
    assert (
        await client.post("/v1/concepts", json={"label": "linux"}, headers=HUMAN)
    ).status_code == 409
    found = (await client.get("/v1/concepts", params={"term": "GNU/Linux"}, headers=AGENT)).json()
    assert [c["id"] for c in found] == ["concept:linux"]
    one = (await client.get("/v1/concepts/ubuntu", headers=AGENT)).json()
    assert one["label"] == "Ubuntu"
    assert (await client.get("/v1/concepts/nope", headers=AGENT)).status_code == 404
    rel = await client.post(
        "/v1/concepts/relations",
        json={"source": "linux", "relation": "is_a", "target": "ubuntu"},
        headers=HUMAN,
    )
    assert rel.status_code == 409  # cycle


async def test_resources_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/resources", json={"uri": "SSH://me@Host.com/", "kind": "account"})
    assert r.status_code == 201 and r.json()["uri"] == "ssh://me@host.com"
    assert (
        await client.post("/v1/resources", json={"uri": "ssh://me@host.com"})
    ).status_code == 409
    assert (await client.post("/v1/resources", json={"uri": "garbage"})).status_code == 422
    assert [x["uri"] for x in (await client.get("/v1/resources")).json()] == ["ssh://me@host.com"]


async def test_recall_briefing_and_check(client: httpx.AsyncClient) -> None:
    await client.post("/v1/concepts", json={"label": "Docker"}, headers=HUMAN)
    await propose(
        client,
        "directive",
        "never prune docker networks without asking the owner",
        strength="must",
        rule="x",
        polarity="avoid",
        about=["concept:docker"],
        anchors=[{"uri": PROJECT}],
    )
    ctx = {"uris": [f"{PROJECT}/src/a.py"], "session": "s1"}
    r = await client.post(
        "/v1/recall", json={"query": "clean up docker networks", "context": ctx}, headers=AGENT
    )
    assert r.status_code == 200, r.text
    pack = r.json()
    assert pack["silent"] is False and pack["must_follow"][0]["anchor"] == PROJECT
    assert "Must follow" in pack["rendered"] and pack["trace"].startswith("recall_trace:")
    # the same session does not get it twice, a briefing with redeliver does
    again = (
        await client.post(
            "/v1/recall", json={"query": "docker networks", "context": ctx}, headers=AGENT
        )
    ).json()
    assert again["silent"] is True
    brief = (
        await client.post(
            "/v1/briefing",
            json={"context": ctx, "redeliver": True, "profile": "compact"},
            headers=AGENT,
        )
    ).json()
    assert [i["strength"] for i in brief["must_follow"]] == ["must"]
    check = (
        await client.post(
            "/v1/check", json={"intent": "docker network prune", "context": ctx}, headers=AGENT
        )
    ).json()
    assert check["verdict"] == "caution" and check["pack"]["path"] == "check"
    other = (
        await client.post(
            "/v1/briefing", json={"context": {"uris": ["file://m/else"]}}, headers=AGENT
        )
    ).json()
    assert other["silent"] is True


async def test_search_ask_overview_detail_neighbors(client: httpx.AsyncClient) -> None:
    await client.post("/v1/concepts", json={"label": "pytest"}, headers=HUMAN)
    mem = await propose(
        client,
        "fact",
        "pytest fixtures live in conftest",
        subject="concept:pytest",
        predicate="has",
        object="conftest",
        about=["concept:pytest"],
    )
    search = (
        await client.post("/v1/search", json={"query": "pytest fixtures"}, headers=AGENT)
    ).json()
    assert search["hits"][0]["memory"] == mem and search["facets"]["kind"] == {"fact": 1}
    ask = (
        await client.post(
            "/v1/ask", json={"question": "absence", "topic": "kubernetes"}, headers=AGENT
        )
    ).json()
    assert ask["answer"]["found"] == [] and "envelope" in ask["answer"]
    bad = await client.post("/v1/ask", json={"question": "applies_here"}, headers=AGENT)
    assert bad.status_code == 422
    ov = (await client.post("/v1/overview", json={}, headers=AGENT)).json()
    assert ov["counts"] == {"fact": 1} and ov["concepts"] == ["pytest (1)"]
    mid = mem.split(":", 1)[1]
    detail = (
        await client.get(
            f"/v1/memories/{mid}/detail",
            params=[("expand", "history"), ("expand", "links")],
            headers=AGENT,
        )
    ).json()
    assert detail["history"]["revisions"][0]["seq"] == 1 and detail["links"] == []
    nb = (await client.get("/v1/neighbors/concept/pytest", headers=AGENT)).json()
    assert nb["memories"][0]["memory"] == mem
    assert (await client.get("/v1/neighbors/memory/nope", headers=AGENT)).status_code == 404
