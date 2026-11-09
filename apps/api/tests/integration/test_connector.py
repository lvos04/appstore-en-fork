"""Connectorcontract §6 (incl. hub-link en unlink, contract 1.2 §3.8) en outbox/events §7."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from appstore.api.common import AppContext
from appstore.events import emit
from appstore.models import OutboxEvent
from appstore.timeutil import utcnow
from appstore.worker import push_outbox, run_once, startup
from tests.fakehub import FakeHub
from tests.integration.conftest import idem, pair

pytestmark = pytest.mark.integration


@pytest.fixture
def linked(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> FakeHub:
    pair(client, hub)
    startup(worker_ctx)
    run_once(worker_ctx)
    return hub


def test_discover_and_health(client: TestClient, linked: FakeHub) -> None:
    d = client.get("/connector/v1/discover", headers=linked.service_auth()).json()
    assert d["app_id"] == "appstore-en-fork" and d["schema_version"] == "1.0" and d["capabilities"] == ["read"]
    assert {"propose", "apply", "rollback", "stream"} <= set(d["unavailable_capabilities"])
    h = client.get("/connector/v1/health", headers=linked.service_auth()).json()
    assert h["status"] in ("healthy", "degraded") and {c["name"] for c in h["checks"]} >= {"database", "hub_link"}
    assert all("observed_at" in c for c in h["checks"])
    assert next(c for c in h["checks"] if c["name"] == "hub_link")["status"] == "healthy"


def test_items_types(client: TestClient, linked: FakeHub) -> None:
    svc = linked.service_auth()
    entries = client.get("/connector/v1/items?type=catalog_entry&limit=2", headers=svc).json()
    assert len(entries["items"]) == 2 and entries["next_cursor"]
    more = client.get(f"/connector/v1/items?type=catalog_entry&cursor={entries['next_cursor']}", headers=svc).json()
    assert len(more["items"]) == 1
    versions = client.get("/connector/v1/items?type=manifest_version", headers=svc).json()
    assert len(versions["items"]) == 4  # 3 geldig + EX47 geweigerd
    assert client.get("/connector/v1/items?type=catalog_source", headers=svc).json()["items"][0]["kind"] == "bundled"
    assert client.get("/connector/v1/items?type=onzin", headers=svc).status_code == 422


@pytest.mark.parametrize("path", ["/connector/v1/propose", "/connector/v1/apply", "/connector/v1/rollback"])
def test_unbuilt_capabilities_are_honest(client: TestClient, linked: FakeHub, path: str) -> None:
    resp = client.post(path, headers=linked.service_auth(), json={})
    assert resp.status_code == 501 and resp.json()["code"] == "capability_unavailable"


def test_events_polling_and_push(client: TestClient, linked: FakeHub, worker_ctx: AppContext) -> None:
    svc = linked.service_auth()
    page = client.get("/connector/v1/events?limit=200", headers=svc).json()
    types = [e["event_type"] for e in page["items"]]
    assert "service.enrollment.completed" in types and "catalog.manifest.added" in types
    assert "catalog.manifest.rejected" in types and "catalog.source.synced" in types
    assert all(len(t.split(".")) == 3 for t in types)  # §7: precies drie segmenten
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
    # push naar de Hub-inbox met het service-token (dienst → Hub blijft Bearer <service-token>, §4.4)
    assert len(linked.inbox) == len(types)
    assert all(a == f"Bearer {linked.last_service_token}" for a in linked.inbox_auth)
    with worker_ctx.sessions() as db:
        rows = db.scalars(select(OutboxEvent)).all()
        assert rows and all(r.delivered_at is not None and r.attempts == 0 for r in rows)  # als afgeleverd gemarkeerd
    _due_now(worker_ctx)
    assert push_outbox(worker_ctx) == 0  # niets dubbel
    assert len(linked.inbox) == len(types)  # ook de Hub ontving niets opnieuw


def test_push_counts_delivered_events(client: TestClient, linked: FakeHub, worker_ctx: AppContext) -> None:
    with worker_ctx.sessions() as db:
        for n in range(3):
            emit(db, "catalog.test.event", "test", str(n), {}, "00000000-0000-0000-0000-000000000000")
        db.commit()
    before = len(linked.inbox)
    assert push_outbox(worker_ctx) == 3
    assert len(linked.inbox) == before + 3
    assert push_outbox(worker_ctx) == 0 and len(linked.inbox) == before + 3


def test_push_5xx_backs_off_and_dead_letters_after_ten(
    client: TestClient, linked: FakeHub, worker_ctx: AppContext
) -> None:
    """§7: retry met backoff, dead-letter na 10 pogingen; een 5xx is nooit 'afgeleverd'."""
    with worker_ctx.sessions() as db:
        emit(db, "catalog.test.event", "test", "x", {}, "00000000-0000-0000-0000-000000000000")
        db.commit()
    linked.inbox_status = 503
    assert push_outbox(worker_ctx) == 0
    with worker_ctx.sessions() as db:
        ev = db.scalars(select(OutboxEvent).where(OutboxEvent.delivered_at.is_(None))).one()
        assert ev.attempts == 1 and ev.last_error == "HTTP 503" and not ev.dead_letter
        assert ev.next_attempt_at > utcnow()  # backoff: niet direct opnieuw
    assert push_outbox(worker_ctx) == 0  # nog niet aan de beurt
    with worker_ctx.sessions() as db:
        assert db.scalars(select(OutboxEvent).where(OutboxEvent.delivered_at.is_(None))).one().attempts == 1
    for _ in range(9):
        _due_now(worker_ctx)
        push_outbox(worker_ctx)
    with worker_ctx.sessions() as db:
        ev = db.scalars(select(OutboxEvent).where(OutboxEvent.delivered_at.is_(None))).one()
        assert ev.attempts == 10 and ev.dead_letter
    linked.inbox_status = 202
    _due_now(worker_ctx)
    assert push_outbox(worker_ctx) == 0  # dead-letter wordt niet meer verstuurd
    health = client.get("/connector/v1/health", headers=linked.service_auth()).json()
    assert health["status"] == "degraded" and health["reason_code"] == "outbox"


def _due_now(worker_ctx: AppContext) -> None:
    with worker_ctx.sessions() as db:
        db.execute(update(OutboxEvent).values(next_attempt_at=utcnow() - timedelta(seconds=1)))
        db.commit()


def test_push_failures_backoff_and_revocation(client: TestClient, linked: FakeHub, worker_ctx: AppContext) -> None:
    with worker_ctx.sessions() as db:
        emit(db, "catalog.test.event", "test", "1", {"token": "geheim"}, "00000000-0000-0000-0000-000000000000")
        db.commit()
    linked.inbox_status = 500
    assert push_outbox(worker_ctx) == 0
    assert worker_ctx.store.read().state == "gekoppeld"  # 5xx is nooit een intrekking
    # de Hub trekt het token in: inbox 401 → services/self ook 401 → ingetrokken (§3.8)
    linked.inbox_status = 202
    linked.revoke_all()
    _due_now(worker_ctx)
    push_outbox(worker_ctx)
    st = worker_ctx.store.read()
    assert st.state == "ingetrokken" and st.service_token is None and st.pins is None and st.previous_pins
    assert st.last_error is not None and st.last_error.code == "ingetrokken_door_hub"
    page = client.get("/").text
    assert "Koppeling ingetrokken" in page and 'action="/koppelen"' not in page  # geen herkoppelen na intrekking
    resp = client.get("/connector/v1/events?limit=200", headers=linked.service_auth())
    assert resp.status_code == 401 and resp.json()["details"]["reason"] == "revoked"  # fail-closed (EX16)


def test_inbox_401_without_revocation_keeps_link(client: TestClient, linked: FakeHub, worker_ctx: AppContext) -> None:
    """Een 401 op de inbox is pas een intrekking als ook services/self 401 geeft."""
    with worker_ctx.sessions() as db:
        emit(db, "catalog.test.event", "test", "1", {}, "00000000-0000-0000-0000-000000000000")
        db.commit()
    original = linked.handler

    def inbox_401(request):  # type: ignore[no-untyped-def]
        if request.url.path == "/api/v1/events/inbox":
            import httpx

            return httpx.Response(401, json={"code": "unauthorized"})
        return original(request)

    linked.handler = inbox_401  # type: ignore[method-assign]
    _due_now(worker_ctx)
    assert push_outbox(worker_ctx) == 0
    assert worker_ctx.store.read().state == "gekoppeld"
    assert ("GET", "/api/v1/services/self") in linked.requests


def test_hub_link_whitelist(client: TestClient, linked: FakeHub, worker_ctx: AppContext) -> None:
    for headers in (linked.service_auth(), linked.auth(roles=["viewer"])):
        resp = client.get("/connector/v1/hub-link", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "gekoppeld" and body["koppelen_bezig"] is False
        assert body["keyset_fingerprint"] == linked.fingerprint()
        assert body["pinned_kids"] == {
            "jwt": [k["kid"] for k in linked.jwks()["keys"]],
            "policy": [linked.policy_jwk()["kid"]],
        }
        assert body["rotation_sequence"] == 0 and body["service_token_last4"] == linked.last_service_token[-4:]  # type: ignore[index]
        text = resp.text
        assert linked.last_service_token not in text and "secret_sha256" not in text and 'service_token"' not in text
    assert client.get("/connector/v1/hub-link", headers=linked.auth(roles=["kind"])).status_code == 403
    assert client.get("/catalog/v1/hub-link", headers=linked.auth()).status_code == 404  # verhuisd (AS6)


def test_unlink_by_hub_then_everything_refused(client: TestClient, linked: FakeHub, worker_ctx: AppContext) -> None:
    assert client.post("/connector/v1/hub-link/unlink", headers=linked.service_auth()).status_code == 400  # geen key
    op = client.post("/connector/v1/hub-link/unlink", headers={**linked.auth(roles=["operator"]), **idem()})
    assert op.status_code == 403
    resp = client.post("/connector/v1/hub-link/unlink", headers={**linked.service_auth(), **idem()})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "ontkoppeld" and resp.json()["service_token_last4"] is None
    st = worker_ctx.store.read()
    assert st.state == "ontkoppeld" and st.service_token is None and st.pins is None and st.previous_pins is not None
    # daarna weigert de dienst elke Hub-JWT; een herhaalde unlink krijgt 401 not_linked (telt voor de Hub als geslaagd)
    again = client.post("/connector/v1/hub-link/unlink", headers={**linked.service_auth(), **idem()})
    assert again.status_code == 401 and again.json()["details"]["reason"] == "not_linked"
    assert client.get("/connector/v1/discover", headers=linked.auth()).json()["details"]["reason"] == "not_linked"
    page = client.get("/").text
    assert "Ontkoppeld" in page and 'action="/koppelen"' not in page
    assert push_outbox(worker_ctx) == 0  # geen token meer: niets naar de Hub


def test_unlink_by_admin_user(client: TestClient, linked: FakeHub) -> None:
    resp = client.post("/connector/v1/hub-link/unlink", headers={**linked.auth(roles=["admin"]), **idem()})
    assert resp.status_code == 200 and resp.json()["state"] == "ontkoppeld"
