"""Dienst-E2E (Sim-getest): enrollment met eenmalig token → JWT-toegang → ingebouwde catalogus → EX47 via HTTP →
events in de Hub-inbox. Echte processen en echte HTTP, gesimuleerde Hub; geen echte NAS."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from conftest import FIXTURES, wait_for
from tests.fakehub import FakeHub


def _linked(url: str) -> bool:
    return "Gekoppeld" in httpx.get(f"{url}/", timeout=5).text


def test_enrollment_and_catalog_flow(service: str, hub: FakeHub) -> None:
    wait_for(lambda: _linked(service), 60)
    assert hub.enroll_requests and hub.enroll_requests[0]["enrollment_token"] == "e2e-enrollment-token-0123456789"
    auth = hub.auth(roles=["operator"])
    entries = wait_for(
        lambda: (
            (r := httpx.get(f"{service}/catalog/v1/entries", headers=auth, timeout=5).json())
            and len(r.get("items", [])) == 2
            and r
        ),
        60,
    )
    assert [e["app_id"] for e in entries["items"]] == ["appstore-en-fork", "voorbeeld-web"]
    # EX47 via de echte HTTP-API: geweigerd vóór uitvoering
    ex47 = (FIXTURES / "ex47-privileged-hostmount.manifest.yaml").read_text()
    resp = httpx.post(
        f"{service}/catalog/v1/preview",
        headers=auth,
        timeout=10,
        json={
            "manifest_yaml": ex47,
            "target": {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"},
            "inventory": {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []},
        },
    )
    body = resp.json()
    assert resp.status_code == 200 and body["ready_for_install"] is False and body["translation"] is None
    assert body["executed"] is False
    # geldige app: getypte app.create-waarden, niets uitgevoerd
    ok = httpx.post(
        f"{service}/catalog/v1/preview",
        headers=auth,
        timeout=10,
        json={
            "app_id": "voorbeeld-web",
            "parameters": {
                "config_path": "/mnt/p1/apps/v",
                "media_path": "/mnt/p1/media",
                "public_url": "http://nas.test:30999",
                "timezone": "Etc/UTC",
            },
            "target": {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"},
            "inventory": {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []},
        },
    ).json()
    assert ok["ready_for_install"] is True and ok["translation"]["payload"]["catalog_app"] == "ix-app"
    # events komen bij de Hub aan (outbox-push met service-token)
    wait_for(lambda: any(e["event_type"] == "catalog.manifest.added" for e in hub.inbox), 30)
    assert all(a == f"Bearer {hub.issued_token}" for a in hub.inbox_auth)
    # connector met service-token
    svc = {"Authorization": f"Bearer {hub.issued_token}"}
    assert httpx.get(f"{service}/connector/v1/discover", headers=svc, timeout=5).json()["capabilities"] == ["read"]
    assert httpx.post(f"{service}/connector/v1/apply", headers=svc, timeout=5).status_code == 501


def test_unauthenticated_and_wrong_audience(service: str, hub: FakeHub) -> None:
    assert httpx.get(f"{service}/catalog/v1/entries", timeout=5).status_code == 401
    wrong = httpx.get(f"{service}/catalog/v1/entries", headers=hub.auth(aud="home-control-hub"), timeout=5)
    assert wrong.status_code == 401 and wrong.json()["code"] == "unauthorized"
