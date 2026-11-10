"""Koppelstatus met de Hub in ``/appdata/secrets/hub-link.json`` (platformcontract 1.3 §3.5).

* Eén bestand, 0600, in ``/appdata/secrets`` (0700); eigenaar is de procesgebruiker (568 in de image).
* Schrijven: nieuw bestand in dezelfde map (``O_CREAT|O_EXCL``, 0600) → ``fsync`` → ``rename`` → ``fsync`` van de map,
  onder een exclusieve vergrendeling op ``hub-link.lock`` (API en worker schrijven allebei; lezen-wijzigen-schrijven
  altijd binnen de vergrendeling).
* Lezen: elk proces leest het bestand opnieuw zodra inode, mtime of grootte verandert, zodat ontkoppelen, intrekken of
  rotatie door het ene proces direct voor het andere geldt.
* Niet in de database: een database-herstel brengt geen oude koppeling, pins of ingetrokken token terug.
* **Onleesbaar** (geen geldige JSON, een andere major van ``schema_version``, ontbrekende verplichte velden, een andere
  ``app_id``, verkeerde eigenaar of rechten) → fail-closed: niets wordt vertrouwd (``not_linked``), de statuspagina
  biedt géén koppelformulier en meldt ``koppelstatus_onleesbaar``; ``/readyz`` blijft 200. Een gewone schrijfactie
  laat zo'n bestand ongemoeid (nooit stil terug naar ``nooit_gekoppeld``). Alleen een poging met een **nieuw env-token**
  vervangt het, bij haar eerste schrijfactie en vóór het verzoek aan de Hub: eerst wordt het origineel bewaard als
  ``hub-link.json.onleesbaar-<UTC-tijd>`` (0600) en ``unreadable_replaced_at`` gezet. Zolang ``first_linked_at`` daarna
  leeg is, blijft de toestand voor de statuspagina "onleesbaar" (geen formulier, geen vertrouwen).
* De dienst schrijft ``schema_version: "1.3"``; een lezer accepteert elke ``1.x`` en bewaart onbekende extra velden.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import stat
import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from . import APP_ID

log = logging.getLogger("appstore.hublink")

LINK_FILE = "hub-link.json"
LOCK_FILE = "hub-link.lock"
MAX_USED_TOKENS = 20
SCHEMA_VERSION = "1.3"
#: verplichte velden van een leesbaar bestand; bij ``gekoppeld`` ook de koppeling zelf (§3.5)
REQUIRED = ("schema_version", "state", "app_id")
REQUIRED_LINKED = ("hub_url", "service_id", "service_token", "pins", "first_linked_at", "linked_at")

State = Literal["nooit_gekoppeld", "gekoppeld", "ontkoppeld", "ingetrokken"]

#: Vaste Nederlandse meldingen per ``last_error.code`` (§3.5). Onbekende codes krijgen een algemene melding.
ERROR_MESSAGES: dict[str, str] = {
    "hub_onbereikbaar": "De Hub was niet bereikbaar (netwerkfout, time-out of storing bij de Hub).",
    "hub_weigerde": "De Hub weigerde: de koppelcode of het token is onbekend, verlopen of al gebruikt.",
    "hub_incompatibel": "De Hub weigerde het verzoek als ongeldig; de versies van Hub en app passen niet bij elkaar.",
    "hub_antwoord_ongeldig": "Het antwoord van de Hub klopte niet; er is niets opgeslagen.",
    "hub_vingerafdruk_onjuist": "De sleutelset-vingerafdruk van de Hub klopt niet; er is niets opgeslagen.",
    "enroll_endpoint_ontbreekt": "Deze Hub-versie kent aanmelden (enrollment) niet.",
    "rate_limited": "De Hub vraagt even te wachten: te veel mislukte pogingen.",
    "koppelen_onderbroken": "Koppelen is onderbroken (herstart); voer de koppelcode opnieuw in of maak een nieuw token.",
    "token_verlopen_na_60_min": "Het token is na 60 minuten niet gelukt en wordt niet meer geprobeerd; maak een nieuw "
    "token in de Hub.",
    "token_ongeldig": "HUB_ENROLLMENT_TOKEN heeft niet het juiste formaat (enr_ + 43 tekens); het wordt niet gebruikt.",
    "ingetrokken_door_hub": "De Hub heeft deze koppeling ingetrokken; opnieuw koppelen gaat via de Hub.",
    "overzetten_onvolledig": "De koppeling uit een oudere versie kon niet volledig worden overgezet; opnieuw koppelen "
    "gaat via de Hub.",
    "vingerafdruk_ongeldig": "HUB_KEY_FINGERPRINT heeft niet de vorm XXXX-XXXX-XXXX-XXXX; er is niets naar de Hub "
    "gestuurd. Verbeter de waarde in de instellingen van de Custom App.",
    "hub_adres_ongeldig": "HUB_URL is geen geldig Hub-adres (http(s)://<NAS-adres>:<poort>, geen localhost); er is niets "
    "naar de Hub gestuurd. Verbeter de waarde in de instellingen van de Custom App.",
    "koppelstatus_onleesbaar": "De koppelstatus van deze dienst is onleesbaar; de dienst vertrouwt niets van de Hub. "
    "Herstel gaat alleen met een nieuw koppeltoken uit de Hub.",
}
#: HTTP-uitkomst van de Hub per vaste ``last_error``-code (§3.3/§3.5, RV6): wat de dienst zag
HTTP_OUTCOME: dict[str, str] = {
    "hub_onbereikbaar": "netwerkfout, time-out, 3xx of 5xx",
    "hub_weigerde": "401 (en overige 4xx)",
    "hub_incompatibel": "413 of 422",
    "enroll_endpoint_ontbreekt": "404",
    "rate_limited": "429",
    "hub_antwoord_ongeldig": "200 met een ongeldig antwoord",
    "hub_vingerafdruk_onjuist": "200 met een andere sleutelset-vingerafdruk",
}


def error_message(code: str | None) -> str | None:
    if code is None:
        return None
    return ERROR_MESSAGES.get(code, "Koppelen is niet gelukt.")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UnreadableError(ValueError):
    """``hub-link.json`` is onleesbaar in de zin van §3.5."""


class PublicJwk(_Model):
    kty: Literal["OKP"] = "OKP"
    crv: Literal["Ed25519"] = "Ed25519"
    x: str


class Pin(_Model):
    kid: str
    jwk: PublicJwk
    thumbprint: str
    pinned_at: str
    source: Literal["enroll", "rotation", "overgezet"] = "enroll"


class Pins(_Model):
    jwt: list[Pin]
    policy: list[Pin]
    keyset_fingerprint: str
    rotation_sequence: int = 0


class Pending(_Model):
    kind: Literal["token", "koppelcode"]
    secret_sha256: str
    hub_url: str
    started_at: str
    attempts: int = 0


class LastError(_Model):
    code: str
    at: str


class LinkState(_Model):
    #: extra velden mogen (§3.5) en blijven bij herschrijven behouden
    model_config = ConfigDict(extra="allow")

    schema_version: str = SCHEMA_VERSION
    state: State = "nooit_gekoppeld"
    app_id: str = APP_ID
    hub_url: str | None = None
    service_id: str | None = None
    via: Literal["token", "koppelcode"] | None = None
    first_linked_at: str | None = None
    linked_at: str | None = None
    confirmed_at: str | None = None
    service_token: str | None = None
    service_token_last4: str | None = None
    pins: Pins | None = None
    previous_pins: Pins | None = None
    pending: Pending | None = None
    used_enrollment_token_sha256: list[str] = Field(default_factory=list)
    last_error: LastError | None = None
    # vastgelegde extra velden (§3.5): rotatieverzoek van de Hub en een geweigerde sleutelrotatie overleven een
    # herstart; een vervangen onleesbaar bestand blijft zichtbaar
    token_rotation_requested_at: str | None = None
    key_rotation_blocked_sequence: int | None = None
    unreadable_replaced_at: str | None = None

    _corrupt: bool = PrivateAttr(default=False)

    @property
    def corrupt(self) -> bool:
        """Het bestand zelf is nu onleesbaar (§3.5)."""
        return self._corrupt

    @property
    def unreadable(self) -> bool:
        """Toestand "koppelstatus onleesbaar" voor §3.7: het bestand is onleesbaar, of het is vervangen en sindsdien is
        er nog nooit gekoppeld (geen formulier, geen vertrouwen)."""
        return self._corrupt or (self.unreadable_replaced_at is not None and self.first_linked_at is None)

    @property
    def linked(self) -> bool:
        """Alleen ``gekoppeld`` mét token en pins vertrouwt Hub-JWT's (§3.5)."""
        return self.state == "gekoppeld" and bool(self.service_token) and self.pins is not None

    def remember_token(self, digest: str) -> None:
        if digest not in self.used_enrollment_token_sha256:
            self.used_enrollment_token_sha256.append(digest)
        self.used_enrollment_token_sha256 = self.used_enrollment_token_sha256[-MAX_USED_TOKENS:]

    def public(self) -> dict[str, Any]:
        """Witte lijst voor statuspagina en ``GET /connector/v1/hub-link`` (§3.5): nooit token of geheim-hash."""
        if self._corrupt:  # de stabiele toestand is onbekend: niet tonen (de witte lijst is een maximum)
            return {
                "state": None,
                "koppelen_bezig": False,
                "hub_url": None,
                "service_id": None,
                "via": None,
                "linked_at": None,
                "first_linked_at": None,
                "keyset_fingerprint": None,
                "service_token_last4": None,
                "last_error": {
                    "code": "koppelstatus_onleesbaar",
                    "message": error_message("koppelstatus_onleesbaar"),
                },
            }
        return {
            "state": self.state,
            "koppelen_bezig": self.pending is not None,
            "hub_url": self.hub_url,
            "service_id": self.service_id,
            "via": self.via,
            "linked_at": self.linked_at,
            "first_linked_at": self.first_linked_at,
            "keyset_fingerprint": self.pins.keyset_fingerprint if self.pins else None,
            "service_token_last4": self.service_token_last4,
            "last_error": (
                {"code": self.last_error.code, "message": error_message(self.last_error.code)}
                if self.last_error
                else None
            ),
        }


