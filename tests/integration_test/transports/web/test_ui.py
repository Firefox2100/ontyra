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
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:8787",
        headers={"Origin": "http://127.0.0.1:8787"},
    ) as client:
        yield client


async def propose(ui: httpx.AsyncClient) -> str:
    r = await ui.post("/v1/proposals", json=DIRECTIVE, headers=AGENT)
    assert r.status_code == 202, r.text
    return r.json()["proposal"].split(":", 1)[1]


async def test_every_page_renders_on_an_empty_store(ui: httpx.AsyncClient) -> None:
    for path in [
        "/ui/",
        "/ui/review",
        "/ui/memories",
        "/ui/concepts",
        "/ui/recall",
        "/ui/feedback",
        "/ui/knowledge",
    ]:
        r = await ui.get(path)
        assert r.status_code == 200, path
        assert "Ontyra" in r.text

    home = await ui.get("/ui/")
    assert 'rel="icon" href="/ui/assets/ontyra-mark.svg"' in home.text
    logo = await ui.get("/ui/assets/ontyra-mark.svg")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/svg+xml")
    assert "An open memory ring" in logo.text


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


async def test_feedback_is_reviewed_vetoed_and_a_user_can_opt_out(ui: httpx.AsyncClient) -> None:
    pid = await propose(ui)
    await ui.post(f"/ui/proposals/{pid}/approve")
    memory = (await ui.get("/v1/memories")).json()[0]["memory"]
    posted = await ui.post(
        "/v1/feedback", json={"memory": memory, "query": "lint it", "label": "irrelevant"}
    )
    event = posted.json()["id"].split(":", 1)[1]
    page = await ui.get("/ui/feedback")
    assert "lint it" in page.text and "Always run the linter" in page.text
    assert "<b>code</b>" not in page.text
    done = await ui.post(f"/ui/feedback/{event}/veto", data={"reason": "slip"})
    assert done.status_code == 303
    after = await ui.get("/ui/feedback")
    assert "Vetoed" in after.text and "slip" in after.text
    await ui.post(f"/ui/feedback/{event}/unveto")
    assert "Vetoed" not in (await ui.get("/ui/feedback")).text
    await ui.post("/ui/feedback/opt-out", data={"enabled": "true"})
    assert "Allow my feedback in training again" in (await ui.get("/ui/feedback")).text
    cross = await ui.post("/ui/feedback/opt-out", headers={"Origin": "http://evil.example"})
    assert cross.status_code in (400, 403)


async def test_a_verdict_from_the_recall_page_is_recorded_with_its_trace(
    ui: httpx.AsyncClient,
) -> None:
    pid = await propose(ui)
    await ui.post(f"/ui/proposals/{pid}/approve")
    memory = (await ui.get("/v1/memories")).json()[0]["memory"]
    page = await ui.get("/ui/recall", params={"query": "run the linter"})
    assert "Was it right?" in page.text
    trace = page.text.split('name="trace" value="')[1].split('"')[0]
    sent = await ui.post(
        "/ui/recall/feedback",
        data={
            "memory": memory,
            "query": "run the linter",
            "label": "helpful",
            "trace": trace,
            "back": "/ui/recall?query=run+the+linter",
        },
    )
    assert sent.status_code == 303 and sent.headers["location"].startswith("/ui/recall?query=")
    listed = (await ui.get("/v1/feedback")).json()
    assert [e["label"] for e in listed] == ["helpful"]
    elsewhere = await ui.post(
        "/ui/recall/feedback",
        data={"memory": memory, "query": "q", "label": "wrong", "back": "https://evil.example"},
    )
    assert elsewhere.headers["location"] == "/ui/recall"
    refused = await ui.post(
        "/ui/recall/feedback", data={"memory": memory, "query": "q", "label": "nope"}
    )
    assert refused.status_code == 422


async def test_a_proposal_can_be_rejected_with_a_reason(ui: httpx.AsyncClient) -> None:
    pid = await propose(ui)
    r = await ui.post(f"/ui/proposals/{pid}/reject", data={"reason": "too vague"})
    assert r.status_code == 303
    stored = (await ui.get(f"/v1/proposals/{pid}")).json()
    assert stored["status"] == "rejected"
    assert "linter" not in (await ui.get("/ui/memories?kind=directive")).text


