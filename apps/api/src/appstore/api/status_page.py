"""Statuspagina ``GET /`` in de ontwerptaal Peil + koppelen met een koppelcode (contract 1.2 §3.7; ADR-0009).

Alle beheerschermen staan in de Hub-UI; deze pagina is HTML zonder JavaScript uit het versie-gepinde Peil-sjabloon
(``design/ontwerptaal/statuspagina.html``, O-14) met CSP ``default-src 'none'``.

Contract 1.2 §3.7:

* Het koppelformulier bestaat **alleen zolang de dienst nog nooit gekoppeld is** (``first_linked_at`` leeg; een
  onleesbaar ``hub-link.json`` geldt als "al eens gekoppeld"). Daarna toont de pagina alleen de status (witte lijst
  §3.5) en "ontkoppelen of opnieuw koppelen gaat via de Hub"; geen ontkoppelen, geen lokale ontkoppelcode, geen
  herkoppelen na intrekking.
* Velden: Hub-adres (zelfde regels als ``HUB_URL``), koppelcode en de **verplichte** Hub-vingerafdruk.
* CSRF (double-submit-cookie, ``SameSite=Strict``, HttpOnly) + ``Origin``-controle; vaste Nederlandse meldingen; het
  Hub-adres, de koppelcode en de vingerafdruk van een mislukte poging worden nergens bewaard of teruggetoond.
* Max. 5 pogingen per 15 min per bron-IP (adres van de TCP-verbinding; ``X-Forwarded-For`` wordt nooit gebruikt) én
  max. 20 per 15 min voor de hele dienst (één API-proces → teller in het geheugen).

Ontwerptaal (ONTWERPTAAL §13.2): het eerste signaal is nooit groen als de dienst niet gereed is (totaal = slechtste van
gezondheid en gereedheid); "Gekoppeld" is een neutrale pil; een fout toont een vaste code en een selecteerbare
trace-ID. Een mislukte koppelpoging geeft direct een pagina terug (geen redirect), zodat de trace-ID die van het
verzoek zelf is (ook in ``X-Trace-Id`` en in de log).
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__, peil
from ..config import normalize_hub_url
from ..hubkeys import normalize_fingerprint, normalize_pairing_code
from ..hublink_store import LinkState, error_message
from ..models import CatalogEntry, CatalogSource, ManifestVersion
from ..timeutil import utcnow
from .common import ctx, get_db, tid
from .health import Readiness, measure

log = logging.getLogger("appstore.statuspagina")
router = APIRouter(tags=["statuspagina"])
DB = Annotated[Session, Depends(get_db)]
CSRF_COOKIE = "appstore_csrf"
WINDOW = 15 * 60
MAX_BRONNEN = 100

DIENST_NAAM = "Appstore en fork"
DIENST_OMSCHRIJVING = (
    "Appstore-catalogus van het TrueNAS Home Platform: controleert app-manifesten en levert installatievoorstellen "
    "aan de Hub. Voert zelf niets uit op TrueNAS."
)
KOPPELCODE_GELDIG = "60 minuten"
CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
MAANDEN = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec")
ERNST = {"gezond": 0, "onbekend": 1, "beperkt": 2, "storing": 3}
STATUSWOORD = {"gezond": "Gezond", "beperkt": "Beperkt", "storing": "Storing", "onbekend": "Onbekend"}

#: formulierveld → (id in het sjabloon, label)
VELDEN = {
    "hub_adres": ("hub", "Adres van de Hub"),
    "koppelcode": ("koppelcode", "Koppelcode"),
    "vingerafdruk": ("vingerafdruk", "Vingerafdruk van de Hub"),
}
#: vaste veldfouten per code → (veld, tekst)
VELDFOUT: dict[str, tuple[str, str]] = {
    "hub_adres_ongeldig": (
        "hub_adres",
        "Vul het adres in als http(s)://<NAS-adres>:<poort>, zonder pad. Geen localhost, geen loopback- of "
        "link-local-adres.",
    ),
    "andere_hub": (
        "hub_adres",
        "Dit adres verschilt van HUB_URL in het formulier van de Custom App. Koppelen met een andere Hub is geweigerd.",
    ),
    "koppelcode_ongeldig": (
        "koppelcode",
        "Een koppelcode heeft 12 tekens in groepen van 4 (XXXX-XXXX-XXXX), zoals de Hub hem toont.",
    ),
    "vingerafdruk_ongeldig": (
        "vingerafdruk",
        "Vul de vingerafdruk in zoals de Hub hem naast de koppelcode toont: 16 tekens in groepen van 4.",
    ),
    "hub_weigerde": (
        "koppelcode",
        "De Hub weigerde deze koppelcode: verlopen, al gebruikt of onbekend. Maak een nieuwe in de Hub.",
    ),
    "hub_vingerafdruk_onjuist": (
        "vingerafdruk",
        "Deze vingerafdruk past niet bij de sleutels van de Hub op dit adres. Er is niets opgeslagen.",
    ),
    "hub_onbereikbaar": (
        "hub_adres",
        "Op dit adres gaf geen Hub antwoord (netwerkfout, time-out, doorverwijzing of storing bij de Hub).",
    ),
}
#: invoercode per veld bij precies één fout (Hub-adres: ``hub_adres_ongeldig``/``andere_hub``; meer fouten:
#: ``invoer_ongeldig``)
INVOER = {"koppelcode": "koppelcode_ongeldig", "vingerafdruk": "vingerafdruk_ongeldig"}
#: veldfouten die uit het antwoord van de Hub komen (``last_error``-codes, §3.5)
HUB_CODES = frozenset({"hub_weigerde", "hub_vingerafdruk_onjuist", "hub_onbereikbaar"})
#: vaste meldingen bij een mislukte koppelpoging → (titel, tekst, HTTP-status)
POGING: dict[str, tuple[str, str, int]] = {
    "sessie_verlopen": (
        "Het formulier is verlopen",
        "Het formulier is verlopen of kwam niet van deze pagina. Laad de pagina opnieuw en vul de velden opnieuw in.",
        403,
    ),
    "al_gekoppeld": (
        "Koppelen kan hier niet meer",
        "Deze dienst is al eens gekoppeld. Ontkoppelen of opnieuw koppelen gaat via de Hub.",
        409,
    ),
    "koppelen_bezig": (
        "Er loopt al een koppelpoging",
        "Wacht even en laad de pagina opnieuw. Er is niets naar de Hub gestuurd.",
        409,
    ),
    "te_veel_pogingen": (
        "Te veel pogingen",
        "Er zijn te veel koppelpogingen gedaan. Wacht 15 minuten en probeer het opnieuw. Er is niets naar de Hub "
        "gestuurd.",
        429,
    ),
    "invoer_ongeldig": (
        "Koppelen is niet gestart",
        "Controleer de gemarkeerde velden. Er is niets naar de Hub gestuurd.",
        422,
    ),
}


# ------------------------------------------------------------------------------------------------- rate-limit
class _Attempts:
    """Schuivend venster per sleutel (bron-IP) én voor de hele dienst."""

    def __init__(self, per_ip: int, total: int, window: float) -> None:
        self.per_ip, self.total, self.window = per_ip, total, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window:
            q.popleft()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, now)
            self._trim(self._all, now)
            if len(q) >= self.per_ip or len(self._all) >= self.total:
                return False
            q.append(now)
            self._all.append(now)
            return True


def pairing_limiter() -> _Attempts:
    return _Attempts(per_ip=5, total=20, window=WINDOW)


# ------------------------------------------------------------------------------------------------- model
@dataclass(frozen=True)
class Controle:
    """Eén regel met een statuspil (toon uit de vaste lijst van het sjabloon)."""

    naam: str
    toon: str
    label: str
    reden: str | None = None

    def __post_init__(self) -> None:
        if self.toon not in peil.TONEN:
            raise ValueError(f"onbekende toon {self.toon!r}")


@dataclass(frozen=True)
class Melding:
    toon: Literal["fout", "info"]
    titel: str
    tekst: str
    code: str | None = None  # met code ⇒ ook de trace-ID (selecteerbaar)
    veld: str | None = None  # formulierveld waar de melding naar linkt


@dataclass(frozen=True)
class BronStand:
    naam: str
    soort: str
    aan: bool
    status: str  # never|ok|failed
    bij: datetime | None


@dataclass(frozen=True)
class Stand:
    """Alles wat de pagina toont, op één moment gemeten."""

    nu: datetime
    gereedheid: Readiness
    link: LinkState
    apps: int | None = None  # None = onbekend (database weg)
    geweigerd: int | None = None
    bronnen: list[BronStand] | None = None
    veldfouten: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------- tijd
def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def klok(value: datetime) -> str:
    return f"{_utc(value):%H:%M} UTC"


def datum_tijd(value: datetime | None) -> str:
    if value is None:
        return "onbekend"
    v = _utc(value)
    return f"{v.day} {MAANDEN[v.month - 1]} {v.year}, {v:%H:%M} UTC"


def iso_z(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def meervoud(n: int, een: str, veel: str) -> str:
    return f"{n} {een if n == 1 else veel}"


# ------------------------------------------------------------------------------------------------- inhoud
def _form_allowed(link: LinkState) -> bool:
    return not link.corrupt and link.first_linked_at is None


def koppel_woord(link: LinkState) -> str:
    if link.corrupt:
        return "koppelstatus onleesbaar"
    return {"gekoppeld": "gekoppeld", "ontkoppeld": "ontkoppeld", "ingetrokken": "ingetrokken"}.get(
        link.state, "niet gekoppeld"
    )


def gereed(stand: Stand) -> tuple[bool, str]:
    """Gereed = database en schema in orde (``/readyz``) én gekoppeld en door de Hub bevestigd."""
    r, link = stand.gereedheid, stand.link
    if not r.database:
        return False, "de database is niet bereikbaar"
    if r.schema == "verouderd":
        return False, "het databaseschema is niet bijgewerkt"
    if r.schema != "ok":
        return False, "de schemaversie is onbekend"
    if link.corrupt:
        return False, "de koppelstatus is onleesbaar"
    if link.state == "ontkoppeld":
        return False, "ontkoppeld door de Hub"
    if link.state == "ingetrokken":
        return False, "koppeling ingetrokken door de Hub"
    if not link.linked:
        return False, "wacht op koppeling met de Hub"
    if link.confirmed_at is None:
        return False, "koppeling nog niet bevestigd door de Hub"
    return True, ""


def koppelcontrole(link: LinkState) -> Controle | None:
    """Regel "Koppeling met de Hub" — alleen als er iets ontbreekt (een bevestigde koppeling is geen gezondheid)."""
    naam = "Koppeling met de Hub"
    if link.corrupt:
        return Controle(
            naam,
            "storing",
            "Onleesbaar",
            "De koppelstatus (/appdata/secrets/hub-link.json) is onleesbaar. De dienst vertrouwt niets van de Hub.",
        )
    if link.state == "ontkoppeld":
        return Controle(
            naam, "onbekend", "Ontkoppeld", "De Hub heeft deze dienst ontkoppeld; hij weigert elke opdracht."
        )
    if link.state == "ingetrokken":
        return Controle(
            naam, "onbekend", "Ingetrokken", "De Hub heeft de koppeling ingetrokken; de dienst weigert elke opdracht."
        )
    if not link.linked:
        bezig = " Er loopt een koppelpoging." if link.pending is not None else ""
        return Controle(
            naam,
            "onbekend",
            "Niet gekoppeld",
            "Zonder koppeling staan de schermen van deze dienst niet in de Hub en weigert hij elke opdracht." + bezig,
        )
    if link.confirmed_at is None:
        return Controle(
            naam,
            "onbekend",
            "Niet bevestigd",
            "Gekoppeld, maar de Hub heeft de koppeling nog niet bevestigd. De dienst probeert het elke minuut opnieuw.",
        )
    return None


def controles(stand: Stand) -> list[Controle]:
    r = stand.gereedheid
    out = [Controle("Proces", "gezond", "Gezond")]
    if r.database:
        out.append(Controle("Eigen database", "gezond", "Gezond"))
    else:
        out.append(
            Controle(
                "Eigen database",
                "storing",
                "Storing",
                "De ingebouwde PostgreSQL reageert niet. Catalogusgegevens zijn nu onbekend.",
            )
        )
    if r.schema == "ok":
        out.append(Controle("Databaseschema", "gezond", "Gezond"))
    elif r.schema == "verouderd":
        out.append(
            Controle(
                "Databaseschema",
                "storing",
                "Storing",
                f"Schemaversie {r.current or 'leeg'}, verwacht {r.head}: de migratie is nog niet uitgevoerd.",
            )
        )
    elif r.schema == "onbekend":
        out.append(Controle("Databaseschema", "onbekend", "Onbekend", "De schemaversie kon niet worden gelezen."))
    else:
        out.append(
            Controle(
                "Databaseschema", "onbekend", "Onbekend", "Niet te controleren zolang de database niet bereikbaar is."
            )
        )
    koppel = koppelcontrole(stand.link)
    if koppel is not None:
        out.append(koppel)
    return out


def bron_controle(b: BronStand) -> Controle:
    naam = f"Bron {b.naam} ({b.soort})"
    if not b.aan:
        return Controle(naam, "gate", "Uit", "Deze bron staat uit en wordt niet gesynchroniseerd.")
    if b.status == "ok":
        return Controle(naam, "gezond", "Gezond", f"Laatst gesynchroniseerd: {datum_tijd(b.bij)}.")
    if b.status == "failed":
        return Controle(naam, "beperkt", "Beperkt", f"Laatste synchronisatie mislukt ({datum_tijd(b.bij)}).")
    return Controle(naam, "onbekend", "Onbekend", "Nog nooit gesynchroniseerd.")


def slechtste(tonen: list[str]) -> str | None:
    gewogen = [t for t in tonen if t in ERNST]  # een gate (voorwaarde) verslechtert het totaal niet
    return max(gewogen, key=ERNST.__getitem__) if gewogen else None


def catalogus(stand: Stand) -> list[Controle]:
    naam = "Apps in de catalogus"
    if stand.apps is None or stand.bronnen is None:
        return [Controle(naam, "onbekend", "Onbekend", "De database is niet bereikbaar; we tonen geen aantal.")]
    if stand.apps == 0:
        out = [
            Controle(
                naam,
                "gate",
                "Nog leeg",
                "Er staan nog geen apps in de catalogus. Bronnen voeg je toe via de Hub. Dit is geen storing; er wacht "
                "een voorwaarde.",
            )
        ]
    else:
        geweigerd = stand.geweigerd or 0
        weiger = (
            "geen manifestversies geweigerd"
            if geweigerd == 0
            else meervoud(geweigerd, "manifestversie", "manifestversies") + " geweigerd door de validator"
        )
        out = [Controle(naam, "gezond", "Gezond", f"{meervoud(stand.apps, 'app', 'apps')}; {weiger}.")]
    bronnen = [bron_controle(b) for b in stand.bronnen]
    out += bronnen[:MAX_BRONNEN]
    rest = bronnen[MAX_BRONNEN:]
    if rest:
        toon = slechtste([c.toon for c in rest]) or "gate"
        out.append(
            Controle(
                f"Nog {meervoud(len(rest), 'bron', 'bronnen')}",
                toon,
                STATUSWOORD.get(toon, "Uit"),
                f"Alleen de eerste {MAX_BRONNEN} bronnen staan hier; dit is de slechtste stand van de rest.",
            )
        )
    return out


def totaal(rijen: list[Controle], klaar: bool, waarom: str) -> tuple[str, str, str]:
    """(toon, label, toelichting). Totaal = slechtste van gezondheid en gereedheid; nooit groen als niet gereed."""
    toon = slechtste([c.toon for c in rijen]) or "onbekend"
    if not klaar and ERNST[toon] < ERNST["onbekend"]:
        toon = "onbekend"
    if not klaar:
        if toon == "onbekend":
            return toon, "Niet gereed", f"Deels onbekend — {waarom}."
        return toon, STATUSWOORD[toon], f"Niet gereed — {waarom}."
    if toon == "gezond":
        return toon, "Alles gezond", "Gereed; alle controles zijn gezond."
    n = sum(1 for c in rijen if c.toon == toon)
    woord = {"onbekend": "onbekend", "beperkt": "beperkt", "storing": "in storing"}[toon]
    label = "Deels onbekend" if toon == "onbekend" else STATUSWOORD[toon]
    return toon, label, f"Gereed; {meervoud(n, 'onderdeel is', 'onderdelen zijn')} {woord}."


def koppel_melding(link: LinkState) -> Melding | None:
    """Melding bovenaan op ``GET /`` uit de opgeslagen koppelstatus (vaste teksten, witte lijst §3.5)."""
    if link.corrupt:
        return Melding(
            "fout",
            "Koppelstatus onleesbaar",
            "Het bestand /appdata/secrets/hub-link.json is onleesbaar; de dienst vertrouwt niets van de Hub. Herstel: "
            "maak in de Hub een nieuw enrollment-token en zet het in het formulier van de Custom App "
            "(HUB_ENROLLMENT_TOKEN).",
            "koppelstatus_onleesbaar",
        )
    fout = link.last_error
    if link.pending is not None:
        via = "het token uit het formulier van de Custom App" if link.pending.kind == "token" else "een koppelcode"
        tekst = f"Er loopt een koppelpoging via {via}. Laad de pagina over een minuut opnieuw."
        if fout is not None:
            tekst += f" Laatste melding: {error_message(fout.code)}"
        return Melding("info", "Koppelen loopt", tekst, fout.code if fout else None)
    if fout is None or (link.state == "ingetrokken" and fout.code == "ingetrokken_door_hub"):
        return None  # intrekking staat al in het blok "Koppeling met de Hub"
    tekst = f"{error_message(fout.code)} Tijdstip: {datum_tijd(parse(fout.at))}."
    if link.linked:
        tekst += " De bestaande koppeling blijft gelden."
    return Melding("fout", "Laatste koppelpoging is mislukt", tekst, fout.code)


def gegevens(
    stand: Stand,
    *,
    csrf: str,
    trace_id: str,
    melding: Melding | None,
) -> dict[str, Any]:
    """Velden voor het sjabloon (alleen vaste teksten en de witte lijst van §3.5)."""
    link = stand.link
    rijen = controles(stand)
    dienst = catalogus(stand)
    klaar, waarom = gereed(stand)
    toon, label, toelichting = totaal(rijen + dienst, klaar, waarom)
    nu_iso, nu_klok = iso_z(stand.nu), klok(stand.nu)
    eerste_veld = next((v for v in VELDEN if v in stand.veldfouten), None)
    titel = f"{label.lower()}, {koppel_woord(link)}"
    if melding is not None and melding.toon == "fout":
        titel = f"{melding.titel.lower()} · {titel}"
    hub_adres = link.hub_url if link.hub_url and re.match(r"^https?://", link.hub_url) else ""
    data: dict[str, Any] = {
        "dienst_naam": DIENST_NAAM,
        "dienst_omschrijving": DIENST_OMSCHRIJVING,
        "titel_toestand": titel,
        "spring_doel": VELDEN[eerste_veld][0] if eerste_veld else "inhoud",
        "spring_tekst": f"Naar het veld {VELDEN[eerste_veld][1]} (fout)" if eerste_veld else "Naar inhoud",
        "totaal_toon": toon,
        "totaal_label": label,
        "totaal_toelichting": toelichting,
        "checks": [
            {"naam": c.naam, "toon": c.toon, "label": c.label, "reden": c.reden, "iso": nu_iso, "tijd": nu_klok}
            for c in rijen
        ],
        "gereed": "Ja" if klaar else f"Nee — {waarom}",
        "versie": __version__,
        "bijgewerkt": datum_tijd(stand.nu),
        "bijgewerkt_iso": nu_iso,
        "dienst_blok_titel": "Catalogus",
        "dienst_blok": [{"naam": c.naam, "toon": c.toon, "label": c.label, "reden": c.reden} for c in dienst],
        # precies één koppeltoestand (een onleesbare koppelstatus heeft er geen: melding + controle zeggen het)
        "koppelformulier": _form_allowed(link),
        "gekoppeld": not link.corrupt and link.state == "gekoppeld",
        "ontkoppeld": not link.corrupt and link.state == "ontkoppeld",
        "ingetrokken": not link.corrupt and link.state == "ingetrokken",
        "gekoppeld_sinds": datum_tijd(parse(link.linked_at)),
        "hub_adres": hub_adres,
        "hub_vingerafdruk": link.pins.keyset_fingerprint if link.pins else "onbekend",
        "koppelcode_geldig": KOPPELCODE_GELDIG,
        "csrf_token": csrf,
        "fout_hub_adres": stand.veldfouten.get("hub_adres"),
        "fout_koppelcode": stand.veldfouten.get("koppelcode"),
        "fout_vingerafdruk": stand.veldfouten.get("vingerafdruk"),
    }
    if melding is not None:
        doel = VELDEN[melding.veld][0] if melding.veld and data["koppelformulier"] else None
        data.update(
            {
                "melding": True,
                "melding_toon": melding.toon,
                "melding_icoon": peil.MELDING_ICOON[melding.toon],
                "melding_titel": melding.titel,
                "melding_tekst": melding.tekst,
                "melding_link": bool(doel),
                "melding_link_doel": doel,
                "melding_link_tekst": f"Naar het veld {VELDEN[melding.veld][1]}" if doel and melding.veld else None,
                "trace_id": trace_id if melding.code else None,
                "melding_code": melding.code,
            }
        )
    return data


def pagina(stand: Stand, *, csrf: str, trace_id: str, melding: Melding | None) -> str:
    return peil.render(peil.sjabloon().tekst, gegevens(stand, csrf=csrf, trace_id=trace_id, melding=melding))


# ------------------------------------------------------------------------------------------------- routes
def _meet(request: Request, db: Session, veldfouten: dict[str, str] | None = None) -> Stand:
    c = ctx(request)
    r = measure(c)
    link = c.store.read()
    apps = geweigerd = None
    bronnen: list[BronStand] | None = None
    if r.database:
        try:
            apps = db.scalar(select(func.count()).select_from(CatalogEntry)) or 0
            geweigerd = (
                db.scalar(select(func.count()).select_from(ManifestVersion).where(ManifestVersion.valid.is_(False)))
                or 0
            )
            bronnen = [
                BronStand(s.name, s.kind, bool(s.enabled), s.last_sync_status, s.last_sync_at)
                for s in db.scalars(select(CatalogSource).order_by(CatalogSource.name)).all()
            ]
        except Exception:
            log.exception("catalogusgegevens voor de statuspagina niet leesbaar")
            db.rollback()
            apps = geweigerd = bronnen = None
    return Stand(utcnow(), r, link, apps, geweigerd, bronnen, veldfouten or {})


def _csrf(request: Request) -> str:
    token = request.cookies.get(CSRF_COOKIE) or ""
    return token if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token) else secrets.token_urlsafe(24)


def _antwoord(html: str, csrf: str, status: int = 200, extra: dict[str, str] | None = None) -> HTMLResponse:
    resp = HTMLResponse(html, status_code=status, headers={**HEADERS, **(extra or {})})
    resp.set_cookie(CSRF_COOKIE, csrf, httponly=True, samesite="strict", max_age=3600)
    return resp


def _same_origin(request: Request) -> bool:
    """``Origin`` moet gelijk zijn aan het eigen adres (schema + Host-header van dit verzoek)."""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or origin == "null":
        return False
    parts = urlsplit(origin)
    return parts.scheme == request.url.scheme and parts.netloc.lower() == host.lower() and not parts.path


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def status_page(request: Request, db: DB, melding: str | None = None) -> HTMLResponse:
    stand = _meet(request, db)
    link = stand.link
    trace = tid(request)
    boven: Melding | None = None
    if melding == "gekoppeld" and link.linked:  # na een geslaagde koppeling (PRG); alleen deze vaste waarde
        boven = Melding(
            "info",
            "Gekoppeld met de Hub",
            "De Hub heeft de koppeling bevestigd."
            if link.confirmed_at
            else "De koppeling is opgeslagen; de bevestiging door de Hub volgt.",
        )
    else:
        boven = koppel_melding(link)
    if boven is not None and boven.code:
        log.info("statuspagina toont melding %s (trace_id %s)", boven.code, trace)
    csrf = _csrf(request)
    return _antwoord(pagina(stand, csrf=csrf, trace_id=trace, melding=boven), csrf)


@router.post("/koppelen", include_in_schema=False)
def pair(
    request: Request,
    db: DB,
    hub_adres: Annotated[str, Form()] = "",
    koppelcode: Annotated[str, Form()] = "",
    hub_vingerafdruk: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    c = ctx(request)
    trace = tid(request)
    client = request.client.host if request.client else "onbekend"  # TCP-adres; nooit X-Forwarded-For

    def mislukt(code: str, veldfouten: dict[str, str] | None = None, retry_after: float | None = None) -> HTMLResponse:
        log.warning("koppelen via de statuspagina: %s (bron-IP %s, trace_id %s)", code, client, trace)
        if code in POGING:
            titel, tekst, status = POGING[code]
        elif code in VELDFOUT and code not in HUB_CODES:  # eigen invoercontrole: niets naar de Hub gestuurd
            titel, tekst, status = POGING["invoer_ongeldig"]
        else:  # uitkomst van de Hub (vaste last_error-codes, §3.5)
            titel, tekst = "Koppelen is mislukt", error_message(code) or "Koppelen is niet gelukt."
            status = {"hub_onbereikbaar": 502, "rate_limited": 429}.get(code, 400)
            veld = VELDFOUT.get(code)
            if veld is not None:
                veldfouten = {veld[0]: veld[1]}
        velden = veldfouten or {}
        eerste = next((v for v in VELDEN if v in velden), None)
        stand = _meet(request, db, velden)
        token = _csrf(request)
        extra = {"Retry-After": str(int(retry_after))} if retry_after else None
        html = pagina(stand, csrf=token, trace_id=trace, melding=Melding("fout", titel, tekst, code, eerste))
        return _antwoord(html, token, status, extra)

    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not csrf or not secrets.compare_digest(cookie, csrf) or not _same_origin(request):
        return mislukt("sessie_verlopen")
    if not _form_allowed(c.store.read()):
        return mislukt("al_gekoppeld")  # §3.7: na de eerste koppeling alleen via de Hub
    if not request.app.state.pairing_attempts.allow(client):
        return mislukt("te_veel_pogingen", retry_after=WINDOW)
    fouten: dict[str, str] = {}
    url: str | None = None
    try:
        url = (
            normalize_hub_url(hub_adres, allow_loopback=c.settings.allow_loopback_hub)
            if len(hub_adres) <= 512
            else None
        )
    except ValueError:
        url = None
    if not url:
        fouten["hub_adres"] = VELDFOUT["hub_adres_ongeldig"][1]
    elif c.settings.hub_url and url != c.settings.hub_url:
        # bij installatie via de Hub staat HUB_URL vast: nooit met een andere (mogelijk vreemde) Hub koppelen
        fouten["hub_adres"] = VELDFOUT["andere_hub"][1]
    code = normalize_pairing_code(koppelcode)
    if code is None:
        fouten["koppelcode"] = VELDFOUT["koppelcode_ongeldig"][1]
    fingerprint = normalize_fingerprint(hub_vingerafdruk)
    if fingerprint is None:
        fouten["vingerafdruk"] = VELDFOUT["vingerafdruk_ongeldig"][1]
    if fouten:
        if len(fouten) > 1:
            naam = "invoer_ongeldig"
        elif "hub_adres" in fouten:
            naam = "andere_hub" if url else "hub_adres_ongeldig"
        else:
            naam = INVOER[next(iter(fouten))]
        return mislukt(naam, fouten)
    assert url is not None and code is not None and fingerprint is not None
    # de claim van de poging gebeurt atomair in de client (bestandsvergrendeling): geen wedloop tussen twee
    # formulieren, of tussen het formulier en een env-token, en nooit na de eerste koppeling (§3.7)
    result = c.hub.pair_with_code(hub_url=url, pairing_code=code, fingerprint=fingerprint, trace_id=trace)
    if result.kind == "refused":
        return mislukt("al_gekoppeld" if result.code == "al_gekoppeld" else "koppelen_bezig")
    if not result.ok:
        return mislukt(result.code or "hub_weigerde", retry_after=result.retry_after)
    log.info("gekoppeld via de statuspagina (bron-IP %s, trace_id %s)", client, trace)
    return RedirectResponse("/?melding=gekoppeld", status_code=303)
