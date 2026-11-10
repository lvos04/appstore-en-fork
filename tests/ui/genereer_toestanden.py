"""Statuspagina per toestand vastleggen uit de **echte** app (FastAPI + PostgreSQL) met de Hub-simulator.

Schrijft per toestand ``<uit>/<toestand>.html`` (de body) en ``<uit>/<toestand>.json`` (HTTP-status en headers, o.a.
de CSP). De Playwright-tests (``statuspagina.spec.ts``) serveren precies die respons aan Chromium, zodat axe, overloop,
CSP en schermafdrukken over de echte uitvoer gaan. Simulator ≠ echte Hub/NAS: uitkomsten zijn "Sim-getest".

Gebruik (vanaf de hoofdmap, met het pakket geïnstalleerd)::

    python tests/ui/genereer_toestanden.py --uit tests/ui/uit

Vereist PostgreSQL (``APPSTORE_TEST_DATABASE_URL`` of 127.0.0.1:5432, gebruiker ``postgres``); maakt en verwijdert een
eigen database ``appstore_ui_<pid>``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from appstore import migrations
from appstore.app import create_app
from appstore.config import Settings
from appstore.hublink_store import LastError, LinkState, Pending
from appstore.timeutil import iso, utcnow
from appstore.worker import build_context, enqueue_bundled_sync, process_one_job
from tests.fakehub import HUB_URL, FakeHub

ADMIN_URL = os.environ.get("APPSTORE_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres")
ORIGIN = {"Origin": "http://testserver"}
#: verplichte toestanden (opdracht AS7) + extra toestanden voor axe/overloop
VERPLICHT = ("niet-gekoppeld", "koppelen-mislukt", "gekoppeld", "ingetrokken", "niet-gereed")


@contextmanager
def database() -> Iterator[str]:
    name = f"appstore_ui_{os.getpid()}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    try:
        migrations.upgrade(url)
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}'"))
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def instellingen(data: Path, database_url: str) -> Settings:
    ingebouwd = data.parent / "catalogus"  # zoals in de image: het eigen manifest is de ingebouwde catalogus
    ingebouwd.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml", ingebouwd)
    return Settings(
        data_dir=data,
        database_url=database_url,
        host="127.0.0.1",
        port=0,
        hub_url=None,
        enrollment_token=None,
        public_url=None,
        bundled_catalog_dir=ingebouwd,
        allow_insecure_sources=False,
    )


def bewaar(uit: Path, naam: str, resp: Any) -> None:
    assert resp.headers["content-type"].startswith("text/html"), (naam, resp.status_code)
    (uit / f"{naam}.html").write_text(resp.text, encoding="utf-8")
    kop = {k.lower(): v for k, v in resp.headers.items() if k.lower() != "set-cookie"}
    (uit / f"{naam}.json").write_text(
        json.dumps({"toestand": naam, "status": resp.status_code, "headers": kop}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{naam}: HTTP {resp.status_code}, {len(resp.text)} tekens")


def formulier(c: TestClient, hub_adres: str, code: str, vinger: str) -> Any:
    c.get("/")
    return c.post(
        "/koppelen",
        data={
            "hub_adres": hub_adres,
            "koppelcode": code,
            "hub_vingerafdruk": vinger,
            "csrf": c.cookies["appstore_csrf"],
        },
        headers=ORIGIN,
        follow_redirects=False,
    )


def met_app(settings: Settings, hub: FakeHub, stap: Callable[[TestClient, Any], None]) -> None:
    app = create_app(settings, http_factory=hub.client_factory())
    try:
        with TestClient(app) as c:
            stap(c, app)
    finally:
        app.state.ctx.engine.dispose()


def catalogus_vullen(settings: Settings, hub: FakeHub) -> None:
    """Ingebouwde catalogus synchroniseren zoals de worker bij de start (echte code, geen nep-rijen)."""
    ctx = build_context(settings, hub.client_factory())
    try:
        with ctx.sessions() as db:
            enqueue_bundled_sync(db)
            db.commit()
        while process_one_job(ctx):
            pass
    finally:
        ctx.engine.dispose()


def genereer(uit: Path) -> None:
    uit.mkdir(parents=True, exist_ok=True)
    with database() as db_url, tempfile.TemporaryDirectory(prefix="appstore-ui-") as tmp:
        hub = FakeHub()
        s = instellingen(Path(tmp) / "appdata", db_url)
        s.data_dir.mkdir(parents=True)

        def nooit_gekoppeld(c: TestClient, app: Any) -> None:
            catalogus_vullen(s, hub)
            bewaar(uit, "niet-gekoppeld", c.get("/"))
            # Hub weigert de koppelcode (verlopen/onbekend): foutpagina met code + trace-ID, veldfout bij de code
            resp = formulier(c, HUB_URL, "ABCD-EFGH-JKMN", hub.fingerprint())
            assert resp.status_code == 400, resp.status_code
            bewaar(uit, "koppelen-mislukt", resp)
            # eigen invoercontrole: drie veldfouten tegelijk, niets naar de Hub
            bewaar(uit, "koppelen-invoerfouten", formulier(c, "http://localhost:30400", "123", "ABCD"))
            # de opgeslagen laatste fout (na herladen) en een lopende poging
            bewaar(uit, "laatste-poging-mislukt", c.get("/"))

            def lopend(st: LinkState) -> LinkState:
                st.pending = Pending(
                    kind="token", secret_sha256="ab" * 32, hub_url=HUB_URL, started_at=iso(utcnow()) or ""
                )
                st.last_error = LastError(code="hub_onbereikbaar", at=iso(utcnow()) or "")
                return st

            app.state.ctx.store.update(lopend)
            bewaar(uit, "koppelen-loopt", c.get("/"))
            # Peil 1.1.1 (E02): lopende poging zonder melding; formulier en stappen verdwijnen
            app.state.ctx.store.update(lambda st: st.model_copy(update={"last_error": None}))
            bewaar(uit, "koppelen-bezig", c.get("/"))
            app.state.ctx.store.update(lambda st: st.model_copy(update={"pending": None, "last_error": None}))
            # contract 1.3 §3.7 (K1): geopend via een publieke naam → vooraf de melding, en de POST wordt geweigerd
            bewaar(uit, "host-niet-toegestaan", c.get("/", headers={"Host": "nas.fritz.box:30430"}))
            resp = c.post(
                "/koppelen",
                data={
                    "hub_adres": HUB_URL,
                    "koppelcode": "ABCD-EFGH-JKMN",
                    "hub_vingerafdruk": hub.fingerprint(),
                    "csrf": c.cookies["appstore_csrf"],
                },
                headers={"Host": "nas.fritz.box:30430", "Origin": "http://nas.fritz.box:30430"},
                follow_redirects=False,
            )
            assert resp.status_code == 403, resp.status_code
            bewaar(uit, "herkomst-host-geweigerd", resp)
            # koppelen met een geldige code en de juiste vingerafdruk
            resp = formulier(c, HUB_URL, hub.add_code(), hub.fingerprint())
            assert resp.status_code == 303, resp.text[:300]
            bewaar(uit, "gekoppeld-melding", c.get(resp.headers["location"]))
            bewaar(uit, "gekoppeld", c.get("/"))

        met_app(s, hub, nooit_gekoppeld)

        # niet gereed: gekoppeld, maar de eigen database is weg (zelfde /appdata, onbereikbare database)
        weg = dataclasses.replace(s, database_url="postgresql+psycopg://postgres@127.0.0.1:9/onbereikbaar")
        met_app(weg, hub, lambda c, _app: bewaar(uit, "niet-gereed", c.get("/")))

        def ingetrokken(c: TestClient, app: Any) -> None:
            hub.revoke_all()
            assert app.state.ctx.hub.confirm(trace_id="ui") is False  # 401 op services/self → ingetrokken (§3.8)
            bewaar(uit, "ingetrokken", c.get("/"))
            app.state.ctx.store.update(lambda st: st.model_copy(update={"state": "ontkoppeld", "last_error": None}))
            bewaar(uit, "ontkoppeld", c.get("/"))
            (s.secrets_dir / "hub-link.json").write_text("{kapot", encoding="utf-8")
            bewaar(uit, "koppelstatus-onleesbaar", c.get("/"))

        met_app(s, hub, ingetrokken)
    for naam in VERPLICHT:
        assert (uit / f"{naam}.html").is_file(), naam


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--uit", type=Path, default=ROOT / "tests" / "ui" / "uit")
    genereer(p.parse_args().uit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
