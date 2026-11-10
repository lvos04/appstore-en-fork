"""Instellingen uit omgevingsvariabelen. De Custom App heeft géén verplichte variabelen (contract §10).

Koppelen (contract 1.3 §3.2): ``HUB_URL``, ``HUB_ENROLLMENT_TOKEN`` en optioneel ``HUB_KEY_FINGERPRINT``. De oude alias
``ENROLLMENT_TOKEN`` wordt in deze release nog gelezen, met een waarschuwing in de log.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
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


_NUMERIC_LABEL = re.compile(r"^(?:[0-9]+|0x[0-9a-f]*)$")
_CANONICAL_IPV4 = re.compile(
    r"^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])$"
)
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class AddressError(ValueError):
    """Een adres breekt de adresregels van contract 1.3 §3.2."""


def parse_host(raw: str) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None]:
    """Host volgens de adresregels van contract 1.3 §3.2 (vorm).

    * IPv4 alleen als ``a.b.c.d`` (vier decimale delen 0–255, zonder voorloopnul). Een host die op een getal eindigt
      (laatste deel alleen cijfers of ``0x…``; zo leest een browser hem als IPv4) en daar niet aan voldoet — bv.
      ``127.1``, ``2130706433``, ``0x7f000001``, ``010.0.0.1``, ``0`` — is ongeldig.
    * IPv6 alleen tussen ``[]``.
    * Eén punt aan het eind van een naam telt niet mee (``localhost.`` = ``localhost``).

    Geeft ``(host in kleine letters zonder haken of slotpunt, ip of None)``; :class:`AddressError` bij een ongeldige vorm.
    """
    host = raw.strip().lower()
    if not host or any(ch.isspace() for ch in host):
        raise AddressError("lege of ongeldige host")
    if host.startswith("["):
        if not host.endswith("]"):
            raise AddressError("IPv6 zonder sluitende ]")
        try:
            ip6 = ipaddress.IPv6Address(host[1:-1])
        except ValueError as exc:
            raise AddressError("geen geldig IPv6-adres tussen []") from exc
        return str(ip6), ip6
    if ":" in host:
        raise AddressError("IPv6 alleen tussen []")
    if host.endswith("."):
        host = host[:-1]
    if not host or host.endswith(".") or ".." in host:
        raise AddressError("ongeldige hostnaam")
    labels = host.split(".")
    if _NUMERIC_LABEL.match(labels[-1]):
        if not _CANONICAL_IPV4.match(host):
            raise AddressError("een IPv4-adres alleen als a.b.c.d (vier decimale delen, zonder voorloopnul)")
        return host, ipaddress.IPv4Address(host)
    if not all(_HOST_LABEL.match(label) for label in labels):
        raise AddressError("ongeldige hostnaam")
    return host, None


def forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_loopback: bool = False) -> str | None:
    """Reden waarom ``ip`` als Hub-adres verboden is (§3.2), of ``None``. Geldt ook voor het opgeloste adres van een
    hostnaam, bij elke verbinding (zie :mod:`appstore.netguard`)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified:
        return "0.0.0.0 of ::"
    if ip.is_link_local:
        return "link-local (169.254.0.0/16, fe80::/10)"
    if ip.is_multicast:
        return "multicast"
    if ip.is_loopback and not allow_loopback:
        return "loopback"
    return None


def normalize_hub_url(value: str | None, *, allow_loopback: bool = False) -> str | None:
    """Hub-adres volgens contract 1.3 §3.2 (ook voor het adres op de statuspagina).

    ``http(s)://<host>[:<poort>]`` zonder pad, query, fragment of inloggegevens; host volgens :func:`parse_host`; geen
    loopback, geen link-local (``169.254.0.0/16``, ``fe80::/10``) en geen ``0.0.0.0``/``::``. Loopback alleen met de
    ontwikkelvlag ``APPSTORE_ALLOW_LOOPBACK_HUB``. Eén afsluitende ``/`` wordt weggelaten.
    """
    if not value:
        return None
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("het Hub-adres moet beginnen met http:// of https:// en een host bevatten")
    if parts.username or parts.password or parts.query or parts.fragment or "@" in parts.netloc:
        raise ValueError("het Hub-adres mag geen gebruikersnaam, wachtwoord, query of fragment bevatten")
    if parts.path not in ("", "/"):
        raise ValueError("het Hub-adres mag geen pad bevatten (alleen http(s)://host[:poort])")
    if any(ch.isspace() for ch in raw):
        raise ValueError("ongeldig Hub-adres")
    host, port = _split_netloc(parts.netloc)
    try:
        name, ip = parse_host(host)
    except AddressError as exc:
        raise ValueError(f"ongeldige host in het Hub-adres: {exc}") from exc
    if ip is not None:
        if reason := forbidden_ip(ip, allow_loopback=allow_loopback):
            raise ValueError(f"het Hub-adres mag geen {reason}-adres zijn")
    elif (name == "localhost" or name.endswith(".localhost")) and not allow_loopback:
        raise ValueError("het Hub-adres mag geen loopback-adres zijn")
    netloc = f"[{name}]" if isinstance(ip, ipaddress.IPv6Address) else name
    if port is not None:
        netloc += f":{port}"
    return f"{parts.scheme}://{netloc}"


