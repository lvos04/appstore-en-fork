"""Audit, outbox-events (contract 1.3 §7) en geredigeerd bewijs.

Events van deze dienst: domein ``catalog`` (eigenaar appstore-en-fork) plus de vaste ``service.*``-lijst
(``service.hub_link.linked`` met payload ``{via}``, ``…unlinked``, ``…revoked``, ``service.token.rotated``,
``service.key_rotation.applied``/``rejected``). Het derde segment is het werkwoord (probleem ``failed``/``rejected``,
herstel ``succeeded``; de rest is informatie) en ``entity`` is het stabiele onderwerp. ``payload.ernst`` (optioneel) is
één van de vijf Peil-woorden. Elk event wordt vóór opslag en vóór verzending tegen de grenzen van de Hub-inbox
gecontroleerd (:func:`envelope_problems`), zodat het niet pas als dead-letter opvalt.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
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


#: vaste ``service.*``-events (§7); de oude namen (``service.enrollment.completed`` e.a.) vervallen
SERVICE_EVENTS = frozenset(
    {
        "service.hub_link.linked",
        "service.hub_link.unlinked",
        "service.hub_link.revoked",
        "service.token.rotated",
        "service.key_rotation.applied",
        "service.key_rotation.rejected",
    }
)
#: eigen domein(en) van deze dienst (§7, domeinen per eigenaar)
OWN_DOMAINS = frozenset({"catalog"})
ERNST = frozenset({"kritiek", "hoog", "middel", "laag", "info"})
PROBLEM_VERBS = frozenset(
    {"failed", "crashed", "down", "degraded", "stalled", "blocked", "rejected", "saturated", "conflict"}
)
RECOVERY_VERBS = frozenset({"recovered", "succeeded", "resolved", "restored"})
MAX_PAYLOAD = 16 * 1024
EVENT_TYPE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+$")
ENTITY_TYPE = re.compile(r"^[a-z0-9_.-]{1,64}$")
ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "source_app",
        "source_event_id",
        "event_type",
        "entity",
        "timestamp_utc",
        "trace_id",
        "correlation_id",
        "actor",
        "classification",
        "payload",
        "payload_ref",
    }
)


def event_type_problem(event_type: str) -> str | None:
    """Naam volgens §7: drie segmenten, eigen domein of de vaste ``service.*``-lijst."""
    if len(event_type) > 128 or not EVENT_TYPE.match(event_type):
        return "event_type heeft niet precies drie segmenten [a-z0-9_] (≤ 128 tekens)"
    domain = event_type.split(".", 1)[0]
    if domain == "service":
        return None if event_type in SERVICE_EVENTS else "geen vast service.*-event"
    if domain not in OWN_DOMAINS:
        return f"domein {domain!r} is niet van deze dienst"
    return None


def payload_size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def fit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """≤ 16 KiB als JSON (§7); groter → zelf inkorten met ``_ingekort: true`` (alleen de sleutelnamen blijven)."""
    if payload_size(payload) <= MAX_PAYLOAD:
        return payload
    kept: dict[str, Any] = {"_ingekort": True}
    if payload.get("ernst") in ERNST:
        kept["ernst"] = payload["ernst"]
    kept["sleutels"] = sorted(str(k) for k in payload)[:100]
    return kept


def envelope_problems(env: dict[str, Any]) -> list[str]:
    """Grenzen van de Hub-inbox (contract 1.3 §7), vóór verzending gecontroleerd. Leeg = in orde."""
    out: list[str] = []
    extra = set(env) - ENVELOPE_FIELDS
    if extra:
        out.append(f"onbekende velden {sorted(extra)}")
    if env.get("schema_version") != "1.0":
        out.append("schema_version moet 1.0 zijn")
    if env.get("source_app") != APP_ID:
        out.append("source_app is niet deze dienst")
    if problem := event_type_problem(str(env.get("event_type", ""))):
        out.append(problem)
    sid = env.get("source_event_id")
    if not isinstance(sid, str) or not 1 <= len(sid) <= 200:
        out.append("source_event_id moet 1–200 tekens zijn")
    raw_entity = env.get("entity")
    entity: dict[str, Any] = raw_entity if isinstance(raw_entity, dict) else {}
    if not ENTITY_TYPE.match(str(entity.get("type", ""))):
        out.append("entity.type past niet in [a-z0-9_.-]{1,64}")
    eid = entity.get("id")
    if not isinstance(eid, str) or not 1 <= len(eid) <= 200:
        out.append("entity.id moet 1–200 tekens zijn")
    raw_actor = env.get("actor")
    actor: dict[str, Any] = raw_actor if isinstance(raw_actor, dict) else {}
    aid = actor.get("id")
    if not isinstance(aid, str) or not 1 <= len(aid) <= 128:
        out.append("actor.id moet 1–128 tekens zijn")
    payload = env.get("payload")
    if not isinstance(payload, dict) or payload_size(payload) > MAX_PAYLOAD:
        out.append("payload moet een object van hooguit 16 KiB zijn")
    elif "ernst" in payload and payload["ernst"] not in ERNST:
        out.append("payload.ernst is geen van kritiek|hoog|middel|laag|info")
    ref = env.get("payload_ref")
    if ref is not None and (not isinstance(ref, str) or len(ref) > 512):
        out.append("payload_ref ≤ 512 tekens")
    ts = env.get("timestamp_utc")
    try:
        if not isinstance(ts, str) or datetime.fromisoformat(ts.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError:
        out.append("timestamp_utc zonder tijdzone")
    return out


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
    if problem := event_type_problem(event_type):  # programmeerfout: nooit een event buiten de vaste namen opslaan
        raise ValueError(f"event_type {event_type!r}: {problem}")
    if not ENTITY_TYPE.match(entity_type) or not 1 <= len(entity_id) <= 200:
        raise ValueError(f"entity {entity_type!r}/{entity_id!r} breekt de grenzen van §7")
    db.add(
        OutboxEvent(
            event_id=uuid.uuid4(),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            classification=classification,
            actor=actor_of(principal),
            payload=fit_payload(redact(payload)),
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
