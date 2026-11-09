"""Koppelstatus met de Hub in ``/appdata/secrets/hub-link.json`` (platformcontract 1.2 §3.5).

* Eén bestand, 0600, in ``/appdata/secrets`` (0700); eigenaar is de procesgebruiker (568 in de image).
* Schrijven: nieuw bestand in dezelfde map (``O_CREAT|O_EXCL``, 0600) → ``fsync`` → ``rename`` → ``fsync`` van de map,
  onder een exclusieve vergrendeling op ``hub-link.lock`` (API en worker schrijven allebei; lezen-wijzigen-schrijven
  altijd binnen de vergrendeling).
* Lezen: elk proces leest het bestand opnieuw zodra inode, mtime of grootte verandert, zodat ontkoppelen, intrekken of
  rotatie door het ene proces direct voor het andere geldt.
* Niet in de database: een database-herstel brengt geen oude koppeling, pins of ingetrokken token terug.
* Onleesbaar bestand → fail-closed: niets wordt vertrouwd (``not_linked``) en de statuspagina biedt géén
  koppelformulier (het is onbekend of de dienst al eens gekoppeld was, dus geldt hij als "al eens gekoppeld");
  herstel via een nieuw token in het formulier.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
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
}


def error_message(code: str | None) -> str | None:
    if code is None:
        return None
    return ERROR_MESSAGES.get(code, "Koppelen is niet gelukt.")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


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
    schema_version: str = "1.2"
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
    # aanvullend (ADR-0008): rotatieverzoek van de Hub en een geweigerde sleutelrotatie overleven een herstart
    token_rotation_requested_at: str | None = None
    key_rotation_blocked_sequence: int | None = None

    _corrupt: bool = PrivateAttr(default=False)

    @property
    def corrupt(self) -> bool:
        return self._corrupt

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
        self._cache: tuple[tuple[int, int, int], LinkState] | None = None
        self._mutex = threading.Lock()

    # -------------------------------------------------------------- lezen
    def _load(self) -> tuple[tuple[int, int, int] | None, LinkState]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None, LinkState()
        key = (st.st_ino, st.st_mtime_ns, st.st_size)
        with self._mutex:
            if self._cache is not None and self._cache[0] == key:
                return key, self._cache[1].model_copy(deep=True)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("geen object")
            state = LinkState.model_validate(data)
        except (OSError, ValueError, ValidationError) as exc:
            log.error("hub-link.json is onleesbaar (%s); niets wordt vertrouwd", type(exc).__name__)
            # onbekend of de dienst al eens gekoppeld was: voorzichtig aannemen van wel, zodat ook na het overschrijven
            # (bv. door een nieuw env-token) het koppelformulier van de statuspagina dicht blijft (§3.7)
            state = LinkState(first_linked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))
            state._corrupt = True
        with self._mutex:
            self._cache = (key, state)
        return key, state.model_copy(deep=True)

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

    def update(self, change: Callable[[LinkState], LinkState | None]) -> LinkState:
        """Lezen-wijzigen-schrijven onder de vergrendeling. ``change`` geeft de nieuwe status, of ``None`` = niets."""
        with self.locked():
            with self._mutex:
                self._cache = None  # binnen de vergrendeling altijd vers lezen
            current = self._load()[1]
            new = change(current.model_copy(deep=True))
            if new is None:
                return current
            self._write(new)
            new._corrupt = False  # het bestand is nu weer leesbaar
            return new

    def write_initial(self, state: LinkState) -> bool:
        """Schrijf alleen als het bestand nog niet bestaat (onder de vergrendeling). Geeft ``True`` als geschreven."""
        with self.locked():
            if self.path.exists():
                return False
            self._write(state)
            return True