async def test_a_reviewer_can_send_a_proposal_back_and_the_agent_amends_it(
    ui: httpx.AsyncClient,
) -> None:
    pid = await propose(ui)
    sent = await ui.post(f"/ui/proposals/{pid}/request-changes", data={"comment": "name the repo"})
    assert sent.status_code == 303
    detail = await ui.get(f"/ui/proposals/{pid}")
    assert "changes_requested" in detail.text and "name the repo" in detail.text
    assert "changes requested" in (await ui.get("/ui/review")).text
    ops = DIRECTIVE["ops"]
    amended = await ui.post(
        f"/v1/proposals/{pid}/amend",
        json={"ops": ops, "why": "named it", "expect_version": 1},
        headers=AGENT,
    )
    assert amended.status_code == 200 and amended.json()["version"] == 2
    assert (await ui.get(f"/v1/proposals/{pid}")).json()["status"] == "pending"


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
    ok = await ui.post(f"/ui/proposals/{pid}/approve", headers={"Origin": "http://127.0.0.1:8787"})
    assert ok.status_code == 303


async def test_the_console_is_off_unless_enabled(review_app: Application) -> None:
    app = create_app(review_app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8787"
    ) as client:
        assert (await client.get("/ui/")).status_code == 404


async def test_the_tasks_page_finds_and_resolves_duplicates(make_app) -> None:
    from ontyra.models import PolicyConfig
    from ontyra.models.consolidation import ConsolidationSettings

    app = await make_app(
        PolicyConfig(default="auto_commit"),
        consolidation=ConsolidationSettings(candidate_cosine=0.4, duplicate_cosine=0.75),
    )
    web = create_app(app, extra_routers=ROUTERS, ui_actor="owner")
    text = "Use uv for Python dependency management in tidewatch and never run pip install."
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web),
        base_url="http://127.0.0.1:8787",
        headers={"Origin": "http://127.0.0.1:8787"},
    ) as ui:
        for ref in ("a", "b"):
            op = {
                "op": "add",
                "ref": ref,
                "kind": "directive",
                "fields": {
                    "text": text + ("" if ref == "a" else " "),
                    "rule": "uv",
                    "strength": "should",
                },
            }
            assert (await ui.post("/v1/proposals", json={"ops": [op]})).status_code == 201
        await app.indexer.backfill()
        empty = await ui.get("/ui/tasks")
        assert empty.status_code == 200 and "No open maintenance work" in empty.text
        found = await ui.post("/ui/tasks/detect", follow_redirects=True)
        assert found.status_code == 200 and "These 2 memories say the same thing" in found.text
        assert "Merge into the oldest" in found.text
        listed = (await ui.get("/v1/tasks")).json()
        tid = listed[0]["id"].split(":", 1)[1]
        assert (
            await ui.post(f"/ui/tasks/{tid}/resolve", data={"decision": "nope"})
        ).status_code == 422
        done = await ui.post(
            f"/ui/tasks/{tid}/resolve", data={"decision": "merge"}, follow_redirects=True
        )
        assert done.status_code == 200 and "No open maintenance work" in done.text
        assert "merge" in done.text  # listed under recently closed
        cross_site = await ui.post("/ui/tasks/detect", headers={"Origin": "http://evil.example"})
        assert cross_site.status_code == 403


async def test_the_ontology_can_be_edited_with_a_preview_and_reverted_in_the_console(
    ui: httpx.AsyncClient,
) -> None:
    made = await ui.post(
        "/ui/ontology/apply",
        data={"op": "define", "label": "Zig", "definition": "a language", "rationale": "new"},
    )
    assert made.status_code == 303 and made.headers["location"] == "/ui/concepts/zig"
    edit = {"op": "edit", "concept": "zig", "definition": "a systems language", "rationale": "x"}
    shown = await ui.post("/ui/ontology/preview", data=edit)
    assert shown.status_code == 200 and "edit concept:zig" in shown.text and "Apply" in shown.text
    bad = await ui.post("/ui/ontology/preview", data={**edit, "concept": "nothing"})
    assert "cannot apply" in bad.text
    assert (await ui.post("/ui/ontology/apply", data=edit)).status_code == 303
    page = await ui.get("/ui/concepts/zig")
    assert "systems language" in page.text and "v2" in page.text and "edit</a>" in page.text
    history = await ui.get("/ui/ontology/history?concept=zig")
    assert "Revert" in history.text
    change = history.text.split("/ui/ontology/changes/")[1].split("/revert")[0]
    assert (await ui.post(f"/ui/ontology/changes/{change}/revert")).status_code == 303
    assert (
        "a systems language"
        not in (await ui.get("/ui/concepts/zig")).text.split("Investigation")[0]
    )
    assert (await ui.get("/ui/ontology/check")).status_code == 200
    assert (await ui.get("/ui/ontology/new?op=split&concept=zig")).status_code == 200


