"""Synchronisatie van een catalogusbron: fail-closed per manifest (ADR-0018, herstel R-VEILIG-3 van S-P2-520).

Eén manifest dat bij het vastleggen faalt, weigert alleen zichzelf (code ``manifest_niet_verwerkt``); de andere
manifesten van de bron komen gewoon in de catalogus en de synchronisatie slaagt. De fout komt niet met haar tekst in de
log (die kan waarden uit het manifest bevatten).
"""

from __future__ import annotations

import copy
import logging
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.worker import run_once, startup
from tests.conftest import load_fixture
from tests.fakehub import FakeHub
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration


@pytest.fixture
def bundled_dir(tmp_path: Path) -> Path:
    """De ingebouwde catalogus van de tests, plus één manifest dat het vastleggen laat mislukken (een ``app_id`` die
    langer is dan de kolom in de database) en één dat de validator vroeger liet crashen (sleutel ``Reisepaß``)."""
    from tests.conftest import FIXTURES, REPO_ROOT

    d = tmp_path / "catalogus"
    d.mkdir()
    for name in (
        "valid-custom-upstream.manifest.yaml",
        "valid-catalog.manifest.yaml",
        "ex47-privileged-hostmount.manifest.yaml",
    ):
        shutil.copy(FIXTURES / "manifests" / name, d / name)
    shutil.copy(
        REPO_ROOT / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml", d / "appstore-en-fork.manifest.yaml"
    )
    lang = copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))
    lang["app_id"] = "a" * 60
    (d / "aa-lang.manifest.yaml").write_text(yaml.safe_dump(lang), encoding="utf-8")
    ss = copy.deepcopy(load_fixture("valid-catalog-1-3.manifest.yaml"))
    ss["values"]["voorbeeld"]["Reisepaß"] = "x"
    (d / "zz-sharp-s.manifest.yaml").write_text(yaml.safe_dump(ss, allow_unicode=True), encoding="utf-8")
    return d


def test_one_failing_manifest_does_not_fail_the_whole_source(
    client: TestClient, hub: FakeHub, worker_ctx: AppContext, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail-closed per manifest: het manifest dat niet vast te leggen is, wordt geweigerd; de rest van de bron komt
    gewoon in de catalogus en de synchronisatie slaagt."""
    from sqlalchemy import select

    from appstore.models import SyncJob

    pair(client, hub)
    startup(worker_ctx)
    with caplog.at_level(logging.INFO):
        assert run_once(worker_ctx)["jobs"] == 1
    with worker_ctx.sessions() as db:
        (job,) = db.scalars(select(SyncJob)).all()
        assert job.status == "succeeded", job.error
        rejected = {r["file"]: r["codes"] for r in job.result["rejected"]}
    assert rejected["aa-lang.manifest.yaml"] == ["manifest_niet_verwerkt"]
    ids = [e["app_id"] for e in client.get("/catalog/v1/entries", headers=hub.auth(roles=["viewer"])).json()["items"]]
    assert ids == ["appstore-en-fork", "voorbeeld-catalogus", "voorbeeld-sso", "voorbeeld-web"]
    src = client.get("/catalog/v1/sources", headers=hub.auth()).json()["items"][0]
    assert src["last_sync"]["status"] == "ok"
    assert "a" * 60 not in caplog.text
