"""Dienst-E2E (Sim-getest): enrollment met eenmalig token + HUB_KEY_FINGERPRINT → gepinde sleutels → gebruikers-JWT
en dienst-JWT → ingebouwde catalogus → EX47 via HTTP → events in de Hub-inbox → hub-link → ontkoppelen door de Hub.
Echte processen (API + worker) en echte HTTP, gesimuleerde Hub volgens contract 1.2; geen echte NAS of echte Hub."""

from __future__ import annotations

import json
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from conftest import FIXTURES, wait_for
from tests.fakehub import FakeHub


def _linked(url: str) -> bool:
    return "Gekoppeld" in httpx.get(f"{url}/", timeout=5).text


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def test_enrollment_and_catalog_flow(service: str, hub: FakeHub) -> None:
    wait_for(lambda: _linked(service), 60)
    req = hub.enroll_requests[0]
    assert set(req) == {"app_id", "enrollment_token", "version", "base_url", "public_key_jwk"}
    assert req["enrollment_token"] == hub.e2e_token  # type: ignore[attr-defined]
    assert hub.fingerprint() in httpx.get(f"{service}/", timeout=5).text
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
        headers=hub.auth(roles=["operator"]),
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
    # geldige app: getypte app.create-waarden met digest-readback, niets uitgevoerd
    ok = httpx.post(
        f"{service}/catalog/v1/preview",
        headers=hub.auth(roles=["operator"]),
        timeout=10,
        json={
            "app_id": "voorbeeld-web",
            "parameters": {
                "config_path": "/mnt/p1/apps/v",
                "media_path": "/mnt/p1/media",
                "public_url": "http://nas.test:30999",
                "timezone": "Etc/UTC",
            },
            "target": {"truenas_version": "25.10.1", "ix_app_version": "1.4.8", "available_versions": ["1.4.8"]},
            "inventory": {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []},
        },
    ).json()
    assert ok["ready_for_install"] is True and ok["translation"]["payload"]["catalog_app"] == "ix-app"
    assert ok["translation"]["digest_readback"]["method"] == "app.image.query"
    # events komen bij de Hub aan (outbox-push met het service-token: dienst → Hub, §4.4)
    wait_for(lambda: any(e["event_type"] == "catalog.manifest.added" for e in hub.inbox), 30)
    assert all(a == f"Bearer {hub.last_service_token}" for a in hub.inbox_auth)
    # Hub → dienst met een dienst-JWT (§4.3); het service-token zelf wordt geweigerd
    svc = hub.service_auth()
    assert httpx.get(f"{service}/connector/v1/discover", headers=svc, timeout=5).json()["capabilities"] == ["read"]
    assert httpx.post(f"{service}/connector/v1/apply", headers=hub.service_auth(), timeout=5).status_code == 501
    refused = httpx.get(
        f"{service}/connector/v1/discover", headers={"Authorization": f"Bearer {hub.last_service_token}"}, timeout=5
    )
    assert refused.status_code == 401 and refused.json()["details"]["reason"] == "service_token_not_accepted"


def test_unauthenticated_wrong_audience_and_replay(service: str, hub: FakeHub) -> None:
    wait_for(lambda: _linked(service), 60)
    assert httpx.get(f"{service}/catalog/v1/entries", timeout=5).status_code == 401
    wrong = httpx.get(f"{service}/catalog/v1/entries", headers=hub.auth(aud="home-control-hub"), timeout=5)
    assert wrong.status_code == 401 and wrong.json()["details"]["reason"] == "invalid_token"
    auth = hub.auth(roles=["viewer"])
    first = httpx.post(f"{service}/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"}, timeout=5)
    assert first.status_code == 200
    second = httpx.post(f"{service}/catalog/v1/validate", headers=auth, json={"manifest_yaml": "a: 1"}, timeout=5)
    assert second.status_code == 401 and second.json()["details"]["reason"] == "token_replayed"


def test_zz_hub_link_and_unlink_by_hub(service: str, hub: FakeHub) -> None:
    """Als laatste: GET /connector/v1/hub-link (witte lijst) en ontkoppelen door de Hub."""
    wait_for(lambda: _linked(service), 60)
    link = httpx.get(f"{service}/connector/v1/hub-link", headers=hub.service_auth(), timeout=5).json()
    assert link["state"] == "gekoppeld" and link["keyset_fingerprint"] == hub.fingerprint()
    assert hub.last_service_token not in json.dumps(link)
    resp = httpx.post(f"{service}/connector/v1/hub-link/unlink", headers={**hub.service_auth(), **_idem()}, timeout=5)
    assert resp.status_code == 200 and resp.json()["state"] == "ontkoppeld"
    again = httpx.post(f"{service}/connector/v1/hub-link/unlink", headers={**hub.service_auth(), **_idem()}, timeout=5)
    assert again.status_code == 401 and again.json()["details"]["reason"] == "not_linked"
    page = httpx.get(f"{service}/", timeout=5).text
    assert "Ontkoppeld" in page and 'action="/koppelen"' not in page


def test_hub_link_file_permissions(service: str, hub: FakeHub) -> None:
    wait_for(lambda: "Ontkoppeld" in httpx.get(f"{service}/", timeout=5).text or _linked(service), 60)
    base = Path(hub.e2e_data_dir)  # type: ignore[attr-defined]
    path = base / "secrets" / "hub-link.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
