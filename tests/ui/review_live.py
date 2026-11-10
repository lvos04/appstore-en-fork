"""Ontwerpreview van de statuspagina tegen de **draaiende** dienst (docs/ontwerp/PEIL_REVIEW.md).

Start echte processen zoals in de image: ``python -m appstore migrate``, ``serve`` en ``worker`` met een eigen
``/appdata`` en een eigen PostgreSQL-database, plus de Hub-simulator als echte HTTP-dienst. Daarna wordt de dienst
achtereenvolgens in elke toestand gebracht — zoveel mogelijk langs de echte weg (Chromium vult het koppelformulier in,
de Hub trekt de koppeling in, de Hub ontkoppelt via de connector-API, bronnen via de catalogus-API, database weg) en
alleen voor een kapot ``hub-link.json`` met foutinjectie — en per toestand draait ``statuspagina.live.ts`` (Playwright,
axe-core, metingen in de DOM, schermafdrukken op 320/360/390/768/1440 × licht/donker).

Simulator ≠ echte Hub/NAS: de uitkomst is **Sim-getest**. Gebruik (vanaf de hoofdmap, venv met het pakket)::

    PATH=/usr/lib/postgresql/16/bin:$PATH .venv/bin/python tests/ui/review_live.py \
        --afdrukken docs/screenshots/peil/statuspagina --meting /tmp/peil-review

Vereist PostgreSQL (``APPSTORE_TEST_DATABASE_URL`` of 127.0.0.1:5432, gebruiker ``postgres``) en ``npm ci`` in tests/ui.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps" / "api"
UI = ROOT / "tests" / "ui"
sys.path.insert(0, str(API))
sys.path.insert(0, str(API / "src"))

from tests.fakehub import FakeHub

ADMIN_URL = os.environ.get("APPSTORE_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
#: draaiende diensten; worden altijd gestopt vóór een database verdwijnt (ook bij een tijdslimiet)
ACTIEF: list[Dienst] = []


def vrije_poort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wacht(fn: Callable[[], Any], tijd: float = 60, wat: str = "") -> Any:
    eind = time.monotonic() + tijd
    laatst: Any = None
    while time.monotonic() < eind:
        try:
            laatst = fn()
            if laatst:
                return laatst
        except Exception as exc:  # de dienst start nog of herstart
            laatst = exc
        time.sleep(0.5)
    raise SystemExit(f"tijdslimiet: {wat} (laatste uitkomst: {laatst!r})")


@contextmanager
def database(naam: str) -> Iterator[str]:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{naam}"'))
        conn.execute(text(f'CREATE DATABASE "{naam}"'))
    try:
        yield make_url(ADMIN_URL).set(database=naam).render_as_string(hide_password=False)
    finally:
        for d in ACTIEF:
            d.stop_alles()
        with admin.connect() as conn:
            conn.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{naam}'"))
            conn.execute(text(f'DROP DATABASE IF EXISTS "{naam}"'))
        admin.dispose()


class Dienst:
    """Eén appstore-installatie: eigen /appdata en database; ``serve`` en ``worker`` als losse processen."""

    def __init__(self, basis: Path, database_url: str, extra: dict[str, str], catalogus: list[Path]) -> None:
        self.data = basis / "appdata"
        self.data.mkdir(parents=True)
        ingebouwd = basis / "catalogus"
        ingebouwd.mkdir()
        for bestand in catalogus:
            shutil.copy(bestand, ingebouwd / bestand.name)
        self.poort = vrije_poort()
        self.url = f"http://127.0.0.1:{self.poort}"
        self.env = {
            **os.environ,
            "APPSTORE_DATA_DIR": str(self.data),
            "APPSTORE_DATABASE_URL": database_url,
            "APPSTORE_HOST": "127.0.0.1",
            "APPSTORE_PORT": str(self.poort),
            "APPSTORE_ALLOW_LOOPBACK_HUB": "1",  # alleen ontwikkeling: de simulator draait op 127.0.0.1
            "APPSTORE_BUNDLED_CATALOG_DIR": str(ingebouwd),
            "APPSTORE_ALLOW_INSECURE_SOURCES": "1",
            "APPSTORE_LOG_LEVEL": "warning",
            "PYTHONPATH": str(API / "src"),
            "NO_PROXY": "127.0.0.1,localhost",
            **extra,
        }
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        ACTIEF.append(self)
        subprocess.run([sys.executable, "-m", "appstore", "migrate"], env=self.env, check=True, capture_output=True)

    def start(self, rol: str, **extra: str) -> None:
        log = (self.data.parent / f"{rol}.log").open("ab")
        self.procs[rol] = subprocess.Popen(
            [sys.executable, "-m", "appstore", rol], env={**self.env, **extra}, stdout=log, stderr=log
        )
        if rol == "serve":
            wacht(lambda: httpx.get(f"{self.url}/healthz", timeout=2).status_code == 200, 60, "serve start")

    def stop(self, rol: str) -> None:
        p = self.procs.pop(rol, None)
        if p is None:
            return
        p.terminate()
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()

    def herstart(self, rol: str, **extra: str) -> None:
        self.stop(rol)
        self.start(rol, **extra)

    def stop_alles(self) -> None:
        for rol in list(self.procs):
            self.stop(rol)

    def pagina(self, pad: str = "/") -> str:
        return httpx.get(self.url + pad, timeout=10).text


class Review:
    def __init__(self, afdrukken: Path | None, meting: Path) -> None:
        self.afdrukken = afdrukken
        self.meting = meting
        self.uitkomst: list[tuple[str, int]] = []

    def meet(
        self,
        dienst: Dienst,
        toestand: str,
        *,
        actie: str = "get",
        pad: str = "/",
        status: int = 200,
        velden: dict[str, str] | None = None,
        koppelcode: str | None = None,
    ) -> None:
        env = {
            **os.environ,
            "LIVE_URL": dienst.url,
            "TOESTAND": toestand,
            "ACTIE": actie,
            "PAD": pad,
            "VERWACHT_STATUS": str(status),
            "VELDEN": json.dumps(velden or {}),
            "METING_UIT": str(self.meting),
        }
        if koppelcode:
            env["KOPPELCODE"] = koppelcode
        if self.afdrukken:
            env["AFDRUKKEN"] = str(self.afdrukken)
        # eigen caches: op een gedeelde bouwmachine kan een gelijktijdige run de gedeelde Node-/Playwright-cache in
        # /tmp half beschreven achterlaten (gezien: SyntaxError bij het laden van Playwright zelf)
        env["NODE_DISABLE_COMPILE_CACHE"] = "1"
        env["PWTEST_CACHE_DIR"] = str(self.meting / ".pw-cache")
        print(f"── {toestand} ({actie} {pad}, verwacht HTTP {status})", flush=True)
        for poging in (1, 2):
            run = subprocess.run(
                ["npx", "playwright", "test", "-c", "playwright.live.config.ts"],
                check=False,
                cwd=UI,
                env=env,
                capture_output=True,
                text=True,
            )
            print(run.stdout + run.stderr, flush=True)
            # alleen opnieuw als Playwright zelf niet startte (de test kwam niet aan de dienst toe)
            if run.returncode == 0 or poging == 2 or "worker process exited unexpectedly" not in run.stdout:
                break
            print(f"   Playwright startte niet; tweede poging voor {toestand}", flush=True)
        self.uitkomst.append((toestand, run.returncode))


def andere_vingerafdruk(vinger: str) -> str:
    """Geldige vorm (16 Crockford-tekens), maar niet die van de Hub: één teken anders."""
    eerste = vinger[0]
    return CROCKFORD[(CROCKFORD.index(eerste) + 1) % len(CROCKFORD)] + vinger[1:]


def koppel_post(dienst: Dienst, velden: dict[str, str]) -> httpx.Response:
    """Een koppelpoging zoals een browser hem doet (cookie + CSRF + Origin), voor de rate-limit-toestand."""
    with httpx.Client(base_url=dienst.url, timeout=15) as c:
        c.get("/")
        return c.post(
            "/koppelen",
            data={**velden, "csrf": c.cookies["appstore_csrf"]},
            headers={"Origin": dienst.url},
            follow_redirects=False,
        )


def draai(review: Review) -> None:
    hub = FakeHub(hub_host="127.0.0.1")
    poort = vrije_poort()
    hub_url = f"http://127.0.0.1:{poort}"
    server = uvicorn.Server(uvicorn.Config(hub.asgi(), host="127.0.0.1", port=poort, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    wacht(lambda: server.started, 15, "Hub-simulator")
    vinger = hub.fingerprint()
    catalogus = [
        ROOT / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml",
        API / "tests" / "fixtures" / "manifests" / "valid-custom-upstream.manifest.yaml",
        API / "tests" / "fixtures" / "manifests" / "ex47-privileged-hostmount.manifest.yaml",
    ]
    pid = os.getpid()
    try:
        with database(f"appstore_review_a_{pid}") as db_a, tempfile.TemporaryDirectory(prefix="peil-review-") as tmp:
            # ── A: installatie zonder HUB_URL/token (koppelen via de statuspagina), gevulde ingebouwde catalogus
            a = Dienst(Path(tmp) / "a", db_a, {}, catalogus)
            a.start("serve")
            a.start("worker")
            wacht(
                lambda: "Ingebouwde catalogus" in a.pagina() and "Gezond" in a.pagina(),
                60,
                "catalogus gesynchroniseerd",
            )
            review.meet(a, "niet-gekoppeld")
            review.meet(
                a,
                "koppelen-invoerfouten",
                actie="formulier",
                status=422,
                velden={"hub_adres": "http://nas.invalid:30400/hub", "koppelcode": "123", "hub_vingerafdruk": "ABCD"},
            )
            review.meet(
                a,
                "vingerafdruk-onjuist",
                actie="formulier",
                status=400,
                velden={
                    "hub_adres": hub_url,
                    "koppelcode": hub.add_code(),
                    "hub_vingerafdruk": andere_vingerafdruk(vinger),
                },
            )
            review.meet(
                a,
                "hub-onbereikbaar",
                actie="formulier",
                status=502,
                velden={"hub_adres": "http://127.0.0.1:9", "koppelcode": hub.add_code(), "hub_vingerafdruk": vinger},
            )
            review.meet(
                a,
                "koppelen-mislukt",
                actie="formulier",
                status=400,
                velden={"hub_adres": hub_url, "koppelcode": "ABCD-EFGH-JKMN", "hub_vingerafdruk": vinger},
            )
            review.meet(a, "laatste-poging-mislukt")
            # vijfde poging binnen 15 minuten vanaf dit adres (echte HTTP), de zesde in de browser → 429
            vijfde = koppel_post(a, {"hub_adres": hub_url, "koppelcode": "ABCD-EFGH-JKMN", "hub_vingerafdruk": vinger})
            assert vijfde.status_code == 400, vijfde.status_code
            review.meet(
                a,
                "te-veel-pogingen",
                actie="formulier",
                status=429,
                velden={"hub_adres": hub_url, "koppelcode": "ABCD-EFGH-JKMN", "hub_vingerafdruk": vinger},
            )
            a.herstart("serve")  # rate-limit (in het geheugen) weer leeg
            # contract 1.3 §3.7: een POST met geldige herkomst telt eerst als poging, daarna pas het formuliertoken;
            # daarom pas na het legen van de teller (anders 429 in plaats van 403)
            review.meet(
                a,
                "sessie-verlopen",
                actie="formulier-zonder-cookie",
                status=403,
                velden={"hub_adres": hub_url, "koppelcode": "ABCD-EFGH-JKMN", "hub_vingerafdruk": vinger},
            )
            # §3.7 punt 6: het echte formulier in Chromium vanuit een verkeerde herkomst → 403, niets gewijzigd. Met een
            # onbekende koppelcode: glipt een geval toch door, dan weigert de Hub en faalt de test (400 ≠ 403).
            review.meet(
                a,
                "herkomst-geweigerd",
                actie="negatief",
                velden={"hub_adres": hub_url, "koppelcode": "ABCD-EFGH-JKMN", "hub_vingerafdruk": vinger},
            )
            hub.self_status = 503  # de Hub bevestigt de koppeling (services/self) nog niet
            review.meet(
                a,
                "al-gekoppeld",
                actie="al-gekoppeld",
                status=409,
                koppelcode=hub.add_code(),
                velden={"hub_adres": hub_url, "koppelcode": "ABCD-EFGH-JKMN", "hub_vingerafdruk": vinger},
            )
            wacht(lambda: "Niet bevestigd" in a.pagina(), 30, "gekoppeld, nog niet bevestigd")
            review.meet(a, "gekoppeld-niet-bevestigd")
            hub.self_status = None
            a.herstart("worker")  # de worker bevestigt bij de start (§3.6)
            wacht(lambda: "Alles gezond" in a.pagina(), 60, "bevestigd door de Hub")
            # "gekoppeld-melding" hoort bij een echte poging van één browser (§3.7): gecontroleerd in de actie
            # al-gekoppeld (tab 2) en met alle eisen in de offline toestanden (genereer_toestanden.py)
            review.meet(a, "gekoppeld")
            # bronnen via de catalogus-API (beheerder-JWT van de Hub, per wijziging een nieuwe: jti wordt maar één keer
            # geaccepteerd): één onbereikbaar, één uit, één nog nooit gesynchroniseerd

            def beheer() -> dict[str, str]:
                return hub.auth(roles=["admin"])

            def bron(naam: str, url: str) -> httpx.Response:
                r = httpx.post(
                    f"{a.url}/catalog/v1/sources",
                    timeout=10,
                    headers={**beheer(), "Idempotency-Key": str(uuid.uuid4())},
                    json={"name": naam, "kind": "http", "url": url},
                )
                assert r.status_code == 201, r.text
                return r

            community = bron("community", "http://127.0.0.1:9/catalogus").json()
            r = httpx.post(  # synchroniseren (operator); het adres antwoordt niet → laatste synchronisatie mislukt
                f"{a.url}/catalog/v1/sources/{community['id']}/sync",
                timeout=10,
                headers={**hub.auth(roles=["operator"]), "Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 202, r.text
            uit = bron("archief", "http://127.0.0.1:9/archief").json()
            r = httpx.patch(
                f"{a.url}/catalog/v1/sources/{uit['id']}",
                timeout=10,
                json={"enabled": False},
                headers={**beheer(), "Idempotency-Key": str(uuid.uuid4()), "If-Match": uit["etag"]},
            )
            assert r.status_code == 200, r.text
            wacht(lambda: "Laatste synchronisatie mislukt" in a.pagina(), 90, "bron community mislukt")
            a.stop("worker")
            nieuw = bron("nieuw", "http://127.0.0.1:9/nieuw").json()  # zonder worker: nog nooit gesynchroniseerd
            review.meet(a, "catalogus-bronnen")
            # niet gereed: de eigen database is weg (zelfde /appdata, dienst blijft gekoppeld)
            a.herstart("serve", APPSTORE_DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:9/onbereikbaar")
            review.meet(a, "niet-gereed")
            a.herstart("serve")
            # bronnen weer uit (gate verslechtert niet), zodat "ingetrokken" alleen de intrekking laat zien
            for naam_id in (community["id"], nieuw["id"]):
                huidig = httpx.get(f"{a.url}/catalog/v1/sources/{naam_id}", timeout=10, headers=beheer())
                r = httpx.patch(
                    f"{a.url}/catalog/v1/sources/{naam_id}",
                    timeout=10,
                    json={"enabled": False},
                    headers={**beheer(), "Idempotency-Key": str(uuid.uuid4()), "If-Match": huidig.headers["etag"]},
                )
                assert r.status_code == 200, r.text
            # de Hub trekt de koppeling in; de worker merkt het bij zijn bevestiging (401 op services/self, §3.8)
            hub.revoke_all()
            a.start("worker")
            wacht(lambda: "Ingetrokken" in a.pagina(), 60, "ingetrokken")
            review.meet(a, "ingetrokken")
            # foutinjectie: hub-link.json onleesbaar (schijffout of handmatig gewijzigd bestand)
            (a.data / "secrets" / "hub-link.json").write_text("{kapot", encoding="utf-8")
            review.meet(a, "koppelstatus-onleesbaar")
            a.stop_alles()

            with database(f"appstore_review_b_{pid}") as db_b:
                # ── B: koppelen met het eenmalige token uit het formulier van de Custom App; lege ingebouwde catalogus
                hub.down = True
                b = Dienst(
                    Path(tmp) / "b",
                    db_b,
                    {"HUB_URL": hub_url, "HUB_ENROLLMENT_TOKEN": hub.add_token(), "HUB_KEY_FINGERPRINT": vinger},
                    [],
                )
                b.start("serve")
                b.start("worker")
                wacht(lambda: "Koppelen bezig" in b.pagina() and "Nog leeg" in b.pagina(), 60, "koppelpoging loopt")
                review.meet(b, "koppelen-loopt")
                hub.down = False
                wacht(
                    lambda: "Gekoppeld" in b.pagina() and "Koppelen bezig" not in b.pagina(), 120, "gekoppeld met token"
                )
                r = httpx.post(
                    f"{b.url}/connector/v1/hub-link/unlink",
                    timeout=10,
                    headers={**hub.service_auth(), "Idempotency-Key": str(uuid.uuid4())},
                )
                assert r.status_code == 200, r.text
                review.meet(b, "ontkoppeld")
                b.stop_alles()
    finally:
        for d in ACTIEF:
            d.stop_alles()
        server.should_exit = True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--afdrukken", type=Path, default=None, help="map voor de schermafdrukken (weglaten = geen)")
    p.add_argument("--meting", type=Path, default=Path(tempfile.gettempdir()) / "peil-review")
    args = p.parse_args()
    if args.afdrukken:
        args.afdrukken = args.afdrukken.resolve()
    review = Review(args.afdrukken, args.meting.resolve())
    draai(review)
    print("\nUitkomst per toestand (0 = alle eisen gehaald):")
    for naam, rc in review.uitkomst:
        print(f"  {naam}: {rc}")
    return 0 if all(rc == 0 for _, rc in review.uitkomst) else 1


if __name__ == "__main__":
    sys.exit(main())
