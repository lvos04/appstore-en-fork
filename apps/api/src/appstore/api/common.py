"""Gedeelde API-hulpen: context, paginering (cursor/limit ≤ 200), ETag en tijden."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..errors import ApiError, trace_id, validation_error
from ..hublink import HubLinkClient
from ..hublink_store import HubLinkStore
from ..secrets_store import Secrets

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    secrets: Secrets
    http_factory: Any  # Callable[[], httpx.Client]
    store: HubLinkStore
    hub: HubLinkClient


def ctx(request: Request) -> AppContext:
    value: AppContext = request.app.state.ctx
    return value


def get_db(request: Request) -> Iterator[Session]:
    db = ctx(request).sessions()
    try:
        yield db
    finally:
        db.close()


def tid(request: Request) -> str:
    return trace_id(request)


def encode_cursor(value: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> Any:
    if not cursor:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError) as exc:
        raise cursor_invalid() from exc


def cursor_invalid() -> ApiError:
    """§5: een ongeldige cursor is ongeldige invoer (422 ``validation_error``, ``details.reason`` ``cursor_invalid``)."""
    return validation_error(
        "De cursor is ongeldig; begin opnieuw zonder cursor.",
        [{"veld": "query.cursor", "fout": "ongeldige cursor", "type": "cursor_invalid"}],
        reason="cursor_invalid",
    )


def check_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise validation_error(
            f"limit moet tussen 1 en {MAX_LIMIT} liggen.",
            [{"veld": "query.limit", "fout": f"1 ≤ limit ≤ {MAX_LIMIT}", "type": "limit_invalid"}],
            reason="limit_invalid",
        )
    return limit


def etag(row_version: int) -> str:
    return f'W/"{row_version}"'


def require_if_match(request: Request, row_version: int) -> None:
    header = request.headers.get("if-match")
    if not header:
        raise ApiError(428, "precondition_required", "Header If-Match (ETag) is verplicht bij wijzigen.")
    if header.strip() not in (etag(row_version), f'"{row_version}"'):
        raise ApiError(
            412,
            "precondition_failed",
            "Het object is intussen gewijzigd; haal het opnieuw op.",
            {"current_etag": etag(row_version)},
        )
