"""Integratietests tegen echte PostgreSQL (database ``appstore_test_<pid>``) met een gesimuleerde Hub."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from appstore import migrations
from appstore.api.common import AppContext
from appstore.app import create_app
from appstore.config import Settings
from appstore.models import Base
from appstore.worker import build_context
from tests.conftest import FIXTURES, REPO_ROOT
from tests.fakehub import FakeHub

ADMIN_URL = os.environ.get("APPSTORE_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    name = f"appstore_test_{os.getpid()}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover
        # in CI (of met APPSTORE_REQUIRE_DB=1) is een ontbrekende database een fout, nooit een stille 'skip'
        if os.environ.get("CI") or os.environ.get("APPSTORE_REQUIRE_DB"):
            pytest.fail(f"PostgreSQL vereist maar niet beschikbaar: {exc}")
        pytest.skip(f"PostgreSQL niet beschikbaar: {exc}")
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    migrations.upgrade(url)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}'"))
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    admin.dispose()


@pytest.fixture(autouse=True)
def _clean(database_url: str) -> Iterator[None]:
    yield
    engine = create_engine(database_url)
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def bundled_dir(tmp_path: Path) -> Path:
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
    return d


@pytest.fixture
def hub() -> FakeHub:
    return FakeHub()


@pytest.fixture
def settings(tmp_path: Path, database_url: str, bundled_dir: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "appdata",
        database_url=database_url,
        host="127.0.0.1",
        port=0,
        hub_url=None,
        enrollment_token=None,
        public_url=None,
        bundled_catalog_dir=bundled_dir,
        allow_insecure_sources=True,
    )


@pytest.fixture
def client(settings: Settings, hub: FakeHub) -> Iterator[TestClient]:
    app = create_app(settings, http_factory=hub.client_factory())
    with TestClient(app) as c:
        yield c
    app.state.ctx.engine.dispose()


@pytest.fixture
def worker_ctx(settings: Settings, hub: FakeHub) -> Iterator[AppContext]:
    c = build_context(settings, hub.client_factory())
    yield c
    c.engine.dispose()


ORIGIN = {"Origin": "http://testserver"}


def pair_form(
    client: TestClient,
    *,
    hub_url: str = "http://hub.test:30400",
    code: str = "",
    fingerprint: str = "",
    csrf: str | None = None,
    origin: dict[str, str] | None = None,
) -> str:
    """POST /koppelen zoals het formulier van de statuspagina; geeft de melding uit de redirect."""
    if csrf is None:
        client.get("/")
        csrf = client.cookies.get("appstore_csrf") or ""
    resp = client.post(
        "/koppelen",
        data={"hub_url": hub_url, "koppelcode": code, "vingerafdruk": fingerprint, "csrf": csrf},
        headers=ORIGIN if origin is None else origin,
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    location = resp.headers["location"]
    assert location.startswith("/?melding="), location
    return location.removeprefix("/?melding=")


def pair(client: TestClient, hub: FakeHub) -> str:
    """Koppelen via de statuspagina met een verse koppelcode + de juiste vingerafdruk; geeft het service-token."""
    melding = pair_form(client, code=hub.add_code(), fingerprint=hub.fingerprint())
    assert melding == "gekoppeld", melding
    assert hub.last_service_token
    return hub.last_service_token


def idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}
