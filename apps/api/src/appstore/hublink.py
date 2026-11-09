"""Koppelen met de Hub volgens platformcontract 1.2 §3 (enrollment, bevestigen, intrekken, rotatie) en §4.5 (pins).

* ``POST {HUB_URL}/api/v1/services/enroll`` met precies ``{app_id, enrollment_token | pairing_code, version, base_url,
  public_key_jwk}`` (§3.3); het antwoord wordt volledig gecontroleerd, de sleutelset-vingerafdruk zelf berekend en
  vergeleken (met ``HUB_KEY_FINGERPRINT`` of de op de statuspagina ingevoerde vingerafdruk); pas daarna wordt in één
  atomaire stap ``hub-link.json`` geschreven en met ``GET /api/v1/services/self`` bevestigd.
* Alle Hub-aanroepen volgen **nooit** een redirect en negeren proxy-omgevingsvariabelen (de HTTP-factory).
* 401 van de Hub op een aanroep met het service-token → eerst ``GET /api/v1/services/self``; pas bij opnieuw 401
  ``ingetrokken``. Netwerkfouten, 5xx en 429 zijn nooit een intrekking (§3.8).
* ``Hub-Token-Rotation: required`` → ``POST /api/v1/services/self/rotate``, het nieuwe token atomair opslaan en met
  ``services/self`` bevestigen (§3.8). Ook na een onderbroken rotatie, want de Hub herhaalt de header.
* Sleutelrotatie (§4.5): ``GET /api/v1/auth/key-rotations?after=<n>`` zonder authenticatie; alleen statements die met
  een **gepinde** sleutel van hetzelfde doel zijn ondertekend, in volgorde en zonder gaten. Een via HTTP opgehaalde
  JWKS voegt nooit vertrouwen toe.

Koppelen, ontkoppelen, intrekken en rotatie zijn geen ``action_id``'s (§8): ze lopen alleen via deze vaste paden.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import jwt
from sqlalchemy.orm import Session, sessionmaker

from . import APP_ID, __version__
from .config import Settings
from .events import audit, emit
from .hubkeys import (
    ENROLLMENT_TOKEN,
    SERVICE_TOKEN,
    InvalidKeyError,
    enroll_jwk,
    fingerprints_equal,
    jwk_thumbprint,
    keyset_fingerprint,
    public_key_from_jwk,
    sha256_hex,
    validate_hub_jwk,
)
from .hublink_store import HubLinkStore, LastError, LinkState, Pending, Pin, Pins, PublicJwk
from .secrets_store import Secrets
from .timeutil import iso, utcnow

log = logging.getLogger("appstore.hublink")

ENROLL_PATH = "/api/v1/services/enroll"
SELF_PATH = "/api/v1/services/self"
ROTATE_PATH = "/api/v1/services/self/rotate"
KEY_ROTATIONS_PATH = "/api/v1/auth/key-rotations"
ENROLLMENT_WINDOW = timedelta(minutes=60)  # §3.3: automatisch stoppen 60 min na de eerste poging
KOPPELCODE_STALE = timedelta(minutes=2)  # een koppelcodepoging duurt hooguit één verzoek (5 s + 15 s)
BACKOFF_START = 5.0
BACKOFF_MAX = 15 * 60.0
KEY_POLL_INTERVAL = 15 * 60.0  # §4.5: ≤ elke 15 min
KEY_POLL_MIN_GAP = 30.0  # §4.5: extra poll bij onbekende kid hooguit 1× per 30 s
RESPONSE_FIELDS: dict[str, type | tuple[type, ...]] = {
    "schema_version": str,
    "service_id": str,
    "app_id": str,
    "service_token": str,
    "token_type": str,
    "hub_issuer": str,
    "jwks": dict,
    "policy_public_key": dict,
    "keyset_fingerprint": str,
    "jwks_url": str,
    "events_inbox_url": str,
    "key_rotation_url": str,
    "enrollment_replayed": bool,
}


def now_iso() -> str:
    value = iso(utcnow())
    assert value is not None
    return value


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def backoff_delay(attempts: int) -> float:
    """Exponentieel met jitter: start 5 s, hooguit 15 min tussen pogingen (§3.3)."""
    base = min(BACKOFF_MAX, BACKOFF_START * float(2 ** max(0, attempts - 1)))
    jitter = random.uniform(0.8, 1.2)  # noqa: S311 - jitter, geen cryptografie
    return float(min(BACKOFF_MAX, base * jitter))


class InvalidResponseError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class EnrollOutcome:
    """Uitkomst van één enroll-poging. ``kind``: ``ok`` | ``definitive`` | ``transient`` | ``rate_limited``."""

    kind: str
    code: str | None = None
    retry_after: float | None = None

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


def _pins_from(jwks: list[dict[str, str]], policy: list[dict[str, str]], source: str, now: str) -> Pins:
    def pin(jwk: dict[str, str]) -> Pin:
        public = {"kty": "OKP", "crv": "Ed25519", "x": jwk["x"]}
        return Pin(
            kid=jwk["kid"],
            jwk=PublicJwk(x=jwk["x"]),
            thumbprint=jwk_thumbprint(public),
            pinned_at=now,
            source=source,
        )

    return Pins(
        jwt=[pin(k) for k in jwks],
        policy=[pin(k) for k in policy],
        keyset_fingerprint=keyset_fingerprint(jwks, policy),
        rotation_sequence=0,
    )


def pin_jwks(pins: Pins) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    def as_jwk(p: Pin) -> dict[str, str]:
        return {"kty": "OKP", "crv": "Ed25519", "x": p.jwk.x, "kid": p.kid}

    return [as_jwk(p) for p in pins.jwt], [as_jwk(p) for p in pins.policy]


def validate_enroll_response(data: Any, expected_fingerprint: str | None) -> dict[str, Any]:
    """Alle controles van §3.3 vóór opslaan. Gooit :class:`InvalidResponseError` met de ``last_error``-code."""
    if not isinstance(data, dict):
        raise InvalidResponseError("hub_antwoord_ongeldig", "antwoord is geen object")
    for name, typ in RESPONSE_FIELDS.items():
        value = data.get(name)
        if not isinstance(value, typ) or (typ is not bool and isinstance(value, bool)):
            raise InvalidResponseError("hub_antwoord_ongeldig", f"veld {name} ontbreekt of heeft het verkeerde type")
    major = data["schema_version"].split(".", 1)[0]
    if major != "1":
        raise InvalidResponseError(
            "hub_antwoord_ongeldig", f"schema_version {data['schema_version']!r} wordt niet ondersteund"
        )
    if data["app_id"] != APP_ID:
        raise InvalidResponseError("hub_antwoord_ongeldig", "app_id in het antwoord is niet die van deze dienst")
    try:
        uuid.UUID(data["service_id"])
    except ValueError as exc:
        raise InvalidResponseError("hub_antwoord_ongeldig", "service_id is geen UUID") from exc
    if not SERVICE_TOKEN.match(data["service_token"]):
        raise InvalidResponseError("hub_antwoord_ongeldig", "service_token past niet in het patroon svc_ + 43 tekens")
    if data["token_type"] != "Bearer" or data["hub_issuer"] != "hub":  # noqa: S105 - vaste waarde, geen geheim
        raise InvalidResponseError("hub_antwoord_ongeldig", "token_type of hub_issuer klopt niet")
    keys = data["jwks"].get("keys")
    if not isinstance(keys, list) or not keys:
        raise InvalidResponseError("hub_antwoord_ongeldig", "jwks bevat geen sleutels")
    try:
        jwks = [validate_hub_jwk(k) for k in keys]
        policy = validate_hub_jwk(data["policy_public_key"])
    except InvalidKeyError as exc:
        raise InvalidResponseError("hub_antwoord_ongeldig", f"ongeldige sleutel: {exc}") from exc
    if len({k["kid"] for k in jwks}) != len(jwks):
        raise InvalidResponseError("hub_antwoord_ongeldig", "kid komt dubbel voor in jwks")
    computed = keyset_fingerprint(jwks, [policy])
    if not fingerprints_equal(data["keyset_fingerprint"], computed):
        raise InvalidResponseError(
            "hub_vingerafdruk_onjuist", "de door de Hub gemelde vingerafdruk past niet bij zijn sleutels"
        )
    if expected_fingerprint is not None and not fingerprints_equal(expected_fingerprint, computed):
        raise InvalidResponseError("hub_vingerafdruk_onjuist", "de sleutelset-vingerafdruk wijkt af van de verwachte")
    return {**data, "_jwks": jwks, "_policy": policy, "_fingerprint": computed}


class HubLinkClient:
    """Alle uitgaande Hub-interactie van deze dienst (API-proces én worker)."""

    def __init__(
        self,
        settings: Settings,
        store: HubLinkStore,
        secrets: Secrets,
        http_factory: Callable[[], httpx.Client],
        sessions: sessionmaker[Session] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.secrets = secrets
        self.http_factory = http_factory
        self.sessions = sessions
        self._next_env_attempt = 0.0
        self._next_confirm = 0.0
        self._next_rotate = 0.0
        self._next_key_poll = 0.0
        self._last_key_poll = 0.0
        self._poll_lock = threading.Lock()

    # ------------------------------------------------------------------ hulp
    def _record(self, action: str, event_type: str, details: dict[str, Any], trace_id: str) -> None:
        """Audit + outbox-event (best effort: de koppelstatus zelf staat in het bestand, niet in de database)."""
        if self.sessions is None:
            return
        try:
            with self.sessions() as db:
                audit(db, None, action, "hub_link", "1", details, trace_id)
                emit(db, event_type, "service", APP_ID, details, trace_id)
                db.commit()
        except Exception:
            log.exception("audit/event van %s kon niet worden vastgelegd", action)

    def enroll_body(self, credential_field: str, credential: str) -> dict[str, Any]:
        """Het verzoek van §3.3, exact deze velden (onbekende velden → 422 bij de Hub)."""
        return {
            "app_id": APP_ID,
            credential_field: credential,
            "version": __version__,
            "base_url": self.settings.public_url,
            "public_key_jwk": enroll_jwk(self.secrets.identity.public_key()),
        }

    # ------------------------------------------------------------------ enrollment
    def _begin(self, kind: str, secret_digest: str, hub_url: str) -> Pending:
        result: dict[str, Pending] = {}

        def change(st: LinkState) -> LinkState:
            if st.pending is not None and st.pending.kind == kind and st.pending.secret_sha256 == secret_digest:
                st.pending.attempts += 1
                st.pending.hub_url = hub_url
            else:
                st.pending = Pending(
                    kind=kind,
                    secret_sha256=secret_digest,
                    hub_url=hub_url,
                    started_at=now_iso(),
                    attempts=1,
                )
            result["p"] = st.pending
            return st

        self.store.update(change)
        return result["p"]

    def _fail(self, code: str, *, kind: str, secret_digest: str, definitive: bool) -> None:
        """Mislukt (§3.6 stap 4): ``pending`` weg, ``last_error``; bestaande koppeling blijft zoals hij was."""

        def change(st: LinkState) -> LinkState:
            if st.pending is not None and st.pending.secret_sha256 == secret_digest:
                st.pending = None
            st.last_error = LastError(code=code, at=now_iso())
            if definitive and kind == "token":
                st.remember_token(secret_digest)
            return st

        self.store.update(change)

    def _transient(self, code: str) -> None:
        """Tijdelijke storing tijdens een env-tokenpoging: ``pending`` blijft, alleen ``last_error`` bijwerken."""

        def change(st: LinkState) -> LinkState:
            st.last_error = LastError(code=code, at=now_iso())
            return st

        self.store.update(change)

    def attempt(
        self, *, hub_url: str, kind: str, credential: str, expected_fingerprint: str | None, trace_id: str
    ) -> EnrollOutcome:
        """Eén enroll-poging (§3.6 stap 2–4). ``kind``: ``token`` (env) of ``koppelcode`` (statuspagina)."""
        digest = sha256_hex(credential)
        self._begin(kind, digest, hub_url)
        field = "enrollment_token" if kind == "token" else "pairing_code"
        body = self.enroll_body(field, credential)
        koppelcode = kind == "koppelcode"
        try:
            with self.http_factory() as client:
                resp = client.post(f"{hub_url}{ENROLL_PATH}", json=body)
        except httpx.HTTPError as exc:
            log.warning("koppelen: Hub niet bereikbaar (%s)", type(exc).__name__)
            return self._transient_or_fail("hub_onbereikbaar", koppelcode, digest)
        status = resp.status_code
        if status == 200:
            try:
                data = validate_enroll_response(resp.json(), expected_fingerprint)
            except (InvalidResponseError, ValueError) as exc:
                code = exc.code if isinstance(exc, InvalidResponseError) else "hub_antwoord_ongeldig"
                log.warning("koppelen geweigerd door deze dienst: %s (%s)", code, exc)
                self._fail(code, kind=kind, secret_digest=digest, definitive=True)
                return EnrollOutcome("definitive", code)
            self._succeed(data, hub_url=hub_url, kind=kind, digest=digest, trace_id=trace_id)
            self.confirm(trace_id=trace_id)
            return EnrollOutcome("ok")
        if status == 429:  # opnieuw na Retry-After, niet eerder (§3.3)
            self._transient_or_fail("rate_limited", koppelcode, digest)
            return EnrollOutcome("rate_limited", "rate_limited", _retry_after(resp))
        if 300 <= status < 400 or status >= 500:  # 3xx = storing, nooit volgen
            return self._transient_or_fail("hub_onbereikbaar", koppelcode, digest)
        codes = {
            401: "hub_weigerde",
            404: "enroll_endpoint_ontbreekt",
            413: "hub_incompatibel",
            422: "hub_incompatibel",
        }
        last = codes.get(status, "hub_weigerde")  # overige 4xx (400/403/409/410): definitief
        log.warning("koppelen: Hub antwoordde HTTP %s (%s)", status, last)
        self._fail(last, kind=kind, secret_digest=digest, definitive=True)
        return EnrollOutcome("definitive", last)

    def _transient_or_fail(self, code: str, koppelcode: bool, digest: str) -> EnrollOutcome:
        # koppelcode: het geheim staat alleen in dit verzoek, dus geen herhaling op de achtergrond (de gebruiker probeert
        # opnieuw); env-token: pending blijft staan en de worker probeert het met backoff opnieuw
        if koppelcode:
            self._fail(code, kind="koppelcode", secret_digest=digest, definitive=False)
        else:
            self._transient(code)
        return EnrollOutcome("transient", code)

    def _succeed(self, data: dict[str, Any], *, hub_url: str, kind: str, digest: str, trace_id: str) -> None:
        now = now_iso()
        token = str(data["service_token"])

        def change(st: LinkState) -> LinkState:
            new = LinkState(
                state="gekoppeld",
                hub_url=hub_url,
                service_id=str(data["service_id"]),
                via=kind,
                first_linked_at=st.first_linked_at or now,
                linked_at=now,
                confirmed_at=None,
                service_token=token,
                service_token_last4=token[-4:],
                pins=_pins_from(data["_jwks"], [data["_policy"]], "enroll", now),
                previous_pins=st.pins or st.previous_pins,
                pending=None,
                used_enrollment_token_sha256=list(st.used_enrollment_token_sha256),
                last_error=None,
            )
            if kind == "token":
                new.remember_token(digest)
            return new

        self.store.update(change)
        log.info("gekoppeld met de Hub via %s (vingerafdruk %s)", kind, data["_fingerprint"])
        self._record(
            "hub.linked",
            "service.enrollment.completed",
            {
                "hub_url": hub_url,
                "via": kind,
                "service_id": str(data["service_id"]),
                "keyset_fingerprint": data["_fingerprint"],
                "enrollment_replayed": bool(data["enrollment_replayed"]),
            },
            trace_id,
        )

    def pair_with_code(self, *, hub_url: str, pairing_code: str, fingerprint: str, trace_id: str) -> EnrollOutcome:
        """Statuspagina (§3.7): één poging met koppelcode + verplichte vingerafdruk."""
        return self.attempt(
            hub_url=hub_url,
            kind="koppelcode",
            credential=pairing_code,
            expected_fingerprint=fingerprint,
            trace_id=trace_id,
        )

    def env_tick(self, trace_id: str, *, now: float | None = None) -> EnrollOutcome | None:
        """Worker: env-token gebruiken zolang het zinvol is (§3.2, §3.6, §3.8). Geeft ``None`` als er niets gebeurde."""
        mono = time.monotonic() if now is None else now
        token = self.settings.enrollment_token
        hub_url = self.settings.hub_url
        st = self.store.read()
        if not token or not hub_url:
            if st.pending is not None and st.pending.kind == "token":
                self._interrupted(st.pending.secret_sha256)
            return None
        digest = sha256_hex(token)
        if digest in st.used_enrollment_token_sha256:
            return None
        if st.pending is not None and st.pending.kind == "koppelcode":
            return None  # een koppelpoging via de statuspagina loopt; nooit twee enrollments tegelijk
        if st.pending is not None and st.pending.kind == "token" and st.pending.secret_sha256 != digest:
            self._interrupted(st.pending.secret_sha256)  # ander env-token: oude poging is onderbroken
            st = self.store.read()
        if not ENROLLMENT_TOKEN.match(token):
            log.warning("HUB_ENROLLMENT_TOKEN heeft niet het formaat enr_ + 43 tekens; niet gebruikt")
            self._fail("token_ongeldig", kind="token", secret_digest=digest, definitive=True)
            return EnrollOutcome("definitive", "token_ongeldig")
        if st.pending is not None and st.pending.kind == "token":
            started = parse_iso(st.pending.started_at)
            if started is not None and utcnow() - started > ENROLLMENT_WINDOW:
                log.warning("koppelen met het env-token is na 60 min niet gelukt; gestopt")
                self._fail("token_verlopen_na_60_min", kind="token", secret_digest=digest, definitive=True)
                return EnrollOutcome("definitive", "token_verlopen_na_60_min")
        if mono < self._next_env_attempt:
            return None
        outcome = self.attempt(
            hub_url=hub_url,
            kind="token",
            credential=token,
            expected_fingerprint=self.settings.hub_key_fingerprint,
            trace_id=trace_id,
        )
        if outcome.kind == "rate_limited":
            self._next_env_attempt = mono + (outcome.retry_after or 60.0)
        elif outcome.kind == "transient":
            pending = self.store.read().pending
            self._next_env_attempt = mono + backoff_delay(pending.attempts if pending else 1)
        return outcome

    def _interrupted(self, digest: str) -> None:
        def change(st: LinkState) -> LinkState | None:
            if st.pending is None or st.pending.secret_sha256 != digest:
                return None
            st.pending = None
            st.last_error = LastError(code="koppelen_onderbroken", at=now_iso())
            return st

        self.store.update(change)

    def recover_after_restart(self, *, process: str) -> None:
        """§3.6 stap 5. ``process``: ``api`` (koppelcodes horen bij het API-proces) of ``worker`` (env-token)."""

        def change(st: LinkState) -> LinkState | None:
            p = st.pending
            if p is None:
                return None
            if p.kind == "koppelcode" and process == "api":
                pass  # het geheim was alleen in het vorige API-proces bekend
            elif p.kind == "koppelcode":
                started = parse_iso(p.started_at)
                if started is not None and utcnow() - started < KOPPELCODE_STALE:
                    return None  # mogelijk nog bezig in het API-proces
            elif p.kind == "token":
                token = self.settings.enrollment_token
                if process != "worker" or (token and sha256_hex(token) == p.secret_sha256):
                    return None  # zelfde env-token: de worker herhaalt de poging (toegestane herhaling)
            st.pending = None
            st.last_error = LastError(code="koppelen_onderbroken", at=now_iso())
            return st

        self.store.update(change)

    # ------------------------------------------------------------------ dienst → Hub
    def _bearer(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _note_rotation(self, resp: httpx.Response) -> None:
        if resp.headers.get("hub-token-rotation", "").strip().lower() == "required":

            def change(st: LinkState) -> LinkState | None:
                if not st.linked or st.token_rotation_requested_at:
                    return None
                st.token_rotation_requested_at = now_iso()
                return st

            self.store.update(change)

    def confirm(self, *, trace_id: str) -> bool | None:
        """``GET /api/v1/services/self`` met het huidige token (§3.6 stap 3). ``True`` bevestigd, ``False`` ingetrokken,
        ``None`` onbekend (storing of oude Hub)."""
        st = self.store.read()
        if not st.linked or st.hub_url is None or st.service_token is None:
            return None
        token = st.service_token
        try:
            with self.http_factory() as client:
                resp = client.get(f"{st.hub_url}{SELF_PATH}", headers=self._bearer(token))
        except httpx.HTTPError:
            return None
        self._note_rotation(resp)
        if resp.status_code == 401:
            self.mark_revoked(trace_id=trace_id, token=token)
            return False
        if resp.status_code != 200:
            if resp.status_code == 404:
                log.warning("de Hub kent GET /api/v1/services/self niet (oude Hub-versie); koppeling onbevestigd")
            return None
        try:
            data = resp.json()
            ok = isinstance(data, dict) and data.get("service_id") == st.service_id
        except ValueError:
            ok = False
        if not ok:
            log.warning("services/self gaf een onverwacht antwoord; koppeling blijft onbevestigd")
            return None

        def change(cur: LinkState) -> LinkState | None:
            if cur.service_token != token:
                return None  # intussen vervangen
            cur.confirmed_at = now_iso()
            return cur

        self.store.update(change)
        if (
            isinstance(data, dict)
            and data.get("keyset_fingerprint")
            and st.pins
            and not fingerprints_equal(str(data["keyset_fingerprint"]), st.pins.keyset_fingerprint)
        ):
            log.warning("de Hub meldt een andere sleutelset-vingerafdruk dan de gepinde; er wordt niets gewijzigd")
        return True

    def call(self, method: str, path: str, *, json_body: Any = None, trace_id: str) -> httpx.Response | None:
        """Geauthenticeerde aanroep naar de Hub met het service-token (§4.4). ``None`` als niet gekoppeld."""
        st = self.store.read()
        if not st.linked or st.hub_url is None or st.service_token is None:
            return None
        token = st.service_token
        with self.http_factory() as client:
            resp = client.request(method, f"{st.hub_url}{path}", json=json_body, headers=self._bearer(token))
        self._note_rotation(resp)
        if resp.status_code == 401:
            self.confirm(trace_id=trace_id)  # §3.8: pas bij 401 op services/self is het een intrekking
        return resp

    def mark_revoked(self, *, trace_id: str, token: str | None = None) -> None:
        def change(st: LinkState) -> LinkState | None:
            if st.state != "gekoppeld" or (token is not None and st.service_token != token):
                return None
            st.state = "ingetrokken"
            st.service_token = None
            st.previous_pins = st.pins or st.previous_pins
            st.pins = None
            st.confirmed_at = None
            st.token_rotation_requested_at = None
            st.last_error = LastError(code="ingetrokken_door_hub", at=now_iso())
            return st

        before = self.store.read().state
        after = self.store.update(change)
        if before == "gekoppeld" and after.state == "ingetrokken":
            log.warning("de Hub heeft de koppeling ingetrokken; Hub-JWT's worden geweigerd")
            self._record("hub.revoked", "service.hub_link.revoked", {"hub_url": after.hub_url}, trace_id)

    def unlink(self, *, trace_id: str, actor: str) -> LinkState:
        """``POST /connector/v1/hub-link/unlink`` (§3.8): ontkoppeld, token gewist, pins alleen nog voor audit."""

        def change(st: LinkState) -> LinkState | None:
            if st.state != "gekoppeld":
                return None
            st.state = "ontkoppeld"
            st.service_token = None
            st.service_token_last4 = None
            st.previous_pins = st.pins or st.previous_pins
            st.pins = None
            st.confirmed_at = None
            st.token_rotation_requested_at = None
            st.last_error = None
            return st

        before = self.store.read().state
        result = self.store.update(change)
        if before == "gekoppeld" and result.state == "ontkoppeld":
            log.info("ontkoppeld van de Hub door %s", actor)
            self._record(
                "hub.unlinked", "service.hub_link.unlinked", {"hub_url": result.hub_url, "by": actor}, trace_id
            )
        return result

    def rotate_token(self, *, trace_id: str) -> bool | None:
        """§3.8: ``POST /api/v1/services/self/rotate`` → nieuw token atomair opslaan → bevestigen met ``services/self``."""
        st = self.store.read()
        if not st.linked or st.hub_url is None or st.service_token is None:
            return None
        old = st.service_token
        try:
            with self.http_factory() as client:
                resp = client.post(f"{st.hub_url}{ROTATE_PATH}", headers=self._bearer(old))
        except httpx.HTTPError:
            return None
        if resp.status_code == 401:
            self.confirm(trace_id=trace_id)
            return False
        if resp.status_code != 200:
            log.warning("token roteren: Hub antwoordde HTTP %s", resp.status_code)
            return None
        try:
            data = resp.json()
            new = data["service_token"]
            if not isinstance(new, str) or not SERVICE_TOKEN.match(new) or data.get("service_id") != st.service_id:
                raise ValueError("ongeldig rotatie-antwoord")
        except (ValueError, KeyError, TypeError):
            log.warning("token roteren: onverwacht antwoord van de Hub; oud token blijft in gebruik")
            return None

        def change(cur: LinkState) -> LinkState | None:
            if cur.service_token != old:
                return None
            cur.service_token = new
            cur.service_token_last4 = new[-4:]
            cur.token_rotation_requested_at = None
            cur.confirmed_at = None
            return cur

        self.store.update(change)
        self._record(
            "hub.token_rotated",
            "service.token.rotated",
            {"service_token_last4": new[-4:], "oude_tokens_geldig_tot": data.get("oude_tokens_geldig_tot")},
            trace_id,
        )
        return self.confirm(trace_id=trace_id) is not False

    # ------------------------------------------------------------------ sleutelrotatie (§4.5)
    def poll_key_rotations(self, *, trace_id: str) -> int | None:
        """Haal ondertekende rotaties op en pas ze in volgorde toe. Geeft het aantal toegepaste statements."""
        with self._poll_lock:
            self._last_key_poll = time.monotonic()
            st = self.store.read()
            if not st.linked or st.pins is None or st.hub_url is None:
                return None
            after = st.pins.rotation_sequence
            try:
                with self.http_factory() as client:
                    resp = client.get(f"{st.hub_url}{KEY_ROTATIONS_PATH}", params={"after": after})
            except httpx.HTTPError:
                return None
            if resp.status_code == 404:
                return 0  # Hub zonder sleutelrotatie (contract 1.2: nog niet gebouwd)
            if resp.status_code != 200:
                return None
            try:
                items = resp.json()["items"]
                if not isinstance(items, list):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                log.warning("sleutelrotaties: onverwacht antwoord van de Hub")
                return None
            return self._apply_rotations(items, trace_id)

    def _apply_rotations(self, items: list[Any], trace_id: str) -> int:
        applied = 0
        for item in sorted((i for i in items if isinstance(i, dict)), key=lambda i: int(i.get("sequence") or 0)):
            st = self.store.read()
            if not st.linked or st.pins is None:
                break
            expected = st.pins.rotation_sequence + 1
            try:
                new_pins = verify_rotation(item, st.pins, expected)
            except InvalidResponseError as exc:
                if st.key_rotation_blocked_sequence != expected:
                    log.error(
                        "sleutelrotatie %s geweigerd: %s; verdere statements worden niet toegepast", expected, exc
                    )

                    def block(cur: LinkState, seq: int = expected) -> LinkState:
                        cur.key_rotation_blocked_sequence = seq
                        return cur

                    self.store.update(block)
                    self._record(
                        "hub.key_rotation_rejected",
                        "service.key_rotation.rejected",
                        {"sequence": expected, "reason": str(exc)},
                        trace_id,
                    )
                break

            def change(cur: LinkState, pins: Pins = new_pins, seq: int = expected) -> LinkState | None:
                if not cur.linked or cur.pins is None or cur.pins.rotation_sequence != seq - 1:
                    return None
                cur.pins = pins
                cur.key_rotation_blocked_sequence = None
                return cur

            self.store.update(change)
            applied += 1
            self._record(
                "hub.key_rotation_applied",
                "service.key_rotation.applied",
                {"sequence": expected, "keyset_fingerprint": new_pins.keyset_fingerprint},
                trace_id,
            )
        return applied

    def request_key_poll(self) -> None:
        """Onbekende ``kid`` in een JWT: op de achtergrond een extra poll (hooguit 1× per 30 s; §4.5)."""
        if time.monotonic() - self._last_key_poll < KEY_POLL_MIN_GAP:
            return
        self._last_key_poll = time.monotonic()
        threading.Thread(
            target=self._safe_poll, name="hub-key-poll", daemon=True, kwargs={"trace_id": str(uuid.uuid4())}
        ).start()

    def _safe_poll(self, trace_id: str) -> None:
        try:
            self.poll_key_rotations(trace_id=trace_id)
        except Exception:
            log.exception("sleutelrotaties ophalen mislukt")

    # ------------------------------------------------------------------ worker-ronde
    def maintenance_tick(self, trace_id: str) -> None:
        """Bevestigen (ook na herstart), rotatie op verzoek van de Hub, sleutelrotaties pollen."""
        mono = time.monotonic()
        st = self.store.read()
        if not st.linked:
            return
        if st.token_rotation_requested_at and mono >= self._next_rotate:
            if self.rotate_token(trace_id=trace_id) is None:
                self._next_rotate = mono + 60.0
            st = self.store.read()
        if (
            st.linked
            and st.confirmed_at is None
            and mono >= self._next_confirm
            and self.confirm(trace_id=trace_id) is None
        ):
            self._next_confirm = mono + 60.0
        if mono >= self._next_key_poll:
            self._next_key_poll = mono + KEY_POLL_INTERVAL
            self.poll_key_rotations(trace_id=trace_id)


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(1.0, float(resp.headers.get("retry-after", "60")))
    except ValueError:
        return 60.0


ROTATION_TYP = "hub-key-rotation+jws"


def verify_rotation(item: dict[str, Any], pins: Pins, expected_sequence: int) -> Pins:
    """Controleer één rotatie-statement (§4.5) tegen de **gepinde** sleutels en geef de nieuwe pins."""
    if item.get("sequence") != expected_sequence:
        raise InvalidResponseError(
            "sleutelrotatie_ongeldig", f"volgnummer {item.get('sequence')!r} ≠ {expected_sequence}"
        )
    statement = item.get("statement")
    if not isinstance(statement, str) or statement.count(".") != 2:
        raise InvalidResponseError("sleutelrotatie_ongeldig", "statement is geen JWS")
    try:
        header = jwt.get_unverified_header(statement)
    except jwt.PyJWTError as exc:
        raise InvalidResponseError("sleutelrotatie_ongeldig", "JWS-header onleesbaar") from exc
    if header.get("alg") != "EdDSA" or header.get("typ") != ROTATION_TYP or not isinstance(header.get("kid"), str):
        raise InvalidResponseError(
            "sleutelrotatie_ongeldig", "header moet alg EdDSA, typ hub-key-rotation+jws en kid hebben"
        )
    signer = None
    for purpose, group in (("jwt", pins.jwt), ("policy", pins.policy)):
        for pin in group:
            if pin.kid == header["kid"]:
                signer = (purpose, pin)
    if signer is None:
        raise InvalidResponseError("sleutelrotatie_ongeldig", "ondertekend met een sleutel die niet gepind is")
    purpose, pin = signer
    try:
        key = public_key_from_jwk({"kty": "OKP", "crv": "Ed25519", "x": pin.jwk.x})
        decoded = jwt.api_jws.decode_complete(statement, key, algorithms=["EdDSA"])
        payload = json.loads(decoded["payload"])
    except (jwt.PyJWTError, ValueError, InvalidKeyError) as exc:
        raise InvalidResponseError("sleutelrotatie_ongeldig", "handtekening of inhoud klopt niet") from exc
    if not isinstance(payload, dict) or payload.get("type") != "hub_key_rotation" or payload.get("iss") != "hub":
        raise InvalidResponseError("sleutelrotatie_ongeldig", "type of iss klopt niet")
    if payload.get("sequence") != expected_sequence or not isinstance(payload.get("issued_at"), str):
        raise InvalidResponseError("sleutelrotatie_ongeldig", "volgnummer of issued_at in de inhoud klopt niet")
    if payload.get("purpose") != purpose:
        raise InvalidResponseError(
            "sleutelrotatie_ongeldig", "één statement wijzigt alleen het doel van zijn eigen sleutel"
        )
    add, retire = payload.get("add"), payload.get("retire")
    if not isinstance(add, list) or not isinstance(retire, list) or not all(isinstance(k, str) for k in retire):
        raise InvalidResponseError("sleutelrotatie_ongeldig", "add/retire hebben het verkeerde type")
    try:
        added = [validate_hub_jwk(k) for k in add]
    except InvalidKeyError as exc:
        raise InvalidResponseError("sleutelrotatie_ongeldig", f"nieuwe sleutel ongeldig: {exc}") from exc
    current = list(pins.jwt if purpose == "jwt" else pins.policy)
    known = {p.kid: p for p in current}
    if any(kid not in known for kid in retire):
        raise InvalidResponseError("sleutelrotatie_ongeldig", "intrekken van een onbekende sleutel")
    now = now_iso()
    kept = [p for p in current if p.kid not in set(retire)]
    for jwk_ in added:
        existing = next((p for p in kept if p.kid == jwk_["kid"]), None)
        if existing is not None and existing.jwk.x != jwk_["x"]:
            raise InvalidResponseError("sleutelrotatie_ongeldig", "bestaande kid met een andere sleutel")
        if existing is None:
            kept.append(
                Pin(
                    kid=jwk_["kid"],
                    jwk=PublicJwk(x=jwk_["x"]),
                    thumbprint=jwk_thumbprint(jwk_),
                    pinned_at=now,
                    source="rotation",
                )
            )
    if not kept:
        raise InvalidResponseError(
            "sleutelrotatie_ongeldig", "de laatste sleutel van een doel mag niet worden ingetrokken"
        )
    jwt_pins = kept if purpose == "jwt" else list(pins.jwt)
    policy_pins = kept if purpose == "policy" else list(pins.policy)
    new = Pins(jwt=jwt_pins, policy=policy_pins, keyset_fingerprint="", rotation_sequence=expected_sequence)
    jwks, policy = pin_jwks(new)
    new.keyset_fingerprint = keyset_fingerprint(jwks, policy)
    return new


# ---------------------------------------------------------------------------------------------- overzetten (1.1 → 1.2)
def _legacy_state(row: Any, secrets: Secrets) -> LinkState:
    """Koppeling uit de tabel ``hub_link`` (versie ≤ 0.1.0 vóór contract 1.2) als bestandsstatus (§3.5)."""
    from .secrets_store import LEGACY_TOKEN_AAD

    now = now_iso()
    linked_at = iso(row.linked_at)
    st = LinkState(
        hub_url=row.hub_url,
        service_id=row.service_id,
        via=row.linked_via if row.linked_via in ("token", "koppelcode") else None,
        first_linked_at=linked_at,
        linked_at=linked_at,
    )
    if row.used_enrollment_token_hash:
        st.remember_token(row.used_enrollment_token_hash)
    if row.status == "revoked":
        st.state = "ingetrokken"
        st.first_linked_at = st.first_linked_at or iso(row.updated_at)
        st.last_error = LastError(code="ingetrokken_door_hub", at=now)
        return st
    if row.status != "linked":
        st.state = "ontkoppeld" if linked_at else "nooit_gekoppeld"
        return st
    try:
        token = secrets.vault.decrypt(row.token_nonce, row.token_ciphertext, LEGACY_TOKEN_AAD)
        jwks = [validate_hub_jwk(k) for k in (row.jwks or {}).get("keys", [])]
        policy = validate_hub_jwk(row.policy_public_jwk)
        if not jwks or not SERVICE_TOKEN.match(token):
            raise ValueError("onvolledig")
        pins = _pins_from(jwks, [policy], "overgezet", now)
    except Exception as exc:  # ontbrekende policy-sleutel, ongeldige JWK, onleesbaar token, ...
        log.warning("oude koppeling niet volledig over te zetten (%s); opnieuw koppelen via de Hub", type(exc).__name__)
        st.state = "ontkoppeld"
        st.last_error = LastError(code="overzetten_onvolledig", at=now)
        return st
    st.state = "gekoppeld"
    st.service_token, st.service_token_last4 = token, token[-4:]
    st.pins = pins
    return st  # confirmed_at leeg: de worker bevestigt met services/self (401 → ingetrokken)


def ensure_link_file(store: HubLinkStore, sessions: sessionmaker[Session], secrets: Secrets) -> str:
    """Eenmalige overzetting (§3.5) en daarna de oude rij verwijderen. Idempotent; geeft wat er gebeurde.

    Alleen als ``hub-link.json`` nog **niet** bestaat wordt de oude rij overgezet (of een lege status geschreven);
    bestaat het bestand al, dan wint het bestand en wordt een (bv. door een database-herstel teruggekomen) oude rij
    alleen verwijderd — zo brengt een database-herstel nooit een oude koppeling terug. Veilig als API en worker het
    tegelijk doen (schrijven onder de bestandsvergrendeling, verwijderen met één ``DELETE``).
    """
    from sqlalchemy import delete

    from .models import HubLink

    with sessions() as db:
        row = db.get(HubLink, 1)
        existed = store.exists()
        written = False
        state: LinkState | None = None
        if not existed:
            state = _legacy_state(row, secrets) if row is not None else LinkState()
            written = store.write_initial(state)
        if row is not None:
            db.execute(delete(HubLink))
            db.commit()
    if row is not None and written and state is not None:
        log.info("koppeling uit de database overgezet naar hub-link.json (state %s)", state.state)
        return "overgezet"
    if row is not None:
        return "oude rij verwijderd"
    return "nieuw" if written else "bestond"
