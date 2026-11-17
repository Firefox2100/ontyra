from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ontyra.transports.rest.deps import OntologyServiceDep
from ontyra.transports.web.deps import UiCallerDep, templates

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


@router.get("/concepts", response_class=HTMLResponse)
async def concepts(
    request: Request,
    caller: UiCallerDep,
    ontology: OntologyServiceDep,
    term: Annotated[str, Query()] = "",
) -> HTMLResponse:
    rows = await ontology.lookup(caller, term) if term.strip() else await ontology.list(caller)
    return templates.TemplateResponse(
        request, "concepts.html", {"rows": rows, "term": term, "nav": "concepts"}
    )
