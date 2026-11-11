"""Catalogus-API ``/catalog/v1``: bronnen, entries, versiegeschiedenis, valideren en installatievoorbeeld.

Er wordt hier nooit iets op TrueNAS uitgevoerd: ``preview`` levert alleen de getypte ``app.create``-waarden,
compatibiliteit en conflicten. Installeren doet de Hub via de executor (R2, menselijke goedkeuring).
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..auth import Principal, require, require_user
from ..catalog.importer import CatalogCandidate, classify_inventory
from ..catalog.service import requirements, trusted_key_id, verify_stored_provenance
from ..catalog.sources import SourceError, check_remote_url
from ..errors import ApiError, validation_error
from ..events import audit, emit, record_evidence
from ..idempotency import run_idempotent
from ..manifest.conflicts import Inventory, analyse
from ..manifest.loader import MAX_MANIFEST_BYTES
from ..manifest.model import Manifest
from ..manifest.translate import Target, translate
from ..manifest.validator import schema, validate_data, validate_text
from ..models import CatalogEntry, CatalogSource, ManifestVersion, SyncJob
from ..timeutil import iso, utcnow
from .common import check_limit, ctx, cursor_invalid, decode_cursor, encode_cursor, etag, get_db, require_if_match, tid

router = APIRouter(prefix="/catalog/v1", tags=["catalogus"])
DB = Annotated[Session, Depends(get_db)]
Viewer = Annotated[Principal, Depends(require("viewer"))]
Operator = Annotated[Principal, Depends(require("operator"))]
Admin = Annotated[Principal, Depends(require_user("admin"))]

_NAME = r"^[a-z0-9][a-z0-9 _.-]{1,78}[a-z0-9]$"


# ---------------------------------------------------------------------------------------------- rate limit
class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise ApiError(
                    429,
                    "rate_limited",
                    "Te veel verzoeken; probeer het over een minuut opnieuw.",
                    headers={"Retry-After": "60"},
                )
            q.append(now)


def heavy_limiter() -> _RateLimiter:
    return _RateLimiter(120)


# ---------------------------------------------------------------------------------------------- modellen
class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(pattern=_NAME)]
    kind: Literal["git", "http"]
    url: Annotated[str, Field(min_length=8, max_length=2048)]
    ref: Annotated[str | None, Field(max_length=200, pattern=r"^[A-Za-z0-9._/-]+$")] = None
    pinned_commit: Annotated[str | None, Field(pattern=r"^[a-f0-9]{40}$")] = None
    subpath: Annotated[str | None, Field(max_length=255, pattern=r"^[A-Za-z0-9._/-]+$")] = None
    require_signature: bool = False
    trusted_public_key: Annotated[str | None, Field(max_length=1024)] = None


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    require_signature: bool | None = None
    trusted_public_key: Annotated[str | None, Field(max_length=1024)] = None
    ref: Annotated[str | None, Field(max_length=200, pattern=r"^[A-Za-z0-9._/-]+$")] = None
    pinned_commit: Annotated[str | None, Field(pattern=r"^[a-f0-9]{40}$")] = None


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_yaml: Annotated[str | None, Field(max_length=MAX_MANIFEST_BYTES)] = None
    manifest: dict[str, Any] | None = None


class ImportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inventory: Inventory


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str | None = None
    version_id: uuid.UUID | None = None
    manifest_yaml: Annotated[str | None, Field(max_length=MAX_MANIFEST_BYTES)] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    target: Target = Field(default_factory=Target)
    inventory: Inventory | None = None
    app_name: Annotated[str | None, Field(pattern=r"^[a-z]([-a-z0-9]*[a-z0-9])?$", max_length=40)] = None


# ---------------------------------------------------------------------------------------------- weergave
def source_out(s: CatalogSource) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "kind": s.kind,
        "url": s.url,
        "ref": s.ref,
        "pinned_commit": s.pinned_commit,
        "subpath": s.subpath,
        "enabled": s.enabled,
        "require_signature": s.require_signature,
        "trusted_key_id": s.trusted_key_id,
        "created_by": s.created_by,
        "created_at": iso(s.created_at),
        "updated_at": iso(s.updated_at),
        "etag": etag(s.row_version),
        "last_sync": {
            "at": iso(s.last_sync_at),
            "status": s.last_sync_status,
            "error": s.last_sync_error,
            "revision": s.last_sync_revision,
        },
    }


def version_summary(v: ManifestVersion) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "app_id": v.app_id,
        "version": v.app_version,
        "manifest_sha256": v.manifest_sha256,
        "valid": v.valid,
        "errors": v.report.get("errors"),
        "warnings": v.report.get("warnings"),
        "source_id": str(v.source_id),
        "file_path": v.file_path,
        "publisher_signature": v.publisher_signature,
        "created_at": iso(v.created_at),
        "superseded_at": iso(v.superseded_at),
    }


def entry_out(e: CatalogEntry, latest: ManifestVersion | None, source: CatalogSource | None) -> dict[str, Any]:
    return {
        "app_id": e.app_id,
        "name": e.name,
        "category": e.category,
        "owner_repo": e.owner_repo,
        "install_method": e.install_method,
        "status": e.status,
        "etag": etag(e.row_version),
        "source": {"id": str(source.id), "name": source.name, "kind": source.kind} if source else None,
        "latest_version": version_summary(latest) if latest else None,
        "description_nl": (latest.manifest or {}).get("description_nl") if latest else None,
        "updated_at": iso(e.updated_at),
        "created_at": iso(e.created_at),
    }


def _job_out(j: SyncJob) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "source_id": str(j.source_id),
        "status": j.status,
        "requested_by": j.requested_by,
        "requested_at": iso(j.requested_at),
        "started_at": iso(j.started_at),
        "finished_at": iso(j.finished_at),
        "result": j.result,
        "error": j.error,
    }


# ---------------------------------------------------------------------------------------------- bronnen
@router.get("/sources", summary="Catalogusbronnen")
def list_sources(db: DB, _: Viewer) -> dict[str, Any]:
    rows = db.scalars(select(CatalogSource).order_by(CatalogSource.created_at)).all()
    return {"items": [source_out(s) for s in rows], "observed_at": iso(utcnow())}


@router.post("/sources", status_code=201, summary="Bron toevoegen (admin, Idempotency-Key)")
def create_source(body: SourceCreate, request: Request, db: DB, principal: Admin) -> Response:
    settings = ctx(request).settings

    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        try:
            if not (settings.allow_insecure_sources and body.kind == "git" and body.url.startswith(("file://", "/"))):
                check_remote_url(body.url, allow_insecure=settings.allow_insecure_sources)
        except SourceError as exc:
            raise ApiError(422, exc.code, exc.message) from exc
        if body.require_signature and not body.trusted_public_key:
            raise ApiError(
                422, "trusted_key_required", "Een bron die handtekeningen vereist heeft een vertrouwde sleutel nodig."
            )
        key_id = None
        if body.trusted_public_key:
            try:
                key_id = trusted_key_id(body.trusted_public_key)
            except ValueError as exc:
                raise ApiError(
                    422, "trusted_key_invalid", "De vertrouwde sleutel is geen geldige Ed25519-sleutel."
                ) from exc
        if db.scalar(select(CatalogSource).where(CatalogSource.name == body.name)):
            raise ApiError(409, "source_name_taken", f"Er bestaat al een bron met de naam {body.name!r}.")
        now = utcnow()
        src = CatalogSource(
            id=uuid.uuid4(),
            name=body.name,
            kind=body.kind,
            url=body.url,
            ref=body.ref,
            pinned_commit=body.pinned_commit,
            subpath=body.subpath,
            enabled=True,
            require_signature=body.require_signature,
            trusted_public_key=body.trusted_public_key,
            trusted_key_id=key_id,
            created_by=principal.label(),
            created_at=now,
            updated_at=now,
            row_version=1,
            last_sync_status="never",
        )
        db.add(src)
        db.flush()
        audit(db, principal, "catalog.source.created", "catalog_source", str(src.id), body.model_dump(), tid(request))
        emit(
            db,
            "catalog.source.created",
            "catalog_source",
            str(src.id),
            {"name": src.name, "kind": src.kind},
            tid(request),
            principal,
        )
        return 201, source_out(src), {"ETag": etag(src.row_version)}

    return run_idempotent(db, request, principal, "source.create", body.model_dump(mode="json"), action)


@router.get("/sources/{source_id}", summary="Eén bron")
def get_source(source_id: uuid.UUID, response: Response, db: DB, _: Viewer) -> dict[str, Any]:
    src = db.get(CatalogSource, source_id)
    if src is None:
        raise ApiError(404, "not_found", "Bron niet gevonden.")
    response.headers["ETag"] = etag(src.row_version)
    return source_out(src)


@router.patch("/sources/{source_id}", summary="Bron wijzigen (admin, If-Match, Idempotency-Key)")
def patch_source(source_id: uuid.UUID, body: SourcePatch, request: Request, db: DB, principal: Admin) -> Response:
    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        src = db.get(CatalogSource, source_id, with_for_update=True)
        if src is None:
            raise ApiError(404, "not_found", "Bron niet gevonden.")
        require_if_match(request, src.row_version)
        changes = body.model_dump(exclude_unset=True)
        if src.kind == "bundled" and set(changes) - {"enabled"}:
            raise ApiError(
                422, "bundled_source_readonly", "Van de ingebouwde bron kan alleen 'enabled' worden gewijzigd."
            )
        if "trusted_public_key" in changes:
            try:
                src.trusted_key_id = (
                    trusted_key_id(changes["trusted_public_key"]) if changes["trusted_public_key"] else None
                )
            except ValueError as exc:
                raise ApiError(
                    422, "trusted_key_invalid", "De vertrouwde sleutel is geen geldige Ed25519-sleutel."
                ) from exc
        for name, value in changes.items():
            setattr(src, name, value)
        if src.require_signature and not src.trusted_public_key:
            raise ApiError(
                422, "trusted_key_required", "Een bron die handtekeningen vereist heeft een vertrouwde sleutel nodig."
            )
        src.row_version += 1
        src.updated_at = utcnow()
        audit(db, principal, "catalog.source.updated", "catalog_source", str(src.id), changes, tid(request))
        return 200, source_out(src), {"ETag": etag(src.row_version)}

    return run_idempotent(
        db, request, principal, f"source.patch:{source_id}", body.model_dump(mode="json", exclude_unset=True), action
    )


@router.post("/sources/{source_id}/sync", status_code=202, summary="Bron synchroniseren (operator, Idempotency-Key)")
def sync_source(source_id: uuid.UUID, request: Request, db: DB, principal: Operator) -> Response:
    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        src = db.get(CatalogSource, source_id)
        if src is None:
            raise ApiError(404, "not_found", "Bron niet gevonden.")
        if not src.enabled:
            raise ApiError(409, "source_disabled", "Deze bron staat uit.")
        job = SyncJob(
            id=uuid.uuid4(), source_id=src.id, status="queued", requested_by=principal.label(), requested_at=utcnow()
        )
        db.add(job)
        db.flush()
        audit(
            db,
            principal,
            "catalog.source.sync_requested",
            "catalog_source",
            str(src.id),
            {"job_id": str(job.id)},
            tid(request),
        )
        return 202, _job_out(job), {"Location": f"/catalog/v1/jobs/{job.id}"}

    return run_idempotent(db, request, principal, f"source.sync:{source_id}", {}, action)


@router.get("/jobs/{job_id}", summary="Status van een synchronisatie")
def get_job(job_id: uuid.UUID, db: DB, _: Viewer) -> dict[str, Any]:
    job = db.get(SyncJob, job_id)
    if job is None:
        raise ApiError(404, "not_found", "Taak niet gevonden.")
    return _job_out(job)


# ---------------------------------------------------------------------------------------------- entries en versies
@router.get("/entries", summary="Catalogus (gepagineerd, zoeken/filteren)")
def list_entries(
    db: DB,
    _: Viewer,
    cursor: str | None = None,
    limit: Annotated[int, Query()] = 50,
    category: str | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    status: Literal["valid", "withdrawn"] | None = None,
) -> dict[str, Any]:
    limit = check_limit(limit)
    stmt = select(CatalogEntry).order_by(CatalogEntry.app_id)
    after = decode_cursor(cursor)
    if after is not None:
        stmt = stmt.where(CatalogEntry.app_id > str(after))
    if category:
        stmt = stmt.where(CatalogEntry.category == category)
    if status:
        stmt = stmt.where(CatalogEntry.status == status)
    if q:
        like = f"%{re.sub(r'[%_]', '', q.lower())}%"
        stmt = stmt.where(or_(func.lower(CatalogEntry.app_id).like(like), func.lower(CatalogEntry.name).like(like)))
    rows = db.scalars(stmt.limit(limit + 1)).all()
    items = rows[:limit]
    versions = {
        v.id: v
        for v in db.scalars(
            select(ManifestVersion).where(
                ManifestVersion.id.in_([e.latest_version_id for e in items if e.latest_version_id])
            )
        )
    }
    sources = {s.id: s for s in db.scalars(select(CatalogSource)).all()}
    return {
        "items": [
            entry_out(e, versions.get(e.latest_version_id) if e.latest_version_id else None, sources.get(e.source_id))
            for e in items
        ],
        "next_cursor": encode_cursor(items[-1].app_id) if len(rows) > limit else None,
        "observed_at": iso(utcnow()),
    }


def _entry(db: Session, app_id: str) -> CatalogEntry:
    entry = db.scalar(select(CatalogEntry).where(CatalogEntry.app_id == app_id))
    if entry is None:
        raise ApiError(404, "not_found", f"App {app_id!r} staat niet in de catalogus.")
    return entry


@router.get("/entries/{app_id}", summary="Appdetail met transparante eisen")
def get_entry(app_id: str, response: Response, db: DB, _: Viewer) -> dict[str, Any]:
    entry = _entry(db, app_id)
    latest = db.get(ManifestVersion, entry.latest_version_id) if entry.latest_version_id else None
    out = entry_out(entry, latest, db.get(CatalogSource, entry.source_id))
    if latest is not None and latest.manifest is not None:
        out["requirements"] = requirements(Manifest.model_validate(latest.manifest))
        out["warnings"] = [f for f in latest.report.get("findings", []) if f.get("severity") == "warning"]
    response.headers["ETag"] = etag(entry.row_version)
    return out


@router.get("/entries/{app_id}/versions", summary="Versiegeschiedenis van een app")
def list_versions(app_id: str, db: DB, _: Viewer, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
    limit = check_limit(limit)
    stmt = (
        select(ManifestVersion)
        .where(ManifestVersion.app_id == app_id)
        .order_by(ManifestVersion.created_at.desc(), ManifestVersion.id.desc())
    )
    after = decode_cursor(cursor)
    if after is not None:
        from datetime import datetime

        try:
            if not isinstance(after, list) or len(after) != 2:
                raise TypeError("cursor heeft niet de vorm [tijd, id]")
            at, vid = datetime.fromisoformat(str(after[0])), uuid.UUID(str(after[1]))
        except (TypeError, ValueError) as exc:
            raise cursor_invalid() from exc
        stmt = stmt.where(
            or_(ManifestVersion.created_at < at, and_(ManifestVersion.created_at == at, ManifestVersion.id < vid))
        )
    rows = db.scalars(stmt.limit(limit + 1)).all()
    if not rows and after is None and db.scalar(select(CatalogEntry).where(CatalogEntry.app_id == app_id)) is None:
        raise ApiError(404, "not_found", f"Geen versies van {app_id!r}.")
    items = rows[:limit]
    nxt = encode_cursor([items[-1].created_at.isoformat(), str(items[-1].id)]) if len(rows) > limit else None
    return {"items": [version_summary(v) for v in items], "next_cursor": nxt, "observed_at": iso(utcnow())}


@router.get("/versions/{version_id}", summary="Eén manifestversie met rapport en provenance")
def get_version(version_id: uuid.UUID, request: Request, db: DB, _: Viewer) -> dict[str, Any]:
    v = db.get(ManifestVersion, version_id)
    if v is None:
        raise ApiError(404, "not_found", "Versie niet gevonden.")
    out = version_summary(v)
    out.update(
        {
            "manifest": v.manifest,
            "report": v.report,
            "provenance": v.provenance,
            "provenance_verified": verify_stored_provenance(v, ctx(request).secrets),
        }
    )
    return out


# ---------------------------------------------------------------------------------------------- valideren / voorbeeld
@router.get("/schema/manifest-v1", summary="JSON Schema van manifest v1")
def get_schema(_: Viewer) -> dict[str, Any]:
    return schema()


@router.post("/validate", summary="Manifest valideren (voert niets uit)")
def validate(body: ValidateRequest, request: Request, db: DB, principal: Viewer) -> dict[str, Any]:
    request.app.state.heavy_limiter.check(principal.label())
    if (body.manifest_yaml is None) == (body.manifest is None):
        raise validation_error(
            "Geef precies één van 'manifest_yaml' of 'manifest'.",
            [{"veld": "body", "fout": "precies één van manifest_yaml of manifest", "type": "one_of"}],
        )
    if body.manifest_yaml is not None:
        report, _, _ = validate_text(body.manifest_yaml)
    else:
        report, _ = validate_data(body.manifest)
    evidence_id = record_evidence(
        db,
        "manifest_validation",
        principal,
        tid(request),
        {
            "app_id": report.app_id,
            "version": report.version,
            "manifest_sha256": report.manifest_sha256,
            "valid": report.valid,
            "errors": report.errors,
            "warnings": report.warnings,
            "codes": report.codes(),
        },
    )
    db.commit()
    out = report.model_dump(mode="json")
    out["evidence_id"] = str(evidence_id)
    out["text_nl"] = report.as_text()
    return out


@router.post("/preview", summary="Installatievoorbeeld: TrueNAS app.create-waarden + conflicten (voert niets uit)")
def preview(body: PreviewRequest, request: Request, db: DB, principal: Operator) -> dict[str, Any]:
    request.app.state.heavy_limiter.check(principal.label())
    chosen = [x for x in (body.app_id, body.version_id, body.manifest_yaml) if x is not None]
    if len(chosen) != 1:
        raise validation_error(
            "Geef precies één van 'app_id', 'version_id' of 'manifest_yaml'.",
            [{"veld": "body", "fout": "precies één van app_id, version_id of manifest_yaml", "type": "one_of"}],
        )
    version: ManifestVersion | None = None
    if body.app_id is not None:
        entry = _entry(db, body.app_id)
        version = db.get(ManifestVersion, entry.latest_version_id) if entry.latest_version_id else None
        if version is None:
            raise ApiError(409, "no_valid_version", "Deze app heeft geen geldige manifestversie.")
    elif body.version_id is not None:
        version = db.get(ManifestVersion, body.version_id)
        if version is None:
            raise ApiError(404, "not_found", "Versie niet gevonden.")
    text = version.raw_text if version is not None else (body.manifest_yaml or "")
    report, manifest, _ = validate_text(text)
    result: dict[str, Any] = {
        "validation": report.model_dump(mode="json"),
        "version_id": str(version.id) if version else None,
    }
    if manifest is None:
        result.update(
            {
                "ready_for_install": False,
                "translation": None,
                "conflicts": None,
                "blocked_reason_nl": "Het manifest is ongeldig; installeren is geweigerd vóór uitvoering.",
            }
        )
    else:
        translation = translate(
            manifest,
            body.parameters,
            body.target,
            app_name=body.app_name,
            manifest_sha256=report.manifest_sha256,
            trusted_app_id=_app_id_bound(db, version, manifest),
        )
        conflicts = analyse(manifest, translation.payload, body.inventory)
        ready = translation.ready_for_install and conflicts.status == "ok"
        result.update(
            {
                "ready_for_install": ready,
                "translation": translation.model_dump(mode="json"),
                "conflicts": conflicts.model_dump(mode="json"),
                "requirements": requirements(manifest),
                "risk_class_install": manifest.risk.install,
                "blocked_reason_nl": None
                if ready
                else "Er zijn blokkerende bevindingen; zie translation.findings en conflicts.findings.",
            }
        )
    result["executed"] = False
    evidence_id = record_evidence(
        db,
        "manifest_preview",
        principal,
        tid(request),
        {
            "app_id": report.app_id,
            "version_id": result["version_id"],
            "manifest_sha256": report.manifest_sha256,
            "ready_for_install": result["ready_for_install"],
            "preview_hash": (result.get("translation") or {}).get("preview_hash"),
            "blocking_codes": sorted(
                {f["code"] for f in (result.get("translation") or {}).get("findings", []) if f["severity"] == "error"}
                | {f.code for f in report.findings if f.severity.value == "error"}
            ),
        },
    )
    db.commit()
    result["evidence_id"] = str(evidence_id)
    return result


def _app_id_bound(db: Session, version: ManifestVersion | None, manifest: Manifest) -> bool:
    """R-VEILIG-1 (ADR-0015): alleen een geldige versie die bij een catalogus-entry met dezelfde ``app_id`` hoort, legt
    de ``app_id`` vast (één bron per ``app_id``, ``app_id_claimed_by_other_source``). Een los manifest
    (``manifest_yaml``) of een geweigerde versie kan zich de ``app_id`` van een andere app geven: dan geen
    vault-verwijzing."""
    if version is None or not version.valid or version.entry_id is None:
        return False
    entry = db.get(CatalogEntry, version.entry_id)
    return entry is not None and entry.app_id == manifest.app_id == version.app_id


# ---------------------------------------------------------------------------------------------- import (B08-002)
def _catalog_candidates(db: Session) -> list[CatalogCandidate]:
    entries = db.scalars(select(CatalogEntry).order_by(CatalogEntry.app_id)).all()
    versions = {
        v.id: v
        for v in db.scalars(
            select(ManifestVersion).where(
                ManifestVersion.id.in_([e.latest_version_id for e in entries if e.latest_version_id])
            )
        )
    }
    out: list[CatalogCandidate] = []
    for e in entries:
        v = versions.get(e.latest_version_id) if e.latest_version_id else None
        manifest = (v.manifest or {}) if v is not None else {}
        out.append(
            CatalogCandidate(
                app_id=e.app_id,
                install_method=e.install_method,
                manifest_sha256=v.manifest_sha256 if v is not None else None,
                catalog_app=(manifest.get("catalog") or {}).get("app"),
                image_repository=(manifest.get("image") or {}).get("repository"),
                valid=e.status == "valid" and v is not None and v.valid,
            )
        )
    return out


@router.post(
    "/import/preview",
    summary="Bestaande apps herkennen (B08-002, alleen lezen; niet-ondersteunde apps blijven unmanaged)",
)
def import_preview(body: ImportPreviewRequest, request: Request, db: DB, principal: Operator) -> dict[str, Any]:
    request.app.state.heavy_limiter.check(principal.label())
    report = classify_inventory(body.inventory, _catalog_candidates(db))
    evidence_id = record_evidence(
        db,
        "import_preview",
        principal,
        tid(request),
        {
            "inventory_observed_at": iso(report.inventory_observed_at),
            "inventory_source": body.inventory.source,
            "status": report.status,
            "counts": report.counts,
            "apps": [{"name": a.name, "status": a.status, "matched_app_id": a.matched_app_id} for a in report.apps],
        },
    )
    db.commit()
    out = report.model_dump(mode="json")
    out["evidence_id"] = str(evidence_id)
    return out
