"""Alembic aansturen (schemamigraties bij de start; zie ``apps/api/alembic``)."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine


def alembic_dir() -> Path:
    env = os.environ.get("APPSTORE_ALEMBIC_DIR")
    if env:
        return Path(env)
    dev = Path(__file__).resolve().parents[2] / "alembic"  # apps/api/alembic in de broncode
    if dev.is_dir():
        return dev
    return Path("/usr/local/share/appstore/alembic")


def config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir()))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(config(database_url), revision)


def head_revision(database_url: str) -> str | None:
    return ScriptDirectory.from_config(config(database_url)).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def pending(engine: Engine, database_url: str) -> bool:
    return current_revision(engine) != head_revision(database_url)
