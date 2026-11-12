"""Herstelronde 4 van S-P2-520 via de API (ADR-0021): ``POST /catalog/v1/validate`` (rol viewer) met één lange sleutel
boven een lange lijst (R-VEILIG-8). De test meet de PIEK van het geheugen tijdens het verzoek (tracemalloc) en draait
onder een eigen grens op het adresgeheugen: komt de fout terug, dan faalt de test met een MemoryError in plaats van
het geheugen van de machine te vullen. Alleen testwaarden."""

from __future__ import annotations

import contextlib
import gc
import os
import resource
import tracemalloc
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.worker import run_once, startup
from tests.fakehub import FakeHub
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration
_PAGE = os.sysconf("SC_PAGE_SIZE")
#: De vorm uit R-VEILIG-8: een sleutel van 65536 tekens met één teken buiten het BMP en daaronder 98 000 nullen; als
#: compacte JSON net onder de grens van 256 KiB voor het veld ``manifest``.
LANGE_SLEUTEL = "k" * 65535 + "\U0001f600"
VEEL = 98_000
PIEK_GRENS_MIB = 50


@pytest.fixture
def linked(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> TestClient:
    pair(client, hub)
    startup(worker_ctx)
    assert run_once(worker_ctx)["jobs"] == 1
    return client


@contextlib.contextmanager
def _geheugengrens(extra_mib: int = 2048) -> Iterator[None]:
    """Zachte grens op het adresgeheugen van dit proces: wat er nu is plus ``extra_mib`` (ruim, want de dienst draait
    hier in eigen threads; de fout van vóór ADR-0021 vroeg 6 tot 25 GiB)."""
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


def test_validate_as_viewer_with_a_long_key_over_a_long_list_stays_small(linked: TestClient, hub: FakeHub) -> None:
    """R-VEILIG-8: één verzoek van de laagste rol met ``{sleutel van 65536 tekens: [0] x 98000}`` in het JSON-veld
    ``manifest`` gaf een MemoryError (doorgerekend 6 tot 25 GiB). Nu: een gewoon antwoord, de piek van het hele verzoek
    onder 50 MiB, en de lange sleutel staat niet in het antwoord (``<verborgen>``; ADR-0021)."""
    opwarmen = linked.post(
        "/catalog/v1/validate", json={"manifest": {"opwarmen": 1}}, headers=hub.auth(roles=["viewer"])
    )
    assert opwarmen.status_code == 200, opwarmen.text[:300]
    body = {"manifest": {LANGE_SLEUTEL: [0] * VEEL}}
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
    assert out["valid"] is False
    assert ("<verborgen>", "unknown_field") in {(f["path"], f["code"]) for f in out["findings"]}
    assert "account_gegevens_verboden" not in {f["code"] for f in out["findings"]}
    assert len(resp.content) < 64 * 1024, len(resp.content)
    assert "kkkkkkkk" not in resp.text
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"
