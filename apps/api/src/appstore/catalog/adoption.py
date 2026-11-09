"""Bestaande apps importeren, herkennen en adopteren (B08-002) — alleen een register, nooit een actie.

* **Idempotent:** dezelfde Hub-meting twee keer verwerken verandert niets (``row_version`` blijft gelijk). Identiteit
  is de TrueNAS-appnaam (``runtime_id`` van de Hub). Een app die verdwijnt wordt nooit verwijderd:
  ``present = false`` + ``gone_since``.
* **Nooit gegokt:** herkenning via :func:`appstore.bestaande_apps.match_app` (catalogusapp of exacte image-repository;
  nooit op naam of op de herkenning van de Hub). Het resultaat is hooguit ``kandidaat``.
* **Adopted** pas na bevestiging van de eigenaar door een beheerder (B08-002). Eigenaar komt uit het register of het
  manifest, nooit uit de meting; versies, tags en digests alleen uit de meting.
* Er gaat **niets** naar TrueNAS of de Hub behalve het lezen van de inventaris: geen installatie, geen herstart, geen
  wijziging van rechten of data.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..bestaande_apps import KnownEntry, MatchResult, Register, match_app, normalize_repository, register_entries
from ..manifest.canonical import canonical_json
from ..models import CatalogEntry, ExistingApp, ManifestVersion
from ..timeutil import iso
from .hubinventory import HubAppDetail

INSTALL_METHODS = ("custom_app", "catalog_app", "custom_compose", "onbekend")
HISTORY_MAX = 20
#: vaste sleutel voor ``pg_try_advisory_xact_lock``: nooit twee imports tegelijk
IMPORT_LOCK = 0x41_50_50_49_4D_50  # "APPIMP"
UNTOUCHED_NL = "De app blijft ongewijzigd draaien; data, opslag en rechten worden niet aangeraakt (alleen lezen)."


class ImportBusyError(Exception):
    """Er loopt al een import (advisory lock bezet)."""


# ------------------------------------------------------------------------------------------------ bekende vermeldingen
def manifest_entries(db: Session) -> list[KnownEntry]:
    """Geldige catalogusmanifesten als herkenningsbron (catalogusapp of image-repository uit het manifest)."""
    entries = db.scalars(select(CatalogEntry).where(CatalogEntry.status == "valid").order_by(CatalogEntry.app_id))
    out: list[KnownEntry] = []
    for e in entries:
        v = db.get(ManifestVersion, e.latest_version_id) if e.latest_version_id else None
        if v is None or not v.valid or not v.manifest:
            continue
        m = v.manifest
        cat = (m.get("catalog") or {}).get("app") if e.install_method == "catalog_app" else None
        repo = (m.get("image") or {}).get("repository") if e.install_method == "custom_app" else None
        out.append(
            KnownEntry(
                e.app_id,
                e.name,
                "manifest",
                frozenset({str(cat).lower()}) if cat else frozenset(),
                frozenset({normalize_repository(str(repo))}) if repo else frozenset(),
                "eigen" if e.owner_repo.startswith("lvos04/") else "derde",
                e.owner_repo,
                f"manifest {v.manifest_sha256}",
            )
        )
    return out


def known_entries(db: Session, reg: Register) -> list[KnownEntry]:
    """Register eerst (APP-xx en eigen platformdiensten), daarna geldige manifesten."""
    return [*register_entries(reg), *manifest_entries(db)]


# ------------------------------------------------------------------------------------------------ meting
def install_method(value: str | None) -> str:
    return value if value in INSTALL_METHODS else "onbekend"


def measured_from(d: HubAppDetail) -> dict[str, Any]:
    """Alleen gemeten velden, in een vaste volgorde (voor de hash). Geen NAS-adres, geen geheimen.

    De hoofdimage komt uit ``image`` van de Hub: daar is ``digest`` de **effectieve** digest (uit het tagveld, of uit
    de readback ``app.image.query`` als die eenduidig is). ``images[0].digest`` is alleen de digest uit het tagveld;
    ``digest_bron`` en ``readback`` staan op ``images[0]`` en tellen alleen als dat dezelfde repository is
    (gecontroleerd tegen de echte Hub-code, ai-server-manager ``hub.catalog.discovery``)."""
    first = d.images[0] if d.images else None
    repository = d.image.repository or (first.repository if first else None)
    same = first is not None and first.repository == repository
    readback = first.readback if same and first is not None else None
    image: dict[str, Any] = {
        "repository": repository,
        "tag": d.image.tag if d.image.repository else (first.tag if first else None),
        "digest": d.image.digest if d.image.repository else (first.digest if first else None),
        "digest_tagveld": first.digest if same and first is not None else None,
        "digest_bron": first.digest_bron if same and first is not None else None,
        "readback": None
        if readback is None
        else {"status": readback.status, "reden": readback.reden, "digests": sorted(set(readback.digests))},
    }
    cat = d.catalogus
    return {
        "image": image,
        "images": [{"repository": i.repository, "tag": i.tag, "digest": i.digest} for i in d.images],
        "catalog": None
        if cat is None
        else {"app": cat.app, "train": cat.trein, "version": cat.versie, "app_version": cat.app_versie},
        "ports": sorted(
            (
                {
                    "container_port": p.container_poort,
                    "host_port": p.host_poort,
                    "host_ip": p.host_ip,
                    "protocol": (p.protocol or "tcp").lower(),
                }
                for p in d.poorten
            ),
            key=lambda p: (p["host_port"] or 0, p["container_port"] or 0, p["protocol"], p["host_ip"] or ""),
        ),
        "storage": sorted(
            (
                {
                    "source": m.bron,
                    "destination": m.doel,
                    "type": m.type,
                    "kind": m.soort if m.soort in ("hostpad", "ixvolume", "overig") else "overig",
                    "dataset": m.dataset,
                }
                for m in d.opslag
            ),
            key=lambda m: (m["destination"] or "", m["source"] or ""),
        ),
        "datasets": sorted(set(d.datasets)),
        "runtime_state": d.runtime_staat,
        "hub_name": d.naam,
    }


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _repositories(d: HubAppDetail) -> list[str]:
    repos = [i.repository for i in d.images if i.repository]
    if d.image.repository:
        repos.append(d.image.repository)
    return repos


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _image_key(measured: dict[str, Any]) -> tuple[Any, Any, Any]:
    img = measured.get("image") or {}
    return img.get("repository"), img.get("tag"), img.get("digest")


def _history_entry(measured: dict[str, Any], at: datetime) -> dict[str, Any]:
    repo, tag, digest = _image_key(measured)
    return {"op": iso(at), "repository": repo, "tag": tag, "digest": digest}


# ------------------------------------------------------------------------------------------------ reconciliatie
@dataclass
class ReconcileResult:
    nieuw: int = 0
    bijgewerkt: int = 0
    ongewijzigd: int = 0
    verdwenen: int = 0
    per_status: dict[str, int] = field(default_factory=dict)
    changed_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "nieuw": self.nieuw,
            "bijgewerkt": self.bijgewerkt,
            "ongewijzigd": self.ongewijzigd,
            "verdwenen": self.verdwenen,
            "per_status": dict(sorted(self.per_status.items())),
        }


def _apply_match(row: ExistingApp, m: MatchResult) -> bool:
    new = (m.status, m.app_id, m.basis, list(m.candidates), m.reason_nl)
    old = (row.status, row.known_app_id, row.match_basis, list(row.candidates or []), row.match_reason_nl)
    if new == old:
        return False
    row.status, row.known_app_id, row.match_basis, row.candidates, row.match_reason_nl = new
    return True


def reconcile(
    db: Session,
    apps: list[HubAppDetail],
    entries: list[KnownEntry],
    *,
    observed_at: datetime,
    now: datetime,
) -> ReconcileResult:
    """Verwerk een **volledige, verse** Hub-meting. Vraagt een transactie-advisory-lock (nooit twee tegelijk)."""
    if not db.scalar(select(func.pg_try_advisory_xact_lock(IMPORT_LOCK))):
        raise ImportBusyError
    res = ReconcileResult()
    rows = {r.runtime_name: r for r in db.scalars(select(ExistingApp).with_for_update())}
    seen: set[str] = set()
    for d in apps:
        rid = d.runtime_id
        seen.add(rid)
        measured = measured_from(d)
        sha = _sha(measured)
        method = install_method(d.installatiewijze)
        m = match_app(
            install_method=method,
            catalog_app=d.catalogus.app if d.catalogus else None,
            repositories=_repositories(d),
            entries=entries,
        )
        seen_at = _aware(d.laatst_gezien) if d.laatst_gezien else observed_at
        gone_at = _aware(d.verdwenen_sinds) if d.verdwenen_sinds else observed_at
        row = rows.get(rid)
        if row is None:
            row = ExistingApp(
                id=uuid.uuid4(),
                runtime_name=rid,
                hub_app_uuid=d.id,
                status=m.status,
                known_app_id=m.app_id,
                match_basis=m.basis,
                candidates=list(m.candidates),
                match_reason_nl=m.reason_nl,
                hub_recognition=d.app_id,
                install_method=method,
                measured=measured,
                measured_sha256=sha,
                image_history=[_history_entry(measured, seen_at)] if _image_key(measured)[0] else [],
                present=d.aanwezig,
                first_seen=seen_at,
                last_seen=seen_at,
                gone_since=None if d.aanwezig else gone_at,
                owner=None,
                history=[],
                created_at=now,
                updated_at=now,
                row_version=1,
            )
            db.add(row)
            rows[rid] = row
            res.nieuw += 1
            res.changed_ids.append(str(row.id))
            continue
        changed = False
        if row.measured_sha256 != sha:
            if _image_key(row.measured) != _image_key(measured) and _image_key(measured)[0]:
                row.image_history = [*(row.image_history or []), _history_entry(measured, seen_at)][-HISTORY_MAX:]
            row.measured, row.measured_sha256 = measured, sha
            changed = True
        for attr, value in (("install_method", method), ("hub_app_uuid", d.id), ("hub_recognition", d.app_id)):
            if getattr(row, attr) != value:
                setattr(row, attr, value)
                changed = True
        if row.status == "adopted":
            warning = None
            if row.match_basis != "gebruiker" and m.app_id != row.known_app_id:
                warning = (
                    f"De huidige meting herkent deze app niet meer als '{row.known_app_id}': {m.reason_nl} "
                    "De adoptie blijft staan tot een beheerder haar loslaat."
                )
            if warning != row.adoption_warning_nl:
                row.adoption_warning_nl = warning
                changed = True
        elif _apply_match(row, m):
            changed = True
        if d.aanwezig != row.present:
            row.present = d.aanwezig
            row.gone_since = None if d.aanwezig else gone_at
            if not d.aanwezig:
                res.verdwenen += 1
            changed = True
        if d.aanwezig and seen_at > row.last_seen:
            row.last_seen = seen_at  # tijdstip van waarnemen is geen wijziging van de app
        if changed:
            row.row_version += 1
            row.updated_at = now
            res.bijgewerkt += 1
            res.changed_ids.append(str(row.id))
        else:
            res.ongewijzigd += 1
    for rid, row in rows.items():
        if rid in seen or not row.present:
            continue
        row.present = False
        row.gone_since = observed_at
        row.row_version += 1
        row.updated_at = now
        res.verdwenen += 1
        res.bijgewerkt += 1
        res.changed_ids.append(str(row.id))
    for row in rows.values():
        res.per_status[row.status] = res.per_status.get(row.status, 0) + 1
    db.flush()
    return res


# ------------------------------------------------------------------------------------------------ adopteren / loslaten
class AdoptionError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def owner_from(entry: KnownEntry) -> dict[str, Any]:
    return {"soort": entry.owner_kind, "repo": entry.owner_repo, "bron": entry.owner_source}


def adopt(
    row: ExistingApp,
    *,
    app_id: str,
    manual: bool,
    note_nl: str | None,
    entries: list[KnownEntry],
    actor: str,
    now: datetime,
) -> None:
    """Bevestig de eigenaar: ``kandidaat`` → ``adopted`` (of een handmatige koppeling van een onbekende app)."""
    if not row.present:
        raise AdoptionError(409, "app_verdwenen", "Deze app staat niet meer in de inventaris van de Hub.")
    if row.status == "adopted":
        raise AdoptionError(
            409,
            "al_adopted",
            f"Deze app is al geadopteerd als '{row.known_app_id}'. Laat haar eerst los om iets anders te kiezen.",
        )
    entry = next((e for e in entries if e.app_id == app_id), None)
    if entry is None:
        raise AdoptionError(
            422, "onbekende_app_id", f"'{app_id}' is geen bekende app in het register of de catalogus.", {}
        )
    if row.status == "kandidaat" and row.known_app_id == app_id:
        basis = row.match_basis
    else:
        if not manual:
            raise AdoptionError(
                409,
                "handmatige_koppeling_niet_bevestigd",
                "Deze app is niet als die app herkend. Koppel alleen handmatig als je zeker weet dat het dezelfde app "
                "is: stuur 'handmatig': true mee.",
                {"herkend_als": row.known_app_id, "kandidaten": list(row.candidates or [])},
            )
        basis = "gebruiker"
    owner = {**owner_from(entry), "bevestigd_door": actor, "bevestigd_op": iso(now), "toelichting_nl": note_nl}
    row.history = [
        *(row.history or []),
        {
            "actie": "adopted",
            "app_id": app_id,
            "basis": basis,
            "door": actor,
            "op": iso(now),
            "toelichting_nl": note_nl,
        },
    ][-HISTORY_MAX:]
    row.status = "adopted"
    row.known_app_id = app_id
    row.match_basis = basis
    row.candidates = [app_id]
    row.owner = owner
    row.adopted_at = now
    row.adopted_by = actor
    row.adoption_warning_nl = None
    row.match_reason_nl = "Eigenaar bevestigd door een beheerder" + (
        " (handmatige koppeling)." if basis == "gebruiker" else "."
    )
    row.row_version += 1
    row.updated_at = now


def release(row: ExistingApp, *, reason_nl: str, entries: list[KnownEntry], actor: str, now: datetime) -> None:
    """Adoptie intrekken (alleen het register): opnieuw herkennen op de laatste meting → ``kandidaat`` of ``onbekend``."""
    if row.status != "adopted":
        raise AdoptionError(409, "niet_adopted", "Deze app is niet geadopteerd.")
    measured = row.measured or {}
    m = match_app(
        install_method=row.install_method,
        catalog_app=(measured.get("catalog") or {}).get("app"),
        repositories=[str(i.get("repository")) for i in measured.get("images") or [] if i.get("repository")]
        + ([str(measured["image"]["repository"])] if (measured.get("image") or {}).get("repository") else []),
        entries=entries,
    )
    row.history = [
        *(row.history or []),
        {"actie": "losgelaten", "app_id": row.known_app_id, "door": actor, "op": iso(now), "reden_nl": reason_nl},
    ][-HISTORY_MAX:]
    row.owner = None
    row.adopted_at = None
    row.adopted_by = None
    row.adoption_warning_nl = None
    row.status, row.known_app_id, row.match_basis, row.candidates, row.match_reason_nl = (
        m.status,
        m.app_id,
        m.basis,
        list(m.candidates),
        m.reason_nl,
    )
    row.row_version += 1
    row.updated_at = now


# ------------------------------------------------------------------------------------------------ afhankelijkheden
def _overlap(a: str, b: str) -> bool:
    a, b = a.rstrip("/") or "/", b.rstrip("/") or "/"
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def shared_storage(row: ExistingApp, others: list[ExistingApp]) -> list[dict[str, Any]]:
    """Gemeten: andere aanwezige apps die (een deel van) dezelfde hostpaden mounten. Belangrijk bij een proefstop of
    snapshot: data van een gedeelde dataset hoort niet alleen bij deze app."""
    mine = [
        str(m["source"])
        for m in (row.measured or {}).get("storage") or []
        if m.get("kind") == "hostpad" and m.get("source")
    ]
    out: list[dict[str, Any]] = []
    for other in others:
        if other.id == row.id or not other.present:
            continue
        theirs = [
            str(m["source"])
            for m in (other.measured or {}).get("storage") or []
            if m.get("kind") == "hostpad" and m.get("source")
        ]
        paths = sorted({p for p in mine for q in theirs if _overlap(p, q)})
        if paths:
            out.append(
                {
                    "runtime_name": other.runtime_name,
                    "app_id": other.known_app_id if other.status != "onbekend" else None,
                    "paden": paths,
                }
            )
    return sorted(out, key=lambda x: x["runtime_name"])
