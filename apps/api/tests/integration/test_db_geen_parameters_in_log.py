"""V-094: een databasefout zet de parameterwaarden niet in de foutmelding of in de app-log.

De productie-engines staan op hide_parameters=True: ``appstore.db.make_engine`` (API, worker en ``db-pending``) en de
engine in ``apps/api/alembic/env.py`` (migraties). Zonder die stand schrijft SQLAlchemy bij elke databasefout
"[parameters: {...}]" met de letterlijke waarden in str(fout); de worker (log.exception) zet die tekst dan in de app-log.
Een parameter kan een geheim of token zijn.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import sqlalchemy
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from appstore import migrations
from appstore.db import make_engine

pytestmark = pytest.mark.integration

# Herkenbare testwaarde (geen echt geheim); te lang voor varchar(8), dus PostgreSQL weigert hem.
TESTWAARDE = "asf-v094-testgeheim-0123456789"


def _controleer_engine(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    with engine.connect() as conn, pytest.raises(DBAPIError) as info:
        conn.execute(text("CREATE TEMP TABLE v094_proef (waarde varchar(8))"))
        conn.execute(text("INSERT INTO v094_proef (waarde) VALUES (:waarde)"), {"waarde": TESTWAARDE})
    fout = info.value
    # De fout zelf blijft bruikbaar: de SQL-tekst en de oorzaak staan er nog in.
    assert "INSERT INTO v094_proef" in str(fout)
    assert "parameters hidden" in str(fout)
    assert TESTWAARDE not in str(fout)
    assert TESTWAARDE not in repr(fout)
    # Zoals de worker het logt (appstore.worker: log.exception).
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="appstore.worker"):
        try:
            raise fout
        except DBAPIError:
            logging.getLogger("appstore.worker").exception("fout in de worker-lus; volgende ronde gaat door")
    assert caplog.records, "de fout is niet gelogd"
    assert "INSERT INTO v094_proef" in caplog.text
    assert TESTWAARDE not in caplog.text


def test_make_engine_zonder_parameterwaarden_in_fout_en_log(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    engine = make_engine(database_url)
    try:
        _controleer_engine(engine, caplog)
    finally:
        engine.dispose()


def test_migratie_engine_zonder_parameterwaarden_in_fout_en_log(
    database_url: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """De engine van alembic/env.py (via migrations.upgrade) onderscheppen en beproeven."""
    echte_create_engine = sqlalchemy.create_engine
    gemaakt: list[Engine] = []

    def spion(*args: Any, **kwargs: Any) -> Engine:
        engine = echte_create_engine(*args, **kwargs)
        gemaakt.append(engine)
        return engine

    # alembic/env.py doet bij elk laden "from sqlalchemy import create_engine"; dat leest dit attribuut.
    monkeypatch.setattr(sqlalchemy, "create_engine", spion)
    migrations.upgrade(database_url)  # al op head: draait alembic/env.py online, zonder wijziging
    monkeypatch.undo()
    assert len(gemaakt) == 1, "alembic/env.py maakte geen eigen engine"
    try:
        _controleer_engine(gemaakt[0], caplog)
    finally:
        gemaakt[0].dispose()
