"""Redaction of credentials on the way into memory.

A memory is written once and read by every later session, so a key pasted into a memory leaks
into every transcript that recalls it. The daemon therefore masks values that look like
credentials *before* validation, and says so in the proposal's findings: the caller must be able
to see that what it sent is not what was stored, and to resend with `redaction: "off"` when the
match is a false positive (a test fixture, a documented example).

Only shapes that are unambiguous are matched — vendor prefixes and assigned passwords. Redaction
is deliberately local and deterministic: proposal content is never sent to a decision model, and
an uncertain model verdict can never silently rewrite persisted content.
"""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from ontyra.models.base import Model
from ontyra.models.proposals import (
    AddMemory,
    Annotate,
    Finding,
    ProposalRequest,
    ReviseMemory,
)

Redaction = Literal["auto", "off"]


class Mask(Model):
    """One value masked out of a proposal, as the caller sees it in the findings."""

    rule: str
    field: str
    preview: str  # the first characters, enough to recognise which value it was


def _masked(value: str) -> str:
    return f"{value[:4]}…[redacted]"


# Each rule: (name, pattern, how to mask the match). The pattern must be narrow enough that a
# false positive is rare: masking something the user wanted stored is worse than leaking a test key.
_RULES_BEFORE: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),  # before the broader sk-
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)
# An assigned password is only a secret when the value is long enough to be one: `password: x`
# in a lesson about configuring a database is a placeholder, not a credential.
_ASSIGNED = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token)\b"
    r"(\s*[:=]\s*)(['\"]?)([^\s'\"]{8,})\3"
)


def mask_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Mask every credential-looking value in `text`.

    Returns the masked text and what was masked, as `(rule, masked)` pairs in the order they
    appeared. A text with nothing to mask comes back unchanged, and the list is empty."""
    found: list[tuple[str, str]] = []
    masked = text
    for rule, pattern in _RULES_BEFORE:

        def replace(match: re.Match[str], rule: str = rule) -> str:
            value = match.group(0)
            preview = _masked(value)
            found.append((rule, preview))
            return preview

        masked = pattern.sub(replace, masked)
    for match in _ASSIGNED.finditer(masked):
        value = match.group(4)
        # A vendor prefix is public and names the key; the start of a chosen password is not, and
        # four characters of an eight-character one is half of it.
        preview = "[redacted]"
        found.append(("assigned-secret", preview))
        masked = masked.replace(value, preview, 1)
    return masked, found


def _walk(value: Any, path: str, out: list[tuple[str, str, str]]) -> Any:
    """Mask strings inside a JSON-ish value, remembering where each one was."""
    if isinstance(value, str):
        masked, found = mask_text(value)
        out.extend((path, rule, preview) for rule, preview in found)
        return masked
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return [_walk(item, f"{path}[{index}]", out) for index, item in enumerate(items)]
    if isinstance(value, dict):
        mapping = cast("dict[str, Any]", value)
        return {
            key: _walk(item, f"{path}.{key}" if path else str(key), out)
            for key, item in mapping.items()
        }
    return value


async def redact_request(request: ProposalRequest) -> tuple[ProposalRequest, list[Finding]]:
    """Mask credentials in every op of `request`, and describe what was masked.

    The request is not modified: a new one is returned with masked fields, so the stored
    proposal records exactly what was validated and committed."""
    if request.redaction == "off":
        return request, []
    findings: list[Finding] = []
    ops: list[Any] = []
    for index, op in enumerate(request.ops):
        if not isinstance(op, (AddMemory, ReviseMemory, Annotate)):
            ops.append(op)
            continue
        found: list[tuple[str, str, str]] = []
        if isinstance(op, Annotate):
            text = _walk(op.text, "text", found)
            ops.append(op.model_copy(update={"text": text}))
        else:
            fields = _walk(op.fields, "fields", found)
            ops.append(op.model_copy(update={"fields": fields}))
        findings.extend(
            Finding(
                severity="warning",
                code="secret_redacted",
                op=index,
                message=(
                    f"a value that looks like {rule} was replaced with `{preview}` before "
                    "storing. If it is not a real credential (a test value, a documented "
                    'example), send the proposal again with `redaction: "off"`.'
                ),
                data={"rule": rule, "field": path, "preview": preview},
            )
            for path, rule, preview in found
        )
    return request.model_copy(update={"ops": ops}), findings


__all__ = ["mask_text", "redact_request"]
