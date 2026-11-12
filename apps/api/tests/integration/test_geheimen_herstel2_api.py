"""Herstelronde 2 na de herreview van S-P2-520 via de API (ADR-0019): ``POST /catalog/v1/preview`` (rol operator)
houdt geen lange parameternamen vast (R-VEILIG-7) en weigert een te grote vertaling fail-closed (vervolgpunt
R-VEILIG-2), zonder payload en zonder de testwaarde in antwoord of bewijs."""

from __future__ import annotations

import gc
import os
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.manifest import translate as vertaling
from appstore.worker import run_once, startup
from tests.conftest import load_fixture
from tests.fakehub import FakeHub
from tests.geheimen_proeven import TESTWAARDEN, met_waarden, wizard
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration
TARGET = {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"}
_PAGE = os.sysconf("SC_PAGE_SIZE")


def _rss_mib() -> float:
    with open("/proc/self/statm", encoding="ascii") as fh:
        return int(fh.read().split()[1]) * _PAGE / 2**20


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


def test_preview_with_long_parameter_names_keeps_no_memory(linked: TestClient, hub: FakeHub) -> None:
    """R-VEILIG-7: 20 previews, elk met een andere onbekende parameternaam van 1 000 000 tekens (net onder de grens
    van 1 MiB voor een verzoek): daarna houdt de dienst minder dan 10 MiB extra vast (met de cache van ADR-0018 bleef
    elke naam staan)."""
    data = load_fixture("valid-custom-upstream.manifest.yaml")
    opwarmen = linked.post(
        "/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_body(data, {"opwarmen": "x"})
    )
    assert opwarmen.status_code == 200, opwarmen.text[:300]
    gc.collect()
    voor = _rss_mib()
    for i in range(20):
        params = {f"p{i:05d}" + "x" * 999_994: "x"}
        resp = linked.post("/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_body(data, params))
        assert resp.status_code == 200, resp.text[:300]
        assert resp.json()["ready_for_install"] is False
        del resp, params
    gc.collect()
    groei = _rss_mib() - voor
    assert groei < 10, f"{groei:.0f} MiB blijft staan"


def test_preview_refuses_a_translation_that_is_too_large(linked: TestClient, hub: FakeHub) -> None:
    """Vervolgpunt R-VEILIG-2: te veel ingevulde plekken geeft fail-closed ``vertaling_te_groot``; geen payload, en
    het bewijs noemt de code."""
    data = met_waarden({f"k{i}": {"param": "w"} for i in range(vertaling.MAX_FILLED_PLACES)}, wizard("w"))
    data["values"]["voorbeeld"].pop("db_password")
    data["parameters"] = [p for p in data["parameters"] if p["name"] != "db_password"]
    params = {"timezone": "Europe/Amsterdam", "data_path": "/mnt/p1/apps/sso", "w": "x"}
    resp = linked.post("/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_body(data, params))
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["ready_for_install"] is False and body["executed"] is False
    assert body["validation"]["valid"] is True
    assert body["translation"]["payload"] is None and body["translation"]["vault_refs"] == []
    blocking = {(f["path"], f["code"]) for f in body["translation"]["findings"] if f["severity"] == "error"}
    assert ("parameters", "vertaling_te_groot") in blocking
    for waarde in TESTWAARDEN:
        assert waarde not in resp.text
    ev = linked.get(f"/connector/v1/evidence/{body['evidence_id']}", headers=hub.auth())
    assert ev.status_code == 200 and "vertaling_te_groot" in ev.text
