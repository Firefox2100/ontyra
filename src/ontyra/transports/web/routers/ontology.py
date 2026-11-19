"""Ontology pages: concepts, one concept with its history, edit forms with a preview step, the
change history (with revert) and the health report (plans/23)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ontyra.models import RecordId
from ontyra.models.ontology import ConceptDefinition, RelationRequest
from ontyra.models.ontology_change import (
    Change,
    ChangeRequest,
    DefineChange,
    DeprecateChange,
    EditChange,
    MergeChange,
    RelateChange,
    ReparentChange,
    RestoreChange,
    SplitChange,
    UnrelateChange,
)
from ontyra.services import errors
from ontyra.services.ontology import OntologyService
from ontyra.transports.rest.deps import (
    OntologyChangeServiceDep,
    OntologyCheckerDep,
    OntologyServiceDep,
)
from ontyra.transports.web.deps import SameOriginDep, UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

OPS = (
    "define",
    "edit",
    "relate",
    "unrelate",
    "reparent",
    "deprecate",
    "merge",
    "split",
    "restore",
)


def _list(text: str, sep: str = ",") -> list[str]:
    return [p.strip() for p in text.replace("\n", sep).split(sep) if p.strip()]


async def build_change(ontology: OntologyService, fields: Mapping[str, Any]) -> Change:
    """The change a form describes (every value is text; empty means not given)."""

    def get(name: str) -> str:
        return str(fields.get(name, "") or "").strip()

    op = get("op")
    if op == "define":
        return DefineChange(
            concept=ConceptDefinition(
                label=get("label"),
                definition=get("definition") or None,
                aliases=_list(get("aliases")),
                is_a=_list(get("is_a")),
            )
        )
    if op == "edit":
        return EditChange(
            concept=get("concept"),
            label=get("label") or None,
            definition=get("definition") if "definition" in fields and get("definition") else None,
            add_aliases=_list(get("add_aliases")),
            remove_aliases=_list(get("remove_aliases")),
        )
    if op in ("relate", "unrelate"):
        rel = RelationRequest(
            source=get("source"),
            relation=get("relation") or "is_a",  # type: ignore[arg-type]
            target=get("target"),
        )
        return RelateChange(relation=rel) if op == "relate" else UnrelateChange(relation=rel)
    if op == "reparent":
        return ReparentChange(
            concept=get("concept"),
            relation=get("relation") or "is_a",  # type: ignore[arg-type]
            from_parent=get("from_parent"),
            to_parent=get("to_parent"),
        )
    if op == "deprecate":
        return DeprecateChange(
            concept=get("concept"),
            replaced_by=get("replaced_by") or None,
            reason=get("reason") or None,
        )
    if op == "merge":
        return MergeChange(source=get("source"), into=get("into"))
    if op == "split":
        into: list[ConceptDefinition | str] = []
        for line in _list(get("into"), "\n"):
            try:
                await ontology.resolve(line)
                into.append(line)
            except (errors.NotFoundError, errors.InvalidRequestError):
                into.append(ConceptDefinition(label=line))
        return SplitChange(source=get("source"), into=into)
    if op == "restore":
        return RestoreChange(concept=get("concept"), version=int(get("version") or 0))
    raise errors.InvalidRequestError(f"unknown operation {op!r}")


@router.get("/concepts", response_class=HTMLResponse)
async def concepts(
    request: Request,
    caller: UiCallerDep,
    ontology: OntologyServiceDep,
    term: Annotated[str, Query()] = "",
    deprecated: Annotated[bool, Query()] = False,
) -> HTMLResponse:
    rows = (
        await ontology.lookup(caller, term)
        if term.strip()
        else await ontology.list(caller, include_deprecated=deprecated)
    )
    return templates.TemplateResponse(
        request,
        "concepts.html",
        {"rows": rows, "term": term, "deprecated": deprecated, "nav": "concepts"},
    )


@router.get("/concepts/{concept_id}", response_class=HTMLResponse)
async def concept(
    request: Request, concept_id: str, caller: UiCallerDep, ontology: OntologyServiceDep
) -> HTMLResponse:
    cid = RecordId("concept", concept_id)
    return templates.TemplateResponse(
        request,
        "concept.html",
        {
            "c": await ontology.get(caller, cid),
            "history": await ontology.history(cid),
            "nav": "concepts",
        },
    )


@router.get("/ontology/new", response_class=HTMLResponse)
async def new_change(
    request: Request, op: Annotated[str, Query()] = "define", concept: Annotated[str, Query()] = ""
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "ontology_form.html",
        {"op": op if op in OPS else "define", "ops": OPS, "concept": concept, "nav": "concepts"},
    )


@router.post("/ontology/preview", response_class=HTMLResponse, dependencies=[SameOriginDep])
async def preview(
    request: Request,
    caller: UiCallerDep,
    ontology: OntologyServiceDep,
    changes: OntologyChangeServiceDep,
) -> HTMLResponse:
    fields = dict((await request.form()).items())
    rationale = str(fields.get("rationale", "")).strip()
    try:
        change = await build_change(ontology, fields)
        shown = await changes.preview(caller, ChangeRequest(change=change, rationale=rationale))
        error = None
    except (errors.ServiceError, ValueError) as exc:
        shown, error = None, str(exc)
    return templates.TemplateResponse(
        request,
        "ontology_preview.html",
        {"p": shown, "error": error, "fields": fields, "rationale": rationale, "nav": "concepts"},
    )


@router.post("/ontology/apply", dependencies=[SameOriginDep])
async def apply(
    request: Request,
    caller: UiCallerDep,
    ontology: OntologyServiceDep,
    changes: OntologyChangeServiceDep,
) -> RedirectResponse:
    fields = dict((await request.form()).items())
    change = await build_change(ontology, fields)
    await changes.propose(
        caller, ChangeRequest(change=change, rationale=str(fields.get("rationale", "")).strip())
    )
    target = fields.get("concept") or fields.get("source") or fields.get("label") or ""
    try:
        rid = await ontology.resolve(str(target))
        return RedirectResponse(f"/ui/concepts/{rid.id}", status_code=303)
    except (errors.ServiceError, ValueError):
        return RedirectResponse("/ui/concepts", status_code=303)


@router.get("/ontology/history", response_class=HTMLResponse)
async def history(
    request: Request,
    caller: UiCallerDep,
    changes: OntologyChangeServiceDep,
    concept: Annotated[str, Query()] = "",
    op: Annotated[str, Query()] = "",
) -> HTMLResponse:
    target = RecordId("concept", concept.removeprefix("concept:")) if concept else None
    rows = await changes.history(caller, 200, concept=target, op=op or None)
    return templates.TemplateResponse(
        request,
        "ontology_history.html",
        {"rows": rows, "concept": concept, "op": op, "nav": "concepts"},
    )


@router.post("/ontology/changes/{change_id}/revert", dependencies=[SameOriginDep])
async def revert(
    change_id: str,
    caller: UiCallerDep,
    changes: OntologyChangeServiceDep,
    reason: Annotated[str, Form()] = "reverted in the console",
) -> RedirectResponse:
    await changes.revert(caller, RecordId("ontology_change", change_id), reason or "reverted")
    return RedirectResponse("/ui/ontology/history", status_code=303)


@router.get("/ontology/check", response_class=HTMLResponse)
async def check(request: Request, caller: UiCallerDep, checker: OntologyCheckerDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "ontology_check.html", {"report": await checker.check(caller), "nav": "concepts"}
    )
