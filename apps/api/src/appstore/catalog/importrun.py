"""Eén importronde (B08-002): inventaris van de Hub lezen → register bijwerken → bewijs, audit en event.

Gedeeld door ``POST /catalog/v1/existing-apps/import`` en de periodieke import in de worker. Een storing of een
onbruikbare inventaris wordt **nooit** als succes vastgelegd: dan ``status`` ``unknown``/``capability_unavailable``,
event ``catalog.import.unavailable`` en er verandert niets aan het register.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..auth import Principal
from ..bestaande_apps import load_register
from ..events import audit, emit, record_evidence
from ..hublink import HubLinkClient
from .adoption import UNTOUCHED_NL, known_entries, reconcile
from .hubinventory import HubInventoryClient


def run_import(
    db: Session, hub: HubLinkClient, principal: Principal | None, *, trace_id: str, now: datetime
) -> dict[str, Any]:
    """Voert één ronde uit binnen de transactie van ``db`` (de aanroeper commit). Kan
    :class:`appstore.catalog.adoption.ImportBusyError` geven als er al een import loopt."""
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
    emit(
        db,
        "catalog.import.completed" if fetched.status == "ok" else "catalog.import.unavailable",
        "existing_apps",
        "inventaris",
        summary,
        trace_id,
        principal,
    )
    changed = bool(counts and (counts["nieuw"] or counts["bijgewerkt"]))
    return {
        **summary,
        "gewijzigd": changed,
        "executed": False,
        "nas_gewijzigd": False,
        "evidence_id": str(evidence_id),
        "toelichting_nl": UNTOUCHED_NL
        if fetched.status == "ok"
        else "Er is niets gewijzigd: zonder bruikbare inventaris blijft alles zoals het was.",
    }
