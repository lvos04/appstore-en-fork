"""Beslisregister per bekende app (APP-01…APP-20, APP_REGISTER): voorgenomen besluit, afvinkbare controlelijst met
bewijs, schriftelijk besluit en een decommission-**plan** volgens I26.

Grenzen (03_RISICO_AUTONOMIE, contract 1.2 §8):

* De appstore **voert niets uit**. Een plan noemt de ``action_id``'s van de executor (``zfs.snapshot.create``,
  ``app.stop``, ``app.start``, ``app.uninstall`` met ``remove_ix_volumes=false``) met hun minimale risicoklasse;
  uitvoeren gaat alleen via voorstel → policy → menselijke goedkeuring → executor (R1/R2).
* **Data verwijderen wordt nooit aangeboden** (``app.uninstall_data`` is R3 en wordt bewust niet aangeboden; I26: "data
  alleen na eigen, afzonderlijke bevestiging verwijderen").
* Een check afvinken vraagt bewijs; een definitief besluit vraagt een volledige controlelijst (behalve "gereed", dat
  pas na acceptatie op de echte NAS kan) en, voor uitschakelen of de container verwijderen, een plan.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bestaande_apps import CheckTemplate, KnownApp, LoadedRegister, checks_for
from ..manifest.canonical import canonical_json
from ..models import DecisionRecord, ExistingApp
from ..timeutil import iso
from .adoption import shared_storage

CheckStatus = Literal["open", "afgevinkt", "nvt"]
FinalDecision = Literal["behouden", "uitschakelen", "container_verwijderen"]
REGISTER_LOCK = 0x41_50_50_52_45_47  # "APPREG"
HISTORY_MAX = 200
PREPARATION_CHECKS = (
    "inventaris-1",
    "inventaris-2",
    "inventaris-3",
    "inventaris-4",
    "inventaris-5",
    "aandachtspunt",
    "a0-meten",
    "a1-integreren",
)
FRESH = timedelta(minutes=15)
NOT_EXECUTED_NL = (
    "De appstore voert niets uit. Elke stap met een action_id gaat als voorstel naar de Hub (policy bepaalt de "
    "risicoklasse; R2/R3 alleen na menselijke goedkeuring per gebonden diff) en wordt door de executor uitgevoerd."
)
NO_DATA_REMOVAL_NL = (
    "Data verwijderen staat los van het stoppen of verwijderen van de app en wordt door het platform nooit aangeboden "
    "(app.uninstall_data is R3 en wordt bewust niet aangeboden, contract 1.2 §8). Data alleen na een eigen, "
    "afzonderlijke bevestiging buiten dit plan verwijderen (I26 §4 punt 6)."
)
ROLLBACK_I26 = (
    "Schakel nieuwe automation/ingest uit.",
    "Bewaar audit en logs.",
    "Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is "
    "veranderd.",
    "Controleer credentials, clients en content integrity.",
    "Maak incidentrapport met wat wel/niet is teruggezet.",
    "Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.",
)


class DecisionError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


# ------------------------------------------------------------------------------------------------ register → tabel
def _check(t: CheckTemplate) -> dict[str, Any]:
    return {
        "id": t.id,
        "fase": t.fase,
        "tekst_nl": t.tekst_nl,
        "bron": t.bron,
        "status": "nvt" if t.default_nvt_nl else "open",
        "reden_nl": t.default_nvt_nl,
        "bewijs": None,
        "bijgewerkt_door": None if not t.default_nvt_nl else "register",
        "bijgewerkt_op": None,
    }


def _intended(app: KnownApp) -> dict[str, Any]:
    return {
        "code": app.voorgenomen_besluit.code,
        "tekst_nl": app.voorgenomen_besluit.tekst_nl,
        "belangrijkste_controle_nl": app.belangrijkste_controle_nl,
        "bron": f"APP_REGISTER.md; 04_BESTAANDE_APPS/{app.a_doc}",
    }


def _push(history: list[dict[str, Any]] | None, entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [*(history or []), entry][-HISTORY_MAX:]


def ensure_register(db: Session, loaded: LoadedRegister, *, now: datetime) -> int:
    """Zorg dat er per app uit het register één beslisrecord is, op de huidige registerversie. Bestaande statussen en
    bewijs blijven staan zolang de tekst van de check gelijk is; een gewijzigde tekst zet die check terug op open (het
    oude bewijs gaat naar de historie). Idempotent; geeft het aantal aangemaakte of bijgewerkte records."""
    apps = loaded.register.apps
    current = db.scalar(
        select(func.count()).select_from(DecisionRecord).where(DecisionRecord.register_sha256 == loaded.sha256)
    )
    if current == len(apps):
        return 0
    db.execute(select(func.pg_advisory_xact_lock(REGISTER_LOCK)))
    records = {r.app_id: r for r in db.scalars(select(DecisionRecord).with_for_update())}
    changed = 0
    for app in apps:
        template = [_check(t) for t in checks_for(app)]
        rec = records.get(app.app_id)
        if rec is None:
            db.add(
                DecisionRecord(
                    app_id=app.app_id,
                    excel_id=app.excel_id,
                    register_sha256=loaded.sha256,
                    intended=_intended(app),
                    checks=template,
                    decision=None,
                    history=[],
                    created_at=now,
                    updated_at=now,
                    row_version=1,
                )
            )
            changed += 1
            continue
        if rec.register_sha256 == loaded.sha256:
            continue
        old = {c["id"]: c for c in rec.checks or []}
        history = list(rec.history or [])
        merged = []
        for t in template:
            prev = old.pop(t["id"], None)
            if prev is not None and prev.get("tekst_nl") == t["tekst_nl"]:
                keep = {k: prev.get(k) for k in ("status", "reden_nl", "bewijs", "bijgewerkt_door", "bijgewerkt_op")}
                merged.append({**t, **keep})
            else:
                merged.append(t)
                if prev is not None and prev.get("status") != "open":
                    history = _push(history, {"actie": "check_tekst_gewijzigd", "op": iso(now), "vorig": prev})
        for prev in old.values():
            history = _push(history, {"actie": "check_vervallen", "op": iso(now), "vorig": prev})
        rec.checks = merged
        rec.intended = _intended(app)
        rec.excel_id = app.excel_id
        rec.register_sha256 = loaded.sha256
        rec.history = _push(history, {"actie": "register_bijgewerkt", "op": iso(now), "sha256": loaded.sha256})
        rec.row_version += 1
        rec.updated_at = now
        changed += 1
    db.flush()
    return changed


# ------------------------------------------------------------------------------------------------ checks en besluit
@dataclass(frozen=True)
class Evidence:
    omschrijving_nl: str
    verwijzing: str | None
    evidence_ids: tuple[str, ...]


def update_check(
    rec: DecisionRecord,
    check_id: str,
    *,
    status: CheckStatus,
    evidence: Evidence | None,
    reason_nl: str | None,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    checks = [dict(c) for c in rec.checks or []]
    check = next((c for c in checks if c["id"] == check_id), None)
    if check is None:
        raise DecisionError(404, "check_onbekend", f"Check {check_id!r} bestaat niet voor {rec.app_id!r}.")
    if status == "afgevinkt" and evidence is None:
        raise DecisionError(
            422, "bewijs_vereist", "Afvinken kan alleen met bewijs (omschrijving, eventueel verwijzing)."
        )
    if status == "nvt" and not reason_nl:
        raise DecisionError(422, "reden_vereist", "'Niet van toepassing' vraagt een reden.")
    before = {k: check.get(k) for k in ("status", "reden_nl", "bewijs")}
    check["status"] = status
    check["reden_nl"] = reason_nl if status == "nvt" else None
    check["bewijs"] = (
        None
        if status != "afgevinkt" or evidence is None
        else {
            "omschrijving_nl": evidence.omschrijving_nl,
            "verwijzing": evidence.verwijzing,
            "evidence_ids": list(evidence.evidence_ids),
        }
    )
    check["bijgewerkt_door"] = actor
    check["bijgewerkt_op"] = iso(now)
    rec.checks = checks
    rec.history = _push(
        rec.history,
        {"actie": "check", "check_id": check_id, "door": actor, "op": iso(now), "vorig": before, "nieuw": status},
    )
    rec.row_version += 1
    rec.updated_at = now
    return check


def open_checks(rec: DecisionRecord, *, ids: tuple[str, ...] | None = None) -> list[str]:
    return [
        c["id"]
        for c in rec.checks or []
        if c["status"] == "open" and c["id"] != "gereed" and (ids is None or c["id"] in ids)
    ]


def record_decision(
    rec: DecisionRecord,
    *,
    code: FinalDecision,
    motivation_nl: str,
    plan: dict[str, Any] | None,
    plan_id: str | None,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    missing = open_checks(rec)
    if missing:
        raise DecisionError(
            409,
            "controlelijst_onvolledig",
            "Een besluit vastleggen kan pas als elke check (behalve 'gereed') is afgevinkt met bewijs of als niet van "
            "toepassing is gemarkeerd.",
            {"open_checks": missing},
        )
    if code != "behouden":
        if plan is None or plan_id is None:
            raise DecisionError(
                409, "plan_vereist", "Uitschakelen of de container verwijderen vraagt eerst een decommission-plan."
            )
        if plan.get("app_id") != rec.app_id:
            raise DecisionError(422, "plan_hoort_niet_bij_app", "Dit plan hoort bij een andere app.")
    decision = {
        "code": code,
        "motivatie_nl": motivation_nl,
        "decommission_plan_id": plan_id,
        "plan_hash": plan.get("plan_hash") if plan else None,
        "door": actor,
        "op": iso(now),
        "uitvoering_nl": NOT_EXECUTED_NL if code != "behouden" else "Geen actie: de app blijft draaien.",
        "data_verwijderen": "niet_aangeboden",
    }
    rec.history = _push(rec.history, {"actie": "besluit", "op": iso(now), "vorig": rec.decision, "nieuw": decision})
    rec.decision = decision
    rec.row_version += 1
    rec.updated_at = now
    return decision


# ------------------------------------------------------------------------------------------------ automatische meting
def measured_hints(app: KnownApp, instances: list[ExistingApp], all_rows: list[ExistingApp]) -> dict[str, Any]:
    """Gemeten gegevens als **voorstel** voor bewijs bij de inventaris-checks; vinkt nooit iets af."""
    rows = [i for i in instances if i.present]
    return {
        "inventaris-1": [
            {
                "runtime_name": i.runtime_name,
                "status": i.status,
                "image": (i.measured or {}).get("image"),
                "catalog": (i.measured or {}).get("catalog"),
                "runtime_state": (i.measured or {}).get("runtime_state"),
                "observed_at": iso(i.last_seen),
                "container_id": None,
                "toelichting_nl": "Container-ID levert de Hub-inventaris niet; owner pas na bevestiging (adopted).",
            }
            for i in rows
        ],
        "inventaris-2": [
            {"runtime_name": i.runtime_name, "ports": (i.measured or {}).get("ports") or []} for i in rows
        ],
        "inventaris-3": [
            {
                "runtime_name": i.runtime_name,
                "storage": (i.measured or {}).get("storage") or [],
                "datasets": (i.measured or {}).get("datasets") or [],
            }
            for i in rows
        ],
        "inventaris-4": {
            "gedocumenteerd": [d.model_dump() for d in app.afhankelijk_van],
            "gedeelde_opslag_gemeten": [
                {"runtime_name": i.runtime_name, "gedeeld_met": shared_storage(i, all_rows)} for i in rows
            ],
            "api_afhankelijkheden_gemeten": "unknown",
        },
        "inventaris-5": {
            "status": "unknown",
            "toelichting_nl": "Laatste gebruiksdatum, logs en performance meet de appstore niet (eigen UI of Hub).",
        },
    }


# ------------------------------------------------------------------------------------------------ decommission-plan
def _gate(gate_id: str, status: str, text_nl: str, **extra: Any) -> dict[str, Any]:
    return {"id": gate_id, "status": status, "tekst_nl": text_nl, **extra}


def build_plan(
    rec: DecisionRecord,
    app: KnownApp,
    instances: list[ExistingApp],
    all_rows: list[ExistingApp],
    loaded: LoadedRegister,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Decommission-plan volgens I26 stap 6 en §4, alleen als voorbereiding. ``plan_hash`` hangt alleen af van
    register, controlelijst, meting en de uitkomst van de gates (niet van het tijdstip van maken)."""
    adopted = sorted((i for i in instances if i.status == "adopted" and i.present), key=lambda i: i.runtime_name)
    unconfirmed = sorted(
        (i.runtime_name for i in instances if i.status == "kandidaat" and i.present),
    )
    datasets = sorted({d for i in adopted for d in (i.measured or {}).get("datasets") or []})
    ixvolumes = sorted(
        {
            f"{i.runtime_name}:{m.get('destination')}"
            for i in adopted
            for m in (i.measured or {}).get("storage") or []
            if m.get("kind") == "ixvolume"
        }
    )
    shared = {i.runtime_name: shared_storage(i, all_rows) for i in adopted}
    reverse = sorted(
        (
            {"app_id": other.app_id, "soort": d.soort, "reden_nl": d.reden_nl, "bron": d.bron}
            for other in loaded.register.apps
            for d in other.afhankelijk_van
            if d.app_id == app.app_id
        ),
        key=lambda x: x["app_id"],
    )
    prep_missing = open_checks(rec, ids=PREPARATION_CHECKS)
    stale = [i.runtime_name for i in adopted if now - i.last_seen > FRESH]
    gates = [
        _gate(
            "eigenaar_bevestigd",
            "ok" if adopted else "open",
            "Minstens één aanwezige instantie is geadopteerd (eigenaar bevestigd door een beheerder).",
        ),
        _gate(
            "voorbereiding_afgevinkt",
            "ok" if not prep_missing else "open",
            "Verplichte inventaris, aandachtspunt, A0 (meten) en A1 (niet-mutatief integreren) zijn afgevinkt met "
            "bewijs (I26 stap 1).",
            open_checks=prep_missing,
        ),
        _gate(
            "inventaris_vers",
            "ok" if adopted and not stale else ("verouderd" if stale else "unknown"),
            "De meting van de instanties is hooguit 15 minuten oud; de executor meet vlak vóór uitvoering opnieuw.",
            verouderd=stale,
        ),
        _gate(
            "backup_gate",
            "unknown",
            "Herstelbare back-up en snapshot vóór elke stap (I26 preflight, 03_RISICO_AUTONOMIE). Deze gate meet de "
            "appstore niet: de Hub/executor controleert hem vlak vóór uitvoering; onbekend is nooit groen.",
        ),
        _gate(
            "voorgenomen_besluit",
            "ok" if app.decommission_candidate else "afwijking",
            f"Voorgenomen besluit: {app.voorgenomen_besluit.tekst_nl}"
            + ("" if app.decommission_candidate else " Dit plan wijkt daarvan af; alleen met expliciete motivatie."),
        ),
    ]
    if app.zonder_gebruik_dagen:
        gates.append(
            _gate(
                "geen_gebruik",
                "unknown",
                f"Uitschakelen alleen na {app.zonder_gebruik_dagen} dagen zonder verzoeken (I26 stap 4: 'Disable "
                "alleen na 7 dagen geen request'). De appstore meet dit niet; leg het bewijs vast bij check "
                "inventaris-5.",
            )
        )
    dependents = sorted({x["app_id"] for x in reverse} | {s["runtime_name"] for v in shared.values() for s in v})
    canary_hours = app.proefstop_uren or 72
    steps: list[dict[str, Any]] = [
        {
            "nr": 1,
            "id": "inventaris",
            "tekst_nl": "Usage- en afhankelijkheidsscan: versie, laatste gebruik, eigenaar, afhankelijkheden, datapad en "
            "netwerkblootstelling vastleggen (I26 stap 1).",
            "action_id": None,
            "risico_min": "R0",
            "uitvoerder": "gebruiker + Hub (alleen lezen)",
            "status": "ok" if not prep_missing else "open",
        },
        {
            "nr": 2,
            "id": "export",
            "tekst_nl": "Bestaande data en configuratie afzonderlijk exporteren in de eigen UI van de app (I26 §1).",
            "action_id": None,
            "risico_min": "R0",
            "uitvoerder": "gebruiker",
            "status": "open",
        },
        {
            "nr": 3,
            "id": "snapshot",
            "tekst_nl": "Snapshot van elke dataset van de app vóór de proefstop.",
            "action_id": "zfs.snapshot.create",
            "risico_min": "R1",
            "uitvoerder": "executor (na policy)",
            "parameters": {"datasets": datasets, "ixvolumes_dataset_onbekend": ixvolumes},
            "toelichting_nl": "Datasets uit de gemeten opslag. Van een ixVolume levert de Hub-inventaris geen "
            "datasetnaam; de executor meet die vóór uitvoering. Een gedeelde dataset blijft van alle apps die hem "
            "mounten.",
            "status": "gepland",
        },
        {
            "nr": 4,
            "id": "proefstop",
            "tekst_nl": f"Proefstop: de app {canary_hours} uur uitschakelen met eigen data intact en de "
            "gebruikersreizen verifiëren (A3; I26 stap 6 '72h disabled canary').",
            "action_id": "app.stop",
            "risico_min": "R1 (niet-kritiek) / R2 (kritiek, netwerk, DNS, auth) — de policy bepaalt",
            "uitvoerder": "executor (na policy)",
            "parameters": {"apps": [i.runtime_name for i in adopted], "duur_uren": canary_hours},
            "status": "gepland",
        },
        {
            "nr": 5,
            "id": "verificatie",
            "tekst_nl": f"Acceptatie: {app.belangrijkste_controle_nl}",
            "action_id": None,
            "risico_min": "R0",
            "uitvoerder": "gebruiker",
            "afhankelijke_apps": dependents,
            "status": "gepland",
        },
        {
            "nr": 6,
            "id": "terugdraaien",
            "tekst_nl": "Rollback vóór definitieve delete: de app weer starten met dezelfde waarden; gebruikers kunnen "
            "in één handeling naar de oude app terug (I26 stap 6; A-document 'Gereed wanneer').",
            "action_id": "app.start",
            "risico_min": "R1 / R2 — de policy bepaalt",
            "uitvoerder": "executor (na policy)",
            "status": "gepland",
        },
        {
            "nr": 7,
            "id": "besluit",
            "tekst_nl": "Beslissing schriftelijk vastleggen (A4) met backup-restoretest en veilige rollback.",
            "action_id": None,
            "risico_min": "R0",
            "uitvoerder": "beheerder (POST /catalog/v1/decisions/{app_id}/decision)",
            "status": "open" if rec.decision is None else "vastgelegd",
        },
        {
            "nr": 8,
            "id": "container_verwijderen",
            "tekst_nl": "Alleen na het besluit 'container_verwijderen': de app (container) verwijderen; data en "
            "ixVolumes blijven staan.",
            "action_id": "app.uninstall",
            "risico_min": "R2",
            "uitvoerder": "executor (na menselijke goedkeuring)",
            "parameters": {"apps": [i.runtime_name for i in adopted], "remove_ix_volumes": False},
            "status": "niet_gepland_zonder_besluit",
        },
    ]
    plan: dict[str, Any] = {
        "soort": "decommission_plan",
        "app_id": app.app_id,
        "excel_id": app.excel_id,
        "naam": app.naam,
        "i26": sorted({*app.i26_stappen, "I26-26-06"}),
        "register_sha256": loaded.sha256,
        "voorgenomen_besluit": _intended(app),
        "instanties": [
            {
                "runtime_name": i.runtime_name,
                "id": str(i.id),
                "installatiewijze": i.install_method,
                "image": (i.measured or {}).get("image"),
                "catalog": (i.measured or {}).get("catalog"),
                "datasets": (i.measured or {}).get("datasets") or [],
                "gedeelde_opslag": shared.get(i.runtime_name, []),
                "measured_sha256": i.measured_sha256,
            }
            for i in adopted
        ],
        "niet_bevestigd": unconfirmed,
        "afhankelijk_van_deze_app": reverse,
        "gates": gates,
        "stappen": steps,
        "terugdraaien_i26": list(ROLLBACK_I26),
        "data_verwijderen": {"aangeboden": False, "reden_nl": NO_DATA_REMOVAL_NL},
        "uitgevoerd": False,
        "uitvoering_nl": NOT_EXECUTED_NL,
        "voorbereiding_compleet": all(
            g["status"] == "ok" for g in gates if g["id"] not in ("backup_gate", "geen_gebruik")
        ),
    }
    plan["plan_hash"] = "sha256:" + hashlib.sha256(canonical_json(plan)).hexdigest()
    plan["gemaakt_op"] = iso(now)
    return plan
