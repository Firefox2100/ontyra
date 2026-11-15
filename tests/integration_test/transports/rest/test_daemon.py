"""The production wiring: settings → connect → schema → services, via the app's lifespan."""

from pathlib import Path

import httpx

from ontyra.app import create_rest_app
from ontyra.config import Settings


async def test_lifespan_connects_applies_the_schema_and_serves(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.db.url = f"surrealkv://{tmp_path}/daemon"
    settings.db.namespace = settings.db.database = "daemon"
    settings.policy.default = "auto_commit"
    app = create_rest_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/health")).json()["status"] == "ok"
        body = {"ops": [{"op": "add", "ref": "a", "kind": "fact", "fields": {
            "text": "the daemon works", "subject": "concept:ontyra", "predicate": "is",
            "object": "up"}}]}  # fmt: skip
        r = await client.post("/v1/proposals", json=body)
        assert r.status_code == 201, r.text
        assert len((await client.get("/v1/memories")).json()) == 1
    # a second start on the same files sees the data (persistence) and the schema (idempotent)
    app2 = create_rest_app(settings)
    async with (
        app2.router.lifespan_context(app2),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app2), base_url="http://test"
        ) as client,
    ):
        assert len((await client.get("/v1/memories")).json()) == 1
