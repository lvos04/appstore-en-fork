"""Audit, outbox-events (contract §7) en geredigeerd bewijs."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from . import APP_ID
from .auth import Principal
from .models import AuditEvent, EvidenceRecord, OutboxEvent
from .timeutil import iso

_SECRET_KEY = re.compile(
    r"(pass(word)?|secret|token|api_?key|private|credential|authorization|koppelcode|pairing)", re.I
)


def redact(value: Any) -> Any:
    """Verwijder geheimen uit data voor logs/evidence (sleutelnaam-gebaseerd)."""
    if isinstance(value, dict):
        return {
            k: ("[GEREDIGEERD]" if _SECRET_KEY.search(str(k)) and v not in (None, "", []) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def actor_of(principal: Principal | None) -> dict[str, str]:
    if principal is None:
        return {"type": "system", "id": APP_ID}
    return {"type": "user" if principal.kind == "user" else "system", "id": principal.label()}


def audit(
    db: Session,
    principal: Principal | None,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None,
    trace_id: str,
) -> None:
    actor = actor_of(principal)
    db.add(
        AuditEvent(
            actor_type=principal.kind if principal else "system",
            actor_id=actor["id"],
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=redact(details or {}),
            trace_id=trace_id,
        )
    )


def emit(
    db: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
    trace_id: str,
    principal: Principal | None = None,
    classification: str = "internal",
    correlation_id: str | None = None,
) -> None:
    db.add(
        OutboxEvent(
            event_id=uuid.uuid4(),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            classification=classification,
            actor=actor_of(principal),
            payload=redact(payload),
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
    )


def envelope(ev: OutboxEvent) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "event_id": str(ev.event_id),
        "source_app": APP_ID,
        "source_event_id": str(ev.seq),
        "event_type": ev.event_type,
        "entity": {"type": ev.entity_type, "id": ev.entity_id},
        "timestamp_utc": iso(ev.created_at),
        "trace_id": ev.trace_id,
        "correlation_id": ev.correlation_id,
        "actor": ev.actor,
        "classification": ev.classification,
        "payload": ev.payload,
        "payload_ref": None,
    }


def record_evidence(
    db: Session, kind: str, principal: Principal | None, trace_id: str, summary: dict[str, Any]
) -> uuid.UUID:
    rec = EvidenceRecord(
        id=uuid.uuid4(), kind=kind, actor=actor_of(principal)["id"], trace_id=trace_id, summary=redact(summary)
    )
    db.add(rec)
    return rec.id
