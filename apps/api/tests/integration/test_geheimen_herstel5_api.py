"""Herstelronde 5 van S-P2-520 via de API en de synchronisatie (ADR-0022, besluit B-1): hooguit 1000 meldingen per
rapport, daarna één vaste fout ``te_veel_meldingen`` (fail-closed). De vorm is die van R-VEILIG-4
(``test_rveilig4_api.py::test_a``): een verzoek van de laagste rol (viewer) met 28 geneste sleutels van 256 tekens met
elk één teken buiten het BMP en daaronder ``password: [1]`` zo vaak als in 256 KiB past (ongeveer 126 000 keer). Op
3227fe1 gaf dat 126 112 meldingen, een antwoord van 281 MiB en ongeveer 2 GiB extra geheugen. De test meet de PIEK
(tracemalloc) en draait onder een eigen grens op het adresgeheugen. Alleen testwaarden."""

from __future__ import annotations

import contextlib
import copy
import gc
import json
import os
import resource
import shutil
import tracemalloc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.manifest.findings import MAX_FINDINGS, TOO_MANY
from appstore.manifest.loader import MAX_DEPTH, MAX_MANIFEST_BYTES
from appstore.worker import run_once, startup
from tests.conftest import FIXTURES, REPO_ROOT, load_fixture
from tests.fakehub import FakeHub
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration
_PAGE = os.sysconf("SC_PAGE_SIZE")
PIEK_GRENS_MIB = 50
ANTWOORD_GRENS = 4 * 2**20
DIEPE_SLEUTELS = [f"{i:02d}" + "n" * 253 + "\U0001f600" for i in range(MAX_DEPTH - 4)]


