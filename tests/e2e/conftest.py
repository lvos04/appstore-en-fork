"""Dienst-E2E met echte processen (uvicorn-API + worker) en een Hub-simulator als echte HTTP-dienst.

Simulator ≠ echte Hub/NAS: uitkomsten zijn "Sim-getest". Vereist PostgreSQL (APPSTORE_TEST_DATABASE_URL of
127.0.0.1:5432, gebruiker postgres) en draait vanaf de hoofdmap: ``python -m pytest tests/e2e``.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps" / "api"
sys.path.insert(0, str(API))
sys.path.insert(0, str(API / "src"))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.fakehub import FakeHub

ADMIN_URL = os.environ.get("APPSTORE_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres")
FIXTURES = API / "tests" / "fixtures" / "manifests"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(fn: Any, timeout: float = 60, interval: float = 0.5) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:
            last = exc
        time.sleep(interval)
    raise AssertionError(f"tijdslimiet verstreken; laatste uitkomst: {last!r}")


@pytest.fixture(scope="session")
def hub() -> Iterator[FakeHub]:
    sim = FakeHub(hub_host="127.0.0.1")
    port = free_port()
    sim.url = f"http://127.0.0.1:{port}"  # type: ignore[attr-defined]
    server = uvicorn.Server(uvicorn.Config(sim.asgi(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started, 15)
    yield sim
    server.should_exit = True
    thread.join(10)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    name = f"appstore_e2e_{os.getpid()}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}'"))
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def service(hub: FakeHub, database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    base = tmp_path_factory.mktemp("e2e")
    catalog = base / "catalogus"
    catalog.mkdir()
    for name in ("valid-custom-upstream.manifest.yaml", "ex47-privileged-hostmount.manifest.yaml"):
        shutil.copy(FIXTURES / name, catalog / name)
    shutil.copy(
        ROOT / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml", catalog / "appstore-en-fork.manifest.yaml"
    )
    hub.valid_tokens.add("e2e-enrollment-token-0123456789")
    port = free_port()
    env = {
        **os.environ,
        "APPSTORE_DATA_DIR": str(base / "appdata"),
        "APPSTORE_DATABASE_URL": database_url,
        "APPSTORE_HOST": "127.0.0.1",
        "APPSTORE_PORT": str(port),
        "HUB_URL": hub.url,  # type: ignore[attr-defined]
        "HUB_ENROLLMENT_TOKEN": "e2e-enrollment-token-0123456789",
        "APPSTORE_BUNDLED_CATALOG_DIR": str(catalog),
        "APPSTORE_ALLOW_INSECURE_SOURCES": "1",
        "PYTHONPATH": str(API / "src"),
        "APPSTORE_LOG_LEVEL": "warning",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        env.pop(var, None)
    py = sys.executable
    subprocess.run([py, "-m", "appstore", "migrate"], env=env, check=True, capture_output=True)
    procs = [subprocess.Popen([py, "-m", "appstore", cmd], env=env) for cmd in ("serve", "worker")]
    url = f"http://127.0.0.1:{port}"
    try:
        wait_for(lambda: httpx.get(f"{url}/readyz", timeout=2).status_code == 200, 60)
        yield url
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill()
