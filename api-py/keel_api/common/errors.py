"""The one error shape every route answers: `{error, hint}` (hint is null when there is none), as common/Errors.kt."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("keel_api")


def body(error: str, hint: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"error": error, "hint": hint}
    if extra:
        out.update(extra)
    return out


def error_response(status: int, error: str, hint: str | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(body(error, hint), status_code=status, headers=headers)


class ApiError(Exception):
    status = 500

    def __init__(self, error: str, hint: str | None = None, extra: dict[str, Any] | None = None, status: int | None = None):
        super().__init__(error)
        self.error = error
        self.hint = hint
        self.extra = extra
        if status is not None:
            self.status = status


class BadRequest(ApiError):
    status = 400


class Forbidden(ApiError):
    status = 403


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError) -> JSONResponse:
        return JSONResponse(body(e.error, e.hint, e.extra), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, e: RequestValidationError) -> JSONResponse:
        errors = e.errors()
        if any(err.get("type") == "json_invalid" for err in errors):
            return error_response(400, "The request body is not valid JSON for this route", str(errors[0].get("msg"))[:300])
        if all(err.get("loc", ("",))[0] == "query" for err in errors):
            return error_response(400, "A query parameter is missing or wrong", _fields(errors))
        return error_response(400, "Some fields are not valid", _fields(errors))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        if e.status_code == 405:
            return error_response(405, "This method is not allowed here", e.detail if isinstance(e.detail, str) else None)
        if e.status_code == 415:
            return error_response(415, "Send JSON", e.detail if isinstance(e.detail, str) else None)
        if e.status_code == 404:
            return error_response(404, "Not found", e.detail if isinstance(e.detail, str) else None)
        return error_response(e.status_code, str(e.detail))

    @app.exception_handler(Exception)
    async def _other(_: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled error")
        if isinstance(e, FileNotFoundError):
            return error_response(500, f"A file went away while keel read it: {e.filename}", "Try again.")
        return error_response(500, "Something went wrong in the api", (str(e) or None) and str(e)[:300])


def _fields(errors: list[dict[str, Any]]) -> str:
    return ", ".join(f"{'.'.join(str(x) for x in err.get('loc', ())[1:])} {err.get('msg', '')}".strip() for err in errors)
