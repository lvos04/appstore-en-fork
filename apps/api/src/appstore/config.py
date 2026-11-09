"""Instellingen uit omgevingsvariabelen. De Custom App heeft géén verplichte variabelen (contract §10)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PORT = 30430
DEFAULT_DB_URL = "postgresql+psycopg://appstore@/appstore?host=/tmp/appstore-pg"
DEFAULT_BUNDLED_DIR = "/usr/local/share/appstore/catalog"


def _opt(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _bool(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "ja", "yes", "on")


def normalize_hub_url(value: str | None) -> str | None:
    """Hub-adres valideren: http(s), host verplicht, geen gebruikersnaam/wachtwoord, geen pad/query."""
    if not value:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("HUB_URL moet beginnen met http:// of https:// en een host bevatten")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("HUB_URL mag geen gebruikersnaam, wachtwoord, query of fragment bevatten")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    host: str
    port: int
    hub_url: str | None
    enrollment_token: str | None
    public_url: str | None
    bundled_catalog_dir: Path
    allow_insecure_sources: bool
    jwks_ttl_seconds: int = 300
    jwks_max_stale_seconds: int = 900
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    log_level: str = "info"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        hub_url = None
        try:
            hub_url = normalize_hub_url(_opt(env, "HUB_URL"))
        except ValueError:
            hub_url = None  # ongeldig adres: niet gekoppeld; de statuspagina toont dat eerlijk
        return cls(
            data_dir=Path(env.get("APPSTORE_DATA_DIR", "/appdata")),
            database_url=env.get("APPSTORE_DATABASE_URL", DEFAULT_DB_URL),
            host=env.get("APPSTORE_HOST", "0.0.0.0"),  # noqa: S104 - container luistert op alle adressen
            port=int(env.get("APPSTORE_PORT", str(DEFAULT_PORT))),
            hub_url=hub_url,
            enrollment_token=_opt(env, "HUB_ENROLLMENT_TOKEN") or _opt(env, "ENROLLMENT_TOKEN"),
            public_url=_opt(env, "APPSTORE_PUBLIC_URL"),
            bundled_catalog_dir=Path(env.get("APPSTORE_BUNDLED_CATALOG_DIR", DEFAULT_BUNDLED_DIR)),
            allow_insecure_sources=_bool(env, "APPSTORE_ALLOW_INSECURE_SOURCES"),
            log_level=env.get("APPSTORE_LOG_LEVEL", "info"),
        )
