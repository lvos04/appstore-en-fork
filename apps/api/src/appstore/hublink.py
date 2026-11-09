"""Koppelen met de Hub (contract §3): eenmalig enrollment-token uit de omgeving, of een koppelcode via de statuspagina.

``POST {HUB_URL}/api/v1/services/enroll`` met ``{app_id, version, enrollment_token | pairing_code, base_url,
public_key_jwk}`` → ``{service_token, jwks, service_id?, policy_public_key?}``. Het service-token wordt versleuteld
(AES-256-GCM, vault) bewaard; alleen de hash dient voor inkomende verificatie en de laatste 4 tekens voor weergave.
Zonder Hub of bij een fout blijft de status eerlijk "niet gekoppeld"/"mislukt"; er wordt nooit iets aangenomen.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from . import APP_ID, __version__
from .auth import _keys_from_jwks, sha256_hex
from .config import Settings
from .events import audit, emit
from .manifest.provenance import public_jwk
from .models import HubLink
from .secrets_store import Secrets
from .timeutil import utcnow

log = logging.getLogger("appstore.hublink")

TOKEN_AAD = "hub_link/service_token"  # noqa: S105 - AAD-label, geen geheim
ENROLLMENT_TOKEN_WINDOW = timedelta(minutes=60)  # contract §3: token vervalt na 60 min


@dataclass(frozen=True)
class EnrollResult:
    ok: bool
    message_nl: str
    status: str


def get_link(db: Session) -> HubLink:
    link = db.get(HubLink, 1)
    if link is None:
        link = HubLink(id=1, status="not_linked", attempts=0, updated_at=utcnow())
        db.add(link)
        db.flush()
    return link


def service_token(db: Session, secrets: Secrets) -> str | None:
    link = db.get(HubLink, 1)
    if link is None or link.status != "linked" or link.token_nonce is None or link.token_ciphertext is None:
        return None
    return secrets.vault.decrypt(link.token_nonce, link.token_ciphertext, TOKEN_AAD)


def _post_with_retry(client: httpx.Client, url: str, body: dict[str, Any]) -> httpx.Response:
    """Max 3 pogingen bij netwerkfouten/5xx, exponentiële backoff met jitter (contract §5). 4xx: direct terug."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.post(url, json=body)
            if resp.status_code < 500:
                return resp
            last = httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
        except httpx.HTTPError as exc:
            last = exc
        if attempt < 2:
            time.sleep(min(4.0, 0.5 * 2**attempt) + random.uniform(0, 0.25))  # noqa: S311 - jitter, geen crypto
    assert last is not None
    raise last


def enroll(
    db: Session,
    settings: Settings,
    secrets: Secrets,
    http_factory: Callable[[], httpx.Client],
    *,
    hub_url: str,
    credential: str,
    via: str,
    trace_id: str,
) -> EnrollResult:
    """Voer één enrollment uit. ``via`` is ``token`` of ``koppelcode``. Commit niet; de aanroeper doet dat."""
    link = get_link(db)
    now = utcnow()
    link.hub_url = hub_url
    link.attempts = (link.attempts or 0) + 1
    link.last_attempt_at = now
    link.first_attempt_at = link.first_attempt_at or now
    link.updated_at = now
    body: dict[str, Any] = {
        "app_id": APP_ID,
        "version": __version__,
        "base_url": settings.public_url,
        "public_key_jwk": public_jwk(secrets.identity.public_key()),
        "capabilities": ["read"],
        ("enrollment_token" if via == "token" else "pairing_code"): credential,
    }
    try:
        with http_factory() as client:
            resp = _post_with_retry(client, f"{hub_url}/api/v1/services/enroll", body)
    except httpx.HTTPError as exc:
        link.status = "failed"
        link.last_error = f"Hub niet bereikbaar ({type(exc).__name__})."
        log.warning("koppelen mislukt: Hub niet bereikbaar")
        return EnrollResult(
            False, "De Hub is niet bereikbaar. Controleer het Hub-adres en probeer het opnieuw.", link.status
        )
    if resp.status_code in (400, 401, 403, 404, 409, 410, 422):
        link.status = "failed"
        if via == "token":
            link.used_enrollment_token_hash = sha256_hex(credential)  # dit token nooit opnieuw proberen
        link.last_error = f"Hub weigerde de koppeling (HTTP {resp.status_code})."
        return EnrollResult(
            False,
            "De Hub weigerde de koppeling: de code of het token is ongeldig, verlopen of al gebruikt.",
            link.status,
        )
    try:
        data = resp.json()
        token = data["service_token"]
        jwks = data["jwks"]
        if not isinstance(token, str) or len(token) < 20 or not isinstance(jwks, dict) or not _keys_from_jwks(jwks):
            raise ValueError("onvolledig antwoord")
    except (ValueError, KeyError, TypeError):
        link.status = "failed"
        link.last_error = f"Onverwacht antwoord van de Hub (HTTP {resp.status_code})."
        return EnrollResult(False, "De Hub gaf een onverwacht antwoord; koppelen is niet gelukt.", link.status)
    nonce, ciphertext = secrets.vault.encrypt(token, TOKEN_AAD)
    link.status = "linked"
    link.service_id = str(data.get("service_id") or "")[:64] or None
    link.linked_via = via
    link.linked_at = now
    link.token_nonce, link.token_ciphertext = nonce, ciphertext
    link.token_hash, link.token_last4 = sha256_hex(token), token[-4:]
    link.jwks, link.jwks_fetched_at = jwks, now
    policy = data.get("policy_public_key") or data.get("policy_public_jwk")
    link.policy_public_jwk = policy if isinstance(policy, dict) else None
    if via == "token":
        link.used_enrollment_token_hash = sha256_hex(credential)
    link.last_error = None
    audit(
        db,
        None,
        "hub.linked",
        "hub_link",
        "1",
        {"hub_url": hub_url, "via": via, "service_id": link.service_id},
        trace_id,
    )
    emit(db, "service.enrollment.completed", "service", APP_ID, {"via": via, "hub_url": hub_url}, trace_id)
    return EnrollResult(True, "Gekoppeld met de Hub.", link.status)


def auto_enroll(
    db: Session, settings: Settings, secrets: Secrets, http_factory: Callable[[], httpx.Client], trace_id: str
) -> EnrollResult | None:
    """Eenmalig token uit de omgeving gebruiken zolang het zinvol is (nog niet gekoppeld, binnen 60 min, niet geweigerd)."""
    if not settings.hub_url or not settings.enrollment_token:
        return None
    link = get_link(db)
    if link.status in ("linked", "revoked"):
        return None
    if link.used_enrollment_token_hash == sha256_hex(settings.enrollment_token):
        return None  # al gebruikt of door de Hub geweigerd: waardeloos
    if link.first_attempt_at and utcnow() - link.first_attempt_at > ENROLLMENT_TOKEN_WINDOW:
        if link.last_error and "verlopen" not in link.last_error:
            link.last_error = (
                link.last_error or ""
            ) + " Het enrollment-token is inmiddels verlopen; koppel met een koppelcode."
        return None
    return enroll(
        db,
        settings,
        secrets,
        http_factory,
        hub_url=settings.hub_url,
        credential=settings.enrollment_token,
        via="token",
        trace_id=trace_id,
    )


def mark_revoked(db: Session, reason: str) -> None:
    link = get_link(db)
    link.status = "revoked"
    link.last_error = reason
    link.updated_at = utcnow()
