"""Gezondheid, foutformaat, statuspagina en koppelen (koppelcode en enrollment-token)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from appstore.config import Settings
from appstore.worker import build_context, run_once
from tests.fakehub import HUB_URL, FakeHub
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration


def test_healthz_and_readyz(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz")
    assert ready.status_code == 200, ready.text
    assert ready.json()["checks"] == {"database": "ok", "schema": "ok"}
    assert "X-Trace-Id" in ready.headers


def test_error_format_is_dutch_with_trace_id(client: TestClient) -> None:
    resp = client.get("/bestaat-niet")
    body = resp.json()
    assert resp.status_code == 404 and body["code"] == "not_found" and body["message"] == "Niet gevonden."
    assert set(body) == {"code", "message", "details", "trace_id"}
    resp = client.get("/catalog/v1/entries")
    assert resp.status_code == 401 and resp.json()["code"] == "unauthorized"
    assert resp.json()["details"]["reason"] == "missing_token"


def test_status_page_not_linked(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert '<html lang="nl">' in html and "Niet gekoppeld" in html and 'action="/koppelen"' in html
    assert "laatst bijgewerkt" in html
    assert page.cookies.get("appstore_csrf")
    assert "default-src 'none'" in page.headers["content-security-policy"]


def test_pairing_with_code(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    html = client.get("/").text
    assert "Gekoppeld" in html and hub.issued_token[-4:] in html
    assert hub.issued_token not in html  # nooit het hele token tonen
    assert 'action="/koppelen"' not in html
    req = hub.enroll_requests[0]
    assert req["app_id"] == "appstore-en-fork" and req["pairing_code"] == "ABCD-EFGH-JKMN"
    assert req["public_key_jwk"]["crv"] == "Ed25519" and "enrollment_token" not in req
    link = client.get("/catalog/v1/hub-link", headers=hub.auth(roles=["viewer"])).json()
    assert (
        link["status"] == "linked"
        and link["linked_via"] == "koppelcode"
        and link["token_last4"] == hub.issued_token[-4:]
    )
    assert "token" not in {k for k in link if k != "token_last4"}


def test_pairing_refuses_bad_code_csrf_and_rate_limits(client: TestClient, hub: FakeHub) -> None:
    client.get("/")
    csrf = client.cookies.get("appstore_csrf")
    r = client.post(
        "/koppelen", data={"hub_url": HUB_URL, "koppelcode": "XXXX-XXXX-XXXX", "csrf": "fout"}, follow_redirects=False
    )
    assert r.headers["location"] == "/?melding=sessie"
    r = client.post(
        "/koppelen", data={"hub_url": HUB_URL, "koppelcode": "XXXX-XXXX-XXXX", "csrf": csrf}, follow_redirects=False
    )
    assert r.headers["location"] == "/?melding=mislukt"
    assert "Koppelen mislukt" in client.get("/?melding=mislukt").text
    r = client.post(
        "/koppelen", data={"hub_url": "ftp://x", "koppelcode": "XXXX-XXXX-XXXX", "csrf": csrf}, follow_redirects=False
    )
    assert r.headers["location"] == "/?melding=ongeldig"
    for _ in range(5):
        r = client.post(
            "/koppelen", data={"hub_url": HUB_URL, "koppelcode": "XXXX-XXXX-XXXX", "csrf": csrf}, follow_redirects=False
        )
    assert r.headers["location"] == "/?melding=te_vaak"


def test_hub_unreachable_is_honest(client: TestClient, hub: FakeHub) -> None:
    hub.down = True
    client.get("/")
    r = client.post(
        "/koppelen",
        data={"hub_url": HUB_URL, "koppelcode": "ABCD-EFGH-JKMN", "csrf": client.cookies.get("appstore_csrf")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/?melding=mislukt"
    assert "Hub niet bereikbaar" in client.get("/").text


def test_auto_enrollment_with_env_token(settings: Settings, hub: FakeHub) -> None:
    hub.valid_tokens.add("eenmalig-token-1234567890")
    s = Settings(**{**settings.__dict__, "hub_url": HUB_URL, "enrollment_token": "eenmalig-token-1234567890"})
    c = build_context(s, hub.client_factory())
    try:
        run_once(c)
        with c.sessions() as db:
            from appstore.models import HubLink

            link = db.get(HubLink, 1)
            assert link is not None and link.status == "linked" and link.linked_via == "token"
            assert link.token_ciphertext and hub.issued_token.encode() not in link.token_ciphertext
        n = len(hub.enroll_requests)
        run_once(c)
        assert len(hub.enroll_requests) == n  # gekoppeld: token niet opnieuw gebruiken
    finally:
        c.engine.dispose()


def test_rejected_env_token_is_not_retried(settings: Settings, hub: FakeHub) -> None:
    s = Settings(**{**settings.__dict__, "hub_url": HUB_URL, "enrollment_token": "verlopen-token-1234567890"})
    c = build_context(s, hub.client_factory())
    try:
        run_once(c)
        run_once(c)
        assert len(hub.enroll_requests) == 1
        from appstore.models import HubLink

        with c.sessions() as db:
            link = db.get(HubLink, 1)
            assert link is not None and link.status == "failed" and "weigerde" in (link.last_error or "")
    finally:
        c.engine.dispose()


def test_hub_5xx_is_retried_then_failed(settings: Settings, hub: FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("appstore.hublink.time.sleep", lambda _s: None)
    hub.enroll_status = 503
    s = Settings(**{**settings.__dict__, "hub_url": HUB_URL, "enrollment_token": "token-abcdefghijklmnop"})
    c = build_context(s, hub.client_factory())
    try:
        run_once(c)
        assert len(hub.enroll_requests) == 3  # max 3 pogingen (contract §5)
    finally:
        c.engine.dispose()
