"""Connectorcontract ``/connector/v1`` (contract §6). Niet-gebouwde functies geven eerlijk ``capability_unavailable``."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import APP_ID, __version__
from ..auth import Principal, require
from ..db import ping
from ..errors import ApiError
from ..events import envelope, redact
from ..hublink import get_link
from ..models import CatalogEntry, CatalogSource, EvidenceRecord, ManifestVersion, OutboxEvent
from ..timeutil import iso, utcnow
from .catalog import entry_out, source_out, version_summary
from .common import check_limit, ctx, decode_cursor, encode_cursor, get_db

router = APIRouter(prefix="/connector/v1", tags=["connector"])
DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[Principal, Depends(require("viewer"))]

CAPABILITIES = ["read"]
NOT_BUILT = {
    "propose": "Voorstellen via de connector zijn nog niet gebouwd; installatievoorbeelden lopen via /catalog/v1/preview.",
    "apply": "Uitvoeren via deze connector bestaat niet: installeren doet de executor van de Hub (R2, goedkeuring).",
    "rollback": "Terugdraaien via deze connector wordt niet ondersteund.",
}


@router.get("/discover", summary="Capabilities en limieten")
def discover(_: Reader) -> dict[str, Any]:
    return {
        "app_id": APP_ID,
        "source_version": __version__,
        "schema_version": "1.0",
        "capabilities": CAPABILITIES,
        "unavailable_capabilities": [*sorted(NOT_BUILT), "stream"],
        "permissions": ["catalog:read", "catalog:validate", "catalog:preview", "catalog:import_preview"],
        "limits": {"page_max": 200, "manifest_max_bytes": 262144, "heavy_requests_per_minute": 120},
        "item_types": ["catalog_entry", "manifest_version", "catalog_source"],
        "timestamp_utc": iso(utcnow()),
    }


@router.get("/health", summary="Gezondheid (healthy/degraded/down/unknown)")
def health(request: Request, db: DB, _: Reader) -> dict[str, Any]:
    now = iso(utcnow())
    checks: list[dict[str, Any]] = []
    db_ok = ping(ctx(request).engine)
    checks.append(
        {
            "name": "database",
            "status": "healthy" if db_ok else "down",
            "observed_at": now,
            "detail": "PostgreSQL bereikbaar" if db_ok else "PostgreSQL niet bereikbaar",
        }
    )
    if db_ok:
        link = get_link(db)
        db.commit()
        checks.append(
            {
                "name": "hub_link",
                "status": "healthy" if link.status == "linked" else "unknown",
                "observed_at": now,
                "detail": {
                    "linked": "gekoppeld",
                    "not_linked": "niet gekoppeld",
                    "failed": "koppelen mislukt",
                    "revoked": "koppeling ingetrokken",
                }.get(link.status, link.status),
            }
        )
        failed = (
            db.scalar(
                select(func.count())
                .select_from(CatalogSource)
                .where(CatalogSource.enabled.is_(True), CatalogSource.last_sync_status == "failed")
            )
            or 0
        )
        never = (
            db.scalar(
                select(func.count())
                .select_from(CatalogSource)
                .where(CatalogSource.enabled.is_(True), CatalogSource.last_sync_status == "never")
            )
            or 0
        )
        checks.append(
            {
                "name": "catalog_sources",
                "status": "degraded" if failed else ("unknown" if never else "healthy"),
                "observed_at": now,
                "detail": f"{failed} bron(nen) mislukt, {never} nooit gesynchroniseerd",
            }
        )
        dead = db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.dead_letter.is_(True))) or 0
        checks.append(
            {
                "name": "outbox",
                "status": "degraded" if dead else "healthy",
                "observed_at": now,
                "detail": f"{dead} event(s) in dead-letter",
            }
        )
    if not db_ok:
        status, reason = "down", "database_unreachable"
    elif any(c["status"] == "degraded" for c in checks):
        status, reason = "degraded", next(c["name"] for c in checks if c["status"] == "degraded")
    else:
        status, reason = "healthy", "ok"
    return {"status": status, "observed_at": now, "reason_code": reason, "checks": checks}


@router.get("/items", summary="Eigen data, gepagineerd")
def items(
    request: Request,
    db: DB,
    _: Reader,
    type: Literal["catalog_entry", "manifest_version", "catalog_source"] = "catalog_entry",
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    limit = check_limit(limit)
    after = decode_cursor(cursor)
    out: list[dict[str, Any]]
    if type == "catalog_entry":
        stmt = select(CatalogEntry).order_by(CatalogEntry.app_id)
        if after is not None:
            stmt = stmt.where(CatalogEntry.app_id > str(after))
        rows = db.scalars(stmt.limit(limit + 1)).all()
        page = rows[:limit]
        out = [
            entry_out(
                e,
                db.get(ManifestVersion, e.latest_version_id) if e.latest_version_id else None,
                db.get(CatalogSource, e.source_id),
            )
            for e in page
        ]
        nxt = encode_cursor(page[-1].app_id) if len(rows) > limit else None
    elif type == "manifest_version":
        stmt2 = select(ManifestVersion).order_by(ManifestVersion.id)
        if after is not None:
            try:
                after_id = uuid.UUID(str(after))
            except ValueError as exc:
                raise ApiError(400, "cursor_invalid", "De cursor is ongeldig; begin opnieuw zonder cursor.") from exc
            stmt2 = stmt2.where(ManifestVersion.id > after_id)
        rows2 = db.scalars(stmt2.limit(limit + 1)).all()
        page2 = rows2[:limit]
        out = [version_summary(v) for v in page2]
        nxt = encode_cursor(str(page2[-1].id)) if len(rows2) > limit else None
    else:
        rows3 = db.scalars(select(CatalogSource).order_by(CatalogSource.name)).all()
        out, nxt = [source_out(s) for s in rows3], None
    return {"type": type, "items": out, "next_cursor": nxt, "observed_at": iso(utcnow())}


@router.post("/propose", status_code=501, summary="Niet beschikbaar (capability_unavailable)")
def propose(_: Reader) -> None:
    raise ApiError(501, "capability_unavailable", NOT_BUILT["propose"], {"capability": "propose"})


@router.post("/apply", status_code=501, summary="Niet beschikbaar (capability_unavailable)")
def apply(_: Reader) -> None:
    raise ApiError(501, "capability_unavailable", NOT_BUILT["apply"], {"capability": "apply"})


@router.post("/rollback", status_code=501, summary="Niet beschikbaar (capability_unavailable)")
def rollback(_: Reader) -> None:
    raise ApiError(501, "capability_unavailable", NOT_BUILT["rollback"], {"capability": "rollback"})


@router.get("/evidence/{evidence_id}", summary="Geredigeerd bewijs")
def evidence(evidence_id: uuid.UUID, db: DB, _: Reader) -> dict[str, Any]:
    rec = db.get(EvidenceRecord, evidence_id)
    if rec is None:
        raise ApiError(404, "not_found", "Bewijs niet gevonden.")
    return {
        "id": str(rec.id),
        "kind": rec.kind,
        "created_at": iso(rec.created_at),
        "actor": rec.actor,
        "trace_id": rec.trace_id,
        "summary": redact(rec.summary),
    }


@router.get("/events", summary="Outbox voor polling (cursor = laatst gelezen volgnummer)")
def events(db: DB, _: Reader, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    limit = check_limit(limit)
    try:
        after = int(cursor) if cursor else 0
    except ValueError as exc:
        raise ApiError(400, "cursor_invalid", "De cursor moet een volgnummer zijn.") from exc
    rows = db.scalars(select(OutboxEvent).where(OutboxEvent.seq > after).order_by(OutboxEvent.seq).limit(limit)).all()
    return {
        "items": [envelope(e) for e in rows],
        "next_cursor": str(rows[-1].seq) if rows else str(after),
        "observed_at": iso(utcnow()),
    }
