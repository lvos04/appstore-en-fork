"""Geheimen en accountgegevens via de API (ADR-0016 W2 en W3; ADR-0017): ``POST /catalog/v1/validate`` en
``POST /catalog/v1/preview`` weigeren elke proef vóór uitvoering, zonder payload, en de testwaarde staat in geen
antwoord, geen bewijs en geen log (V-094)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.worker import run_once, startup
from tests.fakehub import FakeHub
from tests.geheimen_proeven import HASH, NAAMPROEVEN, TEKSTPROEVEN, TESTWAARDEN, met_env, wizard
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


def test_every_proof_is_refused_by_preview_without_payload(
    linked: TestClient, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    proeven: dict[str, tuple[dict[str, Any], set[str]]] = {
        naam: (data, set(expected.values())) for naam, (data, expected) in NAAMPROEVEN.items()
    }
    proeven.update({naam: (met_env({"name": "FLOWS", "value": text}), {CODE}) for naam, text in TEKSTPROEVEN.items()})
    with caplog.at_level(logging.DEBUG):
        for naam, (data, codes) in proeven.items():
            resp = linked.post("/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_body(data, PARAMS))
            assert resp.status_code == 200, (naam, resp.text)
            body = resp.json()
            assert body["ready_for_install"] is False and body["translation"] is None, naam
            assert body["executed"] is False and "vóór uitvoering" in body["blocked_reason_nl"], naam
            found = {f["code"] for f in body["validation"]["findings"] if f["severity"] == "error"}
            assert codes <= found, (naam, found)
            _no_echo(resp.text)
            ev = linked.get(f"/connector/v1/evidence/{body['evidence_id']}", headers=hub.auth())
            assert ev.status_code == 200
            _no_echo(ev.text)
    _no_echo(caplog.text)


def test_validate_endpoint_refuses_without_echo(linked: TestClient, hub: FakeHub) -> None:
    for naam in ("p1", "p2", "p6"):
        data, expected = NAAMPROEVEN[naam]
        resp = linked.post(
            "/catalog/v1/validate", json={"manifest_yaml": yaml.safe_dump(data)}, headers=hub.auth(roles=["viewer"])
        )
        assert resp.status_code == 200 and resp.json()["valid"] is False
        assert set(expected.values()) <= {f["code"] for f in resp.json()["findings"]}
        _no_echo(resp.text)


def test_wizard_value_with_a_hash_is_refused_by_preview(
    linked: TestClient, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    """W3: het manifest is geldig, de ingevulde waarde niet; geen payload en de waarde staat nergens."""
    data = met_env({"name": "WELKOM", "value_from": {"param": "welkomtekst"}}, wizard("welkomtekst"))
    with caplog.at_level(logging.DEBUG):
        resp = linked.post(
            "/catalog/v1/preview",
            headers=hub.auth(roles=["operator"]),
            json=_body(data, dict(PARAMS, welkomtekst=HASH)),
        )
        ok = linked.post(
            "/catalog/v1/preview",
            headers=hub.auth(roles=["operator"]),
            json=_body(data, dict(PARAMS, welkomtekst="Welkom")),
        )
    body = resp.json()
    assert resp.status_code == 200 and body["validation"]["valid"] is True
    assert body["ready_for_install"] is False and body["translation"]["payload"] is None
    assert body["translation"]["preview_hash"] is None and body["translation"]["vault_refs"] == []
    blocking = [(f["path"], f["code"]) for f in body["translation"]["findings"] if f["severity"] == "error"]
    assert blocking == [("values.envs[2].value", CODE)]
    assert set(body["translation"]) == set(ok.json()["translation"])  # geen veld erbij (V-046)
    assert ok.json()["ready_for_install"] is True
    _no_echo(resp.text)
    ev = linked.get(f"/connector/v1/evidence/{body['evidence_id']}", headers=hub.auth())
    assert CODE in ev.json()["summary"]["blocking_codes"]
    _no_echo(ev.text)
    _no_echo(caplog.text)
