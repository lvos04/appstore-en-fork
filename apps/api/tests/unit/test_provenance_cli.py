"""Ondertekende provenance en de jl-manifest-CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appstore.manifest.cli import main
from appstore.manifest.provenance import (
    ProvenanceRecord,
    SourceRef,
    b64url,
    key_id,
    load_public_key,
    raw_public,
    sign_data,
    sign_provenance,
    verify_data,
    verify_provenance,
)
from tests.conftest import FIXTURES

VALID = FIXTURES / "manifests" / "valid-custom-upstream.manifest.yaml"


def test_publisher_signature_roundtrip() -> None:
    key = Ed25519PrivateKey.generate()
    data = {"b": 1, "a": "é"}
    sig = sign_data(key, data)
    assert verify_data(key.public_key(), {"a": "é", "b": 1}, sig)  # canoniek: volgorde maakt niet uit
    assert not verify_data(key.public_key(), {"a": "e", "b": 1}, sig)
    assert not verify_data(key.public_key(), data, "geen-base64!")


def test_load_public_key_formats() -> None:
    key = Ed25519PrivateKey.generate().public_key()
    pem = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    assert key_id(load_public_key(pem)) == key_id(key)
    assert key_id(load_public_key(b64url(raw_public(key)))) == key_id(key)
    with pytest.raises(ValueError):
        load_public_key("abc")


def test_provenance_signed_and_tamper_evident() -> None:
    identity = Ed25519PrivateKey.generate()
    record = ProvenanceRecord(
        app_id="voorbeeld-web",
        version="1.27.3",
        manifest_sha256="sha256:" + "a" * 64,
        schema_sha256="sha256:" + "b" * 64,
        validator_version="0.1.0",
        source=SourceRef(source_id="s1", kind="bundled", url=None, ref=None, commit=None, path="x.manifest.yaml"),
        fetched_at=datetime(2026, 11, 9, tzinfo=UTC),
        publisher_signature="not_required",
        publisher_key_id=None,
        valid=True,
        errors=0,
        warnings=0,
    )
    signed = sign_provenance(identity, record)
    assert verify_provenance(identity.public_key(), signed)
    tampered = signed.model_copy(update={"record": record.model_copy(update={"valid": False})})
    assert not verify_provenance(identity.public_key(), tampered)
    assert not verify_provenance(Ed25519PrivateKey.generate().public_key(), signed)


def test_cli_validate_text_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(VALID)]) == 0
    assert "GELDIG" in capsys.readouterr().out
    assert (
        main(["validate", "--format", "json", str(FIXTURES / "manifests" / "ex47-privileged-hostmount.manifest.yaml")])
        == 1
    )
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is False and out["result"] == "ongeldig"
    assert any(f["code"] == "privileged_forbidden" for f in out["findings"])


def test_cli_strict_and_missing_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    own = Path(__file__).resolve().parents[4] / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml"
    assert main(["validate", str(own)]) == 0
    assert main(["validate", "--strict", str(own)]) == 1
    with pytest.raises(SystemExit) as exc:
        main(["validate", str(tmp_path / "bestaat-niet.yaml")])
    assert exc.value.code == 2
    capsys.readouterr()


def test_cli_translate_sign_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    params = tmp_path / "params.json"
    params.write_text(
        json.dumps(
            {
                "config_path": "/mnt/p1/a/b",
                "media_path": "/mnt/p1/m",
                "public_url": "http://n.test:1",
                "timezone": "Etc/UTC",
            }
        )
    )
    rc = main(
        ["translate", str(VALID), "--params", str(params), "--truenas-version", "25.10.1", "--ix-app-version", "1.4.8"]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ready_for_install"] is True and out["payload"]["catalog_app"] == "ix-app"
    assert main(["translate", str(VALID)]) == 1
    capsys.readouterr()
    key = Ed25519PrivateKey.generate()
    pem = tmp_path / "k.pem"
    pem.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    pub = tmp_path / "k.pub"
    pub.write_text(b64url(raw_public(key.public_key())))
    copy = tmp_path / "m.manifest.yaml"
    copy.write_bytes(VALID.read_bytes())
    assert main(["sign", str(copy), "--key", str(pem)]) == 0
    assert main(["verify", str(copy), "--public-key", str(pub)]) == 0
    copy.write_text(copy.read_text().replace("Voorbeeld web", "Gewijzigd"))
    assert main(["verify", str(copy), "--public-key", str(pub)]) == 1
