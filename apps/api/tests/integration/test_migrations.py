"""Migraties: het schema na ``alembic upgrade head`` is gelijk aan de modellen (geen drift), en 0002 kan terug."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from appstore import migrations
from appstore.models import Base

pytestmark = pytest.mark.integration


def test_models_match_migrations(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        assert diff == [], diff
        assert "jwt_replay" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_replay_table_migration_down_and_up(database_url: str) -> None:
    migrations.downgrade(database_url, "0001")
    engine = create_engine(database_url)
    try:
        assert "jwt_replay" not in inspect(engine).get_table_names()
        assert "hub_link" in inspect(engine).get_table_names()  # oude tabel blijft voor de eenmalige overzetting
    finally:
        engine.dispose()
    migrations.upgrade(database_url)
    engine = create_engine(database_url)
    try:
        assert "jwt_replay" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_existing_apps_migration_down_and_up(database_url: str) -> None:
    """0003 (B08-002) voegt alleen twee tabellen toe; terug naar 0002 verwijdert alleen die twee."""
    migrations.downgrade(database_url, "0002")
    engine = create_engine(database_url)
    try:
        names = set(inspect(engine).get_table_names())
        assert "existing_app" not in names and "decision_record" not in names
        assert {"catalog_entry", "manifest_version", "jwt_replay", "evidence_record"} <= names
    finally:
        engine.dispose()
    migrations.upgrade(database_url)
    engine = create_engine(database_url)
    try:
        assert {"existing_app", "decision_record"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
