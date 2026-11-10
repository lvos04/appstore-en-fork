"""Connectorcontract §6 (incl. hub-link en unlink, §3.8), vaste checknamen en versheid (§6, §6.2) en outbox/events §7
(contract 1.3)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update

from appstore import heartbeat
from appstore.api.common import AppContext
from appstore.events import emit
from appstore.hublink_store import LinkState
from appstore.models import CatalogSource, OutboxEvent, SyncJob
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
    assert d["limits"]["max_age_s"] == {"health": 900, "worker": 120, "jobs": 900, "existing_app": 900}
    h = client.get("/connector/v1/health", headers=linked.service_auth()).json()
    names = ("database", "schema", "worker", "hub_link", "jobs", "catalog_sources", "outbox")
    assert [c["name"] for c in h["checks"]] == list(names)  # vaste namen database/worker/jobs/hub_link (§6 regel 3)
    assert all(set(c) == {"name", "status", "observed_at", "detail"} and c["observed_at"] for c in h["checks"])
    assert all(isinstance(c["detail"], dict) and c["detail"]["tekst"] for c in h["checks"])
    jobs = next(c for c in h["checks"] if c["name"] == "jobs")["detail"]
    assert {"wachtrij", "bezig", "laatste_job_klaar"} <= set(jobs) and jobs["wachtrij"] == 0
    worker = next(c for c in h["checks"] if c["name"] == "worker")
    assert worker["observed_at"] == worker["detail"]["laatste_hartslag"]  # observed_at = laatste hartslag
    # gekoppeld, bevestigd, worker leeft, ingebouwde catalogus gesynchroniseerd, geen dead-letter → pas dan 'healthy'
    assert {c["name"]: c["status"] for c in h["checks"]} == dict.fromkeys(names, "healthy")
    assert (h["status"], h["reason_code"]) == ("healthy", "ok")


def test_health_worker_without_fresh_heartbeat_is_unknown_never_healthy(
    client: TestClient, linked: FakeHub, worker_ctx: AppContext
) -> None:
    """§6.2: een check zonder verse meting is unknown met meting_verouderd; geen hartslag = unknown."""
    svc = linked.service_auth()
    path = heartbeat.path(worker_ctx.settings.data_dir)
    old = (utcnow() - timedelta(seconds=heartbeat.MAX_AGE_S + 30)).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps({"laatste_hartslag": old}), encoding="utf-8")
    h = client.get("/connector/v1/health", headers=svc).json()
    worker = next(c for c in h["checks"] if c["name"] == "worker")
    assert worker["status"] == "unknown" and worker["detail"]["reason_code"] == "meting_verouderd"
    assert (h["status"], h["reason_code"]) == ("unknown", "meting_verouderd")
    path.unlink()
    h = client.get("/connector/v1/health", headers=svc).json()
    worker = next(c for c in h["checks"] if c["name"] == "worker")
    assert worker["status"] == "unknown" and worker["observed_at"] is None and h["status"] == "unknown"


def test_health_jobs_queue_that_does_not_shrink_is_degraded(
    client: TestClient, linked: FakeHub, worker_ctx: AppContext
) -> None:
    import uuid

    with worker_ctx.sessions() as db:
        src = db.scalars(select(CatalogSource)).first()
        assert src is not None
        db.add(
            SyncJob(
                id=uuid.uuid4(),
                source_id=src.id,
                status="queued",
                requested_by="test",
                requested_at=utcnow() - timedelta(minutes=20),
            )
        )
        db.commit()
    h = client.get("/connector/v1/health", headers=linked.service_auth()).json()
    jobs = next(c for c in h["checks"] if c["name"] == "jobs")
    assert jobs["status"] == "degraded" and jobs["detail"]["wachtrij"] == 1
    assert (h["status"], h["reason_code"]) == ("degraded", "jobs")


def test_health_total_is_worst_check_and_unknown_is_never_healthy(
    client: TestClient, linked: FakeHub, worker_ctx: AppContext
) -> None:
    """Review P1: het totaal is de slechtste check (healthy < unknown < degraded < down), zoals de statuspagina;
    een onbekende check mag het totaal nooit 'healthy' laten (contract 1.2 §1, 'onbekend nooit groen')."""
    svc = linked.service_auth()
    # een ingeschakelde bron die nog nooit is gesynchroniseerd → onbekend, niet gezond
    with worker_ctx.sessions() as db:
        db.execute(update(CatalogSource).values(last_sync_status="never"))
        db.commit()
    h = client.get("/connector/v1/health", headers=svc).json()
    assert (h["status"], h["reason_code"]) == ("unknown", "catalog_sources")

    # gekoppeld maar (nog) niet bevestigd door de Hub → onbekend
    def unconfirm(st: LinkState) -> LinkState:
        st.confirmed_at = None
        return st

    worker_ctx.store.update(unconfirm)
    h = client.get("/connector/v1/health", headers=svc).json()
    assert h["status"] == "unknown" and h["reason_code"] in ("hub_link", "catalog_sources")
    # een mislukte bron (degraded) weegt zwaarder dan onbekend
    with worker_ctx.sessions() as db:
        db.execute(update(CatalogSource).values(last_sync_status="failed"))
        db.commit()
    h = client.get("/connector/v1/health", headers=svc).json()
    assert (h["status"], h["reason_code"]) == ("degraded", "catalog_sources")


def test_health_schema_behind_is_down(client: TestClient, linked: FakeHub, database_url: str) -> None:
    """Een openstaande migratie is geen gezonde dienst (zelfde meting als /readyz en de statuspagina)."""
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            conn.execute(text("UPDATE alembic_version SET version_num = '0001'"))
        h = client.get("/connector/v1/health", headers=linked.service_auth()).json()
        schema = next(c for c in h["checks"] if c["name"] == "schema")
        assert schema["status"] == "down" and "0001" in schema["detail"]["tekst"]
        assert (h["status"], h["reason_code"]) == ("down", "schema")
        assert client.get("/readyz").status_code == 503
    finally:
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": current})
        engine.dispose()


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
    assert "service.hub_link.linked" in types and "catalog.manifest.added" in types
    assert "catalog.manifest.rejected" in types and "catalog.source_sync.succeeded" in types
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
    assert "Ingetrokken" in page and "koppeling ingetrokken" in page and 'action="/koppelen"' not in page
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


#: witte lijst van de publieke koppelstatus (contract 1.3 §3.5)
WHITELIST = {
    "state",
    "koppelen_bezig",
    "hub_url",
    "service_id",
    "via",
    "linked_at",
    "first_linked_at",
    "keyset_fingerprint",
    "service_token_last4",
    "last_error",
}


def test_hub_link_whitelist(client: TestClient, linked: FakeHub, worker_ctx: AppContext) -> None:
    for headers in (linked.service_auth(), linked.auth(roles=["viewer"])):
        resp = client.get("/connector/v1/hub-link", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        # §3.5: de witte lijst is een maximum; §6 voegt alleen pinned_kids en rotation_sequence toe, §6.2 observed_at
        assert set(body) == WHITELIST | {"pinned_kids", "rotation_sequence", "observed_at"}, sorted(body)
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
