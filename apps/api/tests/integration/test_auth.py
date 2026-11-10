"""Inkomende authenticatie volgens contract 1.3 §4: gepinde sleutels, token_use, jti + replaycache, vaste reasons,
``aud`` als precies één string en authenticatie vóór rol, ``Idempotency-Key`` en bodyvalidatie (RV9)."""

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
        ({"aud": ["appstore-en-fork"]}, "invalid_token"),  # 1.3 §4.2: een lijst, ook met de eigen app_id
        ({"aud": ["appstore-en-fork", "home-control-hub"]}, "invalid_token"),
        ({"no_kid": True}, "unknown_key"),  # 1.3 §4.7: JWS-header zonder kid
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
    for value in ("Bearer", "Bearer ", "Bearer a.b.c", "Basic abc", "Bearer " + "x" * 9000, ""):
        assert _reason(client.get(URL, headers={"Authorization": value})) == "invalid_token"
    assert _reason(client.get(URL)) == "invalid_token"  # 1.3 §4.7: geen Authorization-header
    # een gepinde kid met een foute handtekening: invalid_token (geen unknown_key)
    good = hub.jwt()
    head, body, sig = good.split(".")
    forged = ".".join((head, body, sig[:-4] + ("AAAA" if not sig.endswith("AAAA") else "BBBB")))
    assert _reason(client.get(URL, headers={"Authorization": f"Bearer {forged}"})) == "invalid_token"


@pytest.mark.parametrize("auth", [None, "Bearer onzin", "Basic abc"])
def test_authentication_comes_before_body_role_and_idempotency_key(
    client: TestClient, hub: FakeHub, auth: str | None
) -> None:
    """1.3 §4.7 (RV9): zonder geldige aanmelding altijd 401 — ook bij onleesbare JSON, een ongeldige body of een
    ontbrekende Idempotency-Key; pas daarna rol (403), Idempotency-Key (400) en schemavalidatie (422)."""
    pair(client, hub)
    headers = {"Content-Type": "application/json", **({"Authorization": auth} if auth else {})}
    for content in (b"{kapot", b'{"name": 3}', b"[]"):
        assert _reason(client.post(URL, content=content, headers=headers)) == "invalid_token"
    assert _reason(client.post(URL, content=b"{kapot", headers={**headers, **idem()})) == "invalid_token"
    # aangemeld: nu pas de body (422 validation_error met details.fouten), rol en Idempotency-Key
    ok = {"Authorization": f"Bearer {hub.jwt(roles=['admin'])}", "Content-Type": "application/json"}
    bad = client.post(URL, content=b"{kapot", headers={**ok, **idem()})
    assert bad.status_code == 422 and bad.json()["code"] == "validation_error" and bad.json()["details"]["fouten"]
    viewer = {"Authorization": f"Bearer {hub.jwt(roles=['viewer'])}", "Content-Type": "application/json"}
    assert client.post(URL, content=b"{kapot", headers=viewer).status_code == 403
    admin = {"Authorization": f"Bearer {hub.jwt(roles=['admin'])}"}
    body = {"name": "x-bron", "kind": "http", "url": "https://bron.test/catalogus/index.json"}
    missing = client.post(URL, json=body, headers=admin)
    assert missing.status_code == 400 and missing.json()["code"] == "idempotency_key_required"


def test_every_mutation_consumes_the_jti_even_without_idempotency_key(client: TestClient, hub: FakeHub) -> None:
    """1.3 §4.6: elke POST verbruikt de jti, ook zonder Idempotency-Key en ook als de route niets wijzigt."""
    pair(client, hub)
    auth = {"Authorization": f"Bearer {hub.jwt(roles=['admin'])}"}
    first = client.post("/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"})
    assert first.status_code == 200, first.text
    again = client.post("/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"})
    assert _reason(again) == "token_replayed"


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
    assert again.status_code == 201 and again.headers.get("Idempotent-Replay") == "true"
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


def test_swapped_key_under_a_pinned_kid_is_refused(client: TestClient, hub: FakeHub) -> None:
    """Gewisselde Hub-sleutel: een JWT met de **gepinde** ``kid`` maar ondertekend met een andere sleutel → 401;
    de vergelijking gebeurt op de sleutel zelf (``x``), niet alleen op ``kid`` (§4.5)."""
    pair(client, hub)
    pinned_kid = client.app.state.ctx.store.read().pins.jwt[0].kid  # type: ignore[attr-defined]
    forged = hub.jwt(key=Ed25519PrivateKey.generate(), kid=pinned_kid, roles=["admin"])
    assert _reason(client.get(URL, headers={"Authorization": f"Bearer {forged}"})) == "invalid_token"
    # ook een policy-sleutel is geen JWT-sleutel: ondertekend met de gepinde policy-sleutel → geweigerd
    by_policy = hub.jwt(key=hub.policy_key, roles=["admin"])
    assert _reason(client.get(URL, headers={"Authorization": f"Bearer {by_policy}"})) == "unknown_key"
    assert client.get(URL, headers=hub.auth(roles=["viewer"])).status_code == 200  # de echte sleutel werkt


def test_jwt_without_kid_is_unknown_key(client: TestClient, hub: FakeHub) -> None:
    import jwt as pyjwt

    pair(client, hub)
    now = int(time.time())
    claims = {"iss": "hub", "aud": "appstore-en-fork", "sub": "x", "roles": ["admin"], "iat": now, "nbf": now}
    claims |= {"exp": now + 60, "token_use": "user", "jti": str(uuid.uuid4())}
    token = pyjwt.encode(claims, hub.key, algorithm="EdDSA")  # geen kid in de header
    assert _reason(client.get(URL, headers={"Authorization": f"Bearer {token}"})) == "unknown_key"


@pytest.mark.parametrize("field", ["iat", "nbf"])
def test_jwt_from_the_future_is_refused(client: TestClient, hub: FakeHub, field: str) -> None:
    """``iat``/``nbf`` verder dan de klokspeling (30 s) in de toekomst → ``invalid_token``."""
    pair(client, hub)
    future = int(time.time()) + 300
    extra = {"nbf": future} if field == "nbf" else {"iat": future, "nbf": future - 300, "exp": future + 60}
    token = hub.jwt(roles=["admin"], extra=extra)
    assert _reason(client.get(URL, headers={"Authorization": f"Bearer {token}"})) == "invalid_token"


def test_replay_cache_is_shared_between_processes(client: TestClient, hub: FakeHub, settings: object) -> None:
    """§4.6: de replaycache is persistent en gedeeld (tabel): een tweede API-proces weigert dezelfde ``jti`` ook,
    ook na een herstart."""
    from appstore.app import create_app

    pair(client, hub)
    token = hub.jwt(roles=["viewer"])
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post("/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"}).status_code == 200
    other = create_app(settings, http_factory=hub.client_factory())  # type: ignore[arg-type]
    try:
        with TestClient(other) as second:
            resp = second.post("/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"})
            assert _reason(resp) == "token_replayed"
    finally:
        other.state.ctx.engine.dispose()
