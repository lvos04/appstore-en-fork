"""Instellingen uit omgevingsvariabelen. De Custom App heeft géén verplichte variabelen (contract §10).

Koppelen (contract 1.2 §3.2): ``HUB_URL``, ``HUB_ENROLLMENT_TOKEN`` en optioneel ``HUB_KEY_FINGERPRINT``. De oude alias
``ENROLLMENT_TOKEN`` wordt in deze release nog gelezen, met een waarschuwing in de log.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("appstore.config")

DEFAULT_PORT = 30430
DEFAULT_DB_URL = "postgresql+psycopg://appstore@/appstore?host=/tmp/appstore-pg"
DEFAULT_BUNDLED_DIR = "/usr/local/share/appstore/catalog"


def _opt(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _interval(env: Mapping[str, str], name: str, default: int) -> int:
    """Interval in seconden: 0 = uit, anders 300…86400 (een ongeldige waarde → standaard, met een waarschuwing)."""
    raw = _opt(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value == 0 or 300 <= value <= 86400:
        return value
    log.warning("%s moet 0 (uit) of 300…86400 seconden zijn; standaard %d s wordt gebruikt", name, default)
    return default


def _bool(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "ja", "yes", "on")


def normalize_hub_url(value: str | None, *, allow_loopback: bool = False) -> str | None:
    """Hub-adres volgens contract 1.2 §3.2 (ook voor het adres op de statuspagina).

    ``http(s)://<host>[:<poort>]`` zonder pad, query, fragment of inloggegevens; geen loopback, geen link-local
    (``169.254.0.0/16``, ``fe80::/10``) en geen ``0.0.0.0``/``::``. Loopback alleen met de ontwikkelvlag
    ``APPSTORE_ALLOW_LOOPBACK_HUB``. Eén afsluitende ``/`` wordt weggelaten.
    """
    if not value:
        return None
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("het Hub-adres moet beginnen met http:// of https:// en een host bevatten")
    if parts.username or parts.password or parts.query or parts.fragment or "@" in parts.netloc:
        raise ValueError("het Hub-adres mag geen gebruikersnaam, wachtwoord, query of fragment bevatten")
    if parts.path not in ("", "/"):
        raise ValueError("het Hub-adres mag geen pad bevatten (alleen http(s)://host[:poort])")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("ongeldige poort in het Hub-adres") from exc
    host = parts.hostname.lower()
    if any(ch.isspace() for ch in raw) or not host:
        raise ValueError("ongeldig Hub-adres")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_unspecified or ip.is_link_local or ip.is_multicast:
            raise ValueError("het Hub-adres mag geen 0.0.0.0, ::, link-local- of multicastadres zijn")
        if ip.is_loopback and not allow_loopback:
            raise ValueError("het Hub-adres mag geen loopback-adres zijn")
    elif (host == "localhost" or host.endswith(".localhost")) and not allow_loopback:
        raise ValueError("het Hub-adres mag geen loopback-adres zijn")
    netloc = f"[{host}]" if ip is not None and ip.version == 6 else host
    if port is not None:
        netloc += f":{port}"
    return f"{parts.scheme}://{netloc}"


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
    hub_key_fingerprint: str | None = None
    allow_loopback_hub: bool = False
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    log_level: str = "info"
    #: B08-002: periodiek de app-inventaris van de Hub lezen (alleen lezen); 0 = uit (alleen op verzoek via de API)
    import_interval_s: int = 900

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        allow_loopback = _bool(env, "APPSTORE_ALLOW_LOOPBACK_HUB")
        hub_url = None
        try:
            hub_url = normalize_hub_url(_opt(env, "HUB_URL"), allow_loopback=allow_loopback)
        except ValueError as exc:
            log.warning("HUB_URL wordt niet gebruikt: %s", exc)  # niet gekoppeld; de statuspagina toont dat eerlijk
        token = _opt(env, "HUB_ENROLLMENT_TOKEN")
        if token is None and _opt(env, "ENROLLMENT_TOKEN"):
            log.warning("ENROLLMENT_TOKEN is vervallen (contract 1.2 §3.2); gebruik HUB_ENROLLMENT_TOKEN")
            token = _opt(env, "ENROLLMENT_TOKEN")
        fingerprint = _opt(env, "HUB_KEY_FINGERPRINT")
        if fingerprint is not None:
            from .hubkeys import normalize_fingerprint

            if normalize_fingerprint(fingerprint) is None:
                log.warning(
                    "HUB_KEY_FINGERPRINT heeft niet het formaat XXXX-XXXX-XXXX-XXXX; koppelen met het env-token zal mislukken"
                )
        return cls(
            data_dir=Path(env.get("APPSTORE_DATA_DIR", "/appdata")),
            database_url=env.get("APPSTORE_DATABASE_URL", DEFAULT_DB_URL),
            host=env.get("APPSTORE_HOST", "0.0.0.0"),  # noqa: S104 - container luistert op alle adressen
            port=int(env.get("APPSTORE_PORT", str(DEFAULT_PORT))),
            hub_url=hub_url,
            enrollment_token=token,
            public_url=_opt(env, "APPSTORE_PUBLIC_URL"),
            bundled_catalog_dir=Path(env.get("APPSTORE_BUNDLED_CATALOG_DIR", DEFAULT_BUNDLED_DIR)),
            allow_insecure_sources=_bool(env, "APPSTORE_ALLOW_INSECURE_SOURCES"),
            log_level=env.get("APPSTORE_LOG_LEVEL", "info"),
            hub_key_fingerprint=fingerprint,
            allow_loopback_hub=allow_loopback,
            import_interval_s=_interval(env, "APPSTORE_IMPORT_INTERVAL_S", 900),
        )
