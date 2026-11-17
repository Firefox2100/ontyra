"""Translate service errors into HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ontyra.services import errors


def _json(status: int, detail: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, **extra})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(errors.NotFoundError)
    async def _not_found(_: Request, exc: errors.NotFoundError) -> JSONResponse:
        return _json(404, str(exc))

    @app.exception_handler(errors.ConflictError)
    async def _conflict(_: Request, exc: errors.ConflictError) -> JSONResponse:
        return _json(409, str(exc))

    @app.exception_handler(errors.InvalidRequestError)
    async def _bad_request(_: Request, exc: errors.InvalidRequestError) -> JSONResponse:
        return _json(422, str(exc))

    @app.exception_handler(errors.ProposalStateError)
    async def _state(_: Request, exc: errors.ProposalStateError) -> JSONResponse:
        return _json(409, str(exc))

    @app.exception_handler(errors.InvalidProposalError)
    async def _invalid(_: Request, exc: errors.InvalidProposalError) -> JSONResponse:
        return _json(422, str(exc), findings=[f.model_dump(mode="json") for f in exc.findings])

    @app.exception_handler(errors.PermissionDeniedError)
    async def _forbidden(_: Request, exc: errors.PermissionDeniedError) -> JSONResponse:
        return _json(403, str(exc))

    @app.exception_handler(errors.AuthenticationError)
    async def _unauthenticated(_: Request, exc: errors.AuthenticationError) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )
