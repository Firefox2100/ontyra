"""Pure scorers: turn what a model said or called into problems (empty list: passed)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from evaluation_test.framework.llm import ToolCall
from evaluation_test.framework.models import DecisionCase, ToolCase


@dataclass
class ToolScore:
    chosen: str | None
    problems: list[str] = field(default_factory=list)
    invalid_args: list[str] = field(default_factory=list)  # schema violations (reported apart)
    skipped: bool = False  # the expected tool is not offered in this tool set

    @property
    def passed(self) -> bool:
        return not self.problems and not self.skipped


def schema_errors(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message[:120]}"
        for e in validator.iter_errors(args)
    ]


def score_tool_call(
    case: ToolCase, calls: list[ToolCall], schemas: dict[str, dict[str, Any]]
) -> ToolScore:
    """`schemas`: tool name -> input schema of the tool set offered to the model."""
    if case.expect and case.expect not in schemas:
        return ToolScore(None, skipped=True)
    first = calls[0] if calls else None
    score = ToolScore(first.name if first else None)
    allowed = {x for x in [case.expect, *case.accept] if x}
    if first is None:
        if case.expect:
            score.problems.append(f"no tool called; expected {case.expect}")
    elif first.name not in allowed:
        wanted = case.expect or "no tool"
        score.problems.append(f"called {first.name}; expected {wanted}")
    for call in calls:
        if call.name in case.forbid:
            score.problems.append(f"called forbidden tool {call.name}")
    if first is not None and first.name in allowed:
        schema = schemas.get(first.name)
        if schema is not None:
            score.invalid_args = schema_errors(schema, first.args)
            if score.invalid_args:
                score.problems.append(f"arguments do not fit the schema: {score.invalid_args[0]}")
        text = json.dumps(first.args, ensure_ascii=False).casefold()
        missing = [s for s in case.contains if s.casefold() not in text]
        if missing:
            score.problems.append(f"arguments lack {missing}")
        if case.contains_any and not any(s.casefold() in text for s in case.contains_any):
            score.problems.append(f"arguments lack any of {case.contains_any}")
    return score


def check_text(answer: str, must: list[str], avoid: list[str]) -> list[str]:
    """Patterns (regular expressions, case-insensitive, multi-line) the answer must/mustn't match."""
    flags = re.IGNORECASE | re.MULTILINE
    problems = [f"missing /{p}/" for p in must if not re.search(p, answer, flags)]
    problems += [f"contains /{p}/" for p in avoid if re.search(p, answer, flags)]
    return problems


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model's answer (fenced or bare), or None."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for blob in candidates:
        start = blob.find("{")
        while start != -1:
            depth = 0
            for i in range(start, len(blob)):
                depth += {"{": 1, "}": -1}.get(blob[i], 0)
                if depth == 0:
                    try:
                        parsed = json.loads(blob[start : i + 1])
                    except ValueError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
            start = blob.find("{", start + 1)
    return None


@dataclass
class DecisionScore:
    decision: str | None
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def score_decision(case: DecisionCase, answer: str, choices: list[str]) -> DecisionScore:
    parsed = extract_json(answer)
    if parsed is None:
        return DecisionScore(None, ["no JSON answer"])
    decision = parsed.get("decision")
    if not isinstance(decision, str) or decision not in choices:
        return DecisionScore(
            decision if isinstance(decision, str) else None,
            [f"decision {decision!r} is not one of {choices}"],
        )
    score = DecisionScore(decision)
    if decision not in case.accept:
        score.problems.append(f"decided {decision}; right: {case.accept}")
    note = parsed.get("note")
    if (
        case.note_needed
        and decision in ("distinct", "keep")
        and not (isinstance(note, str) and note.strip())
    ):
        score.problems.append("no note saying why")
    return score


@dataclass
class WriteScore:
    wrote: bool
    problems: list[str] = field(default_factory=list)
    rejected: int = 0  # `remember` calls the daemon rejected or the model got wrong
    evidence: bool = False  # the proposal cited an episode (informational)

    @property
    def passed(self) -> bool:
        return not self.problems


def score_write(case: Any, calls: list[Any]) -> WriteScore:
    """`calls`: the executed tool calls of the run (`agent.Executed`)."""
    remembers = [c for c in calls if c.call.name == "remember"]
    good = [
        c for c in remembers if not c.error and c.data.get("status") in ("committed", "pending")
    ]
    score = WriteScore(wrote=bool(good), rejected=len(remembers) - len(good))
    if not case.should_write:
        if good:
            score.problems.append("wrote a memory that should not have been written")
        return score
    if not good:
        why = ""
        if remembers:
            findings = remembers[-1].data.get("findings") or []
            why = "; ".join(f"{f.get('code')}" for f in findings[:3]) or remembers[-1].text[:120]
        score.problems.append("nothing was written" + (f" ({why})" if why else ""))
        return score
    ops = [
        op
        for c in good
        for op in c.call.args.get("proposal", {}).get("ops", [])
        if isinstance(op, dict) and op.get("op") == "add"
    ]
    if len(ops) != case.count:
        score.problems.append(f"added {len(ops)} memories; expected {case.count}")
    if case.kinds:
        wrong = sorted({str(o.get("kind")) for o in ops} - set(case.kinds))
        if wrong:
            score.problems.append(f"kind {wrong}; expected one of {case.kinds}")
    text = json.dumps([c.call.args for c in good], ensure_ascii=False).casefold()
    missing = [s for s in case.contains if s.casefold() not in text]
    if missing:
        score.problems.append(f"the proposal lacks {missing}")
    codes = {f.get("code") for c in good for f in (c.data.get("findings") or [])}
    if "unscoped_directive" in codes:
        score.problems.append("a directive without a scope (unscoped_directive)")
    if any(c.data.get("status") != "committed" for c in good):
        score.problems.append("held, not committed")
    score.evidence = any(o.get("evidence") for o in ops)
    return score
