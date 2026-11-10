"""E2E van de alles-in-één image (Docker; zoals TrueNAS een standaard Custom App start: standaardcapabilities,
**zonder** ``--security-opt no-new-privileges`` en **zonder** ``--cap-drop``, één hostmap op /appdata, één poort).
Contract 1.3 §2.2 (A08, C1.3-ASF-01): voor **elk** proces in de procesboom van PID 1 (inclusief PID 1) ``Uid 568``,
``CapEff``, ``CapPrm`` en ``CapAmb`` 0, ``NoNewPrivs 1`` en — bij een start als root — ``CapBnd`` 0; het eigen
``docker exec``-proces van de controle en een lopende healthcheck (buiten de boom, PPid 0) tellen niet mee. De
**healthcheck apart**: hetzelfde commando in exec-vorm met de controlevlag ``--controle`` meldt na de wissel ``Uid 568``,
``CapEff 0``, ``CapPrm 0`` en ``NoNewPrivs 1``. Alleen als ``APPSTORE_E2E_IMAGE`` gezet is, bv.

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
    "out=[{'self':os.getpid()}]\n"
    "for p in sorted(int(x) for x in os.listdir('/proc') if x.isdigit()):\n"
    "  try:\n"
    "    st=dict(l.split(':',1) for l in open(f'/proc/{p}/status').read().splitlines() if ':' in l)\n"
    "    cmd=open(f'/proc/{p}/cmdline','rb').read().replace(b'\\0',b' ').decode()\n"
    "  except Exception: continue\n"
    "  out.append({'pid':p,'ppid':int(st['PPid'].strip()),'uid':int(st['Uid'].split()[0]),"
    "'capeff':st['CapEff'].strip(),'capprm':st['CapPrm'].strip(),'capamb':st['CapAmb'].strip(),"
    "'capbnd':st['CapBnd'].strip(),'nnp':st.get('NoNewPrivs','?').strip(),'cmd':cmd})\n"
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


ZERO = "0000000000000000"
HEALTHCHECK = ["python", "-I", "-S", "/usr/local/lib/appstore/healthcheck.py"]


def processes(c: Container) -> list[dict[str, object]]:
    """De procesboom van PID 1 (inclusief PID 1); niet het eigen ``docker exec``-proces en geen healthcheck."""
    out = json.loads(c.exec("python", "-c", PROC_SCRIPT))
    me = out[0]["self"]
    procs = {int(p["pid"]): p for p in out[1:] if p["pid"] != me}
    tree: set[int] = {1}
    changed = True
    while changed:
        changed = False
        for pid, p in procs.items():
            if pid not in tree and int(p["ppid"]) in tree:
                tree.add(pid)
                changed = True
    return [procs[pid] for pid in sorted(tree) if pid in procs]


def healthcheck_control(c: Container) -> dict[str, object]:
    """De healthcheck apart (§2.2): zoals Docker hem start (als de gebruiker van de image), met de controlevlag."""
    result: dict[str, object] = json.loads(c.exec(*HEALTHCHECK, "--controle"))
    return result


def test_first_start_healthy_status_page_and_hardening(container: Container) -> None:
    assert httpx.get(f"{container.url}/readyz", timeout=5).json()["status"] == "ready"
    page = httpx.get(f"{container.url}/", timeout=5).text
    assert "Niet gekoppeld" in page and 'action="/koppelen"' in page and 'name="hub_vingerafdruk"' in page
    assert page.startswith("<!doctype html>") and "Peil statuspagina-sjabloon 1.1.1" in page and "<script" not in page
    resp = httpx.get(f"{container.url}/catalog/v1/entries", timeout=5)
    assert resp.status_code == 401 and resp.json()["details"]["reason"] == "not_linked"
    # eigen manifest ingebouwd
    wait_for(lambda: "1 app</span>" in httpx.get(f"{container.url}/", timeout=5).text, 60)
    procs = processes(container)
    assert len(procs) >= 4, procs  # bewaker (PID 1), PostgreSQL, API, worker
    pid1 = next(p for p in procs if p["pid"] == 1)
    assert "supervisor.py" in str(pid1["cmd"]) and not any("dumb-init" in str(p["cmd"]) for p in procs), procs
    for p in procs:  # ook PID 1: geen enkel proces meer als root (contract 1.3 §2.2, R11)
        assert p["uid"] == 568, p
        assert p["capeff"] == p["capprm"] == p["capamb"] == ZERO, p
        assert p["capbnd"] == ZERO, p  # start als root: bounding set leeg (K3)
        assert p["nnp"] == "1", p
    # de healthcheck apart: exec-vorm (geen sh -c) en na de wissel 568, zonder capabilities, met no_new_privs
    test = json.loads(docker("inspect", "-f", "{{json .Config.Healthcheck.Test}}", container.name).stdout)
    assert test == ["CMD", *HEALTHCHECK], test
    hc = healthcheck_control(container)
    assert hc["wissel"] is True and hc["Uid"] == "568" and hc["NoNewPrivs"] == "1", hc
    assert hc["CapEff"] == hc["CapPrm"] == hc["CapAmb"] == hc["CapBnd"] == ZERO, hc
    assert docker("exec", container.name, *HEALTHCHECK, check=False).returncode == 0  # gezond na de wissel
    # een proxy in de omgeving (HTTP_PROXY in het formulier) mag de healthcheck niet omleiden (§5: geen proxy-env)
    proxy_env = ("HTTP_PROXY=http://192.0.2.1:9", "http_proxy=http://192.0.2.1:9", "NO_PROXY=", "no_proxy=")
    proxy = [arg for env in proxy_env for arg in ("-e", env)]
    proxied = docker("exec", *proxy, container.name, *HEALTHCHECK, check=False)
    assert proxied.returncode == 0, proxied.stdout + proxied.stderr
    secrets = container.exec("sh", "-c", "stat -c '%a %u %n' /appdata/secrets/*").split("\n")
    assert all(line.startswith("600 568 ") for line in secrets if line), secrets
    assert any(line.endswith("/hub-link.json") for line in secrets), secrets  # koppelstatus in een bestand (§3.5)
    assert container.exec("stat", "-c", "%a %u", "/appdata/secrets").strip() == "700 568"
    link = json.loads(container.exec("cat", "/appdata/secrets/hub-link.json", user="568"))
    assert link["state"] == "nooit_gekoppeld" and link["schema_version"] == "1.3"
    listening = container.exec("sh", "-c", "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null; true")
    ports = {
        int(line.split()[1].split(":")[1], 16)
        for line in listening.splitlines()[1:]
        if len(line.split()) > 3 and line.split()[3] == "0A"
    }
    assert ports == {30430}, ports  # alleen de API; PostgreSQL heeft geen netwerkpoort


def test_pid1_reaps_orphans_and_stops_on_sigterm(container: Container) -> None:
    """PID 1 is de bewaker: verweesde processen worden opgeruimd en SIGTERM stopt alles netjes (geen dumb-init)."""
    container.exec("sh", "-c", "sleep 0.1 & exit 0", user="568")  # wees → kind van PID 1
    time.sleep(5)
    stat_lines = container.exec("sh", "-c", "cat /proc/[0-9]*/stat 2>/dev/null; true")
    zombies = [line for line in stat_lines.splitlines() if line.rsplit(")", 1)[-1].split()[:1] == ["Z"]]
    assert zombies == [], zombies
    docker("stop", "-t", "60", container.name)
    assert docker("inspect", "-f", "{{.State.ExitCode}}", container.name).stdout.strip() == "0"
    assert "gestopt" in container.logs()


def test_restart_keeps_secrets_and_data(container: Container) -> None:
    before = container.exec("sha256sum", "/appdata/secrets/identity.ed25519", "/appdata/secrets/vault.key")
    docker("restart", container.name)
    container.wait_healthy()
    assert container.exec("sha256sum", "/appdata/secrets/identity.ed25519", "/appdata/secrets/vault.key") == before


def _db_fingerprint(c: Container) -> str:
    """Cluster-ID (verandert bij een nieuwe initdb) + alle manifestversies, evidence en een eigen markering."""
    sql = (
        "SELECT (SELECT system_identifier FROM pg_control_system())::text"
        " || '|' || (SELECT string_agg(id::text || ':' || manifest_sha256, ',' ORDER BY id) FROM manifest_version)"
        " || '|' || coalesce((SELECT string_agg(trace_id, ',' ORDER BY trace_id) FROM audit_event"
        " WHERE action = 'e2e.marker'), '')"
    )
    out = c.exec("psql", "-h", "/tmp/appstore-pg", "-U", "appstore", "-d", "appstore", "-tAc", sql, user="568").strip()
    assert out and "|" in out, out
    return out


def _evidence_ids(c: Container) -> set[str]:
    out = c.exec(
        "psql",
        "-h",
        "/tmp/appstore-pg",
        "-U",
        "appstore",
        "-d",
        "appstore",
        "-tAc",
        "SELECT id FROM evidence_record",
        user="568",
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def _write_marker(c: Container) -> str:
    marker = str(uuid.uuid4())
    sql = (
        "INSERT INTO audit_event (at, actor_type, actor_id, action, target_type, target_id, details, trace_id) "
        f"VALUES (now(), 'system', 'e2e', 'e2e.marker', 'test', 'x', '{{}}'::jsonb, '{marker}')"
    )
    c.exec("psql", "-h", "/tmp/appstore-pg", "-U", "appstore", "-d", "appstore", "-tAc", sql, user="568")
    return marker


def test_restart_and_kill_keep_database_rows(container: Container) -> None:
    """Herstart en harde kill: zelfde cluster, zelfde manifestversies en de eigen markering blijft (geen dataverlies)."""
    wait_for(lambda: "1 app</span>" in httpx.get(f"{container.url}/", timeout=5).text, 60)
    marker = _write_marker(container)
    before, evidence = _db_fingerprint(container), _evidence_ids(container)
    assert marker in before and evidence
    docker("restart", container.name)
    container.wait_healthy()
    assert _db_fingerprint(container) == before
    assert evidence <= _evidence_ids(container)  # bestaand bewijs blijft; nieuw bewijs mag erbij
    docker("kill", "-s", "KILL", container.name)
    docker("start", container.name)
    container.wait_healthy()
    assert _db_fingerprint(container) == before
    assert evidence <= _evidence_ids(container)


def test_new_container_on_same_appdata_keeps_database(data_dir: Path) -> None:
    """Zoals een update/herinstallatie in TrueNAS: nieuwe container, zelfde /appdata → zelfde database en geheimen."""
    first = Container(data_dir)
    try:
        first.wait_healthy()
        wait_for(lambda: "1 app</span>" in httpx.get(f"{first.url}/", timeout=5).text, 60)
        marker = _write_marker(first)
        before, evidence = _db_fingerprint(first), _evidence_ids(first)
        keys = first.exec("sha256sum", "/appdata/secrets/identity.ed25519", "/appdata/secrets/vault.key")
    finally:
        first.remove()
    second = Container(data_dir)
    try:
        second.wait_healthy()
        after = _db_fingerprint(second)
        assert after == before and marker in after
        assert evidence <= _evidence_ids(second)
        assert second.exec("sha256sum", "/appdata/secrets/identity.ed25519", "/appdata/secrets/vault.key") == keys
        assert "eerste start: database aanmaken" not in second.logs()
    finally:
        second.remove()


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
        for p in processes(c):  # Custom User 568: CapBnd mag de standaardset zijn; de rest blijft verplicht
            assert p["uid"] == 568 and p["capeff"] == p["capprm"] == p["capamb"] == ZERO and p["nnp"] == "1", p
        hc = healthcheck_control(c)
        assert hc["wissel"] is True and hc["Uid"] == "568" and hc["CapEff"] == hc["CapPrm"] == ZERO, hc
        assert hc["NoNewPrivs"] == "1", hc
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
