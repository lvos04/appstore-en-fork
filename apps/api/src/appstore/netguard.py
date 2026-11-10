"""Verboden adresbereiken ook na DNS-resolutie, bij elke verbinding (contract 1.3 §3.2, A11).

De vormcontrole van een adres (``config.normalize_hub_url``) ziet alleen de tekst. Een hostnaam kan naar loopback,
link-local (bv. ``169.254.169.254``), ``0.0.0.0``/``::`` of multicast wijzen — en bij DNS-rebinding per verbinding naar
een ander adres. Daarom lost deze netwerklaag de naam bij **elke** nieuwe TCP-verbinding zelf op, weigert elk verboden
adres en verbindt met het gecontroleerde IP-adres (TLS controleert nog steeds de naam: httpcore geeft de oorspronkelijke
host als SNI/``server_hostname`` mee). Er is dus geen venster tussen controleren en verbinden.

Geldt voor alle uitgaande HTTP-aanroepen van de dienst (Hub én catalogusbronnen). Loopback alleen met de
ontwikkelvlag ``APPSTORE_ALLOW_LOOPBACK_HUB``.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Callable, Iterable
from typing import Any

import httpcore
import httpx

from .config import forbidden_ip

log = logging.getLogger("appstore.netguard")

Resolver = Callable[[str, int], list[tuple[Any, ...]]]


def _resolve(host: str, port: int) -> list[tuple[Any, ...]]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


class ForbiddenAddressError(httpcore.ConnectError):
    """Het (opgeloste) adres valt in een verboden bereik; er is geen verbinding gemaakt."""


class GuardedBackend(httpcore.SyncBackend):
    def __init__(self, *, allow_loopback: bool = False, resolver: Resolver | None = None) -> None:
        self.allow_loopback = allow_loopback
        self.resolver = resolver or _resolve

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            infos = self.resolver(host, port)
        except OSError as exc:
            raise httpcore.ConnectError(f"naam niet op te lossen: {type(exc).__name__}") from exc
        last: Exception | None = None
        tried: set[str] = set()
        for info in infos:
            address = str(info[4][0]).split("%", 1)[0]
            if address in tried:
                continue
            tried.add(address)
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if reason := forbidden_ip(ip, allow_loopback=self.allow_loopback):
                log.warning("verbinding geweigerd: %s wijst naar een %s-adres (contract §3.2)", host, reason)
                last = ForbiddenAddressError(f"{host} wijst naar een verboden adres ({reason})")
                continue
            try:
                return super().connect_tcp(address, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as exc:
                last = exc
        if last is None:
            raise httpcore.ConnectError(f"geen bruikbaar adres voor {host}")
        raise last


def guarded_transport(*, allow_loopback: bool, resolver: Resolver | None = None) -> httpx.HTTPTransport:
    """``httpx.HTTPTransport`` zonder proxy uit de omgeving, met :class:`GuardedBackend` als netwerklaag."""
    transport = httpx.HTTPTransport(trust_env=False)
    old = transport._pool
    transport._pool = httpcore.ConnectionPool(
        ssl_context=old._ssl_context,
        max_connections=old._max_connections,
        max_keepalive_connections=old._max_keepalive_connections,
        keepalive_expiry=old._keepalive_expiry,
        http1=True,
        http2=False,
        network_backend=GuardedBackend(allow_loopback=allow_loopback, resolver=resolver),
    )
    return transport
