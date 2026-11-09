"""Authenticatie en rollen: Hub-JWT via JWKS (gecachet, fail-closed) en service-token."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from appstore.models import HubLink
from appstore.timeutil import utcnow
from tests.fakehub import FakeHub
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration
URL = "/catalog/v1/sources"


def test_jwt_refused_when_not_linked(client: TestClient, hub: FakeHub) -> None:
    resp = client.get(URL, headers=hub.auth())
    assert resp.status_code == 401 and resp.json()["details"]["reason"] == "hub_not_linked"


def test_valid_jwt_and_roles(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    assert client.get(URL, headers=hub.auth(roles=["viewer"])).status_code == 200
    kind = client.get(URL, headers=hub.auth(roles=["kind"]))
    assert kind.status_code == 403 and kind.json()["code"] == "forbidden"
    assert client.get(URL, headers=hub.auth(roles=[])).status_code == 403


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"aud": "andere-dienst"}, "invalid_token"),
        ({"iss": "niet-de-hub"}, "invalid_token"),
        ({"ttl": 600}, "token_lifetime_too_long"),
        ({"iat": int(time.time()) - 400, "ttl": 60}, "token_expired"),
        ({"key": Ed25519PrivateKey.generate()}, "unknown_key"),
    ],
)
def test_bad_jwts_refused(client: TestClient, hub: FakeHub, kwargs: dict[str, object], reason: str) -> None:
    pair(client, hub)
    resp = client.get(URL, headers={"Authorization": f"Bearer {hub.jwt(**kwargs)}"})  # type: ignore[arg-type]
    assert resp.status_code == 401
    assert resp.json()["details"]["reason"] == reason


def test_garbage_tokens(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    for value in ("Bearer", "Bearer a.b.c", "Basic abc", "Bearer " + "x" * 9000):
        assert client.get(URL, headers={"Authorization": value}).status_code == 401


def test_jwks_fail_closed_after_max_stale(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    ok = client.get(URL, headers=hub.auth())
    assert ok.status_code == 200
    # Hub-JWKS onbereikbaar en de cache ouder dan het maximum → alles weigeren (EX16: nooit open access)
    hub.jwks_status = 503
    with client.app.state.ctx.sessions() as db:  # type: ignore[attr-defined]
        link = db.get(HubLink, 1)
        assert link is not None
        link.jwks_fetched_at = utcnow() - timedelta(hours=2)
        db.commit()
    client.app.state.authenticator.jwks.invalidate()  # type: ignore[attr-defined]
    client.app.state.authenticator.jwks._last_attempt_mono = 0.0  # type: ignore[attr-defined]
    resp = client.get(URL, headers=hub.auth())
    assert resp.status_code == 401 and resp.json()["details"]["reason"] == "jwks_unavailable"
    # Hub weer bereikbaar → verversen lukt → toegang
    hub.jwks_status = 200
    client.app.state.authenticator.jwks._last_attempt_mono = 0.0  # type: ignore[attr-defined]
    assert client.get(URL, headers=hub.auth()).status_code == 200


def test_key_rotation_refreshes_jwks(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    assert client.get(URL, headers=hub.auth()).status_code == 200
    hub.key = Ed25519PrivateKey.generate()  # Hub roteert zijn JWT-sleutel
    client.app.state.authenticator.jwks._last_attempt_mono = 0.0  # type: ignore[attr-defined]
    assert client.get(URL, headers=hub.auth()).status_code == 200
    assert hub.jwks_calls >= 1


def test_service_token(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    good = {"Authorization": f"Bearer {hub.issued_token}"}
    assert client.get("/connector/v1/discover", headers=good).status_code == 200
    assert (
        client.get("/connector/v1/discover", headers={"Authorization": "Bearer svc_fout_token_1234567890"}).status_code
        == 401
    )
    # dienst-token mag geen bronnen beheren (alleen gebruikers met admin)
    resp = client.post(
        URL,
        headers={**good, "Idempotency-Key": "8a1f0a3e-3c55-4a43-9a4c-1d2a8c1c0001"},
        json={"name": "x-bron", "kind": "http", "url": "https://bron.test/catalogus/index.json"},
    )
    assert resp.status_code == 403
