"""appstore-en-fork — procesbewaker van de alles-in-één image (kind van dumb-init).

Eén container draait alles: PostgreSQL 16 (alleen unix-socket, geen netwerkpoort, geen wachtwoord), migraties, de API
(met statuspagina) en de worker. Daardoor is er maar één hostmap (/appdata) en één poort nodig en installeert de app
als standaard TrueNAS Custom App zonder YAML (voorbeeld: Media Source Optimizer ``deploy/allinone``).

Regels:
- Start als root alléén om de eigen submappen van /appdata aan 568:568 te geven; daarna laat het proces alle
  capabilities uit de bounding set vallen, zet ``no_new_privs`` (het standaard Custom App-formulier kan dat niet,
  zie ADR-0003) en wordt 568. Start het als 568 (Custom User in het formulier), dan werkt het ook.
- Eerste start: geheimen in /appdata/secrets (0600, nooit overschreven) en een nieuwe database.
- Vóór openstaande migraties van een bestaande database een ``pg_dump`` in /appdata/backups (nieuwste 3 blijven).
  Mislukt die back-up, dan wordt er NIET gemigreerd en blijft de database ongewijzigd (duidelijke NL-melding).
- Een oude postmaster.pid na een harde stop wordt opgeruimd; kinderen die stoppen worden met backoff herstart;
  SIGTERM stopt worker → api → database netjes.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

UID = 568
GID = 568
DATA = Path(os.environ.get("APPSTORE_DATA_DIR", "/appdata"))
PGDATA = DATA / "postgres"
BACKUPS = DATA / "backups"
SOCKET_DIR = Path("/tmp/appstore-pg")  # noqa: S108 - socketmap binnen de container, 0700 van 568
PORT = os.environ.get("APPSTORE_PORT", "30430")
STOP = threading.Event()
PR_SET_NO_NEW_PRIVS = 38
PR_CAPBSET_DROP = 24
KEEP_BACKUPS = 3
OWN_DIRS = ("postgres", "secrets", "backups", "logs")


def log(msg: str) -> None:
    print(f"[appstore] {msg}", flush=True)


def fail(msg: str, code: int = 78) -> None:
    log("FOUT: " + msg)
    time.sleep(30)  # melding zichtbaar houden in de TrueNAS-applog i.p.v. een snelle herstartlus
    sys.exit(code)


# ------------------------------------------------------------------ rechten
def harden_and_demote() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if os.getuid() == 0:
        for cap in range(64):
            if libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) != 0:
                break  # einde van de bekende capabilities
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        log("let op: no_new_privs kon niet worden gezet")
    if os.getuid() == 0 or os.geteuid() == 0:
        fail("kon de rechten niet verlagen; de app weigert als root te draaien")


def prepare_dirs() -> None:
    if not DATA.exists():
        fail(f"{DATA} bestaat niet. Koppel in de Custom App een dataset (Host Path) aan {DATA}.")
    root = os.getuid() == 0
    if root:
        for path in [DATA, *[p for p in DATA.iterdir() if p.name in OWN_DIRS]]:
            st = path.lstat()
            if (st.st_uid, st.st_gid) != (UID, GID):
                log(f"eigenaar van {path} wordt {UID}:{GID}")
                if path == DATA:
                    os.lchown(path, UID, GID)
                    continue
                for dirpath, dirnames, filenames in os.walk(path):
                    for name in [dirpath, *[os.path.join(dirpath, n) for n in dirnames + filenames]]:
                        with contextlib.suppress(OSError):
                            os.lchown(name, UID, GID)
    for sub in (PGDATA, DATA / "secrets", BACKUPS):
        try:
            sub.mkdir(parents=True, exist_ok=True)
            if root:
                os.chown(sub, UID, GID)
        except PermissionError:
            fail(
                f"geen schrijfrechten op {sub}. Zet 'Custom User' uit (de app regelt de rechten) of geef 568 schrijfrechten."
            )
    try:
        os.chmod(PGDATA, 0o700)
        os.chmod(DATA / "secrets", 0o700)
    except PermissionError:
        fail(f"{PGDATA} is niet van gebruiker {UID}; start als root (zonder Custom User) of maak {UID} eigenaar.")
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    if root:
        os.chown(SOCKET_DIR, UID, GID)
    os.chmod(SOCKET_DIR, 0o700)


# ------------------------------------------------------------------ processen
def env_for(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": "/tmp",  # noqa: S108 - HOME voor de app-gebruiker zonder thuismap
            "APPSTORE_DATA_DIR": str(DATA),
            "PYTHONUNBUFFERED": "1",
            "APPSTORE_DATABASE_URL": f"postgresql+psycopg://appstore@/appstore?host={SOCKET_DIR}",
        }
    )
    with contextlib.suppress(KeyError):
        env["USER"] = pwd.getpwuid(os.getuid()).pw_name
    env.update(extra or {})
    return env


def run(name: str, cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    log(f"{name}: {' '.join(cmd)}")
    return subprocess.run(cmd, env=env_for(), check=False, text=True, capture_output=capture)


class Child:
    def __init__(self, name: str, cmd: list[str], stop_signal: int = signal.SIGTERM) -> None:
        self.name, self.cmd, self.stop_signal = name, cmd, stop_signal
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        log(f"start {self.name}")
        self.proc = subprocess.Popen(self.cmd, env=env_for())

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 25) -> None:
        if not self.alive():
            return
        assert self.proc is not None
        self.proc.send_signal(self.stop_signal)
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            log(f"{self.name} reageert niet; geforceerd stoppen")
            self.proc.kill()
            self.proc.wait(5)


def init_database() -> bool:
    """Geeft ``True`` als de database nieuw is aangemaakt."""
    if (PGDATA / "PG_VERSION").exists():
        major = (PGDATA / "PG_VERSION").read_text().strip()
        if major != "16":
            fail(f"database in {PGDATA} is PostgreSQL {major}; deze image gebruikt 16. Zet de vorige versie terug.")
        pid = PGDATA / "postmaster.pid"
        if pid.exists():
            log("oude postmaster.pid van een onderbroken stop verwijderd")
            pid.unlink()
        return False
    log("eerste start: database aanmaken")
    rc = run(
        "initdb",
        [
            "initdb",
            "-D",
            str(PGDATA),
            "-U",
            "appstore",
            "--auth-local=trust",
            "--auth-host=reject",
            "--encoding=UTF8",
            "--locale=C.UTF-8",
            "--no-instructions",
        ],
    ).returncode
    if rc != 0:
        fail("initdb mislukt (zie log hierboven)")
    return True


def postgres_child() -> Child:
    return Child(
        "database",
        [
            "postgres",
            "-D",
            str(PGDATA),
            "-c",
            "listen_addresses=",
            "-c",
            f"unix_socket_directories={SOCKET_DIR}",
            "-c",
            "max_connections=30",
            "-c",
            "shared_buffers=32MB",
            "-c",
            "jit=off",
            "-c",
            "logging_collector=off",
            "-c",
            "log_min_messages=warning",
        ],
        stop_signal=signal.SIGINT,
    )


def wait_for_db(pg: Child, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pg.alive():
            fail("database stopte tijdens het opstarten (zie log hierboven)")
        rc = subprocess.run(
            ["pg_isready", "-h", str(SOCKET_DIR), "-U", "appstore", "-d", "postgres", "-q"], env=env_for(), check=False
        ).returncode
        if rc == 0:
            break
        time.sleep(1)
    else:
        fail("database niet klaar binnen 90 s")
    exists = run(
        "controle database",
        [
            "psql",
            "-h",
            str(SOCKET_DIR),
            "-U",
            "appstore",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='appstore'",
        ],
        capture=True,
    )
    if exists.stdout.strip() != "1":
        rc = run(
            "createdb",
            ["psql", "-h", str(SOCKET_DIR), "-U", "appstore", "-d", "postgres", "-qc", "CREATE DATABASE appstore"],
        ).returncode
        if rc != 0:
            fail("database 'appstore' aanmaken mislukt")


def backup_before_migrations() -> None:
    """pg_dump vóór openstaande migraties van een bestaande database; zonder geslaagde back-up geen migratie."""
    py = sys.executable
    if run("migraties nodig?", [py, "-m", "appstore", "db-pending"]).returncode != 0:
        return
    has_schema = run(
        "bestaand schema?",
        [
            "psql",
            "-h",
            str(SOCKET_DIR),
            "-U",
            "appstore",
            "-d",
            "appstore",
            "-tAc",
            "SELECT 1 FROM information_schema.tables WHERE table_name='alembic_version'",
        ],
        capture=True,
    )
    if has_schema.stdout.strip() != "1":
        return  # nieuwe database: niets te bewaren
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUPS / f"voor-upgrade-{stamp}.dump"
    rc = run(
        "back-up vóór upgrade",
        ["pg_dump", "-h", str(SOCKET_DIR), "-U", "appstore", "-d", "appstore", "-Fc", "-f", str(target)],
    ).returncode
    if rc != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        fail(
            "back-up vóór de databasemigratie is mislukt (te weinig ruimte?). De database is NIET gewijzigd. "
            "Maak ruimte vrij of zet de vorige versie (tag) terug."
        )
    os.chmod(target, 0o600)
    dumps = sorted(BACKUPS.glob("voor-upgrade-*.dump"))
    for old in dumps[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    log(f"back-up gemaakt: {target.name}")


def main() -> None:
    log(f"appstore-en-fork (alles-in-één) · data {DATA} · poort {PORT}")
    prepare_dirs()
    harden_and_demote()
    py = sys.executable
    if run("geheimen", [py, "-m", "appstore", "init-secrets"]).returncode != 0:
        fail("geheimen in /appdata/secrets aanmaken mislukt")
    init_database()
    pg = postgres_child()
    pg.start()
    wait_for_db(pg)
    backup_before_migrations()
    if run("migraties", [py, "-m", "appstore", "migrate"]).returncode != 0:
        pg.stop()
        fail(
            "databasemigratie mislukt; de transactie is teruggedraaid en de database is ongewijzigd. Zet de vorige versie terug."
        )
    children = [Child("api", [py, "-m", "appstore", "serve"]), Child("worker", [py, "-m", "appstore", "worker"])]
    for c in children:
        c.start()

    def shutdown(signum: int, _frame: object) -> None:
        log(f"signaal {signum}: netjes stoppen")
        STOP.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    backoff: dict[str, float] = {}
    while not STOP.is_set():
        if not pg.alive():
            log("database gestopt; opnieuw starten")
            for c in children:
                c.stop()
            init_database()
            pg.start()
            wait_for_db(pg)
            for c in children:
                c.start()
        for c in children:
            if not c.alive() and not STOP.is_set():
                wait = min(60.0, backoff.get(c.name, 1.0))
                log(f"{c.name} gestopt (code {c.proc.returncode if c.proc else '?'}); herstart over {wait:.0f} s")
                STOP.wait(wait)
                backoff[c.name] = wait * 2
                if not STOP.is_set():
                    c.start()
            elif c.alive():
                backoff[c.name] = max(1.0, backoff.get(c.name, 1.0) * 0.9)
        STOP.wait(2)
    for c in reversed(children):
        c.stop()
    pg.stop(60)
    log("gestopt")


if __name__ == "__main__":
    if shutil.which("postgres") is None:
        fail("postgres ontbreekt in de image")
    main()
