"""Zonder database: hub-link.json (atomisch, 0600, opnieuw lezen, onleesbaar = fail-closed), het exacte enroll-verzoek
(A5), de antwoordcontrole van §3.3, de adresregels van §3.2 en de throttle van de statuspagina (§3.7)."""

from __future__ import annotations

import json
import logging
import os
import stat
import uuid
from pathlib import Path
from typing import Any

import pytest

from appstore import APP_ID
from appstore.api.status_page import _Attempts
from appstore.config import Settings, normalize_hub_url
from appstore.hublink import HubLinkClient, InvalidResponseError, validate_enroll_response
from appstore.hublink_store import ERROR_MESSAGES, HubLinkStore, LastError, LinkState
from appstore.secrets_store import Secrets, ensure_secrets
from tests.fakehub import FakeHub

ENROLL_FIELDS = {"app_id", "version", "base_url", "public_key_jwk"}


# ---------------------------------------------------------------------------------------------- bestand (§3.5)
def test_store_writes_atomically_with_0600(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path / "secrets")
    assert store.read().state == "nooit_gekoppeld" and not store.exists()  # ontbrekend bestand
    store.update(lambda s: s.model_copy(update={"hub_url": "http://hub.test:30400"}))
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600
    assert [p.name for p in store.dir.iterdir() if p.name.startswith(".hub-link.")] == []  # geen restbestanden
    assert json.loads(store.path.read_text())["schema_version"] == "1.2"


def test_other_process_sees_changes_immediately(tmp_path: Path) -> None:
    """Elk proces leest opnieuw zodra inode/mtime verandert (API en worker zijn aparte processen)."""
    api, worker = HubLinkStore(tmp_path), HubLinkStore(tmp_path)
    worker.update(lambda s: s.model_copy(update={"state": "gekoppeld", "service_token": "svc_x"}))
    assert api.read().state == "gekoppeld"
    worker.update(lambda s: s.model_copy(update={"state": "ontkoppeld", "service_token": None}))
    assert api.read().state == "ontkoppeld" and api.read().service_token is None


def test_corrupt_file_is_fail_closed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = HubLinkStore(tmp_path)
    store.path.write_text("{ kapot", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        st = store.read()
    assert st.corrupt and not st.linked and "onleesbaar" in caplog.text
    assert st.first_linked_at  # geldt als 'al eens gekoppeld': geen koppelformulier
    store.path.write_text(json.dumps({"state": "bestaat-niet"}), encoding="utf-8")
    assert store.read().corrupt
    # overschrijven (bv. een mislukte poging met een env-token) opent het formulier niet opnieuw
    after = store.update(lambda s: s.model_copy(update={"last_error": LastError(code="hub_weigerde", at="x")}))
    assert not after.corrupt and after.first_linked_at and HubLinkStore(tmp_path).read().first_linked_at


def test_outgoing_http_ignores_proxies_and_redirects(client: HubLinkClient) -> None:
    """§3.2/§5: nooit een redirect volgen en geen proxy-omgevingsvariabelen gebruiken."""
    from appstore.app import default_http_factory

    with default_http_factory(client.settings)() as http:
        assert http.follow_redirects is False and http.trust_env is False
        assert http.timeout.connect == 5.0 and http.timeout.read == 15.0


def test_public_view_is_a_whitelist(tmp_path: Path) -> None:
    st = LinkState(
        state="gekoppeld",
        service_token="svc_" + "A" * 43,
        service_token_last4="AAAA",
        last_error=LastError(code="rate_limited", at="2026-11-09T12:00:00Z"),
    )
    pub = st.public()
    assert set(pub) == {
        "state",
        "koppelen_bezig",
        "hub_url",
        "service_id",
        "via",
        "linked_at",
        "first_linked_at",
        "keyset_fingerprint",
        "service_token_last4",
        "last_error",
    }
    assert "svc_" not in json.dumps(pub) and pub["last_error"]["message"] == ERROR_MESSAGES["rate_limited"]


def test_used_token_list_keeps_last_20() -> None:
    st = LinkState()
    for i in range(25):
        st.remember_token(f"{i:064x}")
    assert len(st.used_enrollment_token_sha256) == 20 and st.used_enrollment_token_sha256[-1] == f"{24:064x}"


# ---------------------------------------------------------------------------------------------- verzoek en antwoord (§3.3)
@pytest.fixture
def client(tmp_path: Path) -> HubLinkClient:
    ensure_secrets(tmp_path)
    settings = Settings(
        data_dir=tmp_path.parent,
        database_url="postgresql+psycopg://niet-gebruikt",
        host="127.0.0.1",
        port=0,
        hub_url=None,
        enrollment_token=None,
        public_url=None,
        bundled_catalog_dir=tmp_path,
        allow_insecure_sources=False,
    )
    return HubLinkClient(settings, HubLinkStore(tmp_path), Secrets.load(tmp_path), FakeHub().client_factory())


@pytest.mark.parametrize(
    ("field", "value"), [("enrollment_token", "enr_" + "A" * 43), ("pairing_code", "ABCD-EFGH-JKMN")]
)
def test_enroll_request_has_exactly_the_contract_fields(client: HubLinkClient, field: str, value: str) -> None:
    """A5: precies de velden van §3.3 — geen ``capabilities``, ``kind``, ``secret``, ``schema_version`` of aliassen."""
    body = client.enroll_body(field, value)
    assert set(body) == ENROLL_FIELDS | {field}
    assert body["app_id"] == APP_ID == "appstore-en-fork"  # vaste app_id uit §2, niet de TrueNAS-appnaam
    assert set(body["public_key_jwk"]) == {"kty", "crv", "x", "kid"}
    assert FakeHub()._validate(body) is None  # de even strenge simulator accepteert het


def _good() -> dict[str, Any]:
    hub = FakeHub()
    return {
        "schema_version": "1.2",
        "service_id": str(uuid.uuid4()),
        "app_id": APP_ID,
        "service_token": "svc_" + "B" * 43,
        "token_type": "Bearer",
        "hub_issuer": "hub",
        "jwks": hub.jwks(),
        "policy_public_key": hub.policy_jwk(),
        "keyset_fingerprint": hub.fingerprint(),
        "jwks_url": "/api/v1/auth/jwks",
        "events_inbox_url": "/api/v1/events/inbox",
        "key_rotation_url": "/api/v1/auth/key-rotations",
        "enrollment_replayed": False,
    }


def test_valid_response_and_alias_fields_do_not_exist() -> None:
    data = _good()
    out = validate_enroll_response(data, None)
    assert out["_fingerprint"] == data["keyset_fingerprint"]
    for alias in ("token", "id", "policy_public_jwk"):  # §3.3: aliassen uit eerdere aannames bestaan niet
        broken = {k: v for k, v in data.items() if k not in ("service_token", "service_id", "policy_public_key")}
        broken[alias] = {"token": data["service_token"], "id": data["service_id"]}.get(alias, data["policy_public_key"])
        with pytest.raises(InvalidResponseError):
            validate_enroll_response(broken, None)


@pytest.mark.parametrize("missing", sorted(_good()))
def test_every_response_field_is_required(missing: str) -> None:
    data = {k: v for k, v in _good().items() if k != missing}
    with pytest.raises(InvalidResponseError) as exc:
        validate_enroll_response(data, None)
    assert exc.value.code == "hub_antwoord_ongeldig"


def test_schema_version_major_must_be_1() -> None:
    assert validate_enroll_response({**_good(), "schema_version": "1.3"}, None)  # zelfde major: goed
    with pytest.raises(InvalidResponseError):
        validate_enroll_response({**_good(), "schema_version": "2.0"}, None)


# ---------------------------------------------------------------------------------------------- adresregels (§3.2)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://hub.test:30400", "http://hub.test:30400"),
        ("http://HUB.test:30400/", "http://hub.test:30400"),
        ("https://192.0.2.10", "https://192.0.2.10"),
        ("http://[2001:db8::1]:30400", "http://[2001:db8::1]:30400"),
        ("http://hub.test:30400/api", None),
        ("http://hub.test:30400?x=1", None),
        ("http://hub.test:30400#x", None),
        ("http://a:b@hub.test", None),
        ("ftp://hub.test", None),
        ("http://127.0.0.1:30400", None),
        ("http://localhost:30400", None),
        ("http://app.localhost", None),
        ("http://[::1]:30400", None),
        ("http://169.254.10.1", None),
        ("http://[fe80::1]", None),
        ("http://0.0.0.0", None),
        ("http://[::]", None),
        ("http://hub.test:99999", None),
    ],
)
def test_hub_url_rules(value: str, expected: str | None) -> None:
    try:
        assert normalize_hub_url(value) == expected
    except ValueError:
        assert expected is None


