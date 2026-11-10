"""Review fase P0: autorisatie op élke route, nette fouten bij gemanipuleerde invoer, body-limiet zonder
Content-Length, en koppelen alleen met de Hub uit ``HUB_URL``. Tegen echte PostgreSQL met de Hub-simulator."""

from __future__ import annotations

import base64
import dataclasses
import json
import time
import uuid
from collections.abc import Iterator

import jwt as pyjwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from appstore.api import catalog, connector, health, status_page
from appstore.api.common import AppContext
from appstore.app import MAX_BODY, create_app
from appstore.config import Settings
from appstore.worker import run_once, startup
from tests.fakehub import HUB_URL, FakeHub
from tests.integration.conftest import idem, pair, pair_form, post_form

pytestmark = pytest.mark.integration

#: Routes die volgens het contract zonder aanmelding bereikbaar zijn (§5 healthz/readyz, §1 statuspagina, §3 koppelen).
PUBLIC = {("GET", "/healthz"), ("GET", "/readyz"), ("GET", "/"), ("POST", "/koppelen")}


def _concrete(path: str) -> str:
    out = path
    for name in ("source_id", "version_id", "job_id", "evidence_id"):
        out = out.replace("{" + name + "}", str(uuid.uuid4()))
    return out.replace("{app_id}", "voorbeeld-web")


def _routes(client: TestClient) -> list[tuple[str, str]]:
    """Alle API-routes van de dienst (dezelfde routers die ``create_app`` insluit)."""
    out: list[tuple[str, str]] = []
    for router in (health.router, status_page.router, catalog.router, connector.router):
        for route in router.routes:
            if isinstance(route, APIRoute):
                out.extend((method, route.path) for method in sorted(route.methods))
    assert client.app is not None
    return out


def test_every_non_public_route_requires_auth(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)  # gekoppeld: weigering komt door ontbrekende/ongeldige auth, niet door "niet gekoppeld"
    routes = _routes(client)
    assert len(routes) >= 20
    checked = 0
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        url = _concrete(path)
        for headers in ({}, {"Authorization": "Bearer ongeldig-token-1234567890"}):
            resp = client.request(method, url, json={}, headers={**headers, **idem()})
            assert resp.status_code == 401, f"{method} {path} zonder geldige auth gaf {resp.status_code}"
            body = resp.json()
            assert body["code"] == "unauthorized" and set(body) == {"code", "message", "details", "trace_id"}
        checked += 1
    assert checked >= 15


