"""Gezondheid, foutformaat en de statuspagina met koppelen via een koppelcode (contract 1.2 §3.7)."""

from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient

from appstore.config import Settings
from tests.fakehub import HUB_URL, FakeHub
from tests.integration.conftest import ORIGIN, pair, pair_form

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
    assert resp.json()["details"]["reason"] == "not_linked"


def test_status_page_not_linked(client: TestClient, settings: Settings) -> None:
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert '<html lang="nl">' in html and "Niet gekoppeld" in html and 'action="/koppelen"' in html
    assert 'name="vingerafdruk"' in html and "laatst bijgewerkt" in html
    assert page.cookies.get("appstore_csrf")
    assert "default-src 'none'" in page.headers["content-security-policy"]
    # eerste start: hub-link.json bestaat (nooit_gekoppeld), 0600, in een 0700-map
    path = settings.secrets_dir / "hub-link.json"
    assert json.loads(path.read_text())["state"] == "nooit_gekoppeld"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.secrets_dir.stat().st_mode) == 0o700


def test_pairing_with_code_and_fingerprint(client: TestClient, hub: FakeHub, settings: Settings) -> None:
    token = pair(client, hub)
    html = client.get("/").text
    assert "Gekoppeld" in html and token[-4:] in html and hub.fingerprint() in html
    assert token not in html  # nooit het hele token tonen
    assert 'action="/koppelen"' not in html and "gaat via de Hub" in html
    req = hub.enroll_requests[0]
    assert set(req) == {"app_id", "pairing_code", "version", "base_url", "public_key_jwk"}
    assert req["app_id"] == "appstore-en-fork" and set(req["public_key_jwk"]) == {"kty", "crv", "x", "kid"}
    data = json.loads((settings.secrets_dir / "hub-link.json").read_text())
    assert data["state"] == "gekoppeld" and data["via"] == "koppelcode" and data["confirmed_at"]
    assert data["service_token"] == token and data["pins"]["keyset_fingerprint"] == hub.fingerprint()
    assert data["first_linked_at"] and data["pending"] is None and data["used_enrollment_token_sha256"] == []
    assert ("GET", "/api/v1/services/self") in hub.requests  # bevestigd met het nieuwe token


def test_pairing_lowercase_code_with_lookalikes_is_normalized(client: TestClient, hub: FakeHub) -> None:
    code = hub.add_code().replace("0", "o").replace("1", "l").lower().replace("-", " ")
    assert pair_form(client, code=code, fingerprint=hub.fingerprint().lower()) == "gekoppeld"
    assert hub.enroll_requests[-1]["pairing_code"].count("-") == 2


def test_form_only_before_first_link(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    n = len(hub.enroll_requests)
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "al_gekoppeld"
    assert len(hub.enroll_requests) == n  # geen verzoek naar de Hub


@pytest.mark.parametrize(
    ("fields", "melding"),
    [
        ({"hub_url": "ftp://x"}, "ongeldig_adres"),
        ({"hub_url": "http://hub.test:30400/pad"}, "ongeldig_adres"),
        ({"hub_url": "http://127.0.0.1:30400"}, "ongeldig_adres"),
        ({"hub_url": "http://localhost:30400"}, "ongeldig_adres"),
        ({"hub_url": "http://169.254.1.1:30400"}, "ongeldig_adres"),
        ({"hub_url": "http://0.0.0.0:30400"}, "ongeldig_adres"),
        ({"hub_url": "http://[::]:30400"}, "ongeldig_adres"),
        ({"hub_url": "http://[fe80::1]:30400"}, "ongeldig_adres"),
        ({"hub_url": "http://admin:geheim@hub.test:30400"}, "ongeldig_adres"),
        ({"code": "ABCD-EFGH-JKMU"}, "ongeldige_code"),  # U is geen Crockford-teken
        ({"code": "ABCD-EFGH-JKM"}, "ongeldige_code"),  # 11 tekens
        ({"code": "ABCD-EFGH-JKMN-PQRS-TVWX-YZ01-23"}, "ongeldige_code"),
        ({"fingerprint": ""}, "ongeldige_vingerafdruk"),
        ({"fingerprint": "ABCD-EFGH"}, "ongeldige_vingerafdruk"),
    ],
)
def test_invalid_form_input_never_reaches_the_hub(
    client: TestClient, hub: FakeHub, fields: dict[str, str], melding: str
) -> None:
    form = {"hub_url": HUB_URL, "code": hub.add_code(), "fingerprint": hub.fingerprint(), **fields}
    assert pair_form(client, **form) == melding
    assert hub.enroll_requests == []


def test_wrong_fingerprint_stores_nothing(client: TestClient, hub: FakeHub, settings: Settings) -> None:
    assert pair_form(client, code=hub.add_code(), fingerprint="0000-0000-0000-0000") == "mislukt"
    data = json.loads((settings.secrets_dir / "hub-link.json").read_text())
    assert data["state"] == "nooit_gekoppeld" and data["service_token"] is None and data["pins"] is None
    assert data["hub_url"] is None and data["pending"] is None  # adres van een mislukte poging nergens bewaard
    assert data["last_error"]["code"] == "hub_vingerafdruk_onjuist"
    assert "vingerafdruk van de Hub klopt niet" in client.get("/").text
    assert 'action="/koppelen"' in client.get("/").text  # nog nooit gekoppeld: formulier blijft


def test_csrf_and_origin_are_required(client: TestClient, hub: FakeHub) -> None:
    code, fp = hub.add_code(), hub.fingerprint()
    assert pair_form(client, code=code, fingerprint=fp, csrf="fout") == "sessie"
    assert pair_form(client, code=code, fingerprint=fp, origin={}) == "sessie"
    assert pair_form(client, code=code, fingerprint=fp, origin={"Origin": "http://aanvaller.test"}) == "sessie"
    assert pair_form(client, code=code, fingerprint=fp, origin={"Origin": "null"}) == "sessie"
    assert hub.enroll_requests == []
    assert pair_form(client, code=code, fingerprint=fp, origin=ORIGIN) == "gekoppeld"


def test_rate_limit_five_per_ip(client: TestClient, hub: FakeHub) -> None:
    for _ in range(5):
        assert pair_form(client, code="XXXX-XXXX-XXXX", fingerprint=hub.fingerprint()) == "mislukt"
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "te_vaak"
    assert len(hub.enroll_requests) == 5
    assert "Koppelen mislukt" not in client.get("/").text  # oude P0-tekst bestaat niet meer
    assert "onbekend, verlopen of al gebruikt" in client.get("/").text


def test_hub_unreachable_is_honest(client: TestClient, hub: FakeHub) -> None:
    hub.down = True
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "mislukt"
    assert "Hub was niet bereikbaar" in client.get("/").text
    hub.down = False
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "gekoppeld"


def test_hub_rate_limit_and_redirect_are_not_followed(client: TestClient, hub: FakeHub) -> None:
    hub.enroll_status = 302
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "mislukt"
    assert not any("elders.test" in url for url in hub.all_urls)  # 3xx = storing, nooit gevolgd
    hub.enroll_status = 429
    assert pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint()) == "mislukt"
    assert "te veel mislukte pogingen" in client.get("/").text
