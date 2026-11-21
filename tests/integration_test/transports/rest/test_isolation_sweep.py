"""One user's data never reaches another: every route that reads or changes memory, called as a
second user with the first user's ids and words, must come back without a trace of them.

The sweep is driven by the app's own route table, so a new route that is not classified below
fails the test (add it to READS, BY_ID or ADMIN)."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from ontyra.app import create_rest_app
from ontyra.config import Settings
from ontyra.models.policy import PolicyRule

ADMIN = "admin-token-for-sweep-0123456789abcdef"
SECRET = "zephyrquartz-owner-secret"  # a word nobody else could have written
PENDING = "pending-xylophone-owner-secret"


# Routes the sweep calls as a second user (the others are listed in EXEMPT).
SWEPT = {
    f"{m} /v1{p}"
    for m, p in [
        ("GET", "/memories"),
        ("GET", "/memories/{memory_id}"),
        ("GET", "/memories/{memory_id}/history"),
        ("GET", "/memories/{memory_id}/detail"),
        ("GET", "/neighbors/{table}/{item_id}"),
        ("GET", "/proposals"),
        ("POST", "/proposals"),
        ("GET", "/proposals/{proposal_id}"),
        ("POST", "/proposals/{proposal_id}/approve"),
        ("POST", "/proposals/{proposal_id}/reject"),
        ("POST", "/proposals/{proposal_id}/amend"),
        ("POST", "/proposals/{proposal_id}/withdraw"),
        ("POST", "/proposals/{proposal_id}/request-changes"),
        ("POST", "/recall"),
        ("POST", "/briefing"),
        ("POST", "/check"),
        ("POST", "/search"),
        ("POST", "/ask"),
        ("POST", "/overview"),
        ("POST", "/consolidation/scan"),
        ("GET", "/feedback"),
        ("GET", "/feedback/summary"),
        ("POST", "/feedback"),
        ("POST", "/feedback/{feedback_id}/retract"),
        ("GET", "/reranker"),
        ("GET", "/tasks"),
        ("GET", "/tasks/{task_id}"),
        ("POST", "/tasks/{task_id}/claim"),
        ("POST", "/tasks/{task_id}/release"),
        ("POST", "/tasks/{task_id}/complete"),
        ("POST", "/tasks/{task_id}/resolve"),
    ]
}
# Global by design (the ontology and resources are shared, written with `global_write`: see
# test_auth_api), the admin API, and calls that only ever act on the caller's own owner.
EXEMPT = (
    "/v1/concepts",
    "/v1/resources",
    "/v1/ontology",
    "/v1/admin",
    "/v1/episodes",
    "/v1/tasks/detect",
    "/v1/reranker/train",
)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def daemon(tmp_path: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    settings = Settings(_env_file=None, auth={"multi_user": True, "admin_token": ADMIN})  # type: ignore[call-arg, arg-type]
    settings.db.url = f"surrealkv://{tmp_path}/sweep"
    settings.db.namespace = settings.db.database = "sweep"
    settings.policy.default = "review"
    settings.policy.rules = [PolicyRule(actor_kinds=["human"], decision="auto_commit")]
    settings.mcp.enabled = False
    settings.ui.enabled = True
    app = create_rest_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield app, client


async def account(c: httpx.AsyncClient, user: str) -> tuple[dict[str, str], dict[str, str]]:
    """A user with a human credential and an agent credential (whose writes wait for review)."""
    admin = bearer(ADMIN)
    assert (await c.post("/v1/admin/users", json={"name": user}, headers=admin)).status_code == 201
    out: list[dict[str, str]] = []
    for kind in ("human", "agent"):
        r = await c.post(
            f"/v1/admin/users/{user}/credentials", json={"name": kind, "kind": kind}, headers=admin
        )
        assert r.status_code == 201
        out.append(bearer(r.json()["token"]))
    return out[0], out[1]


def leaks(reply: httpx.Response, sent: Any) -> bool:
    """Does the reply carry the secret words, apart from echoing what the caller sent?"""
    text = reply.text
    for value in sorted(strings(sent), key=len, reverse=True):
        text = text.replace(value, "")
    return SECRET in text or PENDING in text


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]  # type: ignore[misc]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]  # type: ignore[misc]
    return []


def directive(text: str) -> dict[str, Any]:
    return {
        "ops": [
            {
                "op": "add",
                "ref": "m",
                "kind": "directive",
                "fields": {"text": text, "strength": "must", "rule": text, "scope": "universal"},
            }
        ]
    }


async def test_a_second_user_sees_nothing_of_the_first(tmp_path: Path) -> None:
    async with daemon(tmp_path) as (app, c):
        (alice, alice_agent), (bob, _) = await account(c, "alice"), await account(c, "bob")

        # alice's data: a committed memory, a pending proposal, an episode, a recall trace, a task
        made = await c.post("/v1/proposals", json=directive(f"Always use {SECRET}."), headers=alice)
        assert made.status_code == 201, made.text
        memory = made.json()["created"]["m"]
        waiting = await c.post(
            "/v1/proposals",
            json=directive(PENDING),
            headers=alice_agent,
        )
        assert waiting.status_code == 202, waiting.text
        proposal = made.json()["proposal"]
        pending = waiting.json()["proposal"]
        episode = await c.post(
            "/v1/episodes",
            json={"session": "s1", "role": "user", "content": f"about {SECRET}"},
            headers=alice,
        )
        assert episode.status_code == 201
        recalled = await c.post(
            "/v1/recall",
            json={"query": f"what about {SECRET}", "context": {"session": "s1"}},
            headers=alice,
        )
        assert recalled.status_code == 200 and SECRET in recalled.text  # alice does see her own
        trace = recalled.json().get("trace")
        assert trace  # the seed must cover traces, or the sweep proves nothing about them
        given = await c.post(
            "/v1/feedback", json={"memory": memory, "query": "q", "label": "helpful"}, headers=alice
        )
        assert given.status_code == 201, given.text
        feedback = given.json()["id"]
        await c.post("/v1/tasks/detect", headers=alice)
        tasks = (await c.get("/v1/tasks", headers=alice)).json()
        assert tasks, "the seed must cover tasks"
        mine = {memory, proposal, pending, episode.json()["id"]} | {t["id"] for t in tasks}
        mine.add(trace)

        # what bob may read
        context = {"context": {"session": "s2"}}
        for method, path, body in [
            ("GET", "/v1/memories", None),
            ("GET", "/v1/memories?status=superseded", None),
            ("GET", "/v1/proposals", None),
            ("GET", "/v1/tasks", None),
            ("GET", "/v1/feedback", None),
            ("GET", "/v1/feedback/summary", None),
            ("GET", "/v1/reranker", None),
            ("GET", "/v1/ontology/changes", None),
            ("POST", "/v1/recall", {"query": SECRET, **context}),
            ("POST", "/v1/recall", {"query": f"always use {SECRET}", "redeliver": True, **context}),
            ("POST", "/v1/briefing", context),
            ("POST", "/v1/check", {"intent": f"deploy {SECRET}", **context}),
            ("POST", "/v1/search", {"query": SECRET, **context}),
            ("POST", "/v1/search", {"query": PENDING, **context}),
            ("POST", "/v1/overview", context),
            ("POST", "/v1/consolidation/scan", {}),
            ("POST", "/v1/ask", {"question": "absence", "topic": SECRET, **context}),
            ("POST", "/v1/ask", {"question": "as_of", "at": "2100-01-01T00:00:00Z", **context}),
            (
                "POST",
                "/v1/ask",
                {"question": "changes_since", "since": "2000-01-01T00:00:00Z", **context},
            ),
            ("POST", "/v1/ask", {"question": "conflicts", **context}),
        ]:
            r = await c.request(method, path, json=body, headers=bob)
            assert r.status_code < 500, (path, r.status_code, r.text)
            assert not leaks(r, body), (method, path, r.text[:300])
            if r.status_code == 200 and path in ("/v1/memories", "/v1/proposals", "/v1/tasks"):
                assert not any(i in r.text for i in mine), (path, r.text[:300])

        # what bob may not touch, by id
        ids = {"memory": memory, "proposal": proposal, "pending": pending, "episode": mine - set()}
        bare = lambda rid: rid.split(":", 1)[1]  # noqa: E731
        calls: list[tuple[str, str, Any]] = [
            ("GET", f"/v1/memories/{bare(memory)}", None),
            ("GET", f"/v1/memories/{bare(memory)}/history", None),
            ("GET", f"/v1/memories/{bare(memory)}/detail?expand=revisions", None),
            ("GET", f"/v1/neighbors/memory/{bare(memory)}", None),
            ("GET", f"/v1/proposals/{bare(pending)}", None),
            ("POST", f"/v1/proposals/{bare(pending)}/approve", {}),
            ("POST", f"/v1/proposals/{bare(pending)}/reject", {"reason": "no"}),
            ("POST", f"/v1/proposals/{bare(pending)}/withdraw", {}),
            ("POST", f"/v1/proposals/{bare(pending)}/amend", directive("changed by bob")),
            ("POST", f"/v1/proposals/{bare(pending)}/request-changes", {"comment": "no"}),
            ("POST", "/v1/ask", {"question": "supersession_chain", "memory": memory, **context}),
            ("POST", "/v1/ask", {"question": "applies_here", "memory": memory, **context}),
            ("POST", "/v1/ask", {"question": "prerequisites_of", "memory": memory, **context}),
            ("POST", "/v1/feedback", {"memory": memory, "query": "x", "label": "wrong"}),
            ("POST", f"/v1/feedback/{bare(feedback)}/retract", {}),
        ]
        calls.append(("POST", "/v1/ask", {"question": "explain", "trace": trace, **context}))
        for task in tasks:
            tid = bare(task["id"])
            calls += [
                ("GET", f"/v1/tasks/{tid}", None),
                ("POST", f"/v1/tasks/{tid}/claim", {}),
                ("POST", f"/v1/tasks/{tid}/complete", {"decision": "keep"}),
                ("POST", f"/v1/tasks/{tid}/release", {}),
                ("POST", f"/v1/tasks/{tid}/resolve", {"decision": "retract"}),
            ]
        for method, path, body in calls:
            r = await c.request(method, path, json=body, headers=bob)
            assert 400 <= r.status_code < 500, (method, path, r.status_code, r.text[:300])
            assert not leaks(r, body), (method, path, r.text[:300])

        # and bob cannot change alice's memory by proposing about it
        for op in (
            {"op": "retract", "memory": memory, "reason": "bob says so"},
            {
                "op": "revise",
                "target": memory,
                "fields": {"text": "bob", "strength": "must", "rule": "bob"},
            },
            {"op": "supersede", "new": memory, "target": memory, "reason_code": "outdated"},
        ):
            r = await c.post("/v1/proposals", json={"ops": [op]}, headers=bob)
            assert r.status_code in (404, 422), (op["op"], r.status_code, r.text[:300])

        # every route is either swept above or global / own-data-only by design
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/v1/") or path.startswith(EXEMPT):
                continue
            for method in set(getattr(route, "methods", ())) - {"HEAD"}:
                assert f"{method} {path}" in SWEPT, f"{method} {path}: not in the isolation sweep"

        # alice's data is intact and still hers
        still = await c.get(f"/v1/memories/{bare(memory)}", headers=alice)
        assert still.status_code == 200 and SECRET in still.text
        assert ids and re.search(SECRET, still.text)
