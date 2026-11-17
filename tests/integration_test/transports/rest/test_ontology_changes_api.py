import httpx

HUMAN = {"X-Ontyra-Actor": "owner", "X-Ontyra-Actor-Kind": "human"}
AGENT = {"X-Ontyra-Actor": "claude", "X-Ontyra-Actor-Kind": "agent"}


async def test_propose_review_and_approve_over_rest(client: httpx.AsyncClient) -> None:
    body = {
        "change": {"op": "define", "concept": {"label": "Zig"}},
        "rationale": "the user works in Zig",
    }
    held = (await client.post("/v1/ontology/changes", json=body, headers=AGENT)).json()
    assert held["status"] == "pending"
    pending = (await client.get("/v1/ontology/changes")).json()
    assert [p["id"] for p in pending] == [held["id"]]
    bare = held["id"].split(":", 1)[1]
    r = await client.post(f"/v1/ontology/changes/{bare}/approve", headers=HUMAN)
    assert r.json()["status"] == "applied"
    assert (await client.get("/v1/concepts", params={"term": "zig"})).json()[0]["label"] == "Zig"
    assert (await client.get("/v1/ontology/changes")).json() == []
    assert len((await client.get("/v1/ontology/changes", params={"pending": False})).json()) == 1


async def test_errors_map_to_http(client: httpx.AsyncClient) -> None:
    bad = {"change": {"op": "merge", "source": "nope", "into": "nada"}, "rationale": "x"}
    assert (await client.post("/v1/ontology/changes", json=bad, headers=HUMAN)).status_code == 404