class HubLinkStore:
    """Lezen (met verversen bij wijziging) en atomisch schrijven van ``hub-link.json``."""

    def __init__(self, secrets_dir: Path) -> None:
        self.dir = secrets_dir
        self.path = secrets_dir / LINK_FILE
        self.lock_path = secrets_dir / LOCK_FILE
        self._cache: tuple[tuple[int, ...], LinkState] | None = None
        self._mutex = threading.Lock()

    # -------------------------------------------------------------- lezen
    def _load(self) -> tuple[tuple[int, ...] | None, LinkState]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None, LinkState()
        key = (st.st_ino, st.st_mtime_ns, st.st_size, st.st_mode, st.st_uid)
        with self._mutex:
            if self._cache is not None and self._cache[0] == key:
                return key, self._cache[1].model_copy(deep=True)
        try:
            state = self._parse(st)
        except (OSError, ValueError, ValidationError) as exc:
            log.error("hub-link.json is onleesbaar (%s); niets wordt vertrouwd", _reason(exc))
            state = LinkState()
            state._corrupt = True
        with self._mutex:
            self._cache = (key, state)
        return key, state.model_copy(deep=True)

    def _parse(self, st: os.stat_result) -> LinkState:
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise UnreadableError("verkeerde eigenaar of rechten (eigenaar = dit proces, 0600)")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise UnreadableError("geen object")
        missing = [f for f in REQUIRED if data.get(f) in (None, "")]
        if missing:
            raise UnreadableError(f"verplichte velden ontbreken: {', '.join(missing)}")
        version = data["schema_version"]
        if not isinstance(version, str) or version.split(".", 1)[0] != "1":
            raise UnreadableError("andere major van schema_version")
        if data["app_id"] != APP_ID:
            raise UnreadableError("app_id is niet die van deze dienst")
        if data.get("state") == "gekoppeld":
            missing = [f for f in REQUIRED_LINKED if data.get(f) in (None, "")]
            if missing:
                raise UnreadableError(f"gekoppeld zonder {', '.join(missing)}")
        return LinkState.model_validate(data)

    def read(self) -> LinkState:
        """Actuele status (leest opnieuw zodra het bestand veranderd is). Ontbreekt het bestand: ``nooit_gekoppeld``."""
        state = self._load()[1]
        return state

    def exists(self) -> bool:
        return self.path.exists()

    # -------------------------------------------------------------- schrijven
    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, state: LinkState) -> None:
        data = state.model_dump(mode="json")
        payload = (json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n").encode("utf-8")
        tmp = self.dir / f".hub-link.{uuid.uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        dir_fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        with self._mutex:
            self._cache = None

    def update(self, change: Callable[[LinkState], LinkState | None], *, replace_unreadable: bool = False) -> LinkState:
        """Lezen-wijzigen-schrijven onder de vergrendeling. ``change`` geeft de nieuwe status, of ``None`` = niets.

        Een onleesbaar bestand wordt nooit stil overschreven (§3.5): ``change`` wordt dan niet aangeroepen en de
        onleesbare status komt terug. Alleen met ``replace_unreadable=True`` (eerste schrijfactie van een poging met
        een nieuw env-token) wordt het origineel eerst bewaard (:meth:`_preserve_unreadable`) en krijgt ``change`` een
        nieuwe status met ``unreadable_replaced_at``.
        """
        with self.locked():
            with self._mutex:
                self._cache = None  # binnen de vergrendeling altijd vers lezen
            current = self._load()[1]
            base = current
            if current.corrupt:
                if not replace_unreadable:
                    return current
                saved = self._preserve_unreadable()
                base = LinkState(unreadable_replaced_at=_now())
                log.warning("onleesbaar hub-link.json vervangen voor een nieuwe koppelpoging; origineel: %s", saved)
            new = change(base.model_copy(deep=True))
            if new is None:
                if base is not current:  # het origineel is al bewaard: de vervanging toch vastleggen
                    self._write(base)
                    return base
                return current
            new.schema_version = SCHEMA_VERSION
            self._write(new)
            new._corrupt = False  # het bestand is nu weer leesbaar
            return new

    def _preserve_unreadable(self) -> str:
        """Bewaar het onleesbare origineel als ``hub-link.json.onleesbaar-<UTC-tijd>`` (0600). Een harde link houdt de
        exacte bytes, ook als dit proces het bestand niet mag lezen; het origineel blijft tot het atomair vervangen is."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.dir / f"{LINK_FILE}.onleesbaar-{stamp}"
        try:
            os.link(self.path, target)
        except OSError:
            data = self.path.read_bytes()
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        with contextlib.suppress(OSError):
            os.chmod(target, 0o600)
        return target.name

    def write_initial(self, state: LinkState) -> bool:
        """Schrijf alleen als het bestand nog niet bestaat (onder de vergrendeling). Geeft ``True`` als geschreven."""
        with self.locked():
            if self.path.exists():
                return False
            state.schema_version = SCHEMA_VERSION
            self._write(state)
            return True


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _reason(exc: Exception) -> str:
    if isinstance(exc, UnreadableError):
        return str(exc)
    if isinstance(exc, ValidationError):
        return f"schemafout ({exc.error_count()})"
    return type(exc).__name__
