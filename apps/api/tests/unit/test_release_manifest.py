"""Release-CI (contract 1.3 §9): het releasemanifest met ingevulde digest is ``--strict`` geldig; de acceptatie van
``digest_pending_release`` verdwijnt (anders ``accepted_finding_unused``)."""

from __future__ import annotations

import importlib.util
from types import ModuleType

import pytest

from appstore.manifest import validate_text
from tests.conftest import REPO_ROOT

OWN = REPO_ROOT / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml"
DIGEST = "sha256:" + "ab" * 32
COMMIT = "c" * 40


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_manifest", REPO_ROOT / "tools" / "release_manifest.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_release_manifest_is_strict_valid_without_pending_acceptance() -> None:
    text = _tool().make_release_manifest(OWN.read_text(encoding="utf-8"), version="0.1.0", digest=DIGEST, commit=COMMIT)
    report, model, _ = validate_text(text)
    assert report.strict_valid and report.findings == [], report.as_text()
    assert model is not None and model.image is not None and model.image.digest == DIGEST
    assert model.accepted_findings == [] and "accepted_findings:" not in text
    assert "# Manifest (manifeststandaard v1" in text  # commentaar blijft


def test_without_removing_the_acceptance_strict_would_fail() -> None:
    """Negatief: alleen de digest invullen (zoals vóór 1.3) laat --strict falen op accepted_finding_unused."""
    text = OWN.read_text(encoding="utf-8").replace("  digest: null", f'  digest: "{DIGEST}"', 1)
    report, _, _ = validate_text(text)
    assert report.valid and not report.strict_valid and report.codes() == ["accepted_finding_unused"]


@pytest.mark.parametrize(
    ("version", "digest", "commit"),
    [("9.9.9", DIGEST, COMMIT), ("0.1.0", "sha256:kort", COMMIT), ("0.1.0", DIGEST, "x")],
)
def test_wrong_input_is_refused(version: str, digest: str, commit: str) -> None:
    with pytest.raises(ValueError):
        _tool().make_release_manifest(OWN.read_text(encoding="utf-8"), version=version, digest=digest, commit=commit)


def test_other_acceptances_stay() -> None:
    src = OWN.read_text(encoding="utf-8") + (
        "  - code: backup_db_export_none\n    reden_nl: Alleen een test van het knippen.\n"
    )
    out = _tool().make_release_manifest(src, version="0.1.0", digest=DIGEST, commit=COMMIT)
    _, model, _ = validate_text(out)
    assert model is None or [a.code for a in model.accepted_findings] == ["backup_db_export_none"]
    assert "accepted_findings:\n  - code: backup_db_export_none" in out and "- code: digest_pending_release" not in out
