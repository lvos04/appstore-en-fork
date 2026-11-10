"""Eén importronde (B08-002): inventaris van de Hub lezen → register bijwerken → bewijs, audit en event.

Gedeeld door ``POST /catalog/v1/existing-apps/import`` en de periodieke import in de worker. Een storing of een
onbruikbare inventaris wordt **nooit** als succes vastgelegd: dan ``status`` ``unknown``/``capability_unavailable``,
event ``catalog.import.unavailable`` en er verandert niets aan het register.

Elke ronde legt bewijs (``hub_import``) en audit vast. Een event naar de Hub gaat alleen mee als de uitkomst
(``status`` + ``reason_code``) anders is dan bij de vorige ronde of als het register veranderde: een ongewijzigde
herhaling (de worker leest elke 15 minuten) is idempotent en stuurt de Hub niet elke ronde hetzelfde bericht.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal
from ..bestaande_apps import load_register
from ..events import audit, emit, record_evidence
from ..hublink import HubLinkClient
from ..models import EvidenceRecord
from .adoption import UNTOUCHED_NL, known_entries, reconcile
from .hubinventory import HubInventoryClient


def run_import(
    db: Session, hub: HubLinkClient, principal: Principal | None, *, trace_id: str, now: datetime
) -> dict[str, Any]:
    """Voert één ronde uit binnen de transactie van ``db`` (de aanroeper commit). Kan
    :class:`appstore.catalog.adoption.ImportBusyError` geven als er al een import loopt."""
    previous = db.scalar(
        select(EvidenceRecord)
        .where(EvidenceRecord.kind == "hub_import")
        .order_by(EvidenceRecord.created_at.desc())
        .limit(1)
    )
    fetched = HubInventoryClient(hub).fetch(trace_id=trace_id, now=now)
    counts: dict[str, Any] | None = None
    if fetched.status == "ok" and fetched.observed_at is not None:
        res = reconcile(
            db,
            fetched.apps,
            known_entries(db, load_register().register),
            observed_at=fetched.observed_at,
            now=now,
        )
        counts = res.as_dict()
    summary = {**fetched.summary(), "counts": counts}
    evidence_id = record_evidence(db, "hub_import", principal, trace_id, summary)
    audit(db, principal, "catalog.import.run", "existing_apps", "inventaris", summary, trace_id)
    changed = bool(counts and (counts["nieuw"] or counts["bijgewerkt"]))
    before = None if previous is None else (previous.summary.get("status"), previous.summary.get("reason_code"))
    event_type: str | None = None
    if changed or before != (fetched.status, fetched.reason_code):
        # contract 1.3 §7: vaste werkwoorden (probleem failed, herstel succeeded) op een stabiel onderwerp
        event_type = "catalog.import.succeeded" if fetched.status == "ok" else "catalog.import.failed"
        payload = summary if fetched.status == "ok" else {**summary, "ernst": "middel"}
        emit(db, event_type, "existing_apps", "inventaris", payload, trace_id, principal)
    return {
        **summary,
        "gewijzigd": changed,
        "event_type": event_type,
        "executed": False,
        "nas_gewijzigd": False,
        "evidence_id": str(evidence_id),
        "toelichting_nl": UNTOUCHED_NL
        if fetched.status == "ok"
        else "Er is niets gewijzigd: zonder bruikbare inventaris blijft alles zoals het was.",
    }
