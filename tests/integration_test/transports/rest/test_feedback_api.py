from __future__ import annotations

import httpx

from ontyra.models import RecordId

HUMAN = {"X-Ontyra-Actor": "owner", "X-Ontyra-Actor-Kind": "human"}


async def _memory(client: httpx.AsyncClient) -> str:
    body = {
        "ops": [
            {
                "op": "add",
                "kind": "directive",
                "ref": "m",
                "fields": {"text": "use pytest", "strength": "must", "rule": "use pytest"},
            }
        ]
    }
    created = (await client.post("/v1/proposals", json=body, headers=HUMAN)).json()
    return created["created"]["m"]


async def test_feedback_roundtrip_over_rest(client: httpx.AsyncClient) -> None:
    memory = await _memory(client)
    posted = await client.post(
        "/v1/feedback", json={"memory": memory, "query": "write tests", "label": "applied"}
    )
    assert posted.status_code == 201 and posted.json()["state"] == "active"
    assert (await client.get("/v1/feedback/summary")).json()["active"] == 1
    listed = (await client.get("/v1/feedback", params={"state": "active"})).json()
    assert len(listed) == 1
    event_id = RecordId.parse(listed[0]["id"]).id
    retracted = await client.post(f"/v1/feedback/{event_id}/retract", headers=HUMAN)
    assert retracted.json()["state"] == "retracted"


async def test_reranker_status_and_training_without_a_trainable_reranker(
    client: httpx.AsyncClient,
) -> None:
    status = (await client.get("/v1/reranker", headers=HUMAN)).json()
    assert status["trainable"] is False and status["reranker"] is None
    refused = await client.post("/v1/reranker/train", headers=HUMAN)
    assert refused.status_code == 422


async def test_review_endpoints_over_rest(client: httpx.AsyncClient) -> None:
    memory = await _memory(client)
    posted = await client.post(
        "/v1/feedback", json={"memory": memory, "query": "write tests", "label": "irrelevant"}
    )
    event = RecordId.parse(posted.json()["id"]).id
    outliers = (await client.get("/v1/feedback/outliers", headers=HUMAN)).json()
    assert [o["feedback"]["label"] for o in outliers] == ["irrelevant"]
    vetoed = await client.post(f"/v1/feedback/{event}/veto", json={"reason": "slip"}, headers=HUMAN)
    assert vetoed.json()["state"] == "vetoed"
    assert (await client.get("/v1/feedback/summary")).json()["trainable_examples"] == 0
    assert (await client.post(f"/v1/feedback/{event}/unveto", headers=HUMAN)).json()[
        "state"
    ] == "active"
    opted = await client.post("/v1/feedback/opt-out", json={"enabled": True}, headers=HUMAN)
    assert opted.json()["kind"] == "opt_out"
    listed = (await client.get("/v1/feedback/exclusions", headers=HUMAN)).json()
    assert [e["kind"] for e in listed] == ["opt_out"]
    lifted = await client.post(
        f"/v1/feedback/exclusions/{RecordId.parse(listed[0]['id']).id}/lift", headers=HUMAN
    )
    assert lifted.json()["state"] == "lifted"
    agent = {"X-Ontyra-Actor": "claude", "X-Ontyra-Actor-Kind": "agent"}
    denied = await client.post(f"/v1/feedback/{event}/veto", json={}, headers=agent)
    assert denied.status_code == 403
