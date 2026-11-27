"""The credential mask: what it takes out, what it leaves, and how the caller learns about it."""

from __future__ import annotations

from ontyra.models.proposals import AddMemory, ProposalRequest
from ontyra.services.secrets import mask_text, redact_request

KEY = "sk-live-0123456789abcdefghijklmnop"


async def test_a_vendor_key_is_masked_and_the_rest_of_the_sentence_survives() -> None:
    text, found = mask_text(f"the deploy key is {KEY}, rotate it monthly")
    assert found and found[0][0] == "openai-key"
    assert KEY not in text
    assert text.startswith("the deploy key is sk-l…[redacted]")
    assert text.endswith("rotate it monthly")


async def test_a_private_key_block_goes_whole() -> None:
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    text, found = mask_text(f"pasted here: {block}")
    assert found and found[0][0] == "private-key"
    assert "MIIEow" not in text and "PRIVATE KEY" not in text


async def test_an_assigned_password_is_masked_but_a_short_placeholder_is_not() -> None:
    text, found = mask_text("password: hunter2hunter2 in the fixture")
    assert found and "hunter2hunter2" not in text
    assert "hunt" not in text  # no part of a chosen password is kept as a preview
    # `password: x` is prose about configuring a database, not a credential.
    prose, none = mask_text("set password: x before connecting")
    assert none == [] and prose == "set password: x before connecting"


async def test_a_short_value_is_not_treated_as_a_secret() -> None:
    _, found = mask_text("token: abc123")
    assert found == []


async def test_the_request_is_masked_without_mutating_the_callers_object() -> None:
    op = AddMemory(kind="lesson", fields={"text": f"the api key is {KEY}", "insight": "rotate it"})
    request = ProposalRequest(ops=[op])
    masked, findings = await redact_request(request)
    assert KEY in op.fields["text"]  # the caller's object is untouched
    assert KEY not in masked.ops[0].fields["text"]  # type: ignore[union-attr]
    assert len(findings) == 1
    finding = findings[0]
    assert finding.code == "secret_redacted" and finding.severity == "warning"
    assert finding.data["rule"] == "openai-key"
    assert finding.data["field"] == "fields.text"
    assert "redaction" in finding.message  # tells the caller how to override


async def test_nested_fields_report_where_the_value_was() -> None:
    request = ProposalRequest(
        ops=[
            AddMemory(
                kind="procedure",
                fields={"text": "rotate keys", "steps": [{"text": f"run rotate with {KEY}"}]},
            )
        ]
    )
    _, findings = await redact_request(request)
    assert [f.data["field"] for f in findings] == ["fields.steps[0].text"]


async def test_redaction_off_stores_what_was_sent() -> None:
    request = ProposalRequest(
        ops=[
            AddMemory(
                kind="fact",
                fields={"subject": "concept:linux", "predicate": "has", "object": f"fixture {KEY}"},
            )
        ],
        redaction="off",
    )
    masked, findings = await redact_request(request)
    assert findings == []
    assert masked is request


async def test_unrecognised_long_values_are_not_sent_elsewhere_or_rewritten() -> None:
    value = "ordinary-project-identifier-0123456789"
    op = AddMemory(kind="fact", fields={"text": f"the deployment id is {value}"})
    redacted, findings = await redact_request(ProposalRequest(ops=[op]))
    assert redacted.ops[0].fields["text"].endswith(value)  # type: ignore[union-attr]
    assert findings == []


async def test_an_anthropic_key_is_named_as_one_not_as_an_openai_key() -> None:
    _, found = mask_text("key sk-ant-api03-0123456789abcdefghijklmnop here")
    assert [rule for rule, _ in found] == ["anthropic-key"]
