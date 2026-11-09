"""Authenticatie (contract §4): Hub-JWT (EdDSA, JWKS van de Hub, gecachet, fail-closed) en het service-token.

* Gebruikers: kortlevende JWT van de Hub (``iss=hub``, ``aud=appstore-en-fork``, ``exp`` ≤ 120 s na ``iat``),
  geverifieerd met de JWKS van ``{HUB_URL}/api/v1/auth/jwks`` (bij koppelen meegeleverd, daarna periodiek ververst).
  Kan de JWKS niet worden ververst, dan blijven de laatst bekende sleutels hoogstens ``jwks_max_stale_seconds``
  bruikbaar; daarna wordt álles geweigerd (fail-closed, nooit "open access", EX16).
* Dienst-naar-dienst: ``Authorization: Bearer <service-token>`` = het token uit de enrollment; alleen de hash wordt
  vergeleken (constante tijd).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from . import APP_ID
from .config import Settings
from .errors import ApiError
from .manifest.provenance import load_public_key
from .models import HubLink
from .timeutil import utcnow

log = logging.getLogger("appstore.auth")

ROLE_RANK = {"kind": 0, "viewer": 1, "operator": 2, "admin": 3}
MAX_JWT_LIFETIME = 120
CLOCK_SKEW = 30


@dataclass(frozen=True)
class Principal:
    kind: str  # user|service
    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def rank(self) -> int:
        if self.kind == "service":
            return ROLE_RANK["operator"]  # de Hub als dienst: lezen, valideren, preview; geen bronbeheer
        return max((ROLE_RANK[r] for r in self.roles if r in ROLE_RANK), default=-1)

    def label(self) -> str:
        return f"{self.kind}:{self.subject}"


def _unauthorized(reason: str, message: str = "Niet aangemeld of ongeldige aanmelding.") -> ApiError:
    return ApiError(401, "unauthorized", message, {"reason": reason}, headers={"WWW-Authenticate": "Bearer"})


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _keys_from_jwks(jwks: dict[str, Any] | None) -> dict[str, Ed25519PublicKey]:
    out: dict[str, Ed25519PublicKey] = {}
    for jwk in (jwks or {}).get("keys", []) or []:
        if not isinstance(jwk, dict) or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
            continue
        kid, x = jwk.get("kid"), jwk.get("x")
        if not isinstance(kid, str) or not isinstance(x, str):
            continue
        try:
            out[kid] = load_public_key(x)
        except ValueError:
            continue
    return out


class JwksCache:
    def __init__(
        self, settings: Settings, sessions: sessionmaker[Session], http_factory: Callable[[], httpx.Client]
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.http_factory = http_factory
        self._lock = threading.Lock()
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_mono: float | None = None
        self._last_attempt_mono = 0.0
        self._hub_url: str | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._keys, self._fetched_mono, self._hub_url = {}, None, None

    def _load_from_db(self) -> None:
        with self.sessions() as db:
            link = db.get(HubLink, 1)
            if link is None or link.status != "linked" or not link.hub_url:
                self._keys, self._fetched_mono, self._hub_url = {}, None, None
                return
            self._hub_url = link.hub_url
            if self._fetched_mono is None and link.jwks and link.jwks_fetched_at:
                age = (utcnow() - link.jwks_fetched_at).total_seconds()
                self._keys = _keys_from_jwks(link.jwks)
                self._fetched_mono = time.monotonic() - max(0.0, age)

    def _refresh(self) -> None:
        if not self._hub_url:
            return
        now = time.monotonic()
        if now - self._last_attempt_mono < 10:  # niet hameren bij onbekende kid of storing
            return
        self._last_attempt_mono = now
        try:
            with self.http_factory() as client:
                resp = client.get(f"{self._hub_url}/api/v1/auth/jwks")
            resp.raise_for_status()
            jwks = resp.json()
            keys = _keys_from_jwks(jwks)
            if not keys:
                raise ValueError("JWKS bevat geen bruikbare Ed25519-sleutels")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("JWKS verversen mislukt: %s", type(exc).__name__)
            return
        self._keys, self._fetched_mono = keys, time.monotonic()
        with self.sessions() as db:
            link = db.get(HubLink, 1)
            if link is not None and link.status == "linked":
                link.jwks, link.jwks_fetched_at = jwks, utcnow()
                db.commit()

    def get(self, kid: str) -> Ed25519PublicKey:
        with self._lock:
            self._load_from_db()
            if not self._hub_url:
                raise _unauthorized("hub_not_linked", "Deze dienst is (nog) niet gekoppeld met de Hub.")
            age = None if self._fetched_mono is None else time.monotonic() - self._fetched_mono
            if age is None or age > self.settings.jwks_ttl_seconds or kid not in self._keys:
                self._refresh()
                age = None if self._fetched_mono is None else time.monotonic() - self._fetched_mono
            if age is None or age > self.settings.jwks_max_stale_seconds:
                raise _unauthorized("jwks_unavailable", "Sleutels van de Hub zijn niet beschikbaar; toegang geweigerd.")
            key = self._keys.get(kid)
            if key is None:
                raise _unauthorized("unknown_key")
            return key


class Authenticator:
    def __init__(
        self, settings: Settings, sessions: sessionmaker[Session], http_factory: Callable[[], httpx.Client]
    ) -> None:
        self.sessions = sessions
        self.jwks = JwksCache(settings, sessions, http_factory)

    def authenticate(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise _unauthorized("missing_token")
        token = authorization[7:].strip()
        if not token or len(token) > 8192:
            raise _unauthorized("missing_token")
        if token.count(".") == 2:
            return self._user(token)
        return self._service(token)

    def _user(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise _unauthorized("malformed_token") from exc
        if header.get("alg") != "EdDSA" or not isinstance(header.get("kid"), str):
            raise _unauthorized("wrong_algorithm")
        key = self.jwks.get(header["kid"])
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                audience=APP_ID,
                issuer="hub",
                leeway=CLOCK_SKEW,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise _unauthorized("token_expired") from exc
        except jwt.PyJWTError as exc:
            raise _unauthorized("invalid_token") from exc
        if int(claims["exp"]) - int(claims["iat"]) > MAX_JWT_LIFETIME:
            raise _unauthorized("token_lifetime_too_long")
        roles = claims.get("roles", [])
        if not isinstance(roles, list):
            raise _unauthorized("invalid_roles")
        return Principal(
            kind="user", subject=str(claims["sub"])[:120], roles=frozenset(r for r in roles if isinstance(r, str))
        )

    def _service(self, token: str) -> Principal:
        presented = sha256_hex(token)
        with self.sessions() as db:
            link = db.get(HubLink, 1)
            stored = link.token_hash if link is not None and link.status == "linked" else None
            service_id = (link.service_id if link else None) or "hub"
        # altijd een vergelijking doen (gelijke tijd), ook als er geen token is
        if not hmac.compare_digest(presented, stored or "0" * 64) or stored is None:
            raise _unauthorized("invalid_service_token")
        return Principal(kind="service", subject=f"hub:{service_id}")


def require(min_role: str) -> Callable[[Request], Principal]:
    """FastAPI-dependency: aangemeld én minimaal ``min_role`` (kind < viewer < operator < admin)."""
    needed = ROLE_RANK[min_role]

    def dependency(request: Request) -> Principal:
        auth: Authenticator = request.app.state.authenticator
        principal = auth.authenticate(request.headers.get("authorization"))
        if principal.rank < needed:
            raise ApiError(
                403, "forbidden", f"Hiervoor is minimaal de rol '{min_role}' nodig.", {"required_role": min_role}
            )
        request.state.principal = principal
        return principal

    return dependency


def require_user(min_role: str) -> Callable[[Request], Principal]:
    """Alleen een gebruiker (Hub-JWT), geen dienst-token: voor beheer van bronnen."""
    base = require(min_role)

    def dependency(request: Request) -> Principal:
        principal = base(request)
        if principal.kind != "user":
            raise ApiError(403, "forbidden", "Deze actie vereist een gebruiker met een Hub-JWT, geen dienst-token.")
        return principal

    return dependency
