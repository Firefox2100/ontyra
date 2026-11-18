"""The REST API end to end: HTTP → services → real database (embedded and server)."""

from typing import Any

import httpx

AGENT = {
    "X-Ontyra-Actor": "claude",
    "X-Ontyra-Actor-Kind": "agent",
    "X-Ontyra-Harness": "claude-code",
}
HUMAN = {"X-Ontyra-Actor": "owner", "X-Ontyra-Actor-Kind": "human"}


def directive(text: str, ref: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "op": "add",
        "ref": ref,
        "kind": "directive",
        "fields": {"text": text, "strength": "must", "rule": text, **extra},
    }


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_committed_proposal_returns_201_and_the_memory_is_readable(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        "/v1/proposals", json={"ops": [directive("use pytest", "d")]}, headers=AGENT
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "committed" and body["proposal"].startswith("proposal:")
    memory_id = body["created"]["d"]
    assert memory_id.startswith("memory:")

    got = await client.get(f"/v1/memories/{memory_id.split(':', 1)[1]}", headers=AGENT)
    assert got.status_code == 200
    view = got.json()
    assert (view["kind"], view["text"], view["status"]) == ("directive", "use pytest", "active")
    assert view["content"]["strength"] == "must"

    listing = await client.get("/v1/memories", params={"kind": "directive"}, headers=AGENT)
    assert [m["memory"] for m in listing.json()] == [memory_id]


async def test_rejected_proposal_returns_422_with_findings(client: httpx.AsyncClient) -> None:
    bad = directive("x", strength="always")
    r = await client.post("/v1/proposals", json={"ops": [bad]}, headers=AGENT)
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "rejected"
    assert body["findings"][0]["code"] == "invalid_field" and body["findings"][0]["op"] == 0


async def test_malformed_request_bodies_are_422_from_the_framework(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/v1/proposals", json={"ops": [{"op": "explode"}]}, headers=AGENT)
    assert r.status_code == 422
    assert (await client.post("/v1/proposals", json={"ops": []}, headers=AGENT)).status_code == 422


async def test_review_flow_over_http(review_client: httpx.AsyncClient) -> None:
    c = review_client
    r = await c.post("/v1/proposals", json={"ops": [directive("held", "d")]}, headers=AGENT)
    assert r.status_code == 202 and r.json()["status"] == "pending"
    pid = r.json()["proposal"].split(":", 1)[1]

    assert (await c.get("/v1/memories", headers=AGENT)).json() == []
    pending = (await c.get("/v1/proposals")).json()
    assert [p["id"] for p in pending] == [f"proposal:{pid}"]
    assert (await c.get(f"/v1/proposals/{pid}")).json()["status"] == "pending"

    approved = await c.post(f"/v1/proposals/{pid}/approve", headers=HUMAN)
    assert approved.status_code == 200 and approved.json()["status"] == "committed"
    assert len((await c.get("/v1/memories", headers=AGENT)).json()) == 1
    again = await c.post(f"/v1/proposals/{pid}/approve", headers=HUMAN)
    assert again.status_code == 409


async def test_reject_over_http(review_client: httpx.AsyncClient) -> None:
    c = review_client
    r = await c.post("/v1/proposals", json={"ops": [directive("nope")]}, headers=AGENT)
    pid = r.json()["proposal"].split(":", 1)[1]
    rej = await c.post(f"/v1/proposals/{pid}/reject", json={"reason": "not needed"}, headers=HUMAN)
    assert rej.status_code == 200 and rej.json()["reason"] == "not needed"
    assert (await c.get("/v1/proposals")).json() == []
    assert (await c.get(f"/v1/proposals/{pid}")).json()["status"] == "rejected"


async def test_history_shows_supersession_reasons(client: httpx.AsyncClient) -> None:
    created = (
        await client.post("/v1/proposals", json={"ops": [directive("v1", "d")]}, headers=AGENT)
    ).json()["created"]
    mid = created["d"]
    revise = {"op": "revise", "memory": mid, "fields": {"text": "v2", "rule": "v2"},
              "reason_code": "corrected", "reason": "typo"}  # fmt: skip
    assert (
        await client.post("/v1/proposals", json={"ops": [revise]}, headers=AGENT)
    ).status_code == 201
    h = (await client.get(f"/v1/memories/{mid.split(':', 1)[1]}/history", headers=AGENT)).json()
    first, second = h["revisions"]
    assert (first["text"], first["lifecycle"]) == ("v1", "superseded")
    assert first["reasons"][0]["reason_code"] == "corrected"
    assert second["lifecycle"] == "active" and first["superseded_by"] == [second["revision"]]


async def test_unknown_memory_and_proposal_are_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/memories/nope", headers=AGENT)).status_code == 404
    assert (await client.get("/v1/memories/nope/history", headers=AGENT)).status_code == 404
    assert (await client.get("/v1/proposals/nope")).status_code == 404


async def test_episode_can_be_cited_as_evidence(client: httpx.AsyncClient) -> None:
    ep = await client.post(
        "/v1/episodes",
        json={"session": "s1", "role": "user", "content": "Please use pytest for tests."},
        headers=AGENT,
    )
    assert ep.status_code == 201
    eid = ep.json()["id"]
    good = {**directive("use pytest", "d"), "evidence": [{"episode": eid, "quote": "use pytest"}]}
    assert (
        await client.post("/v1/proposals", json={"ops": [good]}, headers=AGENT)
    ).status_code == 201
    bad = {**directive("never mock"), "evidence": [{"episode": eid, "quote": "never mock"}]}
    r = await client.post("/v1/proposals", json={"ops": [bad]}, headers=AGENT)
    assert r.status_code == 422 and r.json()["findings"][0]["code"] == "grounding_failed"


async def test_idempotency_key_over_http(client: httpx.AsyncClient) -> None:
    body = {"ops": [directive("once", "d")], "idempotency_key": "k-1"}
    first = await client.post("/v1/proposals", json=body, headers=AGENT)
    again = await client.post("/v1/proposals", json=body, headers=AGENT)
    assert again.json()["proposal"] == first.json()["proposal"]
    assert again.json()["duplicate_of_idempotency_key"] is True
    assert len((await client.get("/v1/memories", headers=AGENT)).json()) == 1


async def test_an_owner_header_does_not_choose_the_owner(client: httpx.AsyncClient) -> None:
    # single-user mode: everything is global; a header cannot create or pick another owner
    mine = {**HUMAN, "X-Ontyra-Owner": "alice"}
    r = await client.post("/v1/proposals", json={"ops": [directive("shared", "d")]}, headers=mine)
    assert r.status_code == 201
    listing = (await client.get("/v1/memories", headers=AGENT)).json()
    assert [m["owner"] for m in listing] == ["owner:global"]


async def test_actor_defaults_and_trust_are_assigned_by_configuration(
    client: httpx.AsyncClient,
) -> None:
    # no headers at all: an anonymous agent. Kind decides trust; the caller cannot set it.
    r = await client.post("/v1/proposals", json={"ops": [directive("anon", "d")]})
    assert r.status_code == 201


async def test_openapi_document_is_served(client: httpx.AsyncClient) -> None:
    doc = (await client.get("/openapi.json")).json()
    assert doc["info"]["title"] == "Ontyra"
    schemas = doc["components"]["schemas"]
    assert {"ProposalRequest", "ProposalResult", "MemoryView", "History"} <= set(schemas)


async def test_the_duplicate_scan_is_a_read_only_report(client: httpx.AsyncClient) -> None:
    text = "Use uv for Python dependency management and never run pip install directly."
    for ref in ("a", "b"):
        added = await client.post(
            "/v1/proposals", json={"ops": [directive(text, ref)]}, headers=HUMAN
        )
        assert added.status_code == 201, added.text
    r = await client.post("/v1/consolidation/scan", json={}, headers=HUMAN)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["scanned"] == 2 and report["decision_model"] is None
    assert set(report) >= {"duplicates", "updates", "rejected", "model_errors", "unembedded"}
    assert (
        await client.post("/v1/consolidation/scan", json={"kinds": ["nope"]})
    ).status_code == 422


async def test_maintenance_tasks_over_http(client: httpx.AsyncClient) -> None:
    text = "Use uv for Python dependency management and never run pip install directly."
    for ref in ("a", "b"):
        added = await client.post(
            "/v1/proposals", json={"ops": [directive(text, ref)]}, headers=HUMAN
        )
        assert added.status_code == 201, added.text
    detected = await client.post("/v1/tasks/detect", headers=HUMAN)
    assert detected.status_code == 200, detected.text
    (task,) = detected.json()["created"]
    assert task["kind"] == "consolidate" and task["card"]["choices"]
    listed = (await client.get("/v1/tasks", headers=HUMAN)).json()
    assert [t["id"] for t in listed] == [task["id"]]
    tid = task["id"].removeprefix("task:")
    assert (await client.post(f"/v1/tasks/{tid}/claim", headers=HUMAN)).json()[
        "status"
    ] == "claimed"
    assert (await client.post(f"/v1/tasks/{tid}/claim", headers=HUMAN)).status_code == 409
    bad = await client.post(f"/v1/tasks/{tid}/complete", json={"decision": "merge"}, headers=HUMAN)
    assert bad.status_code == 422  # a merge must cite its proposal
    done = await client.post(
        f"/v1/tasks/{tid}/complete", json={"decision": "distinct"}, headers=HUMAN
    )
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert (await client.get("/v1/tasks/task:nope", headers=HUMAN)).status_code == 404
