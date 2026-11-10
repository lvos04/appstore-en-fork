"""Verboden adresbereiken ook na DNS-resolutie, bij elke verbinding (contract 1.3 §3.2): een hostnaam die naar
loopback, link-local (bv. ``169.254.169.254``), ``0.0.0.0``/``::`` of multicast wijst, krijgt geen verbinding."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpcore
import httpx
import pytest

from appstore.config import Settings
from appstore.netguard import ForbiddenAddressError, GuardedBackend, guarded_transport


def resolver(table: dict[str, list[str]]) -> Any:
    def resolve(host: str, port: int) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in table[host]
        ]

    return resolve


@pytest.fixture
def local_server() -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "0.0.0.0", "::1", "fe80::1", "::", "224.0.0.1", "::ffff:127.0.0.1"]
)
def test_name_resolving_to_a_forbidden_range_is_refused(address: str) -> None:
    backend = GuardedBackend(resolver=resolver({"rebind.test": [address]}))
    with pytest.raises(ForbiddenAddressError):
        backend.connect_tcp("rebind.test", 80, timeout=1)


def test_every_connection_resolves_again_dns_rebinding(local_server: int) -> None:
    """Eerst een toegestaan adres, daarna (rebinding) loopback: de tweede verbinding wordt geweigerd."""
    answers = iter([["192.0.2.1"], ["127.0.0.1"]])
    seen: list[str] = []

    def resolve(host: str, port: int) -> list[tuple[Any, ...]]:
        addrs = next(answers)
        seen.extend(addrs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addrs]

    backend = GuardedBackend(resolver=resolve)
    with pytest.raises((httpcore.ConnectError, httpcore.ConnectTimeout)) as first:  # TEST-NET-1: onbereikbaar
        backend.connect_tcp("wissel.test", local_server, timeout=0.2)
    assert not isinstance(first.value, ForbiddenAddressError)  # toegestaan adres: wél geprobeerd
    with pytest.raises(ForbiddenAddressError):
        backend.connect_tcp("wissel.test", local_server, timeout=0.2)
    assert seen == ["192.0.2.1", "127.0.0.1"]


def test_loopback_only_with_the_development_flag(local_server: int) -> None:
    table = {"lokaal.test": ["127.0.0.1"]}
    strikt = httpx.Client(transport=guarded_transport(allow_loopback=False, resolver=resolver(table)))
    with strikt as c, pytest.raises(httpx.ConnectError):
        c.get(f"http://lokaal.test:{local_server}/")
    with httpx.Client(transport=guarded_transport(allow_loopback=True, resolver=resolver(table))) as c:
        assert c.get(f"http://lokaal.test:{local_server}/").text == "ok"
    with (
        httpx.Client(
            transport=guarded_transport(allow_loopback=True, resolver=resolver({"m.test": ["169.254.1.1"]}))
        ) as c,
        pytest.raises(httpx.ConnectError),
    ):  # link-local nooit, ook niet met de vlag
        c.get("http://m.test/")


def test_default_factory_uses_the_guard(tmp_path: Any) -> None:
    from appstore.app import default_http_factory

    settings = Settings(
        data_dir=tmp_path,
        database_url="postgresql+psycopg://niet-gebruikt",
        host="127.0.0.1",
        port=0,
        hub_url=None,
        enrollment_token=None,
        public_url=None,
        bundled_catalog_dir=tmp_path,
        allow_insecure_sources=False,
    )
    with default_http_factory(settings)() as client, pytest.raises(httpx.ConnectError):
        client.get("http://127.0.0.1:9/")  # loopback zonder ontwikkelvlag: nooit verbinden