async def test_per_op_controls_the_version_diff_and_the_bulk_edit(ui: httpx.AsyncClient) -> None:
    two = {
        "ops": [
            {
                "op": "add", "ref": "a", "kind": "directive",
                "fields": {"text": "use tabs in makefiles", "strength": "should", "rule": "tabs"},
            },
            {
                "op": "add", "ref": "b", "kind": "directive",
                "fields": {"text": "prefer small commits", "strength": "should", "rule": "small"},
            },
        ]
    }  # fmt: skip
    r = await ui.post("/v1/proposals", json=two, headers=AGENT)
    pid = r.json()["proposal"].split(":", 1)[1]
    other = (await ui.post("/v1/proposals", json=DIRECTIVE, headers=AGENT)).json()["proposal"]
    page = await ui.get(f"/ui/proposals/{pid}")
    assert 'name="ops" value="1"' in page.text and "Approve selected" in page.text
    bulk = await ui.post(
        "/ui/proposals/overlay",
        data={"ids": [pid, other.split(":", 1)[1]], "scope": "universal"},
    )
    assert bulk.status_code == 303
    stored = (await ui.get(f"/v1/proposals/{pid}")).json()
    assert stored["version"] == 2 and all(
        o["fields"]["scope"] == "universal" for o in stored["ops"]
    )
    versions = await ui.get(f"/ui/proposals/{pid}/versions")
    assert "universal" in versions.text and "v1" in versions.text
    done = await ui.post(
        f"/ui/proposals/{pid}/approve-ops",
        data={"ops": ["0"], "reason": "the second is a style call", "version": "2"},
    )
    assert done.status_code == 303
    final = (await ui.get(f"/v1/proposals/{pid}")).json()
    assert final["status"] == "committed" and [d["status"] for d in final["decisions"]] == [
        "approved",
        "rejected",
    ]


async def test_a_rebound_hostname_is_refused_without_an_allowlist(
    review_app: Application,
) -> None:
    app = create_app(review_app, extra_routers=ROUTERS, ui_actor="owner")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://attacker.example:8787",
        headers={"Origin": "http://attacker.example:8787"},
    ) as client:  # Host and Origin agree, but the page did not come from this machine
        denied = await client.post("/ui/tasks/detect")
    assert denied.status_code == 403


async def test_the_knowledge_page_vets_what_policy_committed_and_records_source_checks(
    app: Application,
) -> None:
    from ontyra.models.proposals import AddMemory, Evidence, ProposalRequest
    from ontyra.models.sources import SourceRef

    agent = await app.actors.caller(kind="agent", name="claude", harness="claude-code")
    uri = "file:///papers/gene-x.pdf"
    result = await app.memory.submit(
        agent,
        ProposalRequest(
            ops=[
                AddMemory(
                    kind="fact",
                    ref="m",
                    fields={
                        "text": "Gene X acts on pathway Y",
                        "subject": "concept:linux",
                        "predicate": "relates_to",
                        "object": "pathway Y",
                        "scope": "universal",
                    },
                    evidence=[Evidence(source=SourceRef(uri=uri, quote="X acts on Y"))],
                )
            ]
        ),
    )
    assert result.status == "committed"
    transport = httpx.ASGITransport(app=create_app(app, extra_routers=ROUTERS, ui_actor="owner"))
    origin = {"Origin": "http://127.0.0.1:8787"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8787", headers=origin
    ) as ui:
        page = await ui.get("/ui/knowledge")
        assert page.status_code == 200 and "Gene X acts on pathway Y" in page.text
        assert "/papers/gene-x.pdf" in page.text
        found = await ui.get("/ui/knowledge", params={"uri": uri})
        assert "never checked" in found.text
        done = await ui.post(
            "/ui/knowledge/check", data={"uri": uri, "state": "missing", "note": "moved"}
        )
        assert done.status_code == 303 and "1 review task" in done.headers["location"].replace(
            "%20", " "
        )
        assert "moved" in (await ui.get("/ui/knowledge")).text
        await ui.post("/ui/knowledge/vet", data={"memory": str(result.created["m"])})
        assert "Nothing waits for vetting" in (await ui.get("/ui/knowledge")).text
