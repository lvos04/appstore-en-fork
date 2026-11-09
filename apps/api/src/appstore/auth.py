"""Inkomende authenticatie (platformcontract 1.2 §4): alleen Hub-JWT's, gecontroleerd tegen de **gepinde** sleutels.

* Gebruikers: JWT met ``token_use: "user"`` (§4.2). De Hub als dienst: dienst-JWT met ``token_use: "hub_service"``,
  ``sub: "hub"``, ``roles: ["service"]`` en de eigen ``service_id`` (§4.3) → principal ``service:hub``.
* Een inkomend ``Bearer svc_…`` (het eigen service-token) wordt **niet** geaccepteerd (``service_token_not_accepted``).
* Vertrouwensset = ``pins.jwt`` uit ``hub-link.json``; een via HTTP opgehaalde JWKS voegt nooit iets toe. Onbekende
  ``kid`` → direct 401 ``unknown_key`` en op de achtergrond een extra poll van de ondertekende sleutelrotaties (§4.5).
* ``jti`` verplicht; mutaties (POST/PUT/PATCH/DELETE) met een al gebruikte ``jti`` → 401 ``token_replayed``, behalve een
  exacte herhaling (zelfde ``jti``, methode, pad en ``Idempotency-Key``), die de idempotency-laag beantwoordt.
  Replaycontrole niet mogelijk → 503 ``replay_check_unavailable`` (fail-closed, §4.6).
* Vaste ``reason``-waarden en controlevolgorde: ``state`` → tokensoort → handtekening/``kid`` → claims →
  ``token_use`` → ``jti`` (§4.7). Nooit "open access" (EX16).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from . import APP_ID
from .errors import ApiError
from .hubkeys import InvalidKeyError, public_key_from_jwk
from .hublink_store import HubLinkStore
from .models import JwtReplay

log = logging.getLogger("appstore.auth")

ROLE_RANK = {"kind": 0, "viewer": 1, "operator": 2, "admin": 3}
USER_ROLES = frozenset(ROLE_RANK)
MAX_JWT_LIFETIME = 120
CLOCK_SKEW = 30
MUTATIONS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ISSUER = "hub"

#: Vaste ``reason``-waarden van §4.7 (Engels, voor machines); de ``message`` is Nederlands.
REASONS = (
    "not_linked",
    "revoked",
    "service_token_not_accepted",
    "unknown_key",
    "token_expired",
    "invalid_token",
    "token_use_invalid",
    "jti_missing",
    "token_replayed",
)
_MESSAGES = {
    "not_linked": "Deze dienst is (nog) niet gekoppeld met de Hub.",
    "revoked": "De Hub heeft de koppeling ingetrokken; opnieuw koppelen gaat via de Hub.",
    "service_token_not_accepted": "Een service-token wordt hier niet geaccepteerd; de Hub meldt zich met een dienst-JWT.",
    "unknown_key": "Het token is ondertekend met een sleutel die deze dienst niet vertrouwt.",
    "token_expired": "Het token is verlopen.",
    "invalid_token": "Niet aangemeld of ongeldige aanmelding.",
    "token_use_invalid": "Het token is niet bedoeld voor dit gebruik.",
    "jti_missing": "Het token heeft geen jti.",
    "token_replayed": "Dit token is al gebruikt voor een andere wijziging; vraag een nieuw token aan.",
}


@dataclass(frozen=True)
class Principal:
    kind: str  # user|service
    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def rank(self) -> int:
        if self.kind == "service":
            # de Hub als dienst (ADR-0008): lezen, valideren, voorbeeld, synchroniseren; nooit admin/bronbeheer
            return ROLE_RANK["operator"]
        if "kind" in self.roles:
            return ROLE_RANK["kind"]  # §4.2: rol 'kind' wint altijd van andere rollen in dezelfde JWT
        return max((ROLE_RANK[r] for r in self.roles if r in ROLE_RANK), default=-1)

    def label(self) -> str:
        return f"{self.kind}:{self.subject}"


def unauthorized(reason: str) -> ApiError:
    assert reason in REASONS, reason
    return ApiError(
        401,
        "unauthorized",
        _MESSAGES[reason],
        {"reason": reason},
        headers={"WWW-Authenticate": "Bearer"},
    )


class Authenticator:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        store: HubLinkStore,
        on_unknown_kid: Callable[[], None] | None = None,
    ) -> None:
        self.sessions = sessions
        self.store = store
        self.on_unknown_kid = on_unknown_kid

    def authenticate(self, request: Request) -> Principal:
        return self.verify(
            request.headers.get("authorization"),
            method=request.method,
            path=request.url.path,
            idempotency_key=request.headers.get("idempotency-key"),
        )

    def verify(
        self, authorization: str | None, *, method: str, path: str, idempotency_key: str | None = None
    ) -> Principal:
        # 1. state
        link = self.store.read()
        if link.state == "ingetrokken":
            raise unauthorized("revoked")
        if not link.linked or link.pins is None:
            raise unauthorized("not_linked")
        # 2. tokensoort
        if not authorization or not authorization[:7].lower() == "bearer ":
            raise unauthorized("invalid_token")
        token = authorization[7:].strip()
        if token.startswith("svc_"):
            raise unauthorized("service_token_not_accepted")
        if not token or len(token) > 8192 or token.count(".") != 2:
            raise unauthorized("invalid_token")
        # 3. handtekening en kid (alleen gepinde sleutels)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise unauthorized("invalid_token") from exc
        if header.get("alg") != "EdDSA":
            raise unauthorized("invalid_token")
        kid = header.get("kid")
        pin = next((p for p in link.pins.jwt if isinstance(kid, str) and p.kid == kid), None)
        if pin is None:
            if self.on_unknown_kid is not None:
                self.on_unknown_kid()  # achtergrond-poll; het verzoek wacht er niet op
            raise unauthorized("unknown_key")
        try:
            key = public_key_from_jwk({"kty": "OKP", "crv": "Ed25519", "x": pin.jwk.x})
        except InvalidKeyError as exc:  # pragma: no cover - pins zijn bij het opslaan al gecontroleerd
            raise unauthorized("unknown_key") from exc
        # 4. claims
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                audience=APP_ID,
                issuer=ISSUER,
                leeway=CLOCK_SKEW,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise unauthorized("token_expired") from exc
        except jwt.PyJWTError as exc:
            raise unauthorized("invalid_token") from exc
        try:
            if int(claims["exp"]) - int(claims["iat"]) > MAX_JWT_LIFETIME:
                raise unauthorized("invalid_token")
        except (TypeError, ValueError) as exc:
            raise unauthorized("invalid_token") from exc
        # 5. token_use
        principal = self._principal(claims, link.service_id)
        # 6. jti (+ replaycache voor mutaties)
        jti = claims.get("jti")
        if not isinstance(jti, str) or not 1 <= len(jti) <= 128:
            raise unauthorized("jti_missing")
        if method.upper() in MUTATIONS:
            self._check_replay(jti, int(claims["exp"]), method.upper(), path, idempotency_key)
        return principal

    @staticmethod
    def _principal(claims: dict[str, Any], own_service_id: str | None) -> Principal:
        use = claims.get("token_use")
        roles = claims.get("roles")
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise unauthorized("token_use_invalid")
        if use == "user":
            if "service" in roles:
                raise unauthorized("token_use_invalid")
            return Principal(kind="user", subject=str(claims["sub"])[:120], roles=frozenset(roles) & USER_ROLES)
        if use == "hub_service":
            if (
                claims.get("sub") != "hub"
                or roles != ["service"]
                or not own_service_id
                or claims.get("service_id") != own_service_id
            ):
                raise unauthorized("token_use_invalid")
            return Principal(kind="service", subject="hub", roles=frozenset({"service"}))
        raise unauthorized("token_use_invalid")

    def _check_replay(self, jti: str, exp: int, method: str, path: str, idem: str | None) -> None:
        expires = datetime.fromtimestamp(exp, tz=UTC) + timedelta(seconds=CLOCK_SKEW)
        key = (idem or "").strip()[:64] or None
        try:
            with self.sessions() as db:
                inserted = db.execute(
                    insert(JwtReplay)
                    .values(
                        iss=ISSUER,
                        jti=jti,
                        expires_at=expires,
                        method=method,
                        path=path[:512],
                        idempotency_key=key,
                        first_seen_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing(index_elements=["iss", "jti"])
                    .returning(JwtReplay.jti)
                ).first()
                if inserted is not None:
                    db.commit()
                    return
                seen = db.execute(
                    select(JwtReplay.method, JwtReplay.path, JwtReplay.idempotency_key).where(
                        JwtReplay.iss == ISSUER, JwtReplay.jti == jti
                    )
                ).first()
                db.rollback()
        except Exception as exc:
            log.error("replaycontrole niet mogelijk (%s); verzoek geweigerd", type(exc).__name__)
            raise ApiError(
                503,
                "replay_check_unavailable",
                "De controle op hergebruikte tokens is nu niet mogelijk; probeer het later opnieuw.",
                headers={"Retry-After": "5"},
            ) from exc
        exact = seen is not None and key is not None and tuple(seen) == (method, path[:512], key)
        if not exact:
            raise unauthorized("token_replayed")


def purge_replay(db: Session) -> int:
    """Verlopen ``jti``'s opruimen (``exp`` + 30 s voorbij)."""
    from sqlalchemy import delete

    result = db.execute(delete(JwtReplay).where(JwtReplay.expires_at < datetime.now(UTC)))
    return int(getattr(result, "rowcount", 0) or 0)


