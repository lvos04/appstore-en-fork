"""Schema, model en codecatalogus van manifest v1."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from appstore.manifest import validate_data, validate_text
from appstore.manifest.findings import CODES
from appstore.manifest.validator import schema
from tests.conftest import FIXTURES, REPO_ROOT, load_fixture


def test_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(schema())


def test_normative_schema_copy_is_identical() -> None:
    pkg = Path(__file__).resolve().parents[2] / "src" / "appstore" / "manifest" / "manifest-v1.schema.json"
    repo = REPO_ROOT / "schemas" / "manifest-v1.schema.json"
    assert json.loads(pkg.read_text()) == json.loads(repo.read_text())
    assert pkg.read_bytes() == repo.read_bytes(), (
        "schemas/manifest-v1.schema.json moet byte-gelijk zijn aan de pakketkopie"
    )


def test_valid_fixtures_have_no_findings() -> None:
    for name in ("valid-custom-upstream.manifest.yaml", "valid-catalog.manifest.yaml"):
        report, model = validate_data(load_fixture(name))
        assert report.valid, report.as_text()
        assert model is not None
        assert report.findings == [], report.as_text()


def test_own_manifest_is_valid_with_only_pending_digest(own_manifest_text: str) -> None:
    report, model, _ = validate_text(own_manifest_text)
    assert report.valid, report.as_text()
    assert model is not None and model.app_id == "appstore-en-fork"
    assert report.codes() == ["digest_pending_release"]


def test_all_emitted_codes_are_documented() -> None:
    emitted: set[str] = set()
    for path in (FIXTURES / "manifests").glob("*.yaml"):
        report, _, _ = validate_text(path.read_bytes())
        emitted.update(report.codes())
    assert emitted <= set(CODES), emitted - set(CODES)


def test_every_finding_has_dutch_message_and_path() -> None:
    report, _, _ = validate_text((FIXTURES / "manifests" / "ex47-privileged-hostmount.manifest.yaml").read_bytes())
    for finding in report.findings:
        assert finding.message and finding.path
        assert finding.code in CODES


def test_report_is_machine_readable() -> None:
    report, _, _ = validate_text((FIXTURES / "manifests" / "valid-catalog.manifest.yaml").read_bytes())
    data = json.loads(report.model_dump_json())
    assert data["valid"] is True
    assert data["manifest_sha256"].startswith("sha256:")
    assert data["schema_sha256"].startswith("sha256:")
    assert set(data) >= {"valid", "app_id", "version", "errors", "warnings", "findings", "validator_version"}
