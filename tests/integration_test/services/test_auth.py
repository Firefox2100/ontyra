"""Accounts, credentials, and what each may see and change (multi-user mode)."""

import pytest
from pydantic import SecretStr

from ontyra.app import Application
from ontyra.config import AuthSettings
from ontyra.models import PolicyConfig, RecordId
from ontyra.models.ontology_change import ChangeRequest
from ontyra.models.proposals import ProposalRequest
from ontyra.services import Caller, Principal, errors

ADMIN_TOKEN = "admin-token-for-tests-0123456789"


def directive(text: str) -> dict[str, object]:
    return {
        "op": "add",
        "kind": "directive",
        "fields": {"text": text, "strength": "must", "rule": text, "scope": "universal"},
    }


def request(text: str, **extra: object) -> ProposalRequest:
    return ProposalRequest.model_validate({"ops": [directive(text)], **extra})


@pytest.fixture
async def multi(make_app) -> Application:
    app: Application = await make_app(
        PolicyConfig(default="auto_commit"),
        auth=AuthSettings(multi_user=True, admin_token=SecretStr(ADMIN_TOKEN)),
    )
    await app.auth.bootstrap()
    return app


async def as_user(
    app: Application,
    user: str,
    cred: str,
    kind: str = "human",
    permissions: list[str] | None = None,
) -> Caller:
    """An account (created once) with a credential, authenticated like a request would be."""
    if not any(u.name == user for u in await app.auth.list_users()):
        await app.auth.create_user(user, permissions or [])
    _, token = await app.auth.issue_credential(user, cred, kind)
    principal = await app.auth.authenticate(token)
    assert principal is not None
    return await app.auth.caller(principal, label="ignored", kind="ignored", harness=None)


async def test_the_bootstrap_admin_authenticates_and_a_wrong_token_does_not(
    multi: Application,
) -> None:
    principal = await multi.auth.authenticate(ADMIN_TOKEN)
    assert principal is not None and principal.user is not None
    assert principal.user.name == "admin"
    assert await multi.auth.authenticate("nope") is None
    assert await multi.auth.authenticate(None) is None
    admin = await multi.auth.caller(principal, label="x", kind="agent", harness=None)
    assert admin.actor.kind == "human" and admin.has("admin") and admin.has("global_write")


async def test_multi_user_mode_needs_an_admin_token() -> None:
    with pytest.raises(ValueError, match="ADMIN_TOKEN"):
        AuthSettings(multi_user=True)
    with pytest.raises(ValueError, match="32 characters"):
        AuthSettings(token=SecretStr("short"))


async def test_shared_token_actor_kind_is_server_controlled(make_app) -> None:
    app: Application = await make_app(
        auth=AuthSettings(
            token=SecretStr("shared-token-for-tests-0123456789"),
            single_user_kind="agent",
        )
    )
    principal = await app.auth.authenticate("shared-token-for-tests-0123456789")
    assert principal is not None
    caller = await app.auth.caller(
        principal,
        label="claimed-human",
        kind="human",
        harness="test",
    )
    assert caller.actor.kind == "agent"


async def test_one_account_has_several_credentials_with_their_own_kind(multi: Application) -> None:
    me = await as_user(multi, "alice", "me", "human")
    codex = await as_user(multi, "alice", "codex", "agent")
    assert me.owner == codex.owner != RecordId("owner", "global")
    assert (me.actor.kind, codex.actor.kind) == ("human", "agent")
    assert me.actor_id != codex.actor_id
    assert codex.actor.name == "alice.codex"


async def test_a_revoked_credential_or_disabled_account_stops_working(multi: Application) -> None:
    await multi.auth.create_user("bob", [])
    cred, token = await multi.auth.issue_credential("bob", "laptop", "human")
    assert await multi.auth.authenticate(token) is not None
    assert cred.id is not None
    await multi.auth.revoke_credential(cred.id)
    assert await multi.auth.authenticate(token) is None
    _, token2 = await multi.auth.issue_credential(
        "bob", "laptop", "human"
    )  # the name is free again
    assert await multi.auth.authenticate(token2) is not None
    await multi.auth.update_user("bob", disabled=True)
    assert await multi.auth.authenticate(token2) is None


async def test_credential_names_are_unique_per_account_and_tokens_are_not_stored(
    multi: Application,
) -> None:
    await multi.auth.create_user("carol", [])
    cred, token = await multi.auth.issue_credential("carol", "claude", "agent")
    with pytest.raises(errors.ConflictError):
        await multi.auth.issue_credential("carol", "claude", "agent")
    assert token not in cred.model_dump_json() and token.startswith("ont_")
    assert cred.token_hash != token and cred.token_hint == token[-4:]


async def test_accounts_cannot_see_each_others_memories(multi: Application) -> None:
    alice = await as_user(multi, "alice", "me")
    bob = await as_user(multi, "bob", "me")
    result = await multi.memory.submit(alice, request("alice only"))
    assert result.status == "committed"
    assert [m.text for m in await multi.memory.list_memories(alice)] == ["alice only"]
    assert await multi.memory.list_memories(bob) == []
    with pytest.raises(errors.NotFoundError):
        await multi.memory.get_memory(bob, next(iter(result.created.values())))


