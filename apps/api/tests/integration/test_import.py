"""B08-002 via de API: ``POST /catalog/v1/import/preview`` herkent bestaande apps tegen de echte catalogus
(PostgreSQL) en wijzigt niets; een niet-ondersteunde app blijft unmanaged zonder dataverlies (Sim-getest)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from appstore.api.common import AppContext
from appstore.models import AuditEvent, CatalogEntry, ManifestVersion, OutboxEvent
from appstore.worker import run_once, startup
from tests.fakehub import FakeHub
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration
URL = "/catalog/v1/import/preview"


@pytest.fixture
def linked(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> TestClient:
    pair(client, hub)
    startup(worker_ctx)
    run_once(worker_ctx)
    return client


def _inventory(apps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": apps}


def _counts(ctx: AppContext) -> tuple[int, int, int, int]:
    with ctx.sessions() as db:
        return (
            db.scalar(select(func.count()).select_from(CatalogEntry)) or 0,
            db.scalar(select(func.count()).select_from(ManifestVersion)) or 0,
            db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
            db.scalar(select(func.count()).select_from(OutboxEvent)) or 0,
        )


def test_import_preview_classifies_and_changes_nothing(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext
) -> None:
    with worker_ctx.sessions() as db:
        sha = db.scalar(select(ManifestVersion.manifest_sha256).where(ManifestVersion.app_id == "voorbeeld-web"))
    apps = [
        {
            "name": "voorbeeld-web",
            "state": "RUNNING",
            "labels": {
                "nl.lvos04.platform.app_id": "voorbeeld-web",
                "nl.lvos04.platform.managed_by": "appstore-en-fork",
                "nl.lvos04.platform.manifest_sha256": sha,
            },
            "images": ["docker.io/library/nginx:1.27.3"],
        },
        {"name": "fotos", "state": "RUNNING", "catalog_app": "voorbeeldapp", "version": "1.6.12"},
        {
            "name": "plex",
            "state": "RUNNING",
            "catalog_app": "plex",
            "images": ["plexinc/pms-docker:1.41.0"],
            "volumes": [{"source": "/mnt/p1/media", "destination": "/data", "mode": "rw", "type": "bind"}],
        },
    ]
    before = _counts(worker_ctx)
    resp = linked.post(URL, json={"inventory": _inventory(apps)}, headers=hub.auth(roles=["operator"]))
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["status"] == "ok" and out["executed"] is False
    assert out["owner_confirmation"] == "capability_unavailable"
    by_name = {a["name"]: a for a in out["apps"]}
    assert by_name["voorbeeld-web"]["status"] == "managed" and by_name["voorbeeld-web"]["manifest_current"] is True
    assert by_name["fotos"]["status"] == "candidate" and by_name["fotos"]["requires_owner_confirmation"] is True
    assert by_name["plex"]["status"] == "unmanaged"  # niet ondersteund → blijft ongemoeid
    assert all(a["planned_actions"] == [] and a["data_policy"] == "untouched" for a in out["apps"])
    assert out["counts"] == {"managed": 1, "candidate": 1, "unmanaged": 1}
    # niets gewijzigd: geen catalogus-, audit- of outboxmutaties (alleen een evidence-record)
    assert _counts(worker_ctx) == before
    ev = linked.get(f"/connector/v1/evidence/{out['evidence_id']}", headers=hub.auth(roles=["viewer"])).json()
    assert ev["kind"] == "import_preview" and ev["summary"]["counts"] == out["counts"]


def test_import_preview_requires_operator_and_inventory(linked: TestClient, hub: FakeHub) -> None:
    body = {"inventory": _inventory([])}
    assert linked.post(URL, json=body).status_code == 401
    assert linked.post(URL, json=body, headers=hub.auth(roles=["viewer"])).status_code == 403
    missing = linked.post(URL, json={}, headers=hub.auth(roles=["operator"]))
    assert missing.status_code == 422 and missing.json()["code"] == "validation_failed"
    stale = {"inventory": {**_inventory([{"name": "x"}]), "observed_at": "2020-01-01T00:00:00Z"}}
    out = linked.post(URL, json=stale, headers=hub.auth(roles=["operator"])).json()
    assert out["status"] == "unknown" and out["apps"][0]["status"] == "unknown"
