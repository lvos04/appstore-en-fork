"""Events volgens contract 1.3 §7: vaste ``service.*``-namen, eigen domein ``catalog``, werkwoorden, ``payload.ernst``
en de grenzen van de Hub-inbox — vóór verzending gecontroleerd, zodat een event niet pas als dead-letter opvalt."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from appstore.events import (
    ERNST,
    PROBLEM_VERBS,
    RECOVERY_VERBS,
    SERVICE_EVENTS,
    envelope_problems,
    event_type_problem,
    fit_payload,
    payload_size,
)
from tests.fakehub import event_problem

SRC = Path(__file__).resolve().parents[2] / "src" / "appstore"


def good() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "source_app": "appstore-en-fork",
        "source_event_id": "17",
        "event_type": "catalog.source_sync.failed",
        "entity": {"type": "catalog_source", "id": str(uuid.uuid4())},
        "timestamp_utc": "2026-11-10T12:00:00Z",
        "trace_id": str(uuid.uuid4()),
        "correlation_id": None,
        "actor": {"type": "system", "id": "appstore-en-fork"},
        "classification": "internal",
        "payload": {"code": "source_unreachable", "ernst": "middel"},
        "payload_ref": None,
    }


def test_good_envelope_passes_here_and_in_the_hub_simulator() -> None:
    assert envelope_problems(good()) == []
    assert event_problem(good()) == (0, "")


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"onbekend": 1}, "onbekende velden"),
        ({"schema_version": "1.1"}, "schema_version"),
        ({"source_app": "home-control-hub"}, "source_app"),
        ({"event_type": "catalog.sync_failed"}, "drie segmenten"),
        ({"event_type": "catalog.source.sync.failed"}, "drie segmenten"),
        ({"event_type": "Catalog.source.failed"}, "drie segmenten"),
        ({"event_type": "catalog." + "a" * 130 + ".failed"}, "drie segmenten"),
        ({"event_type": "energy.reading.recorded"}, "niet van deze dienst"),
        ({"event_type": "service.enrollment.completed"}, "geen vast service"),
        ({"source_event_id": ""}, "source_event_id"),
        ({"entity": {"type": "Catalog Source", "id": "x"}}, "entity.type"),
        ({"entity": {"type": "catalog_source", "id": "x" * 201}}, "entity.id"),
        ({"actor": {"type": "user", "id": "u" * 129}}, "actor.id"),
        ({"payload": {"x": "y" * (16 * 1024)}}, "16 KiB"),
        ({"payload": {"ernst": "ernstig"}}, "ernst"),
        ({"payload_ref": "r" * 513}, "payload_ref"),
        ({"timestamp_utc": "2026-11-10T12:00:00"}, "tijdzone"),
    ],
)
def test_inbox_bounds_are_checked_before_sending(change: dict[str, Any], fragment: str) -> None:
    problems = envelope_problems({**good(), **change})
    assert any(fragment in p for p in problems), problems


def test_service_events_are_the_fixed_list_and_old_names_are_gone() -> None:
    assert {
        "service.hub_link.linked",
        "service.hub_link.unlinked",
        "service.hub_link.revoked",
        "service.token.rotated",
        "service.key_rotation.applied",
        "service.key_rotation.rejected",
    } == SERVICE_EVENTS
    code = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    for old in ("service.enrollment.completed", "service.hub_link.paired", "service.hub_key.rotation_rejected"):
        assert f'"{old}"' not in code


def test_every_emitted_event_type_follows_section_7() -> None:
    """Statisch: elk event_type in de code heeft drie segmenten, een eigen domein en een bekend werkwoord."""
    code = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    found = set(re.findall(r'"((?:catalog|service)\.[a-z0-9_]+\.[a-z0-9_]+)"', code))
    assert found, "geen events gevonden"
    for event_type in found:
        assert event_type_problem(event_type) is None, event_type
    verbs = {e.rsplit(".", 1)[1] for e in found}
    assert "failed" in verbs and "succeeded" in verbs and verbs & PROBLEM_VERBS and verbs & RECOVERY_VERBS
    assert "sync_failed" not in verbs and "unavailable" not in verbs  # vóór 1.3: geen vast werkwoord


def test_large_payload_is_shortened_with_marker() -> None:
    big = {"ernst": "hoog", **{f"k{i}": "x" * 400 for i in range(60)}}
    small = fit_payload(big)
    assert small["_ingekort"] is True and small["ernst"] == "hoog" and payload_size(small) <= 16 * 1024
    assert fit_payload({"a": 1}) == {"a": 1}
    assert {"kritiek", "hoog", "middel", "laag", "info"} == ERNST