def test_loopback_only_with_dev_flag() -> None:
    assert normalize_hub_url("http://127.0.0.1:30400", allow_loopback=True) == "http://127.0.0.1:30400"
    with pytest.raises(ValueError):
        normalize_hub_url("http://169.254.1.1", allow_loopback=True)  # link-local nooit


def test_env_settings(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        s = Settings.from_env({"HUB_URL": "http://hub.test:30400/pad", "ENROLLMENT_TOKEN": "enr_x"})
    assert s.hub_url is None and "HUB_URL wordt niet gebruikt" in caplog.text
    assert s.enrollment_token == "enr_x" and "ENROLLMENT_TOKEN is vervallen" in caplog.text
    s = Settings.from_env({"HUB_ENROLLMENT_TOKEN": "enr_y", "ENROLLMENT_TOKEN": "enr_x", "HUB_KEY_FINGERPRINT": "MEGG"})
    assert s.enrollment_token == "enr_y" and s.hub_key_fingerprint == "MEGG"
    assert Settings.from_env({"HUB_URL": "http://127.0.0.1:1"}).hub_url is None
    dev = Settings.from_env({"HUB_URL": "http://127.0.0.1:1", "APPSTORE_ALLOW_LOOPBACK_HUB": "1"})
    assert dev.hub_url == "http://127.0.0.1:1" and dev.allow_loopback_hub


# ---------------------------------------------------------------------------------------------- throttle (§3.7)
def test_pairing_throttle_per_ip_and_per_service() -> None:
    limiter = _Attempts(per_ip=5, total=20, window=900)
    assert all(limiter.allow("10.0.0.1") for _ in range(5))
    assert not limiter.allow("10.0.0.1")  # 6e poging van hetzelfde bron-IP
    for i in range(15):
        assert limiter.allow(f"10.0.1.{i}")
    assert not limiter.allow("10.0.2.1")  # 21e poging voor de hele dienst, ook van een nieuw IP


def test_no_secret_in_file_permissions_for_other_users(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    store.update(lambda s: s.model_copy(update={"service_token": "svc_" + "C" * 43}))
    mode = store.path.stat().st_mode
    assert not mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH)
    assert os.stat(store.path).st_uid == os.getuid()
