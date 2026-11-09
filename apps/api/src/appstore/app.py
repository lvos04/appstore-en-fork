"""FastAPI-applicatie: catalogus-API, connectorcontract, gezondheid en statuspagina."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

from . import APP_ID, __version__
from .api import catalog, connector, health, status_page
from .api.common import AppContext
from .auth import Authenticator
from .config import Settings
from .db import make_engine, make_sessionmaker
from .errors import install_error_handlers
from .secrets_store import Secrets, ensure_secrets

MAX_BODY = 1024 * 1024


class BodyLimit:
    """ASGI-middleware: telt de werkelijk ontvangen bytes, ook zonder ``Content-Length`` (chunked).

    Boven ``limit`` wordt het lezen afgebroken met 413 (``payload_too_large``); FastAPI geeft een
    ``HTTPException`` uit de body-lezer ongewijzigd door aan de foutafhandeling.
    """

    def __init__(self, app: Any, limit: int = MAX_BODY) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited() -> Any:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise HTTPException(status_code=413)
            return message

        await self.app(scope, limited, send)


def default_http_factory(settings: Settings) -> Callable[[], httpx.Client]:
    timeout = httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout)

    def factory() -> httpx.Client:
        return httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": f"{APP_ID}/{__version__}"})

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
        summary="Appstore-catalogus van het TrueNAS Home Platform V2 (B08-001 manifeststandaard).",
        description=(
            "Catalogus-API (`/catalog/v1`), connectorcontract (`/connector/v1`), gezondheid en een kleine NL-statuspagina. "
            "Fouten: `{code, message, details, trace_id}`. Mutaties vereisen `Idempotency-Key`. Er wordt nooit iets op "
            "TrueNAS uitgevoerd: installatievoorbeelden leveren alleen getypte `app.create`-waarden."
        ),
        docs_url=None,
        redoc_url=None,
    )
    app.state.ctx = AppContext(
        settings=settings,
        engine=engine,
        sessions=sessions,
        secrets=Secrets.load(settings.secrets_dir),
        http_factory=factory,
    )
    app.state.authenticator = Authenticator(settings, sessions, factory)
    app.state.pairing_attempts = status_page.pairing_limiter()
    app.state.heavy_limiter = catalog.heavy_limiter()
    install_error_handlers(app)
    # binnenste laag (vóór de http-middleware toegevoegd): de body-lezer van FastAPI ziet de 413 direct
    app.add_middleware(BodyLimit, limit=MAX_BODY)

    @app.middleware("http")
    async def _trace_and_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("x-trace-id")
        try:
            request.state.trace_id = str(uuid.UUID(incoming)) if incoming else str(uuid.uuid4())
        except ValueError:
            request.state.trace_id = str(uuid.uuid4())
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {
                    "code": "payload_too_large",
                    "message": "Verzoek is te groot (max 1 MiB).",
                    "details": {},
                    "trace_id": request.state.trace_id,
                },
                status_code=413,
            )
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(health.router)
    app.include_router(status_page.router)
    app.include_router(catalog.router)
    app.include_router(connector.router)
    return app


def openapi_document(app: FastAPI | None = None) -> dict[str, Any]:
    """OpenAPI zonder database of geheimen te raken (voor export en drift-controle in CI)."""
    from fastapi.openapi.utils import get_openapi

    probe = FastAPI(title="appstore-en-fork", version=__version__)
    for router in (health.router, status_page.router, catalog.router, connector.router):
        probe.include_router(router)
    doc = get_openapi(
        title="appstore-en-fork",
        version=__version__,
        summary="Appstore-catalogus van het TrueNAS Home Platform V2 (B08-001 manifeststandaard).",
        description=(
            "Catalogus-API (`/catalog/v1`), connectorcontract (`/connector/v1`), gezondheid. Fouten: "
            "`{code, message, details, trace_id}`. Mutaties vereisen `Idempotency-Key`. Auth: Hub-JWT (EdDSA, JWKS) "
            "of service-token als `Authorization: Bearer`."
        ),
        routes=probe.routes,
    )
    doc.setdefault("components", {})["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
    doc["security"] = [{"bearer": []}]
    return doc
