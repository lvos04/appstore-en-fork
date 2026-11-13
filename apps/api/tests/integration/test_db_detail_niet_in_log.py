"""KLEIN-34: een onverwachte databasefout zet de DETAIL-regel van PostgreSQL niet in de app-log.

Bij een unique-schending noemt PostgreSQL in DETAIL de sleutelwaarde ("Key (kolom)=(waarde) already exists."), bij een
check- of not-null-schending de hele rij ("Failing row contains (...)"). ``hide_parameters=True`` (V-094, KLEIN-26) haalt
alleen de parameters weg, niet deze regel. De algemene foutafhandeling (API en worker) logt daarom bij een databasefout
alleen soort, sqlstate, constraint en tabel; een logfilter op de handlers van de app haalt DETAIL ook elders weg
(vangnet). Het HTTP-antwoord blijft de vaste 500 ``internal_error``; de taakfout van de worker blijft gelijk.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from appstore.api.common import AppContext
from appstore.db import make_engine, make_sessionmaker
from appstore.errors import install_error_handlers
from appstore.models import SyncJob

pytestmark = pytest.mark.integration

# Herkenbare testwaarden (geen echte geheimen).
SLEUTELWAARDE = "asf-klein34-sleutel-0123456789"
RIJWAARDE = "asf-klein34-rijgeheim-9876543210"
TRACE = "00000000-0000-4000-8000-00000000k034"


def _schending(s: Session, soort: str) -> None:
    """Een echte constraint-schending in een tijdelijke tabel met benoemde constraints."""
    s.execute(
        text(
            "CREATE TEMP TABLE k34_proef (sleutel text, geheim text, rol text, "
            "CONSTRAINT uq_k34_sleutel UNIQUE (sleutel), "
            "CONSTRAINT ck_k34_rol CHECK (rol IN ('lezer', 'beheerder')))"
        )
    )
    s.execute(text("INSERT INTO k34_proef VALUES (:s, :g, 'lezer')"), {"s": SLEUTELWAARDE, "g": RIJWAARDE})
    if soort == "unique":
        s.execute(text("INSERT INTO k34_proef VALUES (:s, :g, 'lezer')"), {"s": SLEUTELWAARDE, "g": RIJWAARDE})
    else:
        s.execute(text("INSERT INTO k34_proef VALUES (:s, :g, 'onbekend')"), {"s": "ander", "g": RIJWAARDE})


def _geen_detail(tekst: str) -> None:
    tekst = tekst.replace("[DETAIL weggelaten]", "")  # de vaste aantekening van het filter telt niet
    for verboden in (SLEUTELWAARDE, RIJWAARDE, "DETAIL", "Failing row", "Key ("):
        assert verboden not in tekst, f"{verboden!r} staat in de log"


@pytest.mark.parametrize(
    ("soort", "sqlstate", "constraint"),
    [("unique", "23505", "uq_k34_sleutel"), ("check", "23514", "ck_k34_rol")],
)
def test_api_databasefout_logt_geen_detail(
    database_url: str, caplog: pytest.LogCaptureFixture, soort: str, sqlstate: str, constraint: str
) -> None:
    engine = make_engine(database_url)
    sessions = make_sessionmaker(engine)
    app = FastAPI()
    install_error_handlers(app)

    @app.middleware("http")
    async def _trace(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.trace_id = TRACE
        return await call_next(request)

    @app.get("/proef")
    def _proef() -> None:
        with sessions() as s:
            _schending(s, soort)

    try:
        with caplog.at_level(logging.DEBUG), TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/proef")
    finally:
        engine.dispose()
    # Het antwoord is precies zoals voorheen: vaste 500 zonder fouttekst.
    assert r.status_code == 500
    assert r.json() == {
        "code": "internal_error",
        "message": "Interne fout; zie de app-log met dit trace_id.",
        "details": {},
        "trace_id": TRACE,
    }
    eigen = [rec for rec in caplog.records if rec.name == "appstore"]
    assert eigen, "de fout is niet gelogd"
    logtekst = "\n".join(caplog.handler.format(rec) for rec in eigen)
    _geen_detail(logtekst)
    assert sqlstate in logtekst and constraint in logtekst and "k34_proef" in logtekst
    assert TRACE in logtekst


def test_worker_databasefout_logt_geen_detail(
    worker_ctx: AppContext, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from appstore import worker

    with worker_ctx.sessions() as db:
        job_id = worker.enqueue_bundled_sync(db)
        db.commit()
    assert job_id is not None

    def kapotte_sync(db: Session, *_: Any) -> Any:
        _schending(db, "check")

    monkeypatch.setattr(worker, "sync_source", kapotte_sync)
    with caplog.at_level(logging.DEBUG, logger="appstore"):
        assert worker.process_one_job(worker_ctx) is True
    with worker_ctx.sessions() as db:
        job = db.get(SyncJob, job_id)
        assert job is not None
        # De taak mislukt eerlijk, met dezelfde vaste fouttekst als voorheen.
        assert job.status == "failed"
        assert job.error == "Interne fout (IntegrityError)."
    eigen = [rec for rec in caplog.records if rec.name.startswith("appstore")]
    assert eigen, "de fout is niet gelogd"
    logtekst = "\n".join(caplog.handler.format(rec) for rec in eigen)
    _geen_detail(logtekst)
    assert "synchronisatie mislukt" in logtekst
    assert "23514" in logtekst and "ck_k34_rol" in logtekst and "k34_proef" in logtekst


@pytest.mark.parametrize("soort", ["unique", "check"])
def test_logfilter_haalt_detail_uit_bericht_en_traceback(database_url: str, soort: str) -> None:
    from appstore.logfilter import DETAIL_WEGGELATEN, install_log_filter

    engine = make_engine(database_url)
    try:
        with make_sessionmaker(engine)() as s, pytest.raises(DBAPIError) as info:
            _schending(s, soort)
    finally:
        engine.dispose()
    assert "DETAIL" in str(info.value)  # zonder filter staat de regel er echt in
    uit = io.StringIO()
    logger = logging.Logger("k34-proef")
    handler = logging.StreamHandler(uit)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    install_log_filter(logger)
    install_log_filter(logger)  # tweemaal aanhaken geeft geen dubbel filter
    assert len(handler.filters) == 1
    try:
        raise info.value
    except DBAPIError as exc:
        logger.exception("ergens anders gelogd: %s", exc)
    tekst = uit.getvalue()
    _geen_detail(tekst)
    assert DETAIL_WEGGELATEN in tekst
    assert "ergens anders gelogd" in tekst and "Traceback" in tekst


def test_logfilter_laat_gewone_berichten_en_argumenten_staan() -> None:
    from appstore.logfilter import DbDetailFilter, scrub_detail

    assert scrub_detail("gewone regel\nnog een") == "gewone regel\nnog een"
    record = logging.LogRecord("x", logging.INFO, __file__, 1, '%s - "%s %s"', ("adres", "GET", "/pad"), None)
    assert DbDetailFilter().filter(record)
    assert record.args == ("adres", "GET", "/pad")  # de access-log van uvicorn leest record.args
    assert scrub_detail("fout\nDETAIL:  Key (a)=(geheim) already exists.\n[SQL: x]") == "fout\n[DETAIL weggelaten]"
    assert scrub_detail("x\n  Failing row contains (1, geheim).\ny") == "x\n[DETAIL weggelaten]\ny"


def test_uvicorn_logconfig_heeft_het_filter_op_elke_handler() -> None:
    from appstore.logfilter import uvicorn_log_config

    config = uvicorn_log_config()
    assert config["handlers"], "geen handlers"
    for naam, handler in config["handlers"].items():
        assert "db_detail" in handler.get("filters", []), naam
    assert config["filters"]["db_detail"]["()"] == "appstore.logfilter.DbDetailFilter"
