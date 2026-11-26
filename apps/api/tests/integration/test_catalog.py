"""Catalogus-API: ingebouwde bron, entries, versies, valideren, voorbeeld (EX47) en bronnenbeheer."""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from appstore.api.common import AppContext
from appstore.manifest.canonical import sha256_digest
from appstore.manifest.provenance import b64url, key_id, raw_public, sign_data, verify_data
from appstore.models import ManifestVersion
from appstore.worker import run_once, startup
from tests.conftest import FIXTURES
from tests.fakehub import FakeHub
from tests.integration.conftest import idem, pair

pytestmark = pytest.mark.integration
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.test:30999",
    "timezone": "Europe/Amsterdam",
}


def _sync_bundled(worker_ctx: AppContext) -> None:
    startup(worker_ctx)
    assert run_once(worker_ctx)["jobs"] == 1


@pytest.fixture
def linked(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> TestClient:
    pair(client, hub)
    _sync_bundled(worker_ctx)
    return client


def test_bundled_sync_entries_and_rejections(linked: TestClient, hub: FakeHub) -> None:
    resp = linked.get("/catalog/v1/entries", headers=hub.auth(roles=["viewer"]))
    assert resp.status_code == 200
    ids = [e["app_id"] for e in resp.json()["items"]]
    assert ids == ["appstore-en-fork", "voorbeeld-catalogus", "voorbeeld-web"]  # EX47 geweigerd
    src = linked.get("/catalog/v1/sources", headers=hub.auth()).json()["items"][0]
    assert src["kind"] == "bundled" and src["last_sync"]["status"] == "ok"
    # paginering
    page = linked.get("/catalog/v1/entries?limit=1", headers=hub.auth()).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    page2 = linked.get(f"/catalog/v1/entries?limit=1&cursor={page['next_cursor']}", headers=hub.auth()).json()
    assert page2["items"][0]["app_id"] == "voorbeeld-catalogus"
    te_veel = linked.get("/catalog/v1/entries?limit=500", headers=hub.auth())
    assert te_veel.status_code == 422 and te_veel.json()["code"] == "validation_error"
    assert te_veel.json()["details"]["reason"] == "limit_invalid"
    assert linked.get("/catalog/v1/entries?q=web", headers=hub.auth()).json()["items"][0]["app_id"] == "voorbeeld-web"
    assert (
        linked.get("/catalog/v1/entries?category=photos", headers=hub.auth()).json()["items"][0]["app_id"]
        == "voorbeeld-catalogus"
    )


def test_entry_detail_requirements_and_versions(linked: TestClient, hub: FakeHub) -> None:
    detail = linked.get("/catalog/v1/entries/voorbeeld-web", headers=hub.auth(roles=["viewer"]))
    assert detail.status_code == 200 and detail.headers["etag"]
    body = detail.json()
    req = body["requirements"]
    assert req["privileges"]["privileged"] is False and req["gpu"] == "intel_render"
    assert req["storage"][0]["required_dataset_role"] == "voorbeeld_config"
    assert req["risk"] == {"install": "R2", "update": "R2", "uninstall": "R2"}
    assert body["latest_version"]["valid"] is True
    versions = linked.get("/catalog/v1/entries/voorbeeld-web/versions", headers=hub.auth()).json()["items"]
    assert len(versions) == 1
    v = linked.get(f"/catalog/v1/versions/{versions[0]['id']}", headers=hub.auth()).json()
    assert v["provenance_verified"] is True
    assert v["provenance"]["record"]["source"]["kind"] == "bundled"
    assert v["provenance"]["record"]["manifest_sha256"] == v["manifest_sha256"]
    assert linked.get("/catalog/v1/entries/bestaat-niet", headers=hub.auth()).status_code == 404


def test_version_history_on_change(linked: TestClient, hub: FakeHub, worker_ctx: AppContext, bundled_dir: Path) -> None:
    path = bundled_dir / "valid-custom-upstream.manifest.yaml"
    path.write_text(path.read_text().replace('version: "1.27.3"', 'version: "1.27.4"'))
    _sync_bundled(worker_ctx)
    versions = linked.get("/catalog/v1/entries/voorbeeld-web/versions", headers=hub.auth()).json()["items"]
    assert [v["version"] for v in versions] == ["1.27.4", "1.27.3"]
    assert versions[1]["superseded_at"] is not None and versions[0]["superseded_at"] is None
    page = linked.get("/catalog/v1/entries/voorbeeld-web/versions?limit=1", headers=hub.auth()).json()
    nxt = linked.get(
        f"/catalog/v1/entries/voorbeeld-web/versions?limit=1&cursor={page['next_cursor']}", headers=hub.auth()
    ).json()
    assert nxt["items"][0]["version"] == "1.27.3"


def test_validate_endpoint_and_evidence(linked: TestClient, hub: FakeHub) -> None:
    text = (FIXTURES / "manifests" / "ex47-privileged-hostmount.manifest.yaml").read_text()
    resp = linked.post("/catalog/v1/validate", json={"manifest_yaml": text}, headers=hub.auth(roles=["viewer"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False and "privileged_forbidden" in [f["code"] for f in body["findings"]]
    assert "ONGELDIG" in body["text_nl"]
    ev = linked.get(f"/connector/v1/evidence/{body['evidence_id']}", headers=hub.auth()).json()
    assert ev["kind"] == "manifest_validation" and ev["summary"]["valid"] is False
    bad = linked.post("/catalog/v1/validate", json={}, headers=hub.auth())
    assert bad.status_code == 422 and bad.json()["code"] == "validation_error" and bad.json()["details"]["fouten"]
    data = yaml.safe_load((FIXTURES / "manifests" / "valid-catalog.manifest.yaml").read_text())
    ok = linked.post("/catalog/v1/validate", json={"manifest": data}, headers=hub.auth())
    assert ok.json()["valid"] is True


def test_ex47_preview_refused_before_execution(linked: TestClient, hub: FakeHub) -> None:
    """EX47 via de API: deployment geweigerd vóór uitvoering; er komt geen app.create-payload."""
    text = (FIXTURES / "manifests" / "ex47-privileged-hostmount.manifest.yaml").read_text()
    resp = linked.post(
        "/catalog/v1/preview",
        headers=hub.auth(roles=["operator"]),
        json={
            "manifest_yaml": text,
            "parameters": {},
            "target": {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"},
            "inventory": {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready_for_install"] is False and body["translation"] is None and body["executed"] is False
    assert "vóór uitvoering" in body["blocked_reason_nl"]
    ev = linked.get(f"/connector/v1/evidence/{body['evidence_id']}", headers=hub.auth()).json()
    assert "privileged_forbidden" in ev["summary"]["blocking_codes"]


CATALOG_13 = "valid-catalog-1-3.manifest.yaml"


def _sso_body(**source: Any) -> dict[str, Any]:
    return {
        **source,
        "parameters": {"timezone": "Etc/UTC", "data_path": "/mnt/p1/sso"},
        "target": {"truenas_version": "25.10.1"},
        "inventory": {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []},
    }


def _add_bundled(worker_ctx: AppContext, bundled_dir: Path, name: str) -> None:
    shutil.copy(FIXTURES / "manifests" / name, bundled_dir / name)
    _sync_bundled(worker_ctx)


def test_preview_secret_only_as_vault_reference_never_echoed(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext, bundled_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """C1.3-ASF-14 via de API: een geheim dat de Hub maakt staat alleen als vault-verwijzing in het antwoord (manifest
    uit de catalogus, via app_id of version_id); een opgegeven waarde wordt geweigerd en staat niet in het antwoord,
    het bewijs of de log (V-094)."""
    _add_bundled(worker_ctx, bundled_dir, CATALOG_13)
    secret = "Geheim-" + "Qw8" * 12
    body = _sso_body(app_id="voorbeeld-sso")
    with caplog.at_level(logging.DEBUG):
        ok = linked.post("/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=body)
        by_version = linked.post(
            "/catalog/v1/preview",
            headers=hub.auth(roles=["operator"]),
            json=_sso_body(version_id=ok.json()["version_id"]),
        )
        refused = linked.post(
            "/catalog/v1/preview",
            headers=hub.auth(roles=["operator"]),
            json={**body, "parameters": {**body["parameters"], "db_password": secret}},
        )
        ev = linked.get(f"/connector/v1/evidence/{refused.json()['evidence_id']}", headers=hub.auth())
    assert ok.status_code == 200 and ok.json()["ready_for_install"] is True, ok.json()
    translation = ok.json()["translation"]
    assert translation["payload"]["values"]["voorbeeld"] == {"db_password": "vault://app/voorbeeld-sso/db_password"}
    assert translation["vault_refs"] == [
        {
            "path": "values.voorbeeld.db_password",
            "parameter": "db_password",
            "ref": "vault://app/voorbeeld-sso/db_password",
        }
    ]
    assert by_version.status_code == 200 and by_version.json()["ready_for_install"] is True
    assert by_version.json()["translation"]["vault_refs"] == translation["vault_refs"]
    assert refused.status_code == 200 and refused.json()["ready_for_install"] is False
    assert refused.json()["translation"]["payload"] is None and refused.json()["translation"]["vault_refs"] == []
    assert secret not in refused.text
    assert ev.status_code == 200 and secret not in ev.text
    assert "parameter_value_not_allowed" in ev.json()["summary"]["blocking_codes"]
    assert secret not in caplog.text


def _blocking(resp: Any) -> set[str]:
    return {f["code"] for f in resp.json()["translation"]["findings"] if f["severity"] == "error"}


def test_preview_of_a_loose_manifest_never_gets_a_vault_reference(linked: TestClient, hub: FakeHub) -> None:
    """R-VEILIG-1: de app_id van een los manifest (``manifest_yaml``) staat niet vast. Een operator kan zo geen
    verwijzing naar het geheim van een andere app (hier ``voorbeeld-catalogus``) in de env van een willekeurige image
    laten zetten: geen verwijzing, niet installeerbaar."""
    data = yaml.safe_load((FIXTURES / "manifests" / "valid-custom-upstream.manifest.yaml").read_text())
    data["app_id"] = "voorbeeld-catalogus"
    data["parameters"].append(
        {
            "name": "db_password",
            "type": "string",
            "source": "generated",
            "secret": True,
            "required": True,
            "description_nl": "Geheim dat de Hub maakt.",
        }
    )
    data["custom_app"]["env"].append({"name": "EXFIL_TOKEN", "value_from": {"param": "db_password"}})
    inventory = {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []}
    resp = linked.post(
        "/catalog/v1/preview",
        headers=hub.auth(roles=["operator"]),
        json={
            "manifest_yaml": yaml.safe_dump(data, sort_keys=False),
            "parameters": PARAMS,
            "target": {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"},
            "inventory": inventory,
        },
    )
    assert resp.status_code == 200 and resp.json()["validation"]["valid"] is True
    assert resp.json()["ready_for_install"] is False
    assert resp.json()["translation"]["payload"] is None and resp.json()["translation"]["vault_refs"] == []
    assert "parameter_value_not_allowed" in _blocking(resp) and "vault://" not in resp.text
    loose = linked.post(
        "/catalog/v1/preview",
        headers=hub.auth(roles=["operator"]),
        json=_sso_body(manifest_yaml=(FIXTURES / "manifests" / CATALOG_13).read_text()),
    )
    assert loose.json()["ready_for_install"] is False and loose.json()["translation"]["vault_refs"] == []
    assert "parameter_value_not_allowed" in _blocking(loose) and "vault://" not in loose.text


@pytest.mark.parametrize("change", [{"valid": False}, {"entry_id": None}])
def test_preview_of_a_version_outside_the_catalog_gets_no_vault_reference(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext, bundled_dir: Path, change: dict[str, Any]
) -> None:
    """R-VEILIG-1: alleen een geldige versie die bij een catalogus-entry met dezelfde app_id hoort, legt de app_id
    vast. Een geweigerde of losse versie (bijvoorbeeld ``app_id_claimed_by_other_source``) krijgt geen verwijzing."""
    _add_bundled(worker_ctx, bundled_dir, CATALOG_13)
    version_id = linked.post(
        "/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_sso_body(app_id="voorbeeld-sso")
    ).json()["version_id"]
    with Session(worker_ctx.engine) as s:
        s.execute(update(ManifestVersion).where(ManifestVersion.id == uuid.UUID(version_id)).values(**change))
        s.commit()
    resp = linked.post(
        "/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json=_sso_body(version_id=version_id)
    )
    assert resp.status_code == 200 and resp.json()["ready_for_install"] is False
    assert resp.json()["translation"]["vault_refs"] == [] and "parameter_value_not_allowed" in _blocking(resp)
    assert "vault://" not in resp.text


def test_preview_ready_and_unknowns(linked: TestClient, hub: FakeHub, own_manifest_without_digest: str) -> None:
    inventory = {
        "observed_at": datetime.now(UTC).isoformat(),
        "source": "simulator",
        "apps": [
            {
                "name": "jellyfin",
                "used_ports": [
                    {
                        "container_port": 8096,
                        "protocol": "tcp",
                        "host_ports": [{"host_port": 8096, "host_ip": "0.0.0.0"}],
                    }
                ],
            }
        ],
    }
    body = {
        "app_id": "voorbeeld-web",
        "parameters": PARAMS,
        "target": {"truenas_version": "25.10.1", "ix_app_version": "1.4.8"},
        "inventory": inventory,
    }
    ready = linked.post("/catalog/v1/preview", json=body, headers=hub.auth(roles=["operator"])).json()
    assert ready["ready_for_install"] is True, ready
    payload = ready["translation"]["payload"]
    assert payload["catalog_app"] == "ix-app" and payload["custom_app"] is False
    assert ready["translation"]["preview_hash"].startswith("sha256:")
    assert ready["executed"] is False
    # zonder inventaris: onbekend, niet gereed
    unknown = linked.post(
        "/catalog/v1/preview", json={**body, "inventory": None}, headers=hub.auth(roles=["operator"])
    ).json()
    assert unknown["ready_for_install"] is False and unknown["conflicts"]["status"] == "unknown"
    # zonder gemeten versies: niet gereed
    noversion = linked.post(
        "/catalog/v1/preview", json={**body, "target": {}}, headers=hub.auth(roles=["operator"])
    ).json()
    assert noversion["ready_for_install"] is False
    assert {"truenas_version_unknown", "ix_app_version_unknown"} <= {
        f["code"] for f in noversion["translation"]["findings"]
    }
    # viewer mag geen voorbeeld maken
    assert linked.post("/catalog/v1/preview", json=body, headers=hub.auth(roles=["viewer"])).status_code == 403
    own_body = {
        "parameters": {"appdata_path": "/mnt/p1/apps/appstore", "timezone": "Etc/UTC"},
        "target": body["target"],
        "inventory": inventory,
    }
    pinned = linked.post(
        "/catalog/v1/preview", headers=hub.auth(roles=["operator"]), json={**own_body, "app_id": "appstore-en-fork"}
    ).json()
    assert pinned["ready_for_install"] is True and pinned["executed"] is False
    assert pinned["translation"]["digest_readback"]["method"] == "app.image.query"
    unpinned = linked.post(
        "/catalog/v1/preview",
        headers=hub.auth(roles=["operator"]),
        json={**own_body, "manifest_yaml": own_manifest_without_digest},
    ).json()
    assert unpinned["ready_for_install"] is False and unpinned["executed"] is False
    assert "digest_not_pinned" in {f["code"] for f in unpinned["translation"]["findings"]}


def test_preview_contract_1_2_readback_gpu_and_unavailable_version(linked: TestClient, hub: FakeHub) -> None:
    """M3/M5 via de API: digest-readback, GPU alleen met gemeten render-node, verdwenen ix-app-versie blokkeert."""
    inventory = {"observed_at": datetime.now(UTC).isoformat(), "source": "simulator", "apps": []}
    target = {"truenas_version": "25.10.1", "ix_app_version": "1.4.8", "available_versions": ["1.4.8"]}
    body = {"app_id": "voorbeeld-web", "parameters": PARAMS, "target": target, "inventory": inventory}
    op = hub.auth(roles=["operator"])
    ok = linked.post("/catalog/v1/preview", json=body, headers=op).json()
    assert ok["ready_for_install"] is True, ok
    readback = ok["translation"]["digest_readback"]
    assert readback["method"] == "app.image.query" and readback["expected_digest"].startswith("sha256:")
    assert ok["translation"]["payload"]["values"]["image"]["tag"] == readback["form_tag"]
    gpu = linked.post(
        "/catalog/v1/preview",
        json={**body, "parameters": {**PARAMS, "gpu": "intel_render"}},
        headers=hub.auth(roles=["operator"]),
    ).json()
    assert gpu["ready_for_install"] is False
    assert "gpu_not_measured" in {f["code"] for f in gpu["translation"]["findings"]}
    gone = linked.post(
        "/catalog/v1/preview",
        json={**body, "target": {**target, "available_versions": ["1.5.0"]}},
        headers=hub.auth(roles=["operator"]),
    ).json()
    assert gone["ready_for_install"] is False
    assert "version_unavailable" in {f["code"] for f in gone["translation"]["findings"]}


def test_sources_crud_idempotency_and_etag(linked: TestClient, hub: FakeHub) -> None:
    body = {"name": "eigen-bron", "kind": "http", "url": "http://bron.test/catalogus/index.json"}
    key = idem()
    first = linked.post("/catalog/v1/sources", json=body, headers={**hub.auth(), **key})
    assert first.status_code == 201, first.text
    again = linked.post("/catalog/v1/sources", json=body, headers={**hub.auth(), **key})
    assert again.status_code == 201 and again.json() == first.json() and again.headers["idempotent-replay"] == "true"
    conflict = linked.post("/catalog/v1/sources", json={**body, "name": "ander"}, headers={**hub.auth(), **key})
    assert conflict.status_code == 422 and conflict.json()["code"] == "idempotency_key_reused"
    missing = linked.post("/catalog/v1/sources", json=body, headers=hub.auth())
    assert missing.status_code == 400 and missing.json()["code"] == "idempotency_key_required"
    dup = linked.post("/catalog/v1/sources", json=body, headers={**hub.auth(), **idem()})
    assert dup.status_code == 409 and dup.json()["code"] == "source_name_taken"
    operator = linked.post(
        "/catalog/v1/sources", json={**body, "name": "x"}, headers={**hub.auth(roles=["operator"]), **idem()}
    )
    assert operator.status_code == 403
    sid = first.json()["id"]
    no_match = linked.patch(f"/catalog/v1/sources/{sid}", json={"enabled": False}, headers={**hub.auth(), **idem()})
    assert no_match.status_code == 428
    stale = linked.patch(
        f"/catalog/v1/sources/{sid}", json={"enabled": False}, headers={**hub.auth(), **idem(), "If-Match": 'W/"99"'}
    )
    assert stale.status_code == 412
    ok = linked.patch(
        f"/catalog/v1/sources/{sid}",
        json={"enabled": False},
        headers={**hub.auth(), **idem(), "If-Match": first.json()["etag"]},
    )
    assert ok.status_code == 200 and ok.json()["enabled"] is False and ok.headers["etag"] == 'W/"2"'
    sync = linked.post(f"/catalog/v1/sources/{sid}/sync", headers={**hub.auth(), **idem()})
    assert sync.status_code == 409 and sync.json()["code"] == "source_disabled"


def test_source_url_rules(linked: TestClient, hub: FakeHub) -> None:
    for url, code in [
        ("https://gebruiker:geheim@bron.test/x.json", "source_url_forbidden"),
        ("ftp://bron.test/x.json", "source_url_forbidden"),
    ]:
        resp = linked.post(
            "/catalog/v1/sources",
            json={"name": f"b{uuid.uuid4().hex[:6]}", "kind": "http", "url": url},
            headers={**hub.auth(), **idem()},
        )
        assert resp.status_code == 422 and resp.json()["code"] == code


def test_http_source_sync_hash_and_etag(linked: TestClient, hub: FakeHub, worker_ctx: AppContext) -> None:
    text = (FIXTURES / "manifests" / "valid-custom-upstream.manifest.yaml").read_text()
    hub.source_files["http-app.manifest.yaml"] = text.replace("app_id: voorbeeld-web", "app_id: http-app").encode()
    created = linked.post(
        "/catalog/v1/sources",
        json={"name": "http-bron", "kind": "http", "url": "http://bron.test/catalogus/index.json"},
        headers={**hub.auth(), **idem()},
    ).json()
    key = idem()
    job = linked.post(f"/catalog/v1/sources/{created['id']}/sync", headers={**hub.auth(roles=["operator"]), **key})
    assert job.status_code == 202
    again = linked.post(f"/catalog/v1/sources/{created['id']}/sync", headers={**hub.auth(roles=["operator"]), **key})
    assert again.json()["id"] == job.json()["id"]  # één taak, geen tweede uitvoering
    run_once(worker_ctx)
    done = linked.get(f"/catalog/v1/jobs/{job.json()['id']}", headers=hub.auth()).json()
    assert done["status"] == "succeeded" and done["result"]["added"] == ["http-app 1.27.3"]
    # zelfde ETag → 304 → niets veranderd
    linked.post(f"/catalog/v1/sources/{created['id']}/sync", headers={**hub.auth(), **idem()})
    run_once(worker_ctx)
    src = linked.get(f"/catalog/v1/sources/{created['id']}", headers=hub.auth()).json()
    assert src["last_sync"]["status"] == "ok"
    # app_id van een andere bron overschrijven → geweigerd
    hub.source_etag = '"v2"'
    hub.source_files["kaper.manifest.yaml"] = text.encode()  # app_id voorbeeld-web hoort bij de ingebouwde bron
    linked.post(f"/catalog/v1/sources/{created['id']}/sync", headers={**hub.auth(), **idem()})
    run_once(worker_ctx)
    jobs_result = linked.get(f"/catalog/v1/sources/{created['id']}", headers=hub.auth()).json()
    assert jobs_result["last_sync"]["status"] == "ok"
    entry = linked.get("/catalog/v1/entries/voorbeeld-web", headers=hub.auth()).json()
    assert entry["source"]["kind"] == "bundled"
    # hash klopt niet met de index → bron mislukt, catalogus ongewijzigd
    hub.source_etag = '"v3"'
    original = hub._source

    def tampered(request: Any) -> Any:
        resp = original(request)
        if request.url.path.endswith("http-app.manifest.yaml"):
            import httpx

            return httpx.Response(200, content=resp.content + b"\n# gewijzigd\n")
        return resp

    hub._source = tampered  # type: ignore[method-assign]
    linked.post(f"/catalog/v1/sources/{created['id']}/sync", headers={**hub.auth(), **idem()})
    run_once(worker_ctx)
    src = linked.get(f"/catalog/v1/sources/{created['id']}", headers=hub.auth()).json()
    assert src["last_sync"]["status"] == "failed" and "sha256" in src["last_sync"]["error"]


def _git_repo(path: Path, files: dict[str, str]) -> str:
    from dulwich import porcelain

    repo = porcelain.open_repo(str(path)) if (path / ".git").exists() else porcelain.init(str(path))
    for name, content in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(content)
        porcelain.add(repo, [str(path / name)])
    sha = porcelain.commit(repo, message=b"catalogus", author=b"Test <t@test>", committer=b"Test <t@test>", sign=False)
    repo.close()
    return sha.decode()


def test_git_source_with_signatures(linked: TestClient, hub: FakeHub, worker_ctx: AppContext, tmp_path: Path) -> None:
    publisher = Ed25519PrivateKey.generate()
    base = (FIXTURES / "manifests" / "valid-custom-upstream.manifest.yaml").read_text()
    signed_text = base.replace("app_id: voorbeeld-web", "app_id: git-getekend")
    unsigned_text = base.replace("app_id: voorbeeld-web", "app_id: git-ongetekend")
    sig = sign_data(publisher, yaml.safe_load(signed_text))
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    commit = _git_repo(
        repo_dir,
        {
            "apps/a.manifest.yaml": signed_text,
            "apps/a.manifest.yaml.sig": sig,
            "apps/b.manifest.yaml": unsigned_text,
            "README.md": "niet relevant",
        },
    )
    created = linked.post(
        "/catalog/v1/sources",
        headers={**hub.auth(), **idem()},
        json={
            "name": "git-bron",
            "kind": "git",
            "url": f"file://{repo_dir}",
            "subpath": "apps",
            "pinned_commit": commit,
            "require_signature": True,
            "trusted_public_key": b64url(raw_public(publisher.public_key())),
        },
    )
    assert created.status_code == 201, created.text
    linked.post(f"/catalog/v1/sources/{created.json()['id']}/sync", headers={**hub.auth(), **idem()})
    run_once(worker_ctx)
    src = linked.get(f"/catalog/v1/sources/{created.json()['id']}", headers=hub.auth()).json()
    assert src["last_sync"]["status"] == "ok" and src["last_sync"]["revision"] == commit
    entry = linked.get("/catalog/v1/entries/git-getekend", headers=hub.auth()).json()
    assert entry["latest_version"]["publisher_signature"] == "verified"
    # contract 1.3 §8.1: het catalogusbewijs precies zoals de Hub (ai-server-manager) het controleert
    version = linked.get(f"/catalog/v1/versions/{entry['latest_version']['id']}", headers=hub.auth()).json()
    rec = version["provenance"]["record"]
    assert set(rec) == {
        "app_id",
        "version",
        "entry_id",
        "version_id",
        "manifest_sha256",
        "schema_sha256",
        "validator_version",
        "source",
        "fetched_at",
        "publisher_signature",
        "publisher_key_id",
        "publisher_key_sha256",
        "valid",
        "errors",
        "warnings",
    }
    assert set(rec["source"]) == {"source_id", "kind", "url", "ref", "commit", "path", "subpath"}
    assert str(uuid.UUID(rec["entry_id"])) == rec["entry_id"] == entry["entry_id"] == version["entry_id"]
    assert str(uuid.UUID(rec["version_id"])) == rec["version_id"] == version["id"]
    assert rec["source"]["kind"] == "git" and rec["source"]["commit"] == commit and rec["source"]["subpath"] == "apps"
    assert rec["source"]["path"] == "a.manifest.yaml" and rec["source"]["source_id"] == created.json()["id"]
    assert rec["publisher_signature"] == "verified" and rec["valid"] is True
    assert rec["publisher_key_sha256"] == "sha256:" + hashlib.sha256(raw_public(publisher.public_key())).hexdigest()
    assert sha256_digest(version["manifest"]) == rec["manifest_sha256"]
    store_key = worker_ctx.secrets.identity.public_key()
    assert version["provenance"]["key_id"] == key_id(store_key) and version["provenance_verified"] is True
    assert verify_data(store_key, rec, version["provenance"]["signature"])
    bron = linked.get(f"/catalog/v1/sources/{rec['source']['source_id']}", headers=hub.auth()).json()
    assert (bron["kind"], bron["enabled"], bron["require_signature"]) == ("git", True, True)
    assert (bron["url"], bron["subpath"], bron["pinned_commit"]) == (
        rec["source"]["url"],
        rec["source"]["subpath"],
        rec["source"]["commit"],
    )
    assert linked.get("/catalog/v1/entries/git-ongetekend", headers=hub.auth()).status_code == 404
    rejected = linked.get("/catalog/v1/entries/git-ongetekend/versions", headers=hub.auth()).json()["items"]
    assert rejected[0]["valid"] is False
    rejected_rec = linked.get(f"/catalog/v1/versions/{rejected[0]['id']}", headers=hub.auth()).json()
    assert rejected_rec["entry_id"] is None and rejected_rec["provenance"]["record"]["entry_id"] is None
    assert rejected_rec["provenance"]["record"]["publisher_key_sha256"] is None
    detail = linked.get(f"/catalog/v1/versions/{rejected[0]['id']}", headers=hub.auth()).json()
    assert "signature_missing" in [f["code"] for f in detail["report"]["findings"]]
    # vastgepinde commit klopt niet meer → mislukt, niets gewijzigd
    _git_repo(repo_dir, {"apps/c.manifest.yaml": base.replace("app_id: voorbeeld-web", "app_id: git-nieuw")})
    linked.post(f"/catalog/v1/sources/{created.json()['id']}/sync", headers={**hub.auth(), **idem()})
    run_once(worker_ctx)
    src = linked.get(f"/catalog/v1/sources/{created.json()['id']}", headers=hub.auth()).json()
    assert src["last_sync"]["status"] == "failed" and "vastgepinde" in src["last_sync"]["error"]
    assert linked.get("/catalog/v1/entries/git-nieuw", headers=hub.auth()).status_code == 404


def test_schema_endpoint(linked: TestClient, hub: FakeHub) -> None:
    schema = linked.get("/catalog/v1/schema/manifest-v1", headers=hub.auth(roles=["viewer"])).json()
    assert schema["$schema"].endswith("2020-12/schema")
