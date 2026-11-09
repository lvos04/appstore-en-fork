"""Idempotency-Key voor mutaties (contract §5): zelfde key + zelfde verzoek → zelfde antwoord, géén tweede uitvoering."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import Principal
from .errors import ApiError
from .manifest.canonical import canonical_json
from .models import IdempotencyRecord


def idempotency_key(request: Request) -> uuid.UUID:
    raw = request.headers.get("idempotency-key")
    if not raw:
        raise ApiError(400, "idempotency_key_required", "Header Idempotency-Key (UUID) is verplicht bij wijzigingen.")
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ApiError(400, "idempotency_key_invalid", "Idempotency-Key moet een UUID zijn.") from exc


def run_idempotent(
    db: Session,
    request: Request,
    principal: Principal,
    operation: str,
    body: Any,
    action: Callable[[], tuple[int, dict[str, Any], dict[str, str]]],
) -> JSONResponse:
    """Voer ``action`` hoogstens één keer uit per (principal, operatie, key). ``action`` mag niet zelf committen."""
    key = idempotency_key(request)
    scope = f"{principal.label()}|{operation}"[:256]
    req_hash = hashlib.sha256(canonical_json({"op": operation, "path": request.url.path, "body": body})).hexdigest()
    existing = db.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key)
    )
    if existing is not None:
        if existing.request_hash != req_hash:
            raise ApiError(
                409, "idempotency_key_conflict", "Deze Idempotency-Key is al gebruikt voor een ander verzoek."
            )
        return JSONResponse(
            existing.response_body, status_code=existing.status_code, headers={"Idempotent-Replayed": "true"}
        )
    status, response, headers = action()
    db.add(IdempotencyRecord(scope=scope, key=key, request_hash=req_hash, status_code=status, response_body=response))
    try:
        db.commit()
    except IntegrityError:  # gelijktijdig verzoek met dezelfde key: het andere won; zijn antwoord teruggeven
        db.rollback()
        winner = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key)
        )
        if winner is None or winner.request_hash != req_hash:
            raise ApiError(409, "idempotency_key_conflict", "Deze Idempotency-Key is al gebruikt.") from None
        return JSONResponse(
            winner.response_body, status_code=winner.status_code, headers={"Idempotent-Replayed": "true"}
        )
    return JSONResponse(response, status_code=status, headers=headers)
