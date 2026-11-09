"""FastAPI-applicatie: catalogus-API, connectorcontract, gezondheid en statuspagina."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import APP_ID, __version__
from .api import catalog, connector, existing_apps, health, status_page
from .api.common import AppContext
from .auth import Authenticator
from .config import Settings
from .db import make_engine, make_sessionmaker
from .errors import install_error_handlers
from .hublink import HubLinkClient, ensure_link_file
from .hublink_store import HubLinkStore
from .secrets_store import Secrets, ensure_secrets

MAX_BODY = 1024 * 1024
log = logging.getLogger("appstore")


async def _no_receive() -> Message:
    return {"type": "http.disconnect"}


class BodyLimit:
    """Harde grens van 1 MiB per verzoek (contract 1.2 §5), **vóór routering en authenticatie**, ook chunked.

    Een te grote ``Content-Length`` wordt direct geweigerd. Anders leest deze laag de body tellend (hooguit 1 MiB in
    het geheugen) vóórdat routering, aanmelding of een route iets doet, en breekt af met
    ``413 {"code":"payload_too_large","details":{"max_bytes":1048576}}`` zodra er meer binnenkomt. Zo krijgt ook een
    route die de body zelf niet leest (bv. ``hub-link/unlink``), een onbekende route of een verzoek zonder aanmelding
    altijd 413 en nooit eerst 401/404/405. Daarna krijgt de app de gebufferde body.
    """

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY) -> None:
        self.app = app
        self.limit = limit

    @staticmethod
    def _trace(scope: Scope) -> str:
        state = scope.get("state")
        value = state.get("trace_id") if isinstance(state, dict) else None
        return str(value) if value else str(uuid.uuid4())

    async def _refuse(self, scope: Scope, send: Send, status: int, code: str, message: str, details: Any) -> None:
        body = {"code": code, "message": message, "details": details, "trace_id": self._trace(scope)}
        await JSONResponse(body, status_code=status, headers={"Connection": "close"})(scope, _no_receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                text = value.decode("latin-1").strip()
                if not text.isdigit():
                    await self._refuse(scope, send, 400, "bad_request", "Ongeldige Content-Length.", {})
                    return
                if int(text) > self.limit:
                    await self._too_large(scope, send)
                    return
        chunks: list[bytes] = []
        total, more = 0, True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                # client is weg vóór de body compleet was: niets uitvoeren, alleen netjes afsluiten
                await self._refuse(scope, send, 400, "bad_request", "Verbinding verbroken.", {})
                return
            part = message.get("body", b"")
            total += len(part)
            if total > self.limit:
                await self._too_large(scope, send)
                return
            chunks.append(part)
            more = bool(message.get("more_body", False))
        buffered: Message = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return buffered
            return await receive()  # daarna alleen nog http.disconnect

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, send: Send) -> None:
        await self._refuse(
            scope,
            send,
            413,
            "payload_too_large",
            "Het verzoek is te groot (maximaal 1 MiB).",
            {"max_bytes": self.limit},
        )


def default_http_factory(settings: Settings) -> Callable[[], httpx.Client]:
    timeout = httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout)

    def factory() -> httpx.Client:
        # nooit redirects volgen en geen proxy-omgevingsvariabelen gebruiken (contract 1.2 §3.2, §5)
        return httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": f"{APP_ID}/{__version__}"},
        )

    return factory


def create_app(settings: Settings | None = None, *, http_factory: Callable[[], httpx.Client] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    ensure_secrets(settings.secrets_dir)  # idempotent; bestaande geheimen worden nooit overschreven
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    factory = http_factory or default_http_factory(settings)
    app = FastAPI(
        title="appstore-en-fork",
        version=__version__,
        summary="Appstore-catalogus van het TrueNAS Home Platform V2 (B08-001 manifeststandaard, B08-002 bestaande apps).",
        description=(
            "Catalogus-API (`/catalog/v1`, incl. bestaande apps en beslisregister), connectorcontract (`/connector/v1`), "
            "gezondheid en een kleine NL-statuspagina. "
            "Fouten: `{code, message, details, trace_id}`. Mutaties vereisen `Idempotency-Key`. Er wordt nooit iets op "
            "TrueNAS uitgevoerd: installatievoorbeelden leveren alleen getypte `app.create`-waarden."
        ),
        docs_url=None,
        redoc_url=None,
    )
    secrets = Secrets.load(settings.secrets_dir)
    store = HubLinkStore(settings.secrets_dir)
    hub = HubLinkClient(settings, store, secrets, factory, sessions)
    try:
        ensure_link_file(store, sessions, secrets)  # eenmalige overzetting uit de database (contract 1.2 §3.5)
    except Exception:
        log.exception("koppelstatus overzetten mislukt; de worker probeert het opnieuw")
    hub.recover_after_restart(process="api")  # koppelcodes van een vorig API-proces zijn onderbroken (§3.6)
    app.state.ctx = AppContext(
        settings=settings,
        engine=engine,
        sessions=sessions,
        secrets=secrets,
        http_factory=factory,
        store=store,
        hub=hub,
    )
    app.state.authenticator = Authenticator(sessions, store, on_unknown_kid=hub.request_key_poll)
    app.state.pairing_attempts = status_page.pairing_limiter()
    app.state.heavy_limiter = catalog.heavy_limiter()
    install_error_handlers(app)
    # vóór de http-middleware toegevoegd = daarbinnen (de trace_id is dan al gezet), maar wel vóór routering en
    # aanmelding: elke route, ook een onbekende of een zonder body-model, krijgt bij > 1 MiB eerst 413 (§5)
    app.add_middleware(BodyLimit, limit=MAX_BODY)

    @app.middleware("http")
    async def _trace_and_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("x-trace-id")
        try:
            request.state.trace_id = str(uuid.UUID(incoming)) if incoming else str(uuid.uuid4())
        except ValueError:
            request.state.trace_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(health.router)
    app.include_router(status_page.router)
    app.include_router(catalog.router)
    app.include_router(existing_apps.router)
    app.include_router(connector.router)
    return app


def openapi_document(app: FastAPI | None = None) -> dict[str, Any]:
    """OpenAPI zonder database of geheimen te raken (voor export en drift-controle in CI)."""
    from fastapi.openapi.utils import get_openapi

    probe = FastAPI(title="appstore-en-fork", version=__version__)
    for router in (health.router, status_page.router, catalog.router, existing_apps.router, connector.router):
        probe.include_router(router)
    doc = get_openapi(
        title="appstore-en-fork",
        version=__version__,
        summary="Appstore-catalogus van het TrueNAS Home Platform V2 (B08-001 manifeststandaard, B08-002 bestaande apps).",
        description=(
            "Catalogus-API (`/catalog/v1`; bestaande apps `existing-apps` en beslisregister `decisions`: alleen een "
            "register, nooit een actie op de NAS), connectorcontract (`/connector/v1`, incl. `hub-link`), gezondheid. "
            "Fouten: "
            "`{code, message, details, trace_id}`. Mutaties vereisen `Idempotency-Key`. Auth (platformcontract 1.2 §4): "
            "alleen Hub-JWT's (EdDSA, gepinde sleutels uit de koppeling) als `Authorization: Bearer` — gebruikers-JWT "
            "(`token_use: user`) of dienst-JWT van de Hub (`token_use: hub_service`); `jti` verplicht, hergebruik bij "
            "mutaties geweigerd; een service-token wordt niet geaccepteerd. 401 `details.reason` ∈ not_linked, "
            "revoked, service_token_not_accepted, unknown_key, token_expired, invalid_token, token_use_invalid, "
            "jti_missing, token_replayed."
        ),
        routes=probe.routes,
    )
    doc.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
    doc["security"] = [{"bearer": []}]
    return doc
