"""E2E van de alles-in-één image (Docker; zoals TrueNAS een standaard Custom App start: standaardcapabilities,
geen no-new-privileges, één hostmap op /appdata, één poort). Alleen als ``APPSTORE_E2E_IMAGE`` gezet is, bv.

    APPSTORE_E2E_IMAGE=appstore-en-fork:dev python -m pytest tests/e2e/test_allinone_image.py

Dit is géén test op TrueNAS zelf (NIET GEVALIDEERD op een echte NAS); wel dezelfde containerinstellingen.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from conftest import free_port, wait_for

IMAGE = os.environ.get("APPSTORE_E2E_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="APPSTORE_E2E_IMAGE niet gezet (image-E2E overgeslagen)")

PROC_SCRIPT = (
    "import os,json\n"
    "out=[]\n"
    "for p in sorted(int(x) for x in os.listdir('/proc') if x.isdigit()):\n"
    "  try:\n"
    "    st=dict(l.split(':',1) for l in open(f'/proc/{p}/status').read().splitlines() if ':' in l)\n"
    "    cmd=open(f'/proc/{p}/cmdline','rb').read().replace(b'\\0',b' ').decode()\n"
    "  except Exception: continue\n"
    "  out.append({'pid':p,'uid':int(st['Uid'].split()[0]),'capeff':st['CapEff'].strip(),'capbnd':st['CapBnd'].strip(),"
    "'nnp':st.get('NoNewPrivs','?').strip(),'cmd':cmd})\n"
    "print(json.dumps(out))\n"
)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True, timeout=180)


class Container:
    def __init__(self, data: Path, user: str | None = None) -> None:
        self.name = f"appstore-e2e-{uuid.uuid4().hex[:8]}"
        self.port = free_port()
        args = ["run", "-d", "--name", self.name, "-p", f"127.0.0.1:{self.port}:30430", "-v", f"{data}:/appdata"]
        if user:
            args += ["--user", user]
        docker(*args, str(IMAGE))
        self.url = f"http://127.0.0.1:{self.port}"

    def health(self) -> str:
        return docker("inspect", "-f", "{{.State.Health.Status}}", self.name).stdout.strip()

    def wait_healthy(self, timeout: float = 180) -> None:
        wait_for(lambda: self.health() == "healthy", timeout, 2)

    def logs(self) -> str:
        res = docker("logs", self.name, check=False)
        return res.stdout + res.stderr

    def exec(self, *cmd: str, user: str | None = None) -> str:
        extra = ["-u", user] if user else []
        return docker("exec", *extra, self.name, *cmd).stdout

    def remove(self) -> None:
        docker("rm", "-f", self.name, check=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "appdata"
    d.mkdir()
    return d


@pytest.fixture
def container(data_dir: Path) -> Iterator[Container]:
    c = Container(data_dir)
    try:
        c.wait_healthy()
        yield c
    finally:
        c.remove()


def test_first_start_healthy_status_page_and_hardening(container: Container) -> None:
    assert httpx.get(f"{container.url}/readyz", timeout=5).json()["status"] == "ready"
    page = httpx.get(f"{container.url}/", timeout=5).text
    assert "Niet gekoppeld" in page and 'action="/koppelen"' in page
    assert httpx.get(f"{container.url}/catalog/v1/entries", timeout=5).status_code == 401
    wait_for(lambda: "1 app(s)" in httpx.get(f"{container.url}/", timeout=5).text, 60)  # eigen manifest ingebouwd
    procs = json.loads(container.exec("python", "-c", PROC_SCRIPT))
    app = [p for p in procs if p["pid"] != 1 and "python -c" not in p["cmd"]]
    assert app, procs
    for p in app:
        assert p["uid"] == 568, p
        assert p["capeff"] == "0000000000000000" and p["capbnd"] == "0000000000000000", p
        assert p["nnp"] == "1", p
    secrets = container.exec("sh", "-c", "stat -c '%a %u %n' /appdata/secrets/*").split("\n")
    assert all(line.startswith("600 568 ") for line in secrets if line), secrets
    listening = container.exec("sh", "-c", "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null; true")
    ports = {
        int(line.split()[1].split(":")[1], 16)
        for line in listening.splitlines()[1:]
        if len(line.split()) > 3 and line.split()[3] == "0A"
    }
    assert ports == {30430}, ports  # alleen de API; PostgreSQL heeft geen netwerkpoort


def test_restart_keeps_secrets_and_data(container: Container) -> None:
    before = container.exec("sha256sum", "/appdata/secrets/identity.ed25519", "/appdata/secrets/vault.key")
    docker("restart", container.name)
    container.wait_healthy()
    assert container.exec("sha256sum", "/appdata/secrets/identity.ed25519", "/appdata/secrets/vault.key") == before


def test_hard_kill_recovers(container: Container) -> None:
    docker("kill", "-s", "KILL", container.name)
    docker("start", container.name)
    container.wait_healthy()
    assert "oude postmaster.pid" in container.logs()


def test_pre_upgrade_backup_before_migration(container: Container, data_dir: Path) -> None:
    container.exec("python", "-m", "appstore", "migrate", "--down", "base", user="568")
    docker("restart", container.name)
    container.wait_healthy()
    logs = container.logs()
    assert "back-up gemaakt: voor-upgrade-" in logs
    assert list((data_dir / "backups").glob("voor-upgrade-*.dump"))


def test_runs_as_custom_user_568(tmp_path: Path) -> None:
    d = tmp_path / "open"
    d.mkdir()
    d.chmod(0o777)
    c = Container(d, user="568:568")
    try:
        c.wait_healthy()
        assert httpx.get(f"{c.url}/healthz", timeout=5).status_code == 200
    finally:
        c.remove()


def test_unwritable_appdata_gives_dutch_error(tmp_path: Path) -> None:
    d = tmp_path / "dicht"
    d.mkdir()
    d.chmod(0o755)
    c = Container(d, user="568:568")
    try:
        wait_for(lambda: "FOUT:" in c.logs(), 60, 1)
        assert "schrijfrechten" in c.logs()
        time.sleep(1)
        assert c.health() != "healthy"
    finally:
        c.remove()
