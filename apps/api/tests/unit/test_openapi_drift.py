"""OpenAPI-export (contract §5): het bestand in docs/openapi moet gelijk zijn aan de code (CI faalt bij drift)."""

from __future__ import annotations

import json

from appstore.app import openapi_document
from tests.conftest import REPO_ROOT

EXPORT = REPO_ROOT / "docs" / "openapi" / "appstore-en-fork.openapi.json"


def test_openapi_export_has_no_drift() -> None:
    current = json.loads(json.dumps(openapi_document(), sort_keys=True))
    stored = json.loads(EXPORT.read_text(encoding="utf-8"))
    assert current == stored, "OpenAPI wijkt af: draai `appstore openapi --out docs/openapi/appstore-en-fork.openapi.json`"


def test_openapi_contains_contract_routes() -> None:
    paths = set(openapi_document()["paths"])
    assert {"/healthz", "/readyz", "/connector/v1/discover", "/connector/v1/health", "/connector/v1/items",
            "/connector/v1/propose", "/connector/v1/apply", "/connector/v1/rollback", "/connector/v1/evidence/{evidence_id}",
            "/connector/v1/events", "/catalog/v1/validate", "/catalog/v1/preview", "/catalog/v1/entries",
            "/catalog/v1/sources"} <= paths
