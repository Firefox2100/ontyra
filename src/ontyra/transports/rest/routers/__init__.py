"""One router per module; this package is the single place that lists them."""

from fastapi import APIRouter

from ontyra.transports.rest.routers import (
    admin,
    concepts,
    consolidation,
    episodes,
    explore,
    feedback,
    health,
    memories,
    ontology_changes,
    proposals,
    recall,
    reranker,
    resources,
    sources,
    tasks,
    vetting,
)

# (router, mounted under /v1?) -- health stays at the root for probes
VERSIONED: list[APIRouter] = [
    episodes.router,
    proposals.router,
    memories.router,
    recall.router,
    explore.router,
    consolidation.router,
    tasks.router,
    concepts.router,
    ontology_changes.router,
    resources.router,
    feedback.router,
    reranker.router,
    sources.router,
    vetting.router,
    admin.router,
]
UNVERSIONED: list[APIRouter] = [health.router]

__all__ = ["UNVERSIONED", "VERSIONED"]
