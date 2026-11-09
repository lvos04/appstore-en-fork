"""Inkomende authenticatie volgens contract 1.2 §4: gepinde sleutels, token_use, jti + replaycache, vaste reasons."""

from __future__ import annotations

import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from tests.fakehub import FakeHub
from tests.integration.conftest import idem, pair

pytestmark = pytest.mark.integration
URL = "/catalog/v1/sources"


def _reason(resp: object) -> str:
    assert resp.status_code == 401, resp.text  # type: ignore[attr-defined]
    body = resp.json()  # type: ignore[attr-defined]
    assert body["code"] == "unauthorized" and set(body) == {"code", "message", "details", "trace_id"}
    return str(body["details"]["reason"])


def test_jwt_refused_when_not_linked(client: TestClient, hub: FakeHub) -> None:
    assert _reason(client.get(URL, headers=hub.auth())) == "not_linked"


def test_valid_user_jwt_and_roles(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    assert client.get(URL, headers=hub.auth(roles=["viewer"])).status_code == 200
    kind = client.get(URL, headers=hub.auth(roles=["kind"]))
    assert kind.status_code == 403 and kind.json()["code"] == "forbidden"
    assert client.get(URL, headers=hub.auth(roles=[])).status_code == 403
    # §4.2: 'kind' wint altijd van andere rollen in dezelfde JWT
    assert client.get(URL, headers=hub.auth(roles=["kind", "admin"])).status_code == 403


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"aud": "andere-dienst"}, "invalid_token"),
        ({"iss": "niet-de-hub"}, "invalid_token"),
        ({"ttl": 600}, "invalid_token"),  # exp − iat > 120 s
        ({"iat": int(time.time()) - 400, "ttl": 60}, "token_expired"),
        ({"key": Ed25519PrivateKey.generate()}, "unknown_key"),
        ({"kid": "onbekende-kid"}, "unknown_key"),
        ({"token_use": None}, "token_use_invalid"),
        ({"token_use": "anders"}, "token_use_invalid"),
        ({"roles": ["service"]}, "token_use_invalid"),  # token_use user met rol service
        ({"jti": None}, "jti_missing"),
        ({"jti": ""}, "jti_missing"),
        ({"jti": None, "token_use": None}, "token_use_invalid"),  # volgorde: token_use vóór jti
        ({"jti": None, "iat": int(time.time()) - 400, "ttl": 60}, "token_expired"),  # claims vóór jti
    ],
)
def test_bad_jwts_refused_with_fixed_reason(
    client: TestClient, hub: FakeHub, kwargs: dict[str, object], reason: str
) -> None:
    pair(client, hub)
    resp = client.get(URL, headers={"Authorization": f"Bearer {hub.jwt(**kwargs)}"})  # type: ignore[arg-type]
    assert _reason(resp) == reason


def test_missing_nbf_is_invalid(client: TestClient, hub: FakeHub) -> None:
    import jwt as pyjwt

    pair(client, hub)
    now = int(time.time())
    claims = {"iss": "hub", "aud": "appstore-en-fork", "sub": "x", "roles": ["admin"], "iat": now, "exp": now + 60}
    claims |= {"token_use": "user", "jti": str(uuid.uuid4())}
    from appstore.manifest.provenance import key_id

    token = pyjwt.encode(claims, hub.key, algorithm="EdDSA", headers={"kid": key_id(hub.key.public_key())})
    assert _reason(client.get(URL, headers={"Authorization": f"Bearer {token}"})) == "invalid_token"


def test_service_token_is_never_accepted(client: TestClient, hub: FakeHub) -> None:
    token = pair(client, hub)
    resp = client.get("/connector/v1/discover", headers={"Authorization": f"Bearer {token}"})
    assert _reason(resp) == "service_token_not_accepted"
    assert _reason(client.get(URL, headers={"Authorization": "Bearer svc_onzin"})) == "service_token_not_accepted"


