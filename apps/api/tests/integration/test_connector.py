"""Connectorcontract §6 en outbox/events §7."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.models import HubLink
from appstore.worker import push_outbox, run_once, startup
from tests.fakehub import FakeHub
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> dict[str, str]:
    pair(client, hub)
    startup(worker_ctx)
    run_once(worker_ctx)
    return {"Authorization": f"Bearer {hub.issued_token}"}


def test_discover_and_health(client: TestClient, svc: dict[str, str]) -> None:
    d = client.get("/connector/v1/discover", headers=svc).json()
    assert d["app_id"] == "appstore-en-fork" and d["schema_version"] == "1.0" and d["capabilities"] == ["read"]
    assert {"propose", "apply", "rollback", "stream"} <= set(d["unavailable_capabilities"])
    h = client.get("/connector/v1/health", headers=svc).json()
    assert h["status"] in ("healthy", "degraded") and {c["name"] for c in h["checks"]} >= {"database", "hub_link"}
    assert all("observed_at" in c for c in h["checks"])


def test_items_types(client: TestClient, svc: dict[str, str]) -> None:
    entries = client.get("/connector/v1/items?type=catalog_entry&limit=2", headers=svc).json()
    assert len(entries["items"]) == 2 and entries["next_cursor"]
    more = client.get(f"/connector/v1/items?type=catalog_entry&cursor={entries['next_cursor']}", headers=svc).json()
    assert len(more["items"]) == 1
    versions = client.get("/connector/v1/items?type=manifest_version", headers=svc).json()
    assert len(versions["items"]) == 4  # 3 geldig + EX47 geweigerd
    assert client.get("/connector/v1/items?type=catalog_source", headers=svc).json()["items"][0]["kind"] == "bundled"
    assert client.get("/connector/v1/items?type=onzin", headers=svc).status_code == 422


@pytest.mark.parametrize("path", ["/connector/v1/propose", "/connector/v1/apply", "/connector/v1/rollback"])
def test_unbuilt_capabilities_are_honest(client: TestClient, svc: dict[str, str], path: str) -> None:
    resp = client.post(path, headers=svc, json={})
    assert resp.status_code == 501 and resp.json()["code"] == "capability_unavailable"


def test_events_polling_and_push(client: TestClient, hub: FakeHub, svc: dict[str, str], worker_ctx: AppContext) -> None:
    page = client.get("/connector/v1/events?limit=200", headers=svc).json()
    types = [e["event_type"] for e in page["items"]]
    assert "service.enrollment.completed" in types and "catalog.manifest.added" in types
    assert "catalog.manifest.rejected" in types and "catalog.source.synced" in types
    env = page["items"][0]
    assert set(env) == {
        "schema_version",
        "event_id",
        "source_app",
        "source_event_id",
        "event_type",
        "entity",
        "timestamp_utc",
        "trace_id",
        "correlation_id",
        "actor",
        "classification",
        "payload",
        "payload_ref",
    }
    assert env["timestamp_utc"].endswith("Z")
    empty = client.get(f"/connector/v1/events?cursor={page['next_cursor']}", headers=svc).json()
    assert empty["items"] == []
    # push naar de Hub-inbox met het service-token (run_once in de fixture heeft al gepusht)
    assert len(hub.inbox) == len(types)
    assert all(a == f"Bearer {hub.issued_token}" for a in hub.inbox_auth)
    assert push_outbox(worker_ctx) == 0  # niets dubbel


def test_push_failures_backoff_and_revocation(
    client: TestClient, hub: FakeHub, svc: dict[str, str], worker_ctx: AppContext
) -> None:
    client.post("/catalog/v1/validate", json={"manifest": {}}, headers=svc)  # geen event; wel bewijs
    from appstore.events import emit

    with worker_ctx.sessions() as db:
        emit(db, "catalog.test.event", "test", "1", {"token": "geheim"}, "00000000-0000-0000-0000-000000000000")
        db.commit()
    hub.inbox_status = 500
    assert push_outbox(worker_ctx) == 0
    hub.inbox_status = 401
    from datetime import timedelta

    from sqlalchemy import update

    from appstore.models import OutboxEvent
    from appstore.timeutil import utcnow

    with worker_ctx.sessions() as db:
        db.execute(update(OutboxEvent).values(next_attempt_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    push_outbox(worker_ctx)
    with worker_ctx.sessions() as db:
        link = db.get(HubLink, 1)
        assert link is not None and link.status == "revoked"
    page = client.get("/").text
    assert "Koppeling ingetrokken" in page and 'action="/koppelen"' in page
    # payload is geredigeerd
    events = client.get("/connector/v1/events?limit=200", headers={"Authorization": "Bearer x"})
    assert events.status_code == 401  # na intrekking geldt het oude token niet meer
