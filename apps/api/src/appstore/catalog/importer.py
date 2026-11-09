"""B08-002 — bestaande TrueNAS-apps herkennen (alleen lezen).

Uitgangspunt van de acceptatie: *een nog niet ondersteunde app blijft unmanaged zonder dataverlies.* Daarom:

* de import **voert nooit iets uit** en plant geen enkele actie (``planned_actions`` is altijd leeg); de appstore heeft
  geen TrueNAS-sleutel en de inventaris komt als gemeten readback van de Hub (``app.query`` / ``app.config``);
* herkennen gaat op volgorde van zekerheid: platformlabels (``nl.lvos04.platform.app_id`` + ``managed_by``),
  daarna de naam van de catalogusapp, daarna de image-repository. Alleen een label geldt als *beheerd*; elke andere
  overeenkomst is een **kandidaat** die de gebruiker als eigenaar moet bevestigen (Excel B08-002);
* geen manifest, een ongeldig/ingetrokken manifest of meerdere kandidaten → ``unmanaged`` (blijft ongemoeid);
* een verouderde, toekomstige of ontbrekende inventaris → alles ``unknown``, nooit "beheerd".

Het vastleggen van de eigenaarbevestiging (status *Geïmporteerd*) is nog niet gebouwd: ``owner_confirmation`` is
``capability_unavailable``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..manifest.conflicts import MAX_INVENTORY_AGE, MAX_INVENTORY_FUTURE, InstalledApp, Inventory
from ..manifest.truenas import LABEL_PREFIX

MANAGED_BY = "appstore-en-fork"
UNTOUCHED_NL = "De app blijft ongewijzigd draaien; data, opslag en rechten worden niet aangeraakt (alleen lezen)."
ImportStatus = Literal["managed", "candidate", "unmanaged", "unknown"]


@dataclass(frozen=True)
class CatalogCandidate:
    """Eén geldige catalogusvermelding (laatste geldige manifestversie)."""

    app_id: str
    install_method: str
    manifest_sha256: str | None
    catalog_app: str | None = None  # bij install.method catalog_app
    image_repository: str | None = None  # bij install.method custom_app
    valid: bool = True


class ImportedApp(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: str | None
    status: ImportStatus
    matched_app_id: str | None
    match_basis: Literal["label", "catalog_app", "image"] | None
    candidates: list[str]
    manifest_current: bool | None
    requires_owner_confirmation: bool
    planned_actions: list[str]
    data_policy: Literal["untouched"] = "untouched"
    reasons_nl: list[str]


class ImportReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "unknown"]
    inventory_observed_at: datetime | None
    executed: Literal[False] = False
    owner_confirmation: Literal["capability_unavailable"] = "capability_unavailable"
    counts: dict[str, int]
    apps: list[ImportedApp]
    reasons_nl: list[str]


def _repo(image: str) -> str:
    """``docker.io/library/nginx:1.27@sha256:…`` → ``docker.io/library/nginx`` (kleine letters)."""
    ref = image.split("@", 1)[0]
    last = ref.rsplit("/", 1)
    if ":" in last[-1]:
        ref = ref[: ref.rfind(":")]
    return ref.lower()


def _repo_variants(repository: str) -> set[str]:
    """Docker Hub-namen worden in TrueNAS soms zonder ``docker.io/`` en ``library/`` gerapporteerd."""
    repo = repository.lower()
    out = {repo}
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/library/", "index.docker.io/"):
        if repo.startswith(prefix):
            out.add(repo.removeprefix(prefix))
    if "/" not in repo:
        out.update({f"library/{repo}", f"docker.io/library/{repo}"})
    return out


def _labels(app: InstalledApp) -> dict[str, str]:
    labels = dict(app.labels)
    if app.platform_app_id and f"{LABEL_PREFIX}.app_id" not in labels:
        labels[f"{LABEL_PREFIX}.app_id"] = app.platform_app_id
    return labels


def _classify(app: InstalledApp, catalog: list[CatalogCandidate]) -> ImportedApp:
    by_id = {c.app_id: c for c in catalog}
    labels = _labels(app)
    label_id = labels.get(f"{LABEL_PREFIX}.app_id")
    reasons: list[str] = []

    def result(
        status: ImportStatus,
        matched: CatalogCandidate | None = None,
        basis: Literal["label", "catalog_app", "image"] | None = None,
        candidates: list[str] | None = None,
        current: bool | None = None,
    ) -> ImportedApp:
        return ImportedApp(
            name=app.name,
            state=app.state,
            status=status,
            matched_app_id=matched.app_id if matched else None,
            match_basis=basis,
            candidates=sorted(candidates or []),
            manifest_current=current,
            requires_owner_confirmation=status == "candidate",
            planned_actions=[],  # de import wijzigt nooit iets aan een app of zijn data
            reasons_nl=[*reasons, UNTOUCHED_NL],
        )

    if label_id:
        entry = by_id.get(label_id)
        if labels.get(f"{LABEL_PREFIX}.managed_by") != MANAGED_BY:
            reasons.append(
                f"Label {LABEL_PREFIX}.app_id={label_id!r} zonder managed_by={MANAGED_BY!r}; niet als beheerd aangenomen."
            )
        elif entry is None or not entry.valid:
            reasons.append(
                f"Label verwijst naar {label_id!r}, maar daarvoor staat geen geldig manifest in de catalogus."
            )
            return result("unmanaged")
        else:
            installed_sha = labels.get(f"{LABEL_PREFIX}.manifest_sha256")
            current = installed_sha is not None and installed_sha == entry.manifest_sha256
            reasons.append(
                "Herkend aan de platformlabels (eerder via dit platform geïnstalleerd)."
                + (
                    ""
                    if current
                    else " Het manifest is sindsdien gewijzigd; een update gaat via preview + goedkeuring."
                )
            )
            return result("managed", entry, "label", [entry.app_id], current)

    matches: dict[str, tuple[CatalogCandidate, Literal["catalog_app", "image"]]] = {}
    if app.catalog_app and app.catalog_app != "ix-app":
        for c in catalog:
            if c.install_method == "catalog_app" and c.catalog_app == app.catalog_app:
                matches.setdefault(c.app_id, (c, "catalog_app"))
    if not matches:
        installed = {v for img in app.images for v in _repo_variants(_repo(img))}
        for c in catalog:
            if (
                c.install_method == "custom_app"
                and c.image_repository
                and _repo_variants(c.image_repository) & installed
            ):
                matches.setdefault(c.app_id, (c, "image"))
    valid = {k: v for k, v in matches.items() if v[0].valid}
    if not matches:
        reasons.append("Geen manifest in de catalogus voor deze app: nog niet ondersteund.")
        return result("unmanaged")
    if not valid:
        reasons.append("Het bijpassende manifest is ongeldig of ingetrokken: nog niet ondersteund.")
        return result("unmanaged", candidates=list(matches))
    if len(valid) > 1:
        reasons.append("Meerdere catalogusvermeldingen passen; de gebruiker moet eerst kiezen. Tot dan: unmanaged.")
        return result("unmanaged", candidates=list(valid))
    entry, basis = next(iter(valid.values()))
    reasons.append(
        f"Mogelijke overeenkomst via {'de catalogusapp' if basis == 'catalog_app' else 'de image-repository'}; "
        "de gebruiker moet bevestigen dat deze app bij dit manifest hoort voordat het platform hem beheert."
    )
    return result("candidate", entry, basis, [entry.app_id])


def classify_inventory(
    inventory: Inventory | None, catalog: list[CatalogCandidate], *, now: datetime | None = None
) -> ImportReport:
    """Classificeer alle geïnstalleerde apps. Voert niets uit; zonder bruikbare inventaris is alles ``unknown``."""
    if inventory is None:
        return ImportReport(
            status="unknown",
            inventory_observed_at=None,
            counts={"unknown": 0},
            apps=[],
            reasons_nl=["Geen gemeten TrueNAS-inventaris: welke apps er draaien is onbekend."],
        )
    now = now or datetime.now(UTC)
    observed = inventory.observed_at if inventory.observed_at.tzinfo else inventory.observed_at.replace(tzinfo=UTC)
    report_reasons: list[str] = []
    if now - observed > MAX_INVENTORY_AGE or observed - now > MAX_INVENTORY_FUTURE:
        report_reasons.append(
            f"Inventaris van {observed.isoformat()} is verouderd of ligt in de toekomst; meet opnieuw. "
            "Tot dan is van geen enkele app bekend of hij beheerd wordt."
        )
        apps = [
            ImportedApp(
                name=a.name,
                state=a.state,
                status="unknown",
                matched_app_id=None,
                match_basis=None,
                candidates=[],
                manifest_current=None,
                requires_owner_confirmation=False,
                planned_actions=[],
                reasons_nl=report_reasons,
            )
            for a in inventory.apps
        ]
        status: Literal["ok", "unknown"] = "unknown"
    else:
        apps = [_classify(a, catalog) for a in inventory.apps]
        status = "ok"
    counts: dict[str, int] = {}
    for a in apps:
        counts[a.status] = counts.get(a.status, 0) + 1
    return ImportReport(
        status=status,
        inventory_observed_at=observed,
        counts=counts,
        apps=sorted(apps, key=lambda a: a.name),
        reasons_nl=report_reasons,
    )
