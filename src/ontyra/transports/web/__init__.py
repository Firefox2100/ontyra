"""Operator console: server-rendered pages over the same services as REST (plans/19).

Opt-in (`ONTYRA_UI__ENABLED`). With no credential required it acts as a human caller with no
login, so keep the daemon on localhost; otherwise it signs in with a human credential or the
single-user token (`/ui/login`, a cookie session). Pages hold no logic: they call a service
and render its models.
"""

from __future__ import annotations

from fastapi import APIRouter

from ontyra.transports.web.routers import (
    feedback,
    home,
    memories,
    ontology,
    recall,
    review,
    session,
    tasks,
)

ROUTERS: list[APIRouter] = [
    home.router,
    review.router,
    tasks.router,
    feedback.router,
    memories.router,
    ontology.router,
    recall.router,
    session.router,
]

__all__ = ["ROUTERS"]
