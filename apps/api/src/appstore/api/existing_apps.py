"""Bestaande apps (B08-002) en beslisregister (APP-01…APP-20): ``/catalog/v1/existing-apps`` en
``/catalog/v1/decisions``.

* ``POST /existing-apps/import`` leest de app-inventaris van de Hub (service-token; de appstore heeft geen TrueNAS-key)
  en werkt het register idempotent bij. Niets op de NAS of in de Hub wordt gewijzigd.
* ``POST /existing-apps/{id}/adopt`` — een **beheerder** bevestigt de eigenaar (``kandidaat`` → ``adopted``);
  ``/release`` maakt dat ongedaan (alleen het register).
* ``GET /existing-apps/{id}/manifest`` — adoptiemanifest (``kind: adoption``, niet installeerbaar).
* ``/decisions…`` — voorgenomen besluit, afvinkbare controlelijst met bewijs, schriftelijk besluit en een
  decommission-**plan** (I26). Uitvoeren gebeurt nooit hier (R2/R3 via lifecycle/executor); data verwijderen wordt nooit
  aangeboden.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, require, require_user
from ..bestaande_apps import KEY_PATTERN, KnownApp, LoadedRegister, load_register
from ..catalog import adoption_manifest
from ..catalog.adoption import AdoptionError, ImportBusyError, adopt, known_entries, release, shared_storage
from ..catalog.decisions import (
    DecisionError,
    Evidence,
    build_plan,
    ensure_register,
    measured_hints,
    record_decision,
    update_check,
)
from ..catalog.importrun import run_import as import_round
from ..errors import ApiError
from ..events import audit, emit, record_evidence
from ..idempotency import run_idempotent
from ..models import DecisionRecord, EvidenceRecord, ExistingApp
from ..timeutil import iso, utcnow
from .common import check_limit, ctx, decode_cursor, encode_cursor, etag, get_db, require_if_match, tid

router = APIRouter(prefix="/catalog/v1", tags=["bestaande apps"])
DB = Annotated[Session, Depends(get_db)]
Viewer = Annotated[Principal, Depends(require("viewer"))]
Operator = Annotated[Principal, Depends(require("operator"))]
UserOperator = Annotated[Principal, Depends(require_user("operator"))]
UserAdmin = Annotated[Principal, Depends(require_user("admin"))]

STATUS_LABEL_NL = {"adopted": "Geïmporteerd", "kandidaat": "Kandidaat", "onbekend": "Onbekend"}
LAST_USED = {
    "status": "unknown",
    "toelichting_nl": "Laatste gebruik meet de appstore niet; dat volgt uit de eigen UI van de app of de Hub (check "
    "inventaris-5).",
}
EvidenceRef = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")]


# ---------------------------------------------------------------------------------------------- modellen
class AdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: Annotated[str, Field(pattern=KEY_PATTERN)]
    eigenaar_bevestigd: Literal[True]
    handmatig: bool = False
    toelichting_nl: Annotated[str | None, Field(max_length=2000)] = None


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reden_nl: Annotated[str, Field(min_length=3, max_length=2000)]


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    omschrijving_nl: Annotated[str, Field(min_length=10, max_length=4000)]
    verwijzing: Annotated[str | None, Field(max_length=500)] = None
    evidence_ids: Annotated[list[EvidenceRef], Field(max_length=20)] = Field(default_factory=list)


class CheckUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "afgevinkt", "nvt"]
    bewijs: EvidenceIn | None = None
    reden_nl: Annotated[str | None, Field(min_length=3, max_length=2000)] = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["behouden", "uitschakelen", "container_verwijderen"]
    motivatie_nl: Annotated[str, Field(min_length=10, max_length=4000)]
    decommission_plan_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------------------------- weergave
def _register() -> LoadedRegister:
    return load_register()


def _known(app_id: str | None) -> KnownApp | None:
    return _register().register.get(app_id) if app_id else None


def existing_out(row: ExistingApp) -> dict[str, Any]:
    m = row.measured or {}
    known = _known(row.known_app_id)
    return {
        "id": str(row.id),
        "runtime_name": row.runtime_name,
        "status": row.status,
        "status_nl": STATUS_LABEL_NL.get(row.status, row.status),
        "present": row.present,
        "gone_since": iso(row.gone_since),
        "known_app_id": row.known_app_id,
        "excel_id": known.excel_id if known else None,
        "match": {
            "basis": row.match_basis,
            "kandidaten": list(row.candidates or []),
            "reden_nl": row.match_reason_nl,
            "hub_herkenning": row.hub_recognition,
            "hub_herkenning_toelichting_nl": "Alleen ter informatie: de herkenning door de Hub telt nooit als basis.",
        },
        "install_method": row.install_method,
        "image": m.get("image"),
        "catalog": m.get("catalog"),
        "ports": m.get("ports") or [],
        "storage": m.get("storage") or [],
        "datasets": m.get("datasets") or [],
        "runtime_state": m.get("runtime_state"),
        "owner": row.owner,
        "adopted_at": iso(row.adopted_at),
        "adopted_by": row.adopted_by,
        "adoption_warning_nl": row.adoption_warning_nl,
        "last_used": LAST_USED,
        "first_seen": iso(row.first_seen),
        "last_seen": iso(row.last_seen),
        "source": "hub GET /api/v1/apps",
        "data_policy": "untouched",
        "planned_actions": [],
        "etag": etag(row.row_version),
    }


def _dependencies(row: ExistingApp, all_rows: list[ExistingApp]) -> dict[str, Any]:
    known = _known(row.known_app_id)
    reg = _register().register
    return {
        "gedocumenteerd": [d.model_dump() for d in known.afhankelijk_van] if known else [],
        "afhankelijk_van_deze_app": sorted(
            (
                {"app_id": other.app_id, "soort": d.soort, "reden_nl": d.reden_nl, "bron": d.bron}
                for other in reg.apps
                for d in other.afhankelijk_van
                if known and d.app_id == known.app_id
            ),
            key=lambda x: x["app_id"],
        ),
        "gedeelde_opslag_gemeten": shared_storage(row, all_rows),
        "api_afhankelijkheden_gemeten": "unknown",
        "toelichting_nl": "Gedocumenteerd = uit de A-documenten en APP_REGISTER (geen meting). Gedeelde opslag is "
        "gemeten (overlappende hostpaden). API-verkeer tussen apps meet de appstore niet.",
    }


def decision_summary(rec: DecisionRecord) -> dict[str, Any]:
    counts: dict[str, int] = {"open": 0, "afgevinkt": 0, "nvt": 0}
    for c in rec.checks or []:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {
        "app_id": rec.app_id,
        "excel_id": rec.excel_id,
        "voorgenomen_besluit": rec.intended,
        "checks": counts,
        "besluit": rec.decision,
        "etag": etag(rec.row_version),
    }


def _get_row(db: Session, app_uuid: uuid.UUID, *, lock: bool = False) -> ExistingApp:
    row = db.get(ExistingApp, app_uuid, with_for_update=lock)
    if row is None:
        raise ApiError(404, "not_found", "Deze app staat niet in het register van bestaande apps.")
    return row


def _get_record(db: Session, app_id: str, *, lock: bool = False) -> tuple[DecisionRecord, KnownApp]:
    known = _known(app_id)
    if known is None:
        raise ApiError(404, "not_found", f"{app_id!r} staat niet in het beslisregister (APP-01…APP-20).")
    ensure_register(db, _register(), now=utcnow())
    rec = db.get(DecisionRecord, app_id, with_for_update=lock)
    assert rec is not None
    return rec, known


# ---------------------------------------------------------------------------------------------- import
@router.post(
    "/existing-apps/import",
    summary="Inventaris van de Hub lezen en het register bijwerken (B08-002; idempotent; niets op de NAS)",
)
def run_import(request: Request, db: DB, principal: Operator) -> Response:
    request.app.state.heavy_limiter.check(principal.label())
    c = ctx(request)

    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        try:
            body = import_round(db, c.hub, principal, trace_id=tid(request), now=utcnow())
        except ImportBusyError as exc:
            raise ApiError(
                409, "import_bezig", "Er loopt al een import; probeer het zo opnieuw.", headers={"Retry-After": "5"}
            ) from exc
        return 200, body, {}

    return run_idempotent(db, request, principal, "existing_apps.import", {}, action)


# ---------------------------------------------------------------------------------------------- register
@router.get("/existing-apps", summary="Bestaande apps (Geïmporteerd/Kandidaat/Onbekend), gepagineerd")
def list_existing(
    db: DB,
    _: Viewer,
    cursor: str | None = None,
    limit: Annotated[int, Query()] = 50,
    status: Literal["adopted", "kandidaat", "onbekend"] | None = None,
    alleen_aanwezig: bool = True,
    known_app_id: Annotated[str | None, Query(pattern=KEY_PATTERN)] = None,
) -> dict[str, Any]:
    limit = check_limit(limit)
    stmt = select(ExistingApp).order_by(ExistingApp.runtime_name)
    after = decode_cursor(cursor)
    if after is not None:
        stmt = stmt.where(ExistingApp.runtime_name > str(after))
    if status:
        stmt = stmt.where(ExistingApp.status == status)
    if alleen_aanwezig:
        stmt = stmt.where(ExistingApp.present.is_(True))
    if known_app_id:
        stmt = stmt.where(ExistingApp.known_app_id == known_app_id)
    rows = db.scalars(stmt.limit(limit + 1)).all()
    page = rows[:limit]
    last = db.scalar(
        select(EvidenceRecord)
        .where(EvidenceRecord.kind == "hub_import")
        .order_by(EvidenceRecord.created_at.desc())
        .limit(1)
    )
    return {
        "items": [existing_out(r) for r in page],
        "next_cursor": encode_cursor(page[-1].runtime_name) if len(rows) > limit else None,
        "laatste_import": None
        if last is None
        else {
            "op": iso(last.created_at),
            "status": last.summary.get("status"),
            "reason_code": last.summary.get("reason_code"),
            "melding_nl": last.summary.get("melding_nl"),
            "inventory_observed_at": last.summary.get("inventory_observed_at"),
            "evidence_id": str(last.id),
        },
        "observed_at": iso(utcnow()),
        "alleen_lezen": True,
    }


@router.get("/existing-apps/{app_uuid}", summary="Eén bestaande app: meting, herkenning, eigenaar, afhankelijkheden")
def get_existing(app_uuid: uuid.UUID, response: Response, db: DB, _: Viewer) -> dict[str, Any]:
    row = _get_row(db, app_uuid)
    all_rows = list(db.scalars(select(ExistingApp)))
    out = existing_out(row)
    rec = db.get(DecisionRecord, row.known_app_id) if row.known_app_id else None
    out.update(
        {
            "image_history": row.image_history or [],
            "history": row.history or [],
            "dependencies": _dependencies(row, all_rows),
            "beslisregister": decision_summary(rec) if rec else None,
            "measured_sha256": row.measured_sha256,
        }
    )
    response.headers["ETag"] = etag(row.row_version)
    return out


@router.post(
    "/existing-apps/{app_uuid}/adopt",
    summary="Eigenaar bevestigen: kandidaat → adopted (beheerder, If-Match, Idempotency-Key; niets op de NAS)",
)
def adopt_existing(app_uuid: uuid.UUID, body: AdoptRequest, request: Request, db: DB, principal: UserAdmin) -> Response:
    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        row = _get_row(db, app_uuid, lock=True)
        require_if_match(request, row.row_version)
        now = utcnow()
        try:
            adopt(
                row,
                app_id=body.app_id,
                manual=body.handmatig,
                note_nl=body.toelichting_nl,
                entries=known_entries(db, _register().register),
                actor=principal.label(),
                now=now,
            )
        except AdoptionError as exc:
            raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
        details = {"runtime_name": row.runtime_name, "app_id": row.known_app_id, "basis": row.match_basis}
        audit(db, principal, "catalog.app.adopted", "existing_app", str(row.id), details, tid(request))
        emit(db, "catalog.app.adopted", "existing_app", str(row.id), details, tid(request), principal)
        db.flush()
        return 200, existing_out(row), {"ETag": etag(row.row_version)}

    return run_idempotent(
        db, request, principal, f"existing_apps.adopt:{app_uuid}", body.model_dump(mode="json"), action
    )


@router.post(
    "/existing-apps/{app_uuid}/release",
    summary="Adoptie intrekken (alleen het register; beheerder, If-Match, Idempotency-Key)",
)
def release_existing(
    app_uuid: uuid.UUID, body: ReleaseRequest, request: Request, db: DB, principal: UserAdmin
) -> Response:
    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        row = _get_row(db, app_uuid, lock=True)
        require_if_match(request, row.row_version)
        previous = row.known_app_id
        try:
            release(
                row,
                reason_nl=body.reden_nl,
                entries=known_entries(db, _register().register),
                actor=principal.label(),
                now=utcnow(),
            )
        except AdoptionError as exc:
            raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
        details = {"runtime_name": row.runtime_name, "app_id": previous, "reden_nl": body.reden_nl}
        audit(db, principal, "catalog.app.released", "existing_app", str(row.id), details, tid(request))
        emit(db, "catalog.app.released", "existing_app", str(row.id), details, tid(request), principal)
        db.flush()
        return 200, existing_out(row), {"ETag": etag(row.row_version)}

    return run_idempotent(
        db, request, principal, f"existing_apps.release:{app_uuid}", body.model_dump(mode="json"), action
    )


@router.get(
    "/existing-apps/{app_uuid}/manifest",
    summary="Adoptiemanifest (kind: adoption) — alleen inventaris, nooit installeerbaar",
)
def get_adoption_manifest(app_uuid: uuid.UUID, db: DB, _: Viewer) -> dict[str, Any]:
    row = _get_row(db, app_uuid)
    entries = known_entries(db, _register().register)
    entry = next((e for e in entries if e.app_id == row.known_app_id), None)
    if row.status != "adopted" or entry is None:
        raise ApiError(409, "niet_adopted", "Alleen een geadopteerde app (eigenaar bevestigd) krijgt een manifest.")
    try:
        doc = adoption_manifest.build(row, entry, _known(row.known_app_id))
    except adoption_manifest.AdoptionManifestError as exc:
        raise ApiError(409, exc.code, exc.message) from exc
    sha, text = adoption_manifest.render(doc)
    return {
        "manifest": doc,
        "manifest_sha256": sha,
        "manifest_yaml": text,
        "installable": False,
        "toelichting_nl": "Alleen voor inventaris en beslisregister. POST /catalog/v1/preview weigert dit document "
        "(adoption_manifest_not_installable); er wordt niets geïnstalleerd of herinstalleerd.",
    }


# ---------------------------------------------------------------------------------------------- beslisregister
def _instances(db: Session, app_id: str) -> list[ExistingApp]:
    return list(db.scalars(select(ExistingApp).where(ExistingApp.known_app_id == app_id)))


def _instances_out(rows: list[ExistingApp]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(r.id),
            "runtime_name": r.runtime_name,
            "status": r.status,
            "status_nl": STATUS_LABEL_NL.get(r.status, r.status),
            "present": r.present,
        }
        for r in sorted(rows, key=lambda r: r.runtime_name)
    ]


@router.get("/decisions", summary="Beslisregister APP-01…APP-20: voorgenomen besluit, voortgang controlelijst, besluit")
def list_decisions(db: DB, _: Viewer) -> dict[str, Any]:
    loaded = _register()
    if ensure_register(db, loaded, now=utcnow()):
        db.commit()
    records = {r.app_id: r for r in db.scalars(select(DecisionRecord))}
    rows = list(db.scalars(select(ExistingApp).where(ExistingApp.known_app_id.is_not(None))))
    items = []
    for app in loaded.register.apps:
        rec = records[app.app_id]
        items.append(
            {
                **decision_summary(rec),
                "naam": app.naam,
                "a_doc": f"04_BESTAANDE_APPS/{app.a_doc}",
                "instanties": _instances_out([r for r in rows if r.known_app_id == app.app_id]),
            }
        )
    return {
        "items": items,
        "register_versie": loaded.register.register_versie,
        "register_sha256": loaded.sha256,
        "observed_at": iso(utcnow()),
    }


@router.get("/decisions/{app_id}", summary="Beslisrecord van één app met controlelijst, bewijs en gemeten hints")
def get_decision(app_id: str, response: Response, db: DB, _: Viewer) -> dict[str, Any]:
    rec, known = _get_record(db, app_id)
    db.commit()
    instances = _instances(db, app_id)
    all_rows = list(db.scalars(select(ExistingApp)))
    reg = _register().register
    response.headers["ETag"] = etag(rec.row_version)
    return {
        **decision_summary(rec),
        "naam": known.naam,
        "a_doc": f"04_BESTAANDE_APPS/{known.a_doc}",
        "waarom_nl": known.waarom_nl,
        "eigenaar": known.eigenaar.model_dump(),
        "i26_stappen": known.i26_stappen,
        "herkenning": known.herkenning.model_dump(),
        "afhankelijk_van": [d.model_dump() for d in known.afhankelijk_van],
        "afhankelijk_van_deze_app": sorted(
            (
                {"app_id": o.app_id, "soort": d.soort, "reden_nl": d.reden_nl, "bron": d.bron}
                for o in reg.apps
                for d in o.afhankelijk_van
                if d.app_id == app_id
            ),
            key=lambda x: x["app_id"],
        ),
        "controlelijst": rec.checks,
        "gemeten_hints": measured_hints(known, instances, all_rows),
        "instanties": _instances_out(instances),
        "history": rec.history or [],
    }


@router.post(
    "/decisions/{app_id}/checks/{check_id}",
    summary="Check afvinken (met bewijs), openzetten of n.v.t. (gebruiker operator+, If-Match, Idempotency-Key)",
)
def set_check(
    app_id: str, check_id: str, body: CheckUpdate, request: Request, db: DB, principal: UserOperator
) -> Response:
    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        rec, _known_app = _get_record(db, app_id, lock=True)
        require_if_match(request, rec.row_version)
        evidence = (
            Evidence(body.bewijs.omschrijving_nl, body.bewijs.verwijzing, tuple(body.bewijs.evidence_ids))
            if body.bewijs is not None
            else None
        )
        try:
            check = update_check(
                rec,
                check_id,
                status=body.status,
                evidence=evidence,
                reason_nl=body.reden_nl,
                actor=principal.label(),
                now=utcnow(),
            )
        except DecisionError as exc:
            raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
        details = {"app_id": app_id, "check_id": check_id, "status": body.status}
        audit(db, principal, "catalog.check.updated", "decision_record", app_id, details, tid(request))
        emit(db, "catalog.check.updated", "decision_record", app_id, details, tid(request), principal)
        db.flush()
        return 200, {"check": check, **decision_summary(rec)}, {"ETag": etag(rec.row_version)}

    return run_idempotent(
        db, request, principal, f"decisions.check:{app_id}:{check_id}", body.model_dump(mode="json"), action
    )


@router.post(
    "/decisions/{app_id}/decommission-plan",
    summary="Decommission-plan voorbereiden volgens I26 (voert niets uit; nooit data verwijderen)",
)
def make_plan(app_id: str, request: Request, db: DB, principal: Operator) -> Response:
    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        rec, known = _get_record(db, app_id)
        now = utcnow()
        plan = build_plan(
            rec, known, _instances(db, app_id), list(db.scalars(select(ExistingApp))), _register(), now=now
        )
        evidence_id = record_evidence(db, "decommission_plan", principal, tid(request), plan)
        details = {"app_id": app_id, "plan_hash": plan["plan_hash"], "evidence_id": str(evidence_id)}
        audit(db, principal, "catalog.decommission.planned", "decision_record", app_id, details, tid(request))
        emit(db, "catalog.decommission.planned", "decision_record", app_id, details, tid(request), principal)
        db.flush()
        return 201, {**plan, "plan_id": str(evidence_id)}, {"Location": f"/connector/v1/evidence/{evidence_id}"}

    return run_idempotent(db, request, principal, f"decisions.plan:{app_id}", {}, action)


@router.post(
    "/decisions/{app_id}/decision",
    summary="Besluit schriftelijk vastleggen (beheerder; controlelijst volledig; voert niets uit)",
)
def set_decision(app_id: str, body: DecisionRequest, request: Request, db: DB, principal: UserAdmin) -> Response:
    def action() -> tuple[int, dict[str, Any], dict[str, str]]:
        rec, _known_app = _get_record(db, app_id, lock=True)
        require_if_match(request, rec.row_version)
        plan: dict[str, Any] | None = None
        if body.decommission_plan_id is not None:
            ev = db.get(EvidenceRecord, body.decommission_plan_id)
            if ev is None or ev.kind != "decommission_plan":
                raise ApiError(422, "plan_onbekend", "Dit decommission-plan bestaat niet.")
            plan = ev.summary
        try:
            decision = record_decision(
                rec,
                code=body.code,
                motivation_nl=body.motivatie_nl,
                plan=plan,
                plan_id=str(body.decommission_plan_id) if body.decommission_plan_id else None,
                actor=principal.label(),
                now=utcnow(),
            )
        except DecisionError as exc:
            raise ApiError(exc.status, exc.code, exc.message, exc.details) from exc
        details = {"app_id": app_id, "code": body.code, "plan_id": decision["decommission_plan_id"]}
        audit(db, principal, "catalog.decision.recorded", "decision_record", app_id, details, tid(request))
        emit(db, "catalog.decision.recorded", "decision_record", app_id, details, tid(request), principal)
        db.flush()
        return 200, decision_summary(rec), {"ETag": etag(rec.row_version)}

    return run_idempotent(db, request, principal, f"decisions.decision:{app_id}", body.model_dump(mode="json"), action)
