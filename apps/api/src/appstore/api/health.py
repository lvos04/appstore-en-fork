"""``/healthz`` (proces leeft) en ``/readyz`` (database bereikbaar en schema up-to-date), zonder auth of geheimen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import APP_ID, __version__
from ..db import ping
from ..migrations import current_revision, head_revision
from ..timeutil import iso, utcnow
from .common import AppContext, ctx

router = APIRouter(tags=["gezondheid"])


@router.get("/healthz", summary="Proces leeft")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "app_id": APP_ID, "version": __version__, "observed_at": iso(utcnow())}


@dataclass(frozen=True)
class Readiness:
    """Eén meting van de afhankelijkheden; gedeeld door ``/readyz`` en de statuspagina."""

    database: bool
    schema: Literal["ok", "verouderd", "onbekend"] | None  # None = niet te meten (database weg)
    current: str | None = None
    head: str | None = None

    def checks(self) -> dict[str, str]:
        out = {"database": "ok" if self.database else "down"}
        if self.schema == "ok":
            out["schema"] = "ok"
        elif self.schema == "verouderd":
            out["schema"] = f"verouderd ({self.current} ≠ {self.head})"
        elif self.schema == "onbekend":
            out["schema"] = "onbekend"
        return out

    @property
    def ready(self) -> bool:
        return self.database and self.schema == "ok"


def measure(c: AppContext) -> Readiness:
    if not ping(c.engine):
        return Readiness(database=False, schema=None)
    try:
        current, head = current_revision(c.engine), head_revision(c.settings.database_url)
    except Exception:
        return Readiness(database=True, schema="onbekend")
    return Readiness(database=True, schema="ok" if current == head else "verouderd", current=current, head=head)


@router.get(
    "/readyz",
    summary="Afhankelijkheden in orde",
    description=(
        "200 als database en schema in orde zijn. De koppeling met de Hub telt **niet** mee (contract 1.3 §5): een "
        "niet-gekoppelde, ontkoppelde of ingetrokken dienst, of een onleesbare koppelstatus, is ready. `hub_link` geeft "
        "alleen ter informatie de toestand en `last_error` uit de witte lijst (§3.5)."
    ),
)
def readyz(request: Request) -> JSONResponse:
    c = ctx(request)
    m = measure(c)
    checks, ready = m.checks(), m.ready
    public = c.store.read().public()
    hub_link = {k: public[k] for k in ("state", "koppelen_bezig", "last_error")}
    return JSONResponse(
        {
            "status": "ready" if ready else "not_ready",
            "checks": checks,
            "hub_link": hub_link,
            "observed_at": iso(utcnow()),
        },
        status_code=200 if ready else 503,
    )
