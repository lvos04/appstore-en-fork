"""Foutformaat en foutcatalogus (contract 1.3 §5): ``{"code", "message" (NL), "details", "trace_id"}``.

Eén code per situatie, gelijk aan de referentie van de Hub: 400 ``idempotency_key_required``/``…_invalid``; 401
``unauthorized`` + ``details.reason`` (alleen authenticatie); 403 ``forbidden`` of een vaste eigen code; 404 ``not_found``
(ook "niet zichtbaar", geen orakel); 409 ``idempotency_in_progress``, ``conflict`` of een eigen code; 412/428 voor ETag;
413 ``payload_too_large``; 422 ``validation_error`` + ``details.fouten[]`` (schemafout, onbekend veld, te lange invoer),
``idempotency_key_reused`` of een eigen code voor foute invoer binnen een geldige sessie; 429 ``rate_limited`` +
``Retry-After``; 501 ``capability_unavailable``; 503 ``<afhankelijkheid>_unavailable`` + ``Retry-After``.

Authenticatie vóór bodyvalidatie (§4.7, RV9): ook een onleesbare JSON-body op een beveiligde route geeft zonder geldige
aanmelding altijd eerst 401 (zie :func:`_auth_before_body`).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logfilter import db_error_text

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


def validation_error(message: str, fouten: list[dict[str, Any]], **details: Any) -> ApiError:
    """422 ``validation_error`` + ``details.fouten[]`` (§5); ``details.reason`` voor een eigen situatie."""
    return ApiError(422, "validation_error", message, {"fouten": fouten, **details})


def _auth_dependency(request: Request) -> Any:
    """De aanmeldings-dependency van de route (gemarkeerd met ``_appstore_auth``), of ``None`` (openbare route)."""
    route = request.scope.get("route")
    stack = [getattr(route, "dependant", None)]
    while stack:
        dep = stack.pop()
        if dep is None:
            continue
        call = getattr(dep, "call", None)
        if call is not None and getattr(call, "_appstore_auth", False):
            return call
        stack.extend(getattr(dep, "dependencies", []) or [])
    return None


def _auth_before_body(request: Request, exc: RequestValidationError) -> None:
    """§4.7: authenticatie vóór schemavalidatie. FastAPI leest een JSON-body vóór de dependencies; bij onleesbare JSON
    wordt de aanmelding hier alsnog eerst gedaan (gooit :class:`ApiError` 401/403/503)."""
    if getattr(request.state, "principal", None) is not None:
        return  # al aangemeld (de dependency liep vóór de schemafout)
    if not any(err.get("type") == "json_invalid" for err in exc.errors()):
        return
    dependency = _auth_dependency(request)
    if dependency is not None:
        dependency(request)


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
        try:
            _auth_before_body(request, exc)
        except ApiError as auth_error:
            return JSONResponse(
                error_body(request, auth_error.code, auth_error.message, auth_error.details),
                status_code=auth_error.status,
                headers=auth_error.headers,
            )
        fouten = [
            {"veld": ".".join(str(p) for p in err.get("loc", ())), "fout": err.get("msg", ""), "type": err.get("type")}
            for err in exc.errors()
        ]
        return JSONResponse(
            error_body(request, "validation_error", "De invoer is ongeldig; zie details.fouten.", {"fouten": fouten}),
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
        if isinstance(exc, DBAPIError):
            # KLEIN-34: geen str(exc)/traceback; daarin staat de DETAIL-regel van PostgreSQL (sleutelwaarde of rij)
            log.error("onverwachte fout (trace_id=%s): %s", trace_id(request), db_error_text(exc))
        else:
            log.exception("onverwachte fout (trace_id=%s)", trace_id(request))
        return JSONResponse(
            error_body(request, "internal_error", "Interne fout; zie de app-log met dit trace_id."), status_code=500
        )
