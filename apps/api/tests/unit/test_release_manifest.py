"""Release-CI (contract 1.3 §9): het releasemanifest met ingevulde digest is ``--strict`` geldig; de acceptatie van
``digest_pending_release`` verdwijnt (anders ``accepted_finding_unused``)."""

from __future__ import annotations

import importlib.util
import re
from types import ModuleType

import pytest

from appstore.manifest import validate_text
from tests.conftest import REPO_ROOT

DIGEST = "sha256:" + "ab" * 32
COMMIT = "c" * 40


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_manifest", REPO_ROOT / "tools" / "release_manifest.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def prerelease_manifest(own_manifest_without_digest: str) -> str:
    source = re.sub(r"^  source_commit: .+$", "  source_commit: null", own_manifest_without_digest, flags=re.MULTILINE)
    return source + (
        "accepted_findings:\n"
        "  - code: digest_pending_release\n"
        "    reden_nl: De eerste release moet de digest nog leveren.\n"
    )


def test_release_manifest_is_strict_valid_without_pending_acceptance(prerelease_manifest: str) -> None:
    before, _, _ = validate_text(prerelease_manifest)
    assert before.strict_valid and before.codes() == ["digest_pending_release"] and before.findings[0].accepted
    text = _tool().make_release_manifest(prerelease_manifest, version="0.1.0", digest=DIGEST, commit=COMMIT)
    report, model, _ = validate_text(text)
    assert report.strict_valid and report.findings == [], report.as_text()
    assert model is not None and model.image is not None and model.image.digest == DIGEST
    assert model.provenance.source_commit == COMMIT
    assert model.accepted_findings == [] and "accepted_findings:" not in text
    assert "# Manifest (manifeststandaard v1" in text  # commentaar blijft


def test_without_removing_the_acceptance_strict_would_fail(prerelease_manifest: str) -> None:
    """Negatief: alleen de digest invullen (zoals vóór 1.3) laat --strict falen op accepted_finding_unused."""
    text = prerelease_manifest.replace("  digest: null", f'  digest: "{DIGEST}"', 1)
    report, _, _ = validate_text(text)
    assert report.valid and not report.strict_valid and report.codes() == ["accepted_finding_unused"]


@pytest.mark.parametrize(
    ("version", "digest", "commit"),
    [("9.9.9", DIGEST, COMMIT), ("0.1.0", "sha256:kort", COMMIT), ("0.1.0", DIGEST, "x")],
)
def test_wrong_input_is_refused(prerelease_manifest: str, version: str, digest: str, commit: str) -> None:
    with pytest.raises(ValueError):
        _tool().make_release_manifest(prerelease_manifest, version=version, digest=digest, commit=commit)


def test_other_acceptances_stay(prerelease_manifest: str) -> None:
    src = prerelease_manifest + "  - code: backup_db_export_none\n    reden_nl: Alleen een test van het knippen.\n"
    out = _tool().make_release_manifest(src, version="0.1.0", digest=DIGEST, commit=COMMIT)
    _, model, _ = validate_text(out)
    assert model is not None and [a.code for a in model.accepted_findings] == ["backup_db_export_none"]
    assert "accepted_findings:\n  - code: backup_db_export_none" in out and "- code: digest_pending_release" not in out
