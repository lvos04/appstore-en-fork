"""Register van bekende bestaande apps (APP-01…APP-20) en de herkenning daarvan (B08-002).

Het register (``register.yaml``) bevat alleen wat in het pakket staat (APP_REGISTER + A01…A20) plus herkenningsregels
met een vastgelegde bron. Het zegt **niets** over de NAS van de gebruiker: tags, digests, poorten en datasets komen
uitsluitend als gemeten readback uit de Hub-inventaris.

Herkenning is altijd een **voorstel** (``kandidaat``); de eigenaar bevestigt zelf (B08-002 "user confirmation of
owner"). Volgorde van zekerheid:

1. installatiewijze ``catalog_app``: exacte naam van de TrueNAS-catalogusapp;
2. installatiewijze ``custom_app`` (``ix-app``) of onbekend: exacte image-repository (genormaliseerd: Docker Hub-
   voorvoegsels, LinuxServer-spiegels);
3. alleen de appnaam of alleen de herkenning door de Hub telt **nooit**.

Meerdere passende vermeldingen → ``onbekend`` (de gebruiker kiest), nooit gegokt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REGISTER_PATH = Path(__file__).with_name("register.yaml")
KEY_PATTERN = r"^[a-z][a-z0-9_-]{0,38}[a-z0-9]$"
Key = Annotated[str, Field(pattern=KEY_PATTERN)]

DecisionCode = Literal["behouden", "voorwaardelijk_behouden", "kandidaat_uitschakelen", "bouwen_integreren"]
OwnerKind = Literal["derde", "bestaand", "eigen_los", "eigen"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IntendedDecision(_Strict):
    code: DecisionCode
    tekst_nl: str


class Owner(_Strict):
    soort: OwnerKind
    repo: str | None


class Recognition(_Strict):
    catalogus_apps: list[str]
    image_repositories: list[str]
    toelichting_nl: str | None = None


class Dependency(_Strict):
    app_id: Key
    soort: Literal["vereist", "optioneel", "te_controleren"]
    reden_nl: str
    bron: str


class KnownApp(_Strict):
    app_id: Key
    excel_id: Annotated[str, Field(pattern=r"^APP-\d{2}$")]
    naam: str
    a_doc: str
    voorgenomen_besluit: IntendedDecision
    waarom_nl: str
    belangrijkste_controle_nl: str
    eigenaar: Owner
    i26_stappen: list[Annotated[str, Field(pattern=r"^I26-26-0[1-6]$")]]
    proefstop_uren: Annotated[int, Field(ge=1, le=24 * 30)] | None = None
    zonder_gebruik_dagen: Annotated[int, Field(ge=1, le=365)] | None = None
    herkenning: Recognition
    afhankelijk_van: list[Dependency]

    @property
    def decommission_candidate(self) -> bool:
        """Voorgenomen besluit laat uitschakelen toe (A3 'Proefstop (alleen indien kandidaat)')."""
        return self.voorgenomen_besluit.code in ("kandidaat_uitschakelen", "voorwaardelijk_behouden")


class RecognitionSources(_Strict):
    truenas_catalogus: str
    eigen_images: str
    upstream_gecontroleerd_op: str


class PlatformApp(_Strict):
    """Eigen nieuwe dienst (contract 1.2 §2): vaste app_id en image; geen APP-ID en geen beslisregister."""

    app_id: Key
    naam: str
    repo: str
    image_repositories: list[str]


class Register(_Strict):
    register_versie: str
    bron: str
    herkenning_bron: RecognitionSources
    platform_apps: list[PlatformApp]
    apps: list[KnownApp]

    def get(self, app_id: str) -> KnownApp | None:
        return next((a for a in self.apps if a.app_id == app_id), None)

    def platform(self, app_id: str) -> PlatformApp | None:
        return next((a for a in self.platform_apps if a.app_id == app_id), None)


@dataclass(frozen=True)
class LoadedRegister:
    register: Register
    sha256: str


@lru_cache(maxsize=1)
def load_register(path: Path = REGISTER_PATH) -> LoadedRegister:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    reg = Register.model_validate(data)
    ids = [a.app_id for a in reg.apps] + [p.app_id for p in reg.platform_apps]
    if len(ids) != len(set(ids)):
        raise ValueError("register.yaml: dubbele app_id")
    excel = [a.excel_id for a in reg.apps]
    if len(excel) != len(set(excel)):
        raise ValueError("register.yaml: dubbele excel_id")
    for app in reg.apps:
        for dep in app.afhankelijk_van:
            if reg.get(dep.app_id) is None:
                raise ValueError(f"register.yaml: {app.app_id} hangt af van onbekende app {dep.app_id}")
    return LoadedRegister(reg, hashlib.sha256(raw).hexdigest())


# ------------------------------------------------------------------------------------------------ controlelijst
#: Letterlijke teksten uit het sjabloon van de A-documenten (A01…A20 zijn op deze teksten na gelijk);
#: ``tests/unit/test_bestaande_apps_register.py`` controleert dat elke tekst in elk A-document staat.
INVENTORY_ITEMS: tuple[tuple[str, str], ...] = (
    (
        "inventaris-1",
        "Draaiende TrueNAS image-tag/digest, container ID, owner en status (niet alleen repositoryversie).",
    ),
    ("inventaris-2", "Netwerkpoorten, domein, eventuele VPN/tunnel en gekoppelde app credentials (geschoond)."),
    ("inventaris-3", "Datasets, database en restoretype; controleer OneDrive-dekking en snapshot."),
    ("inventaris-4", "API dependencies: welke apps praten ermee en welke acties mogen zij uitvoeren?"),
    ("inventaris-5", "Laatste echte gebruiksdatum, hulpcode, logs en performance."),
)
PHASES: tuple[tuple[str, str, str], ...] = (
    ("a0-meten", "A0: Meten", "documenteer bovenstaande, inclusief screenshot van huidige UI en configuratie-export."),
    ("a1-integreren", "A1: Niet-mutatief integreren", "koppel in Hub als catalogusitem, toon status en diep-link."),
    (
        "a2-verbeteren",
        "A2: Verbeteren of vervangen",
        "voer alleen functies die aantoonbaar ontbreken uit via de in B-plannen vastgelegde data-eigenaar.",
    ),
    (
        "a3-proefstop",
        "A3: Proefstop (alleen indien kandidaat)",
        "schakel tijdelijk uit met eigen data intact en verifieer gebruikersreizen.",
    ),
    (
        "a4-besluit",
        "A4: Behoud/decommission",
        "beslissing schriftelijk, backup restore test, veilige rollback; data verwijderen staat los van app-stop.",
    ),
)
READY_TEXT = (
    "installatiestatus en functionele proef op echte TrueNAS gelogd zijn; bij stoppen kunnen gebruikers in één "
    "handeling naar de oude app terug."
)
CheckPhase = Literal["inventaris", "meten", "integreren", "verbeteren", "proefstop", "besluit", "acceptatie", "gereed"]


@dataclass(frozen=True)
class CheckTemplate:
    id: str
    fase: CheckPhase
    tekst_nl: str
    bron: str
    default_nvt_nl: str | None = None  # standaard 'n.v.t.' met deze reden (de gebruiker kan dat wijzigen)


def checks_for(app: KnownApp) -> list[CheckTemplate]:
    """Controlelijst van één app: verplichte inventaris, fasen A0…A4, aandachtspunt, acceptatie en 'gereed'."""
    src = f"04_BESTAANDE_APPS/{app.a_doc}"
    out = [CheckTemplate(cid, "inventaris", text, f"{src} §Verplichte inventaris") for cid, text in INVENTORY_ITEMS]
    phase_of: dict[str, CheckPhase] = {
        "a0-meten": "meten",
        "a1-integreren": "integreren",
        "a2-verbeteren": "verbeteren",
        "a3-proefstop": "proefstop",
        "a4-besluit": "besluit",
    }
    for cid, label, text in PHASES:
        nvt = None
        if cid == "a3-proefstop" and not app.decommission_candidate:
            nvt = f"Voorgenomen besluit is '{app.voorgenomen_besluit.tekst_nl}': geen proefstop gepland."
        out.append(CheckTemplate(cid, phase_of[cid], f"{label}: {text}", f"{src} §Gefaseerde aanpak", nvt))
    out.append(
        CheckTemplate("aandachtspunt", "inventaris", f"Aandachtspunt: {app.waarom_nl}", f"{src} §Waarom"),
    )
    out.append(
        CheckTemplate(
            "acceptatie", "acceptatie", app.belangrijkste_controle_nl, f"{src} §Testcase en beslissing; APP_REGISTER.md"
        )
    )
    out.append(CheckTemplate("gereed", "gereed", f"Gereed wanneer: {READY_TEXT}", f"{src} §Testcase en beslissing"))
    return out


# ------------------------------------------------------------------------------------------------ herkenning
_LSIO_MIRRORS = ("lscr.io/linuxserver/", "ghcr.io/linuxserver/", "docker.io/linuxserver/", "linuxserver/")


def normalize_repository(repository: str) -> str:
    """Canonieke vorm van een image-repository (kleine letters, zonder tag/digest, zonder Docker Hub-voorvoegsels;
    LinuxServer-spiegels ``lscr.io``/``ghcr.io``/Docker Hub zijn dezelfde image)."""
    ref = repository.strip().lower().split("@", 1)[0]
    last = ref.rsplit("/", 1)
    if ":" in last[-1]:
        ref = ref[: ref.rfind(":")]
    for prefix in ("index.docker.io/", "docker.io/", "registry-1.docker.io/"):
        if ref.startswith(prefix):
            ref = ref.removeprefix(prefix)
            break
    if ref.startswith("library/"):
        ref = ref.removeprefix("library/")
    for mirror in _LSIO_MIRRORS:
        if ref.startswith(mirror):
            return "lscr.io/linuxserver/" + ref.removeprefix(mirror)
    return ref


MatchBasis = Literal["catalogusapp", "image", "manifest_catalogusapp", "manifest_image", "gebruiker"]


@dataclass(frozen=True)
class KnownEntry:
    """Eén bekende vermelding om tegen te herkennen: uit het register (APP-xx of eigen platformdienst) of uit een
    geldig catalogusmanifest. ``owner_*`` komt uit diezelfde bron, nooit uit de meting."""

    app_id: str
    naam: str
    bron: Literal["register", "manifest"]
    catalog_apps: frozenset[str]
    repositories: frozenset[str]
    owner_kind: str
    owner_repo: str | None
    owner_source: str
    excel_id: str | None = None


def register_entries(reg: Register) -> list[KnownEntry]:
    out = [
        KnownEntry(
            app.app_id,
            app.naam,
            "register",
            frozenset(a.lower() for a in app.herkenning.catalogus_apps),
            frozenset(normalize_repository(r) for r in app.herkenning.image_repositories),
            app.eigenaar.soort,
            app.eigenaar.repo,
            f"APP_REGISTER.md / {app.a_doc}",
            app.excel_id,
        )
        for app in reg.apps
    ]
    out += [
        KnownEntry(
            p.app_id,
            p.naam,
            "register",
            frozenset(),
            frozenset(normalize_repository(r) for r in p.image_repositories),
            "eigen",
            p.repo,
            "platformcontract 1.2 §2",
        )
        for p in reg.platform_apps
    ]
    return out


@dataclass(frozen=True)
class MatchResult:
    status: Literal["kandidaat", "onbekend"]
    app_id: str | None
    basis: MatchBasis | None
    candidates: tuple[str, ...]
    reason_nl: str


def match_app(
    *,
    install_method: str,
    catalog_app: str | None,
    repositories: list[str],
    entries: list[KnownEntry],
) -> MatchResult:
    """Herken één geïnstalleerde app. Alleen catalogusapp-naam (bij ``catalog_app``) of exacte image-repository telt;
    de appnaam en de herkenning door de Hub nooit."""
    found: dict[str, MatchBasis] = {}
    cat = (catalog_app or "").strip().lower()
    if install_method == "catalog_app" and cat and cat != "ix-app":
        for e in entries:
            if cat in e.catalog_apps:
                found.setdefault(e.app_id, "catalogusapp" if e.bron == "register" else "manifest_catalogusapp")
    if not found:
        repos = {normalize_repository(r) for r in repositories if r}
        for e in entries:
            if repos & e.repositories:
                found.setdefault(e.app_id, "image" if e.bron == "register" else "manifest_image")
    if not found:
        return MatchResult(
            "onbekend",
            None,
            None,
            (),
            "Geen bekende app herkend aan de catalogusapp of de image-repository; op de appnaam wordt nooit herkend. "
            "De app blijft onbekend en ongemoeid.",
        )
    if len(found) > 1:
        return MatchResult(
            "onbekend",
            None,
            None,
            tuple(sorted(found)),
            "Meerdere bekende apps passen; er wordt niet gegokt. Een beheerder kan de juiste app handmatig kiezen.",
        )
    app_id, basis = next(iter(found.items()))
    how = "de catalogusapp" if basis in ("catalogusapp", "manifest_catalogusapp") else "de image-repository"
    return MatchResult(
        "kandidaat",
        app_id,
        basis,
        (app_id,),
        f"Mogelijke overeenkomst via {how}. Een beheerder moet de eigenaar bevestigen voordat de app als 'adopted' "
        "in de catalogus staat.",
    )
