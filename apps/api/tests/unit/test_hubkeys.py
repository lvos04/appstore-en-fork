"""Contract 1.3 §4.5-testvectoren (A5, A17), normalisatie van vingerafdruk en koppelcode, en het exacte enroll-verzoek."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from appstore.hubkeys import (
    InvalidKeyError,
    enroll_jwk,
    fingerprints_equal,
    jwk_thumbprint,
    keyset_fingerprint,
    normalize_fingerprint,
    normalize_pairing_code,
    validate_hub_jwk,
)

# Testvectoren uit PLATFORM_CONTRACTEN.md 1.3 §4.5 (publieke sleutels; RFC 8037 A.1 voor de eerste)
JWT_1 = {"kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}
POLICY = {"kty": "OKP", "crv": "Ed25519", "x": "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"}
JWT_2 = {"kty": "OKP", "crv": "Ed25519", "x": "Kay64UG8yvCyLhqU000LxzYeUm0L_hLIl5S8kyKWbdc"}


def test_rfc7638_thumbprints_from_contract() -> None:
    assert jwk_thumbprint(JWT_1) == "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"
    assert jwk_thumbprint(POLICY) == "1IG2tMH7J2wbJZnOf8LJzQitKf7LMvoAElsuDMVM54Y"
    assert jwk_thumbprint(JWT_2) == "AkXIZFzonb59ZmGwyKgi3H3BwMi6amevqdKQiLZhdtc"
    # extra velden (kid/use/alg) veranderen de thumbprint niet
    assert jwk_thumbprint({**JWT_1, "kid": "x", "use": "sig", "alg": "EdDSA"}) == jwk_thumbprint(JWT_1)


def test_keyset_fingerprint_vectors_from_contract() -> None:
    assert keyset_fingerprint([JWT_1], [POLICY]) == "MEGG-8GFG-2E4Y-K2RQ"
    assert keyset_fingerprint([JWT_1, JWT_2], [POLICY]) == "CYRC-JS8C-14AN-JR17"
    assert keyset_fingerprint([JWT_2, JWT_1], [POLICY]) == "CYRC-JS8C-14AN-JR17"  # volgorde maakt niet uit


def test_keyset_fingerprint_counts_every_policy_key() -> None:
    """Nieuw in 1.3 (A17): tijdens een policy-rotatie telt elke gepinde policy-sleutel mee, in elke volgorde."""
    assert keyset_fingerprint([JWT_1], [POLICY, JWT_2]) == "HN69-2PHK-XV2D-X85W"
    assert keyset_fingerprint([JWT_1], [JWT_2, POLICY]) == "HN69-2PHK-XV2D-X85W"
    # nooit alleen de laatst gepinde policy-sleutel
    assert keyset_fingerprint([JWT_1], [JWT_2]) != "HN69-2PHK-XV2D-X85W"
    assert keyset_fingerprint([JWT_1], [POLICY]) == "MEGG-8GFG-2E4Y-K2RQ"  # één policy-sleutel = 1.2


@pytest.mark.parametrize(
    "jwk",
    [
        {**JWT_1, "d": "geheim"},
        {**JWT_1, "kty": "EC"},
        {**JWT_1, "crv": "X25519"},
        {**JWT_1, "x": JWT_1["x"] + "A"},  # niet 32 bytes
        {"kty": "OKP", "crv": "Ed25519"},
        {**JWT_1, "x": 12},
    ],
)
def test_invalid_jwks_are_refused(jwk: dict[str, object]) -> None:
    with pytest.raises(InvalidKeyError):
        jwk_thumbprint(jwk)


def test_hub_jwk_requires_use_alg_and_kid() -> None:
    good = {**JWT_1, "kid": "abc", "use": "sig", "alg": "EdDSA"}
    assert validate_hub_jwk(good) == {**JWT_1, "kid": "abc"}
    for broken in (
        {**good, "use": "enc"},
        {**good, "alg": "RS256"},
        {k: v for k, v in good.items() if k != "kid"},
        {**good, "d": "x"},
        "geen object",
    ):
        with pytest.raises(InvalidKeyError):
            validate_hub_jwk(broken)


def test_own_enroll_jwk_has_thumbprint_kid_and_no_use_alg() -> None:
    """A7/AS1: alleen {kty, crv, x, kid} met kid = RFC 7638-thumbprint (de Hub weigert use/alg met 422)."""
    jwk = enroll_jwk(Ed25519PrivateKey.generate().public_key())
    assert set(jwk) == {"kty", "crv", "x", "kid"}
    assert jwk["kid"] == jwk_thumbprint(jwk)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("MEGG-8GFG-2E4Y-K2RQ", "MEGG8GFG2E4YK2RQ"),
        ("megg 8gfg 2e4y k2rq", "MEGG8GFG2E4YK2RQ"),
        ("MEGG-8GFG-2E4Y-K2RO", "MEGG8GFG2E4YK2R0"),  # O → 0
        ("MEGG-8GFG-2E4Y-K2RU", None),  # U zit niet in Crockford
        ("MEGG-8GFG-2E4Y", None),
        ("", None),
        (None, None),
    ],
)
def test_fingerprint_normalization(value: str | None, expected: str | None) -> None:
    assert normalize_fingerprint(value) == expected


def test_fingerprints_equal_after_normalization() -> None:
    assert fingerprints_equal("megg-8gfg-2e4y-k2rq", "MEGG-8GFG-2E4Y-K2RQ")
    assert not fingerprints_equal(None, "MEGG-8GFG-2E4Y-K2RQ")
    assert not fingerprints_equal("CYRC-JS8C-14AN-JR17", "MEGG-8GFG-2E4Y-K2RQ")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ABCD-EFGH-JKMN", "ABCD-EFGH-JKMN"),
        ("abcd efgh jkmn", "ABCD-EFGH-JKMN"),
        ("ABCD-EFGH-JKMO", "ABCD-EFGH-JKM0"),
        ("ABCD-EFGH-JKMI", "ABCD-EFGH-JKM1"),
        ("ABCD-EFGH-JKML", "ABCD-EFGH-JKM1"),
        ("ABCD-EFGH-JKMU", None),
        ("ABCD-EFGH-JKM", None),
        ("ABCD-EFGH-JKMN-P", None),
        ("A" * 33, None),
        ("ÄBCD-EFGH-JKMN", None),
    ],
)
def test_pairing_code_normalization(value: str, expected: str | None) -> None:
    assert normalize_pairing_code(value) == expected
