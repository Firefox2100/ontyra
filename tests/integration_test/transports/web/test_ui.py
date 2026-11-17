"""The operator console over the real services (in-process, no sockets)."""

from collections.abc import AsyncIterator

import httpx
import pytest

from ontyra.app import Application
from ontyra.transports.rest import create_app
from ontyra.transports.web import ROUTERS

AGENT = {"X-Ontyra-Actor": "claude", "X-Ontyra-Actor-Kind": "agent"}
DIRECTIVE = {
    "ops": [
        {
            "op": "add",
            "kind": "directive",
            "fields": {
                "text": "Always run the linter before committing <b>code</b>",
                "rule": "lint first",
                "strength": "must",
                "polarity": "do",
                "scope": "universal",
            },
        }
    ]
}


@pytest.fixture
async def ui(review_app: Application) -> AsyncIterator[httpx.AsyncClient]:
    """The console over an application whose policy holds everything for review."""
    app = create_app(review_app, extra_routers=ROUTERS, ui_actor="owner")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def propose(ui: httpx.AsyncClient) -> str:
    r = await ui.post("/v1/proposals", json=DIRECTIVE, headers=AGENT)
    assert r.status_code == 202, r.text
    return r.json()["proposal"].split(":", 1)[1]


async def test_every_page_renders_on_an_empty_store(ui: httpx.AsyncClient) -> None:
    for path in ["/ui/", "/ui/review", "/ui/memories", "/ui/concepts", "/ui/recall"]:
        r = await ui.get(path)
        assert r.status_code == 200, path
        assert "ONTYRA" in r.text


async def test_a_held_proposal_is_listed_viewed_and_approved(ui: httpx.AsyncClient) -> None:
    pid = await propose(ui)
    review = await ui.get("/ui/review")
    assert f"/ui/proposals/{pid}" in review.text and "Approve" in review.text
    detail = await ui.get(f"/ui/proposals/{pid}")
    assert "linter" in detail.text
    assert "<b>code</b>" not in detail.text  # memory text is escaped, never injected as HTML
    done = await ui.post(f"/ui/proposals/{pid}/approve")
    assert done.status_code == 303 and done.headers["location"] == "/ui/review"
    memories = await ui.get("/ui/memories?kind=directive")
    assert "linter" in memories.text
    assert "linter" not in (await ui.get("/ui/review")).text  # no longer waiting


async def test_a_proposal_can_be_rejected_with_a_reason(ui: httpx.AsyncClient) -> None:
    pid = await propose(ui)
    r = await ui.post(f"/ui/proposals/{pid}/reject", data={"reason": "too vague"})
    assert r.status_code == 303
    stored = (await ui.get(f"/v1/proposals/{pid}")).json()
    assert stored["status"] == "rejected"
    assert "linter" not in (await ui.get("/ui/memories?kind=directive")).text


async def test_ontology_changes_are_reviewed_in_the_console(ui: httpx.AsyncClient) -> None:
    body = {"change": {"op": "define", "concept": {"label": "Zig"}}, "rationale": "user uses Zig"}
    held = (await ui.post("/v1/ontology/changes", json=body, headers=AGENT)).json()
    assert "Zig" in (await ui.get("/ui/review")).text
    bare = held["id"].split(":", 1)[1]
    assert (await ui.post(f"/ui/ontology/changes/{bare}/approve")).status_code == 303
    assert "Zig" in (await ui.get("/ui/concepts?term=zig")).text


async def test_memory_detail_and_recall_playground(ui: httpx.AsyncClient) -> None:
    pid = await propose(ui)
    await ui.post(f"/ui/proposals/{pid}/approve")
    mem = (await ui.get("/v1/memories")).json()[0]["memory"].split(":", 1)[1]
    assert "History" in (await ui.get(f"/ui/memories/{mem}")).text
    briefing = await ui.get("/ui/recall?mode=briefing")
    assert "linter" in briefing.text
    found = await ui.get("/ui/recall", params={"query": "run the linter before committing"})
    assert found.status_code == 200


async def test_a_cross_site_post_is_refused(ui: httpx.AsyncClient) -> None:
    pid = await propose(ui)
    r = await ui.post(f"/ui/proposals/{pid}/approve", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert (await ui.get(f"/v1/proposals/{pid}")).json()["status"] == "pending"
    ok = await ui.post(f"/ui/proposals/{pid}/approve", headers={"Origin": "http://test"})
    assert ok.status_code == 303


async def test_the_console_is_off_unless_enabled(review_app: Application) -> None:
    app = create_app(review_app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/ui/")).status_code == 404