def _authenticate(request: Request) -> Principal:
    auth: Authenticator = request.app.state.authenticator
    principal = auth.authenticate(request)
    request.state.principal = principal
    return principal


def require(min_role: str) -> Callable[[Request], Principal]:
    """FastAPI-dependency: aangemeld én minimaal ``min_role`` (kind < viewer < operator < admin)."""
    needed = ROLE_RANK[min_role]

    def dependency(request: Request) -> Principal:
        principal = _authenticate(request)
        if principal.rank < needed:
            raise ApiError(
                403, "forbidden", f"Hiervoor is minimaal de rol '{min_role}' nodig.", {"required_role": min_role}
            )
        return principal

    return dependency


def require_user(min_role: str) -> Callable[[Request], Principal]:
    """Alleen een gebruiker (gebruikers-JWT), geen dienst-JWT: voor beheer van bronnen."""
    base = require(min_role)

    def dependency(request: Request) -> Principal:
        principal = base(request)
        if principal.kind != "user":
            raise ApiError(403, "forbidden", "Deze actie vereist een gebruiker met een Hub-JWT, geen dienst-JWT.")
        return principal

    return dependency


def require_service_or_admin(request: Request) -> Principal:
    """Ontkoppelen (§3.8, §6): de Hub met een dienst-JWT of een gebruiker met rol ``admin``."""
    principal = _authenticate(request)
    if principal.kind == "service" or principal.rank >= ROLE_RANK["admin"]:
        return principal
    raise ApiError(403, "forbidden", "Ontkoppelen mag alleen de Hub (dienst-JWT) of een beheerder.")