def test_garbage_tokens(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    for value in ("Bearer", "Bearer a.b.c", "Basic abc", "Bearer " + "x" * 9000, ""):
        assert _reason(client.get(URL, headers={"Authorization": value})) == "invalid_token"


def test_hub_service_jwt(client: TestClient, hub: FakeHub) -> None:
    """§4.3: dienst-JWT → principal service:hub (lezen ja, bronbeheer nooit)."""
    pair(client, hub)
    assert client.get("/connector/v1/discover", headers=hub.service_auth()).status_code == 200
    assert client.get(URL, headers=hub.service_auth()).status_code == 200
    resp = client.post(
        URL,
        headers={**hub.service_auth(), **idem()},
        json={"name": "x-bron", "kind": "http", "url": "https://bron.test/catalogus/index.json"},
    )
    assert resp.status_code == 403  # service is nooit admin
    for kwargs in (
        {"service_id": str(uuid.uuid4())},
        {"sub": "iemand"},
        {"roles": ["service", "admin"]},
        {"roles": ["admin"]},
    ):
        assert _reason(client.get(URL, headers=hub.service_auth(**kwargs))) == "token_use_invalid", kwargs


def test_mutation_replay_is_refused_except_exact_repeat(client: TestClient, hub: FakeHub) -> None:
    """§4.6: zelfde jti bij een andere mutatie → token_replayed; exacte herhaling → idempotency-antwoord."""
    pair(client, hub)
    token = hub.jwt(roles=["admin"])
    auth = {"Authorization": f"Bearer {token}"}
    key = idem()
    body = {"name": "replay-bron", "kind": "http", "url": "https://bron.test/catalogus/index.json"}
    first = client.post(URL, headers={**auth, **key}, json=body)
    assert first.status_code == 201, first.text
    again = client.post(URL, headers={**auth, **key}, json=body)
    assert again.status_code == 201 and again.headers.get("Idempotent-Replayed") == "true"
    assert again.json() == first.json()
    other = client.post(URL, headers={**auth, **idem()}, json={**body, "name": "andere-bron"})
    assert _reason(other) == "token_replayed"
    elsewhere = client.post("/catalog/v1/validate", headers={**auth, **key}, json={"manifest_yaml": "a: 1"})
    assert _reason(elsewhere) == "token_replayed"
    # lezen met dezelfde jti mag binnen de levensduur vaker
    assert client.get(URL, headers=auth).status_code == 200
    assert client.get(URL, headers=auth).status_code == 200


def test_replay_without_idempotency_key_is_refused(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    auth = {"Authorization": f"Bearer {hub.jwt(roles=['viewer'])}"}
    assert client.post("/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"}).status_code == 200
    assert (
        _reason(client.post("/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"})) == "token_replayed"
    )


def test_http_jwks_never_adds_trust(client: TestClient, hub: FakeHub) -> None:
    """§4.5: de Hub wisselt zijn sleutel zonder ondertekende rotatie → onbekende kid, ook al serveert /jwks hem."""
    pair(client, hub)
    assert client.get(URL, headers=hub.auth()).status_code == 200
    hub.key = Ed25519PrivateKey.generate()
    assert _reason(client.get(URL, headers=hub.auth())) == "unknown_key"
    assert ("GET", "/api/v1/auth/jwks") not in hub.requests  # er wordt niets via HTTP vertrouwd


def test_unknown_kid_triggers_background_poll(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    hub_client = client.app.state.ctx.hub  # type: ignore[attr-defined]
    hub_client._last_key_poll = 0.0
    before = hub.key_rotation_calls
    assert _reason(client.get(URL, headers=hub.auth(key=Ed25519PrivateKey.generate()))) == "unknown_key"
    deadline = time.monotonic() + 5
    while hub.key_rotation_calls == before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert hub.key_rotation_calls == before + 1
    # hooguit 1× per 30 s
    assert _reason(client.get(URL, headers=hub.auth(key=Ed25519PrivateKey.generate()))) == "unknown_key"
    time.sleep(0.2)
    assert hub.key_rotation_calls == before + 1


def test_replay_check_unavailable_is_fail_closed(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    authenticator = client.app.state.authenticator  # type: ignore[attr-defined]

    def broken() -> object:
        raise RuntimeError("database weg")

    original = authenticator.sessions
    authenticator.sessions = broken
    try:
        resp = client.post("/catalog/v1/validate", headers=hub.auth(roles=["viewer"]), json={"manifest_yaml": "a: 1"})
        assert resp.status_code == 503 and resp.json()["code"] == "replay_check_unavailable"
        assert client.get(URL, headers=hub.auth()).status_code == 200  # lezen heeft de replaycache niet nodig
    finally:
        authenticator.sessions = original
