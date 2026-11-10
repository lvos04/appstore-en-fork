"""Connectorcontract ``/connector/v1`` (contract 1.2 §6). Niet-gebouwde functies geven eerlijk ``capability_unavailable``.

Nieuw in 1.2: ``GET /connector/v1/hub-link`` (koppelstatus, witte lijst §3.5 + gepinde kids en ``rotation_sequence``)
en ``POST /connector/v1/hub-link/unlink`` (ontkoppelen door de Hub met een dienst-JWT of een beheerder, §3.8).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import APP_ID, __version__
from ..auth import Principal, require, require_service_or_admin
from ..errors import ApiError
from ..events import envelope, redact
from ..hublink_store import LinkState
from ..idempotency import run_idempotent
from ..models import (
    CatalogEntry,
    CatalogSource,
    DecisionRecord,
    EvidenceRecord,
    ExistingApp,
    ManifestVersion,
    OutboxEvent,
)
from ..timeutil import iso, utcnow
from .catalog import entry_out, source_out, version_summary
from .common import check_limit, ctx, cursor_invalid, decode_cursor, encode_cursor, get_db, tid
from .existing_apps import decision_summary, existing_out
from .health import measure

router = APIRouter(prefix="/connector/v1", tags=["connector"])
DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[Principal, Depends(require("viewer"))]
Unlinker = Annotated[Principal, Depends(require_service_or_admin)]

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
        "permissions": [
            "catalog:read",
            "catalog:validate",
            "catalog:preview",
            "catalog:import_preview",
            "catalog:import",
            "catalog:decommission_plan",
        ],
        "limits": {"page_max": 200, "manifest_max_bytes": 262144, "heavy_requests_per_minute": 120},
        "item_types": ["catalog_entry", "manifest_version", "catalog_source", "existing_app", "decision_record"],
        "timestamp_utc": iso(utcnow()),
    }


#: ernst van een check; het totaal is de slechtste (zoals de statuspagina): onbekend is nooit 'healthy' (contract §1)
SEVERITY = {"healthy": 0, "unknown": 1, "degraded": 2, "down": 3}


@router.get("/health", summary="Gezondheid (healthy/degraded/down/unknown; totaal = slechtste check)")
def health(request: Request, db: DB, _: Reader) -> dict[str, Any]:
    now = iso(utcnow())
    checks: list[dict[str, Any]] = []
    ready = measure(ctx(request))
    db_ok = ready.database
    checks.append(
        {
            "name": "database",
            "status": "healthy" if db_ok else "down",
            "observed_at": now,
            "detail": "PostgreSQL bereikbaar" if db_ok else "PostgreSQL niet bereikbaar",
        }
    )
    if db_ok:
        schema_status, schema_detail = {
            "ok": ("healthy", "schema up-to-date"),
            "verouderd": ("down", f"migratie niet uitgevoerd ({ready.current or 'leeg'} ≠ {ready.head})"),
        }.get(ready.schema or "", ("unknown", "schemaversie niet leesbaar"))
        checks.append({"name": "schema", "status": schema_status, "observed_at": now, "detail": schema_detail})
    link = ctx(request).store.read()
    checks.append(
        {
            "name": "hub_link",
            "status": "healthy" if link.linked and link.confirmed_at else "unknown",
            "observed_at": now,
            "detail": (
                ("gekoppeld en bevestigd" if link.confirmed_at else "gekoppeld, nog niet bevestigd door de Hub")
                if link.linked
                else link.state
            ),
        }
    )
    if db_ok and ready.schema == "ok":
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
    status = max((c["status"] for c in checks), key=SEVERITY.__getitem__)
    if not db_ok:
        reason = "database_unreachable"
    elif status == "healthy":
        reason = "ok"
    else:
        reason = next(c["name"] for c in checks if c["status"] == status)
    return {"status": status, "observed_at": now, "reason_code": reason, "checks": checks}


@router.get("/items", summary="Eigen data, gepagineerd")
def items(
    request: Request,
    db: DB,
    _: Reader,
    type: Literal[
        "catalog_entry", "manifest_version", "catalog_source", "existing_app", "decision_record"
    ] = "catalog_entry",
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
                raise cursor_invalid() from exc
            stmt2 = stmt2.where(ManifestVersion.id > after_id)
        rows2 = db.scalars(stmt2.limit(limit + 1)).all()
        page2 = rows2[:limit]
        out = [version_summary(v) for v in page2]
        nxt = encode_cursor(str(page2[-1].id)) if len(rows2) > limit else None
    elif type == "existing_app":
        stmt4 = select(ExistingApp).order_by(ExistingApp.runtime_name)
        if after is not None:
            stmt4 = stmt4.where(ExistingApp.runtime_name > str(after))
        rows4 = db.scalars(stmt4.limit(limit + 1)).all()
        page4 = rows4[:limit]
        out = [existing_out(r) for r in page4]
        nxt = encode_cursor(page4[-1].runtime_name) if len(rows4) > limit else None
    elif type == "decision_record":
        stmt5 = select(DecisionRecord).order_by(DecisionRecord.app_id)
        if after is not None:
            stmt5 = stmt5.where(DecisionRecord.app_id > str(after))
        rows5 = db.scalars(stmt5.limit(limit + 1)).all()
        page5 = rows5[:limit]
        out = [decision_summary(r) for r in page5]
        nxt = encode_cursor(page5[-1].app_id) if len(rows5) > limit else None
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
        raise cursor_invalid() from exc
    rows = db.scalars(select(OutboxEvent).where(OutboxEvent.seq > after).order_by(OutboxEvent.seq).limit(limit)).all()
    return {
        "items": [envelope(e) for e in rows],
        "next_cursor": str(rows[-1].seq) if rows else str(after),
        "observed_at": iso(utcnow()),
    }


def hub_link_view(link: LinkState) -> dict[str, Any]:
    out = link.public()
    pins = link.pins
    out["pinned_kids"] = {
        "jwt": [p.kid for p in pins.jwt] if pins else [],
        "policy": [p.kid for p in pins.policy] if pins else [],
    }
    out["rotation_sequence"] = pins.rotation_sequence if pins else None
    out["confirmed_at"] = link.confirmed_at
    out["observed_at"] = iso(utcnow())
    return out


@router.get("/hub-link", summary="Koppelstatus met de Hub (witte lijst, geen geheimen)")
def hub_link(request: Request, _: Reader) -> dict[str, Any]:
    return hub_link_view(ctx(request).store.read())


@router.post(
    "/hub-link/unlink",
    summary="Ontkoppelen (dienst-JWT van de Hub of beheerder, Idempotency-Key)",
    description=(
        "Zet de koppeling op `ontkoppeld`, wist het service-token en bewaart de pins alleen nog voor audit. Daarna "
        "weigert de dienst elke Hub-JWT (401 `not_linked`); een herhaalde aanroep krijgt dus 401 `not_linked`, wat voor "
        "de Hub als geslaagd telt (contract 1.2 §3.8). Opnieuw koppelen gaat alleen via de Hub."
    ),
)
def hub_link_unlink(request: Request, db: DB, principal: Unlinker) -> Response:
    c = ctx(request)

    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        result = c.hub.unlink(trace_id=tid(request), actor=principal.label())
        return 200, hub_link_view(result), {}

    return run_idempotent(db, request, principal, "hub_link.unlink", {}, action)