async def test_the_global_memory_is_readable_by_all_and_writable_only_with_permission(
    multi: Application,
) -> None:
    writer = await as_user(multi, "writer", "me", permissions=["global_write"])
    reader = await as_user(multi, "reader", "me")
    shared = await multi.memory.submit(writer, request("team rule", space="global"))
    assert shared.status == "committed"
    assert [m.text for m in await multi.memory.list_memories(reader)] == ["team rule"]
    denied = await multi.memory.submit(reader, request("sneaky", space="global"))
    assert denied.status == "rejected"
    assert any(f.code == "permission_denied" for f in denied.findings)
    # and the reader cannot change what the writer put there
    rid = next(iter(shared.created.values()))
    attempt = ProposalRequest.model_validate(
        {"ops": [{"op": "retract", "memory": str(rid), "reason": "no"}]}
    )
    assert (await multi.memory.submit(reader, attempt)).status == "rejected"


async def test_an_agent_credential_is_held_and_only_a_human_of_the_owner_approves(
    make_app,
) -> None:
    app: Application = await make_app(
        PolicyConfig(default="review"),
        auth=AuthSettings(multi_user=True, admin_token=SecretStr(ADMIN_TOKEN)),
    )
    await app.auth.bootstrap()
    me = await as_user(app, "alice", "me", "human")
    codex = await as_user(app, "alice", "codex", "agent")
    other = await as_user(app, "bob", "me", "human")
    held = await app.memory.submit(codex, request("pending thing"))
    assert held.status == "pending"
    assert [p.id for p in await app.memory.pending(me)] == [held.proposal]
    assert await app.memory.pending(other) == []
    with pytest.raises(errors.NotFoundError):
        await app.memory.get_proposal(other, held.proposal)
    with pytest.raises(errors.PermissionDeniedError):
        await app.memory.approve(codex, held.proposal)  # an agent never reviews
    with pytest.raises(errors.PermissionDeniedError):
        await app.memory.approve(other, held.proposal)  # nor someone else's human
    assert (await app.memory.approve(me, held.proposal)).status == "committed"
    assert [m.text for m in await app.memory.list_memories(me)] == ["pending thing"]


async def test_global_proposals_are_reviewed_by_global_writers_only(make_app) -> None:
    app: Application = await make_app(
        PolicyConfig(default="review"),
        auth=AuthSettings(multi_user=True, admin_token=SecretStr(ADMIN_TOKEN)),
    )
    await app.auth.bootstrap()
    writer = await as_user(app, "writer", "me", "human", ["global_write"])
    writer_agent = await as_user(app, "writer", "codex", "agent")
    plain = await as_user(app, "plain", "me", "human")
    held = await app.memory.submit(writer_agent, request("shared advice", space="global"))
    assert held.status == "pending"
    assert [p.id for p in await app.memory.pending(writer)] == [held.proposal]
    assert await app.memory.pending(plain) == []
    with pytest.raises(errors.PermissionDeniedError):
        await app.memory.approve(plain, held.proposal)
    assert (await app.memory.approve(writer, held.proposal)).status == "committed"
    assert [m.text for m in await app.memory.list_memories(plain)] == ["shared advice"]


async def test_the_ontology_is_shared_so_changing_it_needs_global_write(multi: Application) -> None:
    plain = await as_user(multi, "plain", "me", "human")
    writer = await as_user(multi, "writer", "me", "human", ["global_write"])
    define = ChangeRequest.model_validate(
        {"change": {"op": "define", "concept": {"label": "thing"}}, "rationale": "needed"}
    )
    held = await multi.ontology_changes.propose(plain, define)
    assert held.status == "pending"  # even a human without permission only proposes
    with pytest.raises(errors.PermissionDeniedError):
        await multi.ontology_changes.approve(plain, held.id)
    assert (await multi.ontology_changes.approve(writer, held.id)).status == "applied"


async def test_ontology_auto_apply_and_queues_remain_tenant_scoped(multi: Application) -> None:
    alice = await as_user(multi, "alice-onto", "me", "human")
    bob = await as_user(multi, "bob-onto", "me", "human")
    writer = await as_user(multi, "ontology-writer", "me", "human", ["global_write"])
    multi.ontology_changes.auto_apply = {"define"}
    alice_change = await multi.ontology_changes.propose(
        alice,
        ChangeRequest.model_validate(
            {
                "change": {"op": "define", "concept": {"label": "Alice private proposal"}},
                "rationale": "alice rationale",
            }
        ),
    )
    bob_change = await multi.ontology_changes.propose(
        bob,
        ChangeRequest.model_validate(
            {
                "change": {"op": "define", "concept": {"label": "Bob private proposal"}},
                "rationale": "bob rationale",
            }
        ),
    )
    assert alice_change.status == bob_change.status == "pending"
    assert [c.id for c in await multi.ontology_changes.pending(alice)] == [alice_change.id]
    assert [c.id for c in await multi.ontology_changes.pending(bob)] == [bob_change.id]
    assert {c.id for c in await multi.ontology_changes.pending(writer)} == {
        alice_change.id,
        bob_change.id,
    }


async def test_single_user_mode_has_no_accounts() -> None:
    from ontyra.services import AuthService

    auth = AuthService(None, None, None, token="x" * 20)  # type: ignore[arg-type]
    assert auth.required and not auth.multi_user
    assert await auth.authenticate("x" * 20) == Principal()
    assert await auth.authenticate("y" * 20) is None
    with pytest.raises(errors.NotFoundError):
        await auth.list_users()


async def test_bootstrap_repairs_a_locked_out_admin(multi: Application) -> None:
    await multi.auth.update_user("admin", permissions=[], disabled=True)
    assert await multi.auth.authenticate(ADMIN_TOKEN) is None  # locked out
    await multi.auth.bootstrap()  # what a daemon start does
    principal = await multi.auth.authenticate(ADMIN_TOKEN)
    assert principal is not None and principal.user is not None
    caller = await multi.auth.caller(principal, label="x", kind="human", harness=None)
    assert caller.has("admin") and caller.has("global_write")