def test_kind_and_roleless_users_get_nothing(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    for method, path in _routes(client):
        if (method, path) in PUBLIC:
            continue
        for roles in (["kind"], [], ["superuser"]):
            resp = client.request(method, _concrete(path), json={}, headers={**hub.auth(roles=roles), **idem()})
            assert resp.status_code == 403, f"{method} {path} met rollen {roles} gaf {resp.status_code}"


def test_hub_service_jwt_cannot_manage_sources(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    body = {"name": "eigen bron", "kind": "http", "url": "https://bron.test/index.json"}
    resp = client.post("/catalog/v1/sources", json=body, headers={**hub.service_auth(), **idem()})
    assert resp.status_code == 403 and resp.json()["code"] == "forbidden"


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_algorithm_confusion_refused(client: TestClient, hub: FakeHub, alg: str) -> None:
    pair(client, hub)
    now = int(time.time())
    claims = {"iss": "hub", "aud": "appstore-en-fork", "sub": "x", "roles": ["admin"], "iat": now, "exp": now + 60}
    claims |= {"nbf": now, "token_use": "user", "jti": "j-1"}
    if alg == "none":
        token = pyjwt.encode(claims, None, algorithm="none", headers={"kid": "x"})
    else:
        jwk_x = hub.jwks()["keys"][0]["x"]  # publieke sleutel als HMAC-geheim (klassieke verwarring)
        token = pyjwt.encode(claims, jwk_x.encode() * 2, algorithm="HS256", headers={"kid": "x"})
    resp = client.get("/catalog/v1/sources", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401 and resp.json()["details"]["reason"] == "invalid_token"


def _cursor(value: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "cursor", ["!!!", _cursor({"a": 1}), _cursor(5), _cursor(["geen-datum", "x"]), _cursor(["2026-11-09T00:00:00", 1])]
)
def test_manipulated_cursors_give_400_not_500(client: TestClient, hub: FakeHub, cursor: str) -> None:
    pair(client, hub)
    viewer = hub.auth(roles=["viewer"])
    for url in (
        f"/catalog/v1/entries/voorbeeld-web/versions?cursor={cursor}",
        f"/connector/v1/items?type=manifest_version&cursor={cursor}",
        f"/catalog/v1/entries?cursor={cursor}",
    ):
        resp = client.get(url, headers=viewer)
        assert resp.status_code in (200, 422), f"{url}: {resp.status_code} {resp.text}"
        if resp.status_code == 422:  # contract 1.3 §5: één code per situatie (ongeldige invoer)
            assert resp.json()["code"] == "validation_error"
            assert resp.json()["details"]["reason"] == "cursor_invalid" and resp.json()["details"]["fouten"]


def test_body_limit_without_content_length(client: TestClient, hub: FakeHub) -> None:
    """Een chunked verzoek (geen Content-Length) boven 1 MiB wordt afgebroken met 413, ook vóór aanmelding."""
    pair(client, hub)

    def chunks() -> Iterator[bytes]:
        yield b'{"manifest_yaml": "'
        for _ in range(MAX_BODY // 65536 + 2):
            yield b"x" * 65536
        yield b'"}'

    for headers in ({}, hub.auth(roles=["viewer"])):
        resp = client.post(
            "/catalog/v1/validate", content=chunks(), headers={**headers, "Content-Type": "application/json"}
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["code"] == "payload_too_large"
    small = client.post("/catalog/v1/validate", json={"manifest_yaml": "a: 1"}, headers=hub.auth(roles=["viewer"]))
    assert small.status_code == 200


def _chunked(total: int, piece: int = 65536) -> Iterator[bytes]:
    sent = 0
    while sent < total:
        part = min(piece, total - sent)
        sent += part
        yield b"x" * part


def _assert_413(resp: object) -> None:
    assert resp.status_code == 413, resp.text  # type: ignore[attr-defined]
    body = resp.json()  # type: ignore[attr-defined]
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["code"] == "payload_too_large" and body["details"] == {"max_bytes": MAX_BODY}  # §5
    assert resp.headers["X-Trace-Id"] == body["trace_id"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/connector/v1/hub-link/unlink"),  # route zonder body-model: eerst 401 zou §5 schenden
        ("POST", "/connector/v1/propose"),
        ("GET", "/healthz"),
        ("GET", "/connector/v1/discover"),
        ("POST", "/bestaat-niet"),  # vóór routering: geen 404
        ("PUT", "/healthz"),  # vóór routering: geen 405
        ("POST", "/koppelen"),  # statuspagina (formulier)
    ],
)
def test_chunked_body_over_limit_is_413_before_routing_and_auth(
    client: TestClient, hub: FakeHub, method: str, path: str
) -> None:
    """Contract 1.2 §5: een ASGI-laag vóór routering en authenticatie telt de body, ook zonder ``Content-Length``."""
    pair(client, hub)
    for headers in ({}, hub.service_auth()):
        resp = client.request(method, path, content=_chunked(MAX_BODY + 1), headers={**headers, **idem()})
        _assert_413(resp)
    st = client.app.state.ctx.store.read()  # type: ignore[attr-defined]
    assert st.state == "gekoppeld"  # het verzoek is nooit verwerkt (geen ontkoppeling)


def test_content_length_over_limit_is_refused_directly(client: TestClient, hub: FakeHub) -> None:
    pair(client, hub)
    body = b"x" * (MAX_BODY + 1)
    for path in ("/connector/v1/hub-link/unlink", "/bestaat-niet", "/catalog/v1/validate"):
        _assert_413(client.post(path, content=body, headers={"Content-Type": "application/json", **idem()}))


def test_body_of_exactly_one_mib_is_not_refused(client: TestClient, hub: FakeHub) -> None:
    """Precies 1 048 576 bytes valt binnen de grens: de route (en de aanmelding) beslist, niet de bodylaag."""
    pair(client, hub)
    assert client.request("GET", "/healthz", content=_chunked(MAX_BODY)).status_code == 200
    resp = client.post("/connector/v1/hub-link/unlink", content=_chunked(MAX_BODY), headers=idem())
    assert resp.status_code == 401  # zonder aanmelding: gewone weigering, geen 413
    ok = client.post(
        "/catalog/v1/validate",
        json={"manifest_yaml": "a: 1", "opvulling": "x" * (MAX_BODY - 100)},
        headers=hub.auth(roles=["viewer"]),
    )
    assert ok.status_code != 413, ok.text


def test_pairing_only_with_configured_hub_url(settings: Settings, hub: FakeHub) -> None:
    """Met HUB_URL uit de installatie wordt een andere (mogelijk vreemde) Hub op de statuspagina geweigerd."""
    s = dataclasses.replace(settings, hub_url=HUB_URL)
    app = create_app(s, http_factory=hub.client_factory())
    code = hub.add_code()
    with TestClient(app) as c:
        melding = pair_form(c, hub_url="http://vreemde-hub.test:30400", code=code, fingerprint=hub.fingerprint())
        assert melding == "andere_hub"
        assert hub.enroll_requests == []  # er is niets naar een andere Hub gestuurd
        page = post_form(c, hub_url="http://vreemde-hub.test:30400", code=code, fingerprint=hub.fingerprint())
        assert "verschilt van HUB_URL" in page.text and "vreemde-hub" not in page.text  # adres nooit teruggetoond
        assert pair_form(c, hub_url=HUB_URL, code=code, fingerprint=hub.fingerprint()) == "gekoppeld"
    app.state.ctx.engine.dispose()


def test_preview_with_bypass_path_is_refused_before_execution(
    client: TestClient, hub: FakeHub, worker_ctx: AppContext
) -> None:
    """EX47-variant via de API: geldig manifest, maar het hostpad uit de 'datasetkiezer' omzeilt de verboden gebieden."""
    pair(client, hub)
    startup(worker_ctx)
    run_once(worker_ctx)
    body = {
        "app_id": "voorbeeld-web",
        "parameters": {
            "config_path": "//mnt/.ix-apps/app_configs",
            "media_path": "/mnt/p1/media",
            "public_url": "http://nas.test:30999",
            "timezone": "Europe/Amsterdam",
        },
        "target": {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"},
    }
    resp = client.post("/catalog/v1/preview", json=body, headers=hub.auth(roles=["operator"]))
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["ready_for_install"] is False and out["executed"] is False
    assert out["translation"]["payload"] is None
    assert "parameter_value_invalid" in {f["code"] for f in out["translation"]["findings"]}