@pytest.fixture
def linked(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> TestClient:
    pair(client, hub)
    startup(worker_ctx)
    assert run_once(worker_ctx)["jobs"] == 1
    return client


@contextlib.contextmanager
def _geheugengrens(extra_mib: int = 2048) -> Iterator[None]:
    """Zachte grens op het adresgeheugen van dit proces: wat er nu is plus ``extra_mib`` (ruim, want de dienst draait
    hier in eigen threads). Zonder de grens op het aantal meldingen vraagt één verzoek ongeveer 2 GiB extra."""
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    with open("/proc/self/statm", encoding="ascii") as fh:
        grens = int(fh.read().split()[0]) * _PAGE + extra_mib * 2**20
    if hard != resource.RLIM_INFINITY:
        grens = min(grens, hard)
    resource.setrlimit(resource.RLIMIT_AS, (grens, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _grootte(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())


def _bouw(aantal: int) -> dict[str, Any]:
    data = load_fixture("valid-catalog-1-3.manifest.yaml")
    node = data["values"]["voorbeeld"]
    for sleutel in DIEPE_SLEUTELS:
        node[sleutel] = {}
        node = node[sleutel]
    node["password"] = [1] * aantal
    return data


def _r_veilig_4_vorm() -> dict[str, Any]:
    laag, hoog = 1, 200_000
    while laag < hoog:
        midden = (laag + hoog + 1) // 2
        laag, hoog = (midden, hoog) if _grootte(_bouw(midden)) <= MAX_MANIFEST_BYTES else (laag, midden - 1)
    assert laag > 120_000
    return _bouw(laag)


def _begrensd(findings: list[dict[str, Any]]) -> None:
    assert len(findings) == MAX_FINDINGS + 1, len(findings)
    assert [f["code"] for f in findings].count(TOO_MANY) == 1


def test_validate_as_viewer_with_the_r_veilig_4_form_stops_at_the_maximum(linked: TestClient, hub: FakeHub) -> None:
    """R-VEILIG-10, besluit B-1: het JSON-veld ``manifest`` met de vorm van R-VEILIG-4. Nu: een gewoon antwoord met de
    eerste 1000 meldingen en ``te_veel_meldingen``, ongeldig, het antwoord onder 4 MiB en de piek van het hele verzoek
    onder 50 MiB, zonder MemoryError."""
    opwarmen = linked.post("/catalog/v1/validate", json={"manifest": {"x": 1}}, headers=hub.auth(roles=["viewer"]))
    assert opwarmen.status_code == 200, opwarmen.text[:300]
    body = {"manifest": _r_veilig_4_vorm()}
    headers = hub.auth(roles=["viewer"])  # een token is eenmalig
    gc.collect()
    tracemalloc.start()
    try:
        with _geheugengrens():
            resp = linked.post("/catalog/v1/validate", json=body, headers=headers)
        piek = tracemalloc.get_traced_memory()[1] / 2**20
    finally:
        tracemalloc.stop()
    assert resp.status_code == 200, resp.text[:300]
    out = resp.json()
    assert out["valid"] is False and out["strict_valid"] is False
    _begrensd(out["findings"])
    assert TOO_MANY in out["text_nl"]
    assert len(resp.content) < ANTWOORD_GRENS, len(resp.content)
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"


def test_validate_as_viewer_with_manifest_yaml_stops_at_the_maximum(linked: TestClient, hub: FakeHub) -> None:
    """Hetzelfde via ``manifest_yaml`` (de tekst wordt eerst als YAML ingelezen; dat inlezen zelf piekt rond 100 MiB,
    zie ADR-0021). Het antwoord is begrensd: de eerste 1000 meldingen, ``te_veel_meldingen``, onder 4 MiB."""
    tekst = json.dumps(_r_veilig_4_vorm(), ensure_ascii=False, separators=(",", ":"))
    assert len(tekst.encode()) <= MAX_MANIFEST_BYTES
    with _geheugengrens():
        resp = linked.post("/catalog/v1/validate", json={"manifest_yaml": tekst}, headers=hub.auth(roles=["viewer"]))
    assert resp.status_code == 200, resp.text[:300]
    out = resp.json()
    assert out["valid"] is False
    _begrensd(out["findings"])
    assert len(resp.content) < ANTWOORD_GRENS, len(resp.content)


def test_preview_as_operator_stops_the_translation_at_the_maximum(linked: TestClient, hub: FakeHub) -> None:
    """``POST /catalog/v1/preview`` (rol operator) met een geldig los manifest zonder geheimen en 3000 letterlijke
    vault-teksten in ``values``: de vertaling geeft de eerste 1000 meldingen en ``te_veel_meldingen``, zonder payload
    en zonder ``preview_hash``; installeren is geweigerd."""
    data = load_fixture("valid-catalog.manifest.yaml")
    data["values"]["l"] = ["vault://x"] * 3000
    body = {
        "manifest_yaml": json.dumps(data),
        "parameters": {"timezone": "Etc/UTC", "library_path": "/mnt/p1/fotos"},
        "target": {"truenas_version": "25.10.1"},
    }
    with _geheugengrens():
        resp = linked.post("/catalog/v1/preview", json=body, headers=hub.auth(roles=["operator"]))
    assert resp.status_code == 200, resp.text[:300]
    out = resp.json()
    assert out["validation"]["valid"] is True
    assert out["ready_for_install"] is False and out["executed"] is False
    translation = out["translation"]
    _begrensd(translation["findings"])
    assert translation["payload"] is None and translation["preview_hash"] is None and translation["vault_refs"] == []
    assert len(resp.content) < ANTWOORD_GRENS, len(resp.content)


@pytest.fixture
def bundled_dir(tmp_path: Path) -> Path:
    """De ingebouwde catalogus van de tests plus één manifest met 3000 meldingen (``password: [1] x 3000``)."""
    d = tmp_path / "catalogus"
    d.mkdir()
    for name in ("valid-custom-upstream.manifest.yaml", "valid-catalog.manifest.yaml"):
        shutil.copy(FIXTURES / "manifests" / name, d / name)
    shutil.copy(
        REPO_ROOT / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml", d / "appstore-en-fork.manifest.yaml"
    )
    veel = copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))
    veel["values"]["voorbeeld"]["password"] = [1] * 3000
    (d / "zz-veel-meldingen.manifest.yaml").write_text(json.dumps(veel), encoding="utf-8")
    return d


def test_sync_records_a_bounded_report(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> None:
    """De catalogussynchronisatie legt per manifestversie het rapport vast. Een manifest met 3000 meldingen wordt
    geweigerd met ``te_veel_meldingen``; het vastgelegde rapport heeft de eerste 1000 meldingen en de vaste fout; de
    andere manifesten van de bron komen gewoon in de catalogus."""
    from sqlalchemy import select

    from appstore.models import ManifestVersion, SyncJob

    pair(client, hub)
    startup(worker_ctx)
    assert run_once(worker_ctx)["jobs"] == 1
    with worker_ctx.sessions() as db:
        (job,) = db.scalars(select(SyncJob)).all()
        assert job.status == "succeeded", job.error
        rejected = {r["file"]: r["codes"] for r in job.result["rejected"]}
        assert TOO_MANY in rejected["zz-veel-meldingen.manifest.yaml"]
        (version,) = db.scalars(
            select(ManifestVersion).where(ManifestVersion.file_path == "zz-veel-meldingen.manifest.yaml")
        ).all()
        assert version.valid is False
        _begrensd(version.report["findings"])
    ids = [e["app_id"] for e in client.get("/catalog/v1/entries", headers=hub.auth(roles=["viewer"])).json()["items"]]
    assert ids == ["appstore-en-fork", "voorbeeld-catalogus", "voorbeeld-web"]
