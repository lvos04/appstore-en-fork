"""Herstel na de review van S-P2-520 via de API (ADR-0018): ``POST /catalog/v1/validate`` (rol viewer) en
``POST /catalog/v1/preview`` weigeren zonder payload en zonder de testwaarde in antwoord, bewijs of log; een te groot of
te diep JSON-manifest wordt geweigerd. De synchronisatie per manifest staat in ``test_catalog_sync_per_manifest.py``."""

from __future__ import annotations

import copy
import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.manifest.loader import MAX_DEPTH, MAX_MANIFEST_BYTES
from appstore.worker import run_once, startup
from tests.conftest import load_fixture
from tests.fakehub import FakeHub
from tests.geheimen_proeven import HASH, PROEFSLEUTEL, TESTWAARDEN, WITRUIMTE, flows, met_env, met_waarden, wizard
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration
CODE = "account_gegevens_verboden"
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.test:30999",
    "timezone": "Europe/Amsterdam",
}
TARGET = {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"}


@pytest.fixture
def linked(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> TestClient:
    pair(client, hub)
    startup(worker_ctx)
    assert run_once(worker_ctx)["jobs"] == 1
    return client


def _body(data: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_yaml": yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        "parameters": parameters,
        "target": TARGET,
        "inventory": {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []},
    }


def _no_echo(text: str) -> None:
    for waarde in TESTWAARDEN:
        assert waarde not in text, "een testwaarde staat in het antwoord"


def _preview(client: TestClient, hub: FakeHub, data: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_body(data, params))
    assert resp.status_code == 200, resp.text
    _no_echo(resp.text)
    body: dict[str, Any] = resp.json()
    assert body["ready_for_install"] is False and body["executed"] is False
    assert body["translation"] is None or body["translation"]["payload"] is None
    return body


# ---------------------------------------------------------------------------------------------- R-VEILIG-1
@pytest.mark.parametrize("soort", sorted(WITRUIMTE))
def test_whitespace_values_are_refused_by_preview(linked: TestClient, hub: FakeHub, soort: str) -> None:
    ws = WITRUIMTE[soort]
    gevallen = [
        met_env({"name": "GOTIFY_DEFAULTUSER_PASS", "value": ws}),
        met_env({"name": "APP_SECRET_KEY", "value": ws}),
        met_env({"name": "FLOWS", "value": flows('-D settings={"credentialSecret":"' + ws + '"}')}),
    ]
    if soort != "spaties":
        gevallen.append(met_env({"name": "FLOWS", "value": flows("-D credentialSecret=" + ws)}))
    for data in gevallen:
        body = _preview(linked, hub, data, PARAMS)
        assert body["translation"] is None and body["validation"]["valid"] is False
    wizardwaarde = met_env({"name": "WELKOM", "value_from": {"param": "welkomtekst"}}, wizard("welkomtekst"))
    body = _preview(
        linked, hub, wizardwaarde, dict(PARAMS, welkomtekst='-D settings={"credentialSecret":"' + ws + '"}')
    )
    assert body["validation"]["valid"] is True and body["translation"]["payload"] is None


# ---------------------------------------------------------------------------------------------- R-VEILIG-2
@pytest.mark.parametrize("stuk", ["$", '"\\', "users."], ids=["dollar", "aanhalingsteken-backslash", "users-punt"])
def test_viewer_validate_with_a_large_json_manifest_is_fast(linked: TestClient, hub: FakeHub, stuk: str) -> None:
    """Een viewer met een JSON-manifest net onder de grens: binnen 1 s een antwoord (vóór het herstel minuten)."""
    data = copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))
    lengte = 240_000 * len(stuk) // (len(json.dumps(stuk)) - 2)  # als JSON net onder MAX_MANIFEST_BYTES
    data["values"]["voorbeeld"]["tekst"] = (stuk * lengte)[:lengte]
    start = time.perf_counter()
    resp = linked.post("/catalog/v1/validate", json={"manifest": data}, headers=hub.auth(roles=["viewer"]))
    took = time.perf_counter() - start
    assert resp.status_code == 200 and resp.json()["valid"] is False
    assert CODE in {f["code"] for f in resp.json()["findings"]}
    assert took < 1.0, took