def _split_netloc(netloc: str) -> tuple[str, int | None]:
    """``host[:poort]`` (IPv6 tussen ``[]``) → (host, poort). :class:`ValueError` bij een ongeldige poort."""
    if netloc.startswith("["):
        end = netloc.find("]")
        host, rest = netloc[: end + 1], netloc[end + 1 :]
    else:
        host, _, port_text = netloc.partition(":")
        rest = f":{port_text}" if _ else ""
    if not rest:
        return host, None
    if not rest.startswith(":") or not rest[1:].isdigit() or not 1 <= int(rest[1:]) <= 65535:
        raise ValueError("ongeldige poort in het Hub-adres")
    return host, int(rest[1:])


def normalize_base_url(value: str | None, *, allow_loopback: bool = False) -> str | None:
    """``base_url`` voor het enroll-verzoek (contract 1.3 §3.3): alleen ``http(s)://host[:poort][/pad]``, ≤ 512 tekens en
    volgens de adresregels van §3.2; anders ``None`` (de dienst stuurt dan ``null``, nooit een ongeldige waarde)."""
    if not value or len(value) > 512:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        return None
    if parts.username or parts.password or "@" in parts.netloc or any(ch.isspace() for ch in value.strip()):
        return None
    if parts.path and not re.fullmatch(r"(/[A-Za-z0-9._~-]*)+", parts.path):
        return None
    try:
        base = normalize_hub_url(f"{parts.scheme}://{parts.netloc}", allow_loopback=allow_loopback)
    except ValueError:
        return None
    if base is None:
        return None
    path = parts.path.rstrip("/")
    return base + path


def host_allowed_for_form(host_header: str | None) -> bool:
    """``Host``-beperking tegen DNS-rebinding (contract 1.3 §3.7 punt 4, keuze K1).

    Een formulier-POST alleen als de ``Host``-header (zonder poort) een IP-adres in de vorm van §3.2 is (IPv6 tussen
    ``[]``), een naam zonder punt, of een naam onder ``.local``, ``.lan``, ``.internal`` of ``.home.arpa``.
    """
    if not host_header:
        return False
    try:
        host, _ = _split_netloc(host_header.strip())
        name, ip = parse_host(host)
    except ValueError:
        return False
    if ip is not None:
        return True
    if "." not in name:
        return True
    return name.endswith((".local", ".lan", ".internal", ".home.arpa"))


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
    #: ``HUB_URL`` stond in het formulier maar breekt §3.2 (dan is ``hub_url`` ``None``; ``last_error`` hub_adres_ongeldig)
    hub_url_invalid: bool = False

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        allow_loopback = _bool(env, "APPSTORE_ALLOW_LOOPBACK_HUB")
        hub_url = None
        hub_url_invalid = False
        try:
            hub_url = normalize_hub_url(_opt(env, "HUB_URL"), allow_loopback=allow_loopback)
        except ValueError as exc:
            hub_url_invalid = True  # §3.5: last_error hub_adres_ongeldig; de Hub wordt niet aangesproken
            log.warning("HUB_URL wordt niet gebruikt: %s", exc)
        token = _opt(env, "HUB_ENROLLMENT_TOKEN")
        if token is None and _opt(env, "ENROLLMENT_TOKEN"):
            log.warning("ENROLLMENT_TOKEN is vervallen (contract 1.2 §3.2); gebruik HUB_ENROLLMENT_TOKEN")
            token = _opt(env, "ENROLLMENT_TOKEN")
        fingerprint = _opt(env, "HUB_KEY_FINGERPRINT")
        if fingerprint is not None:
            from .hubkeys import normalize_fingerprint

            if normalize_fingerprint(fingerprint) is None:
                log.warning(
                    "HUB_KEY_FINGERPRINT heeft niet het formaat XXXX-XXXX-XXXX-XXXX; koppelen met het env-token start niet"
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
            hub_url_invalid=hub_url_invalid,
        )
