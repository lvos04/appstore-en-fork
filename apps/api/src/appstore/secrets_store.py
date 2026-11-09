"""Geheimen in ``/appdata/secrets`` (contract §3): bij eerste start aangemaakt, 0600, nooit overschreven.

- ``vault.key``: 32 willekeurige bytes voor AES-256-GCM (opgeslagen credentials, bv. het service-token);
- ``identity.ed25519``: eigen Ed25519-identiteit (provenance-handtekeningen, publieke sleutel naar de Hub).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULT_KEY = "vault.key"
IDENTITY_KEY = "identity.ed25519"


def _write_new(path: Path, data: bytes) -> bool:
    """Schrijf alleen als het bestand nog niet bestaat (O_EXCL). Geeft ``True`` als het nieuw is."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return True


def ensure_secrets(secrets_dir: Path) -> list[str]:
    """Maak ontbrekende geheimen aan; bestaande blijven onaangeroerd. Geeft de namen van nieuw gemaakte bestanden."""
    secrets_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(secrets_dir, 0o700)
    created: list[str] = []
    if _write_new(secrets_dir / VAULT_KEY, secrets.token_bytes(32)):
        created.append(VAULT_KEY)
    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    if _write_new(secrets_dir / IDENTITY_KEY, pem):
        created.append(IDENTITY_KEY)
    for name in (VAULT_KEY, IDENTITY_KEY):
        os.chmod(secrets_dir / name, 0o600)
    return created


class Vault:
    """AES-256-GCM met de vault-sleutel; ``aad`` bindt de ciphertext aan zijn doel (bv. ``hub_link/service_token``)."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("vault.key moet 32 bytes zijn")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, aad: str) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        return nonce, self._aead.encrypt(nonce, plaintext.encode(), aad.encode())

    def decrypt(self, nonce: bytes, ciphertext: bytes, aad: str) -> str:
        return self._aead.decrypt(nonce, ciphertext, aad.encode()).decode()


@dataclass(frozen=True)
class Secrets:
    vault: Vault
    identity: Ed25519PrivateKey

    @classmethod
    def load(cls, secrets_dir: Path) -> Secrets:
        key = serialization.load_pem_private_key((secrets_dir / IDENTITY_KEY).read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("identity.ed25519 is geen Ed25519-sleutel")
        return cls(vault=Vault((secrets_dir / VAULT_KEY).read_bytes()), identity=key)
