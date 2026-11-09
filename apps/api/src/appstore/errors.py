"""Foutformaat (contract §5): ``{"code", "message" (NL), "details", "trace_id"}``."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("appstore")


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def trace_id(request: Request) -> str:
    value = getattr(request.state, "trace_id", None)
    return str(value) if value else str(uuid.uuid4())


def error_body(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}, "trace_id": trace_id(request)}


_HTTP_CODES = {
    400: ("bad_request", "Ongeldig verzoek."),
    401: ("unauthorized", "Niet aangemeld of ongeldige aanmelding."),
    403: ("forbidden", "Geen toegang."),
    404: ("not_found", "Niet gevonden."),
    405: ("method_not_allowed", "Deze methode is hier niet toegestaan."),
    409: ("conflict", "Conflict met de huidige toestand."),
    413: ("payload_too_large", "Verzoek is te groot."),
    415: ("unsupported_media_type", "Inhoudstype wordt niet ondersteund."),
    429: ("rate_limited", "Te veel verzoeken; probeer het later opnieuw."),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            error_body(request, exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"loc": [str(p) for p in err.get("loc", ())], "type": err.get("type"), "msg": err.get("msg")}
            for err in exc.errors()
        ]
        return JSONResponse(
            error_body(request, "validation_failed", "De invoer klopt niet; zie details.", {"errors": problems}),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "Fout bij de verwerking."))
        return JSONResponse(
            error_body(request, code, message), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("onverwachte fout (trace_id=%s)", trace_id(request))
        return JSONResponse(
            error_body(request, "internal_error", "Interne fout; zie de app-log met dit trace_id."), status_code=500
        )
