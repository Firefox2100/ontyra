"""Operator console: server-rendered pages over the same services as REST (plans/19).

Opt-in (`ONTYRA_UI__ENABLED`). It acts as a human caller with no login, so keep the daemon on
localhost. Pages hold no logic: they call a service and render its models.
"""

from __future__ import annotations

from fastapi import APIRouter

from ontyra.transports.web.routers import home, memories, ontology, recall, review

ROUTERS: list[APIRouter] = [
    home.router,
    review.router,
    memories.router,
    ontology.router,
    recall.router,
]

__all__ = ["ROUTERS"]