def test_viewer_validate_applies_size_and_depth_limits_to_the_json_field(linked: TestClient, hub: FakeHub) -> None:
    data = copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))
    data["values"]["voorbeeld"]["tekst"] = "a" * (MAX_MANIFEST_BYTES + 1)
    resp = linked.post("/catalog/v1/validate", json={"manifest": data}, headers=hub.auth(roles=["viewer"]))
    assert resp.status_code == 200 and [f["code"] for f in resp.json()["findings"]] == ["manifest_too_large"]
    diep = copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))
    nest: dict[str, Any] = {}
    diep["values"]["voorbeeld"]["diep"] = nest
    for _ in range(MAX_DEPTH):
        nest["x"] = {}
        nest = nest["x"]
    resp = linked.post("/catalog/v1/validate", json={"manifest": diep}, headers=hub.auth(roles=["viewer"]))
    assert resp.status_code == 200 and [f["code"] for f in resp.json()["findings"]] == ["yaml_too_deep"]
    gewoon = linked.post(
        "/catalog/v1/validate",
        json={"manifest": load_fixture("valid-catalog-1-3.manifest.yaml")},
        headers=hub.auth(roles=["viewer"]),
    )
    assert gewoon.status_code == 200 and gewoon.json()["valid"] is True


# ---------------------------------------------------------------------------------------------- R-VEILIG-3
def test_sharp_s_never_gives_a_500(linked: TestClient, hub: FakeHub) -> None:
    flows_data = met_env({"name": "FLOWS", "value": "flows.json -D Reisepaß=x"})
    resp = linked.post(
        "/catalog/v1/validate",
        json={"manifest_yaml": yaml.safe_dump(flows_data, allow_unicode=True)},
        headers=hub.auth(roles=["viewer"]),
    )
    assert resp.status_code == 200 and resp.json()["valid"] is True
    sleutel = met_waarden({"Reisepaß": "x"})
    resp = linked.post("/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_body(sleutel, {}))
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------------------------- R-VEILIG-4
def test_enrollment_name_from_the_wizard_gives_no_payload(linked: TestClient, hub: FakeHub) -> None:
    data = copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))
    # zonder het geheim van de Hub: bij een los manifest weigert de vertaling een vault-verwijzing al
    data["values"]["voorbeeld"].pop("db_password")
    data["parameters"] = [p for p in data["parameters"] if p["name"] != "db_password"]
    data["values"]["voorbeeld"]["additional_envs"] = [{"name": {"param": "envnaam"}, "value": {"param": "envwaarde"}}]
    data["parameters"] += [wizard("envnaam"), wizard("envwaarde")]
    params = {"timezone": "Europe/Amsterdam", "data_path": "/mnt/p1/apps/sso"}
    for naam, waarde in (
        ("HUB_ENROLLMENT_TOKEN", HASH),
        ("HUB_ENROLLMENT_TOKEN", f"-D credentialSecret={PROEFSLEUTEL}"),
        ("HUB_URL", "http://ander.test:30400"),
    ):
        body = _preview(linked, hub, data, dict(params, envnaam=naam, envwaarde=waarde))
        blocking = {(f["path"], f["code"]) for f in body["translation"]["findings"] if f["severity"] == "error"}
        assert ("values.voorbeeld.additional_envs[0].name", "reserved_env") in blocking
    ok = linked.post(
        "/catalog/v1/preview",
        headers=hub.auth(roles=["operator"]),
        json=_body(data, dict(params, envnaam="APP_THEMA", envwaarde="donker")),
    )
    assert ok.status_code == 200 and ok.json()["ready_for_install"] is True


# ---------------------------------------------------------------------------------------------- R-VEILIG-5
def test_recognised_app_id_is_not_in_answer_or_evidence(linked: TestClient, hub: FakeHub) -> None:
    data = copy.deepcopy(load_fixture("valid-custom-upstream.manifest.yaml"))
    data["app_id"] = HASH
    resp = linked.post(
        "/catalog/v1/validate", json={"manifest_yaml": yaml.safe_dump(data)}, headers=hub.auth(roles=["viewer"])
    )
    assert resp.status_code == 200 and resp.json()["valid"] is False and resp.json()["app_id"] is None
    _no_echo(resp.text)
    ev = linked.get(f"/connector/v1/evidence/{resp.json()['evidence_id']}", headers=hub.auth())
    assert ev.status_code == 200
    _no_echo(ev.text)
    body = _preview(linked, hub, data, PARAMS)
    ev = linked.get(f"/connector/v1/evidence/{body['evidence_id']}", headers=hub.auth())
    _no_echo(ev.text)
