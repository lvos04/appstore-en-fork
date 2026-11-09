"""``/healthz`` (proces leeft) en ``/readyz`` (database bereikbaar en schema up-to-date), zonder auth of geheimen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import APP_ID, __version__
from ..db import ping
from ..migrations import current_revision, head_revision
from ..timeutil import iso, utcnow
from .common import ctx

router = APIRouter(tags=["gezondheid"])


@router.get("/healthz", summary="Proces leeft")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "app_id": APP_ID, "version": __version__, "observed_at": iso(utcnow())}


@router.get("/readyz", summary="Afhankelijkheden in orde")
def readyz(request: Request) -> JSONResponse:
    c = ctx(request)
    checks: dict[str, str] = {}
    db_ok = ping(c.engine)
    checks["database"] = "ok" if db_ok else "down"
    if db_ok:
        try:
            current, head = current_revision(c.engine), head_revision(c.settings.database_url)
            checks["schema"] = "ok" if current == head else f"verouderd ({current} ≠ {head})"
        except Exception:
            checks["schema"] = "onbekend"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks, "observed_at": iso(utcnow())},
        status_code=200 if ready else 503,
    )
