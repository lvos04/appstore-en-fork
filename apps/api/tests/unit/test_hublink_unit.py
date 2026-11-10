"""Zonder database: hub-link.json (atomisch, 0600, opnieuw lezen, onleesbaar = fail-closed, extra velden, 1.x lezen),
het exacte enroll-verzoek (A5), de antwoordcontrole van §3.3 (ook ``rotation_sequence``), de adresregels van §3.2
(canonieke IP-vormen, slotpunt, ``base_url``, ``Host``-beperking) en de throttle van de statuspagina (§3.7). Contract 1.3."""

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
from appstore.config import Settings, host_allowed_for_form, normalize_base_url, normalize_hub_url
from appstore.hublink import HubLinkClient, InvalidResponseError, validate_enroll_response
from appstore.hublink_store import ERROR_MESSAGES, HubLinkStore, LastError, LinkState, Pins
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
    assert json.loads(store.path.read_text())["schema_version"] == "1.3"


def test_other_process_sees_changes_immediately(tmp_path: Path) -> None:
    """Elk proces leest opnieuw zodra inode/mtime verandert (API en worker zijn aparte processen)."""
    api, worker = HubLinkStore(tmp_path), HubLinkStore(tmp_path)
    linked = {
        "state": "gekoppeld",
        "service_token": "svc_x",
        "hub_url": "http://hub.test:30400",
        "service_id": str(uuid.uuid4()),
        "first_linked_at": "2026-11-10T12:00:00Z",
        "linked_at": "2026-11-10T12:00:00Z",
        "pins": Pins(jwt=[], policy=[], keyset_fingerprint="MEGG-8GFG-2E4Y-K2RQ"),
    }
    worker.update(lambda s: s.model_copy(update=linked))
    assert api.read().state == "gekoppeld"
    worker.update(lambda s: s.model_copy(update={"state": "ontkoppeld", "service_token": None}))
    assert api.read().state == "ontkoppeld" and api.read().service_token is None


def _write_raw(store: HubLinkStore, data: object, mode: int = 0o600) -> None:
    store.dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    os.chmod(store.path, mode)


GOOD = {"schema_version": "1.3", "state": "nooit_gekoppeld", "app_id": APP_ID}


@pytest.mark.parametrize(
    ("data", "mode"),
    [
        ("{ kapot", 0o600),  # geen geldige JSON
        ([], 0o600),  # geen object
        ({**GOOD, "schema_version": "2.0"}, 0o600),  # andere major
        ({k: v for k, v in GOOD.items() if k != "schema_version"}, 0o600),  # verplicht veld ontbreekt
        ({k: v for k, v in GOOD.items() if k != "state"}, 0o600),
        ({**GOOD, "state": "bestaat-niet"}, 0o600),
        ({**GOOD, "app_id": "andere-dienst"}, 0o600),
        ({**GOOD, "state": "gekoppeld"}, 0o600),  # gekoppeld zonder token, pins, adres
        (GOOD, 0o644),  # verkeerde rechten
    ],
)
def test_unreadable_file_is_fail_closed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, data: object, mode: int
) -> None:
    """§3.5: elke vorm van onleesbaar → niets vertrouwen, geen formulier (``unreadable``), fout in de log."""
    store = HubLinkStore(tmp_path)
    _write_raw(store, data, mode)
    with caplog.at_level(logging.ERROR):
        st = store.read()
    assert st.corrupt and st.unreadable and not st.linked and "onleesbaar" in caplog.text
    assert st.first_linked_at is None  # geen verzonnen tijdstip meer
    pub = st.public()
    assert pub["state"] is None and pub["last_error"]["code"] == "koppelstatus_onleesbaar"


def test_readers_accept_any_1_x_and_keep_extra_fields(tmp_path: Path) -> None:
    store = HubLinkStore(tmp_path)
    _write_raw(store, {**GOOD, "schema_version": "1.7", "nieuw_veld": {"x": 1}})
    st = store.read()
    assert not st.corrupt and st.state == "nooit_gekoppeld"
    store.update(lambda s: s.model_copy(update={"hub_url": "http://hub.test:30400"}))
    data = json.loads(store.path.read_text())
    assert data["schema_version"] == "1.3" and data["nieuw_veld"] == {"x": 1}  # extra velden blijven staan


def test_an_ordinary_write_never_replaces_an_unreadable_file(tmp_path: Path) -> None:
    """§3.5: een gewone schrijfactie zet een onleesbaar bestand nooit stil terug naar ``nooit_gekoppeld``."""
    store = HubLinkStore(tmp_path)
    _write_raw(store, "{ kapot")
    called: list[bool] = []
    after = store.update(lambda s: called.append(True) or s)  # type: ignore[func-returns-value]
    assert called == [] and after.corrupt and store.path.read_text() == "{ kapot"
    assert not [p for p in tmp_path.iterdir() if ".onleesbaar-" in p.name]


def test_replacing_an_unreadable_file_keeps_the_original_first(tmp_path: Path) -> None:
    """§3.5: alleen bij een poging met een nieuw env-token; eerst het origineel bewaren (0600), dan vervangen."""
    store = HubLinkStore(tmp_path)
    _write_raw(store, "{ kapot")
    after = store.update(
        lambda s: s.model_copy(update={"last_error": LastError(code="hub_weigerde", at="x")}), replace_unreadable=True
    )
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("hub-link.json.onleesbaar-")]
    assert len(backups) == 1 and backups[0].read_text() == "{ kapot"
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    st = HubLinkStore(tmp_path).read()
    assert not st.corrupt and st.unreadable_replaced_at and st.first_linked_at is None
    assert st.unreadable and not after.corrupt  # zolang first_linked_at leeg is: nog steeds "onleesbaar" (§3.7)
    assert st.state == "nooit_gekoppeld" and not st.linked


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
        "schema_version": "1.3",
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
        "rotation_sequence": 0,
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


@pytest.mark.parametrize("missing", sorted(set(_good()) - {"rotation_sequence"}))
def test_every_response_field_is_required(missing: str) -> None:
    data = {k: v for k, v in _good().items() if k != missing}
    with pytest.raises(InvalidResponseError) as exc:
        validate_enroll_response(data, None)
    assert exc.value.code == "hub_antwoord_ongeldig"


def test_schema_version_major_must_be_1() -> None:
    assert validate_enroll_response({**_good(), "schema_version": "1.2"}, None)  # oudere Hub: zelfde major, goed
    assert validate_enroll_response({**_good(), "schema_version": "1.9"}, None)
    with pytest.raises(InvalidResponseError):
        validate_enroll_response({**_good(), "schema_version": "2.0"}, None)


def test_rotation_sequence_is_optional_but_typed() -> None:
    """1.3 §3.3: ``rotation_sequence`` (geheel getal ≥ 0); een Hub 1.2 stuurt het niet → 0; extra velden negeren."""
    old_hub = {k: v for k, v in _good().items() if k != "rotation_sequence"} | {"schema_version": "1.2"}
    assert validate_enroll_response(old_hub, None)["_sequence"] == 0
    assert validate_enroll_response({**_good(), "rotation_sequence": 7, "onbekend": 1}, None)["_sequence"] == 7
    for bad in (-1, "3", True, 1.5, None):
        with pytest.raises(InvalidResponseError) as exc:
            validate_enroll_response({**_good(), "rotation_sequence": bad}, None)
        assert exc.value.code == "hub_antwoord_ongeldig"


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
        # contract 1.3 §3.2: alleen canonieke IP-vormen; slotpunt telt niet mee
        ("http://127.1:30400", None),
        ("http://2130706433", None),
        ("http://0x7f000001", None),
        ("http://010.0.0.1", None),
        ("http://0", None),
        ("http://192.168.001.10", None),
        ("http://1.2.3.256", None),
        ("http://hub.0x1", None),
        ("http://localhost.:30400", None),
        ("http://hub.test.:30400", "http://hub.test:30400"),
        ("http://[::ffff:127.0.0.1]", None),
        ("http://hub..test", None),
        ("http://hub_test:30400", None),
    ],
)
def test_hub_url_rules(value: str, expected: str | None) -> None:
    try:
        assert normalize_hub_url(value) == expected
    except ValueError:
        assert expected is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("http://192.0.2.10:30430", "http://192.0.2.10:30430"),
        ("http://192.0.2.10:30430/", "http://192.0.2.10:30430"),
        ("http://nas.local:30430/appstore", "http://nas.local:30430/appstore"),
        ("http://127.1:30430", None),
        ("http://192.0.2.10:30430/?x=1", None),
        ("http://192.0.2.10:30430/<script>", None),
        ("http://" + "a" * 600, None),
        ("ftp://192.0.2.10", None),
    ],
)
def test_base_url_only_when_valid(value: str | None, expected: str | None) -> None:
    """§3.3: ``base_url`` alleen als hij aan de regels voldoet, anders ``null`` (nooit een reden voor 422)."""
    assert normalize_base_url(value) == expected


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("192.0.2.10:30430", True),
        ("[2001:db8::1]:30430", True),
        ("nas", True),
        ("nas:30430", True),
        ("testserver", True),
        ("nas.local", True),
        ("nas.lan:30430", True),
        ("nas.internal", True),
        ("nas.home.arpa", True),
        ("NAS.LAN.", True),
        ("nas.fritz.box", False),  # .box is een publieke TLD (keuze K1)
        ("hub.example.com", False),
        ("evil.com:30430", False),
        ("127.1", False),
        ("2130706433:30430", False),
        ("", False),
        (None, False),
        ("a b", False),
    ],
)
def test_host_restriction_against_dns_rebinding(host: str | None, allowed: bool) -> None:
    """§3.7 punt 4 (K1): IP in de vorm van §3.2, naam zonder punt of onder .local/.lan/.internal/.home.arpa."""
    assert host_allowed_for_form(host) is allowed


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
    with caplog.at_level(logging.WARNING):
        Settings.from_env({"HUB_KEY_FINGERPRINT": "geen-vingerafdruk"})
    assert "HUB_KEY_FINGERPRINT heeft niet het formaat" in caplog.text
    dev = Settings.from_env({"HUB_URL": "http://127.0.0.1:1", "APPSTORE_ALLOW_LOOPBACK_HUB": "1"})
    assert dev.hub_url == "http://127.0.0.1:1" and dev.allow_loopback_hub
    bad = Settings.from_env({"HUB_URL": "http://127.1:30400", "HUB_ENROLLMENT_TOKEN": "enr_" + "A" * 43})
    assert bad.hub_url is None and bad.hub_url_invalid  # §3.5: hub_adres_ongeldig, de Hub niet aanspreken
    assert not Settings.from_env({}).hub_url_invalid


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
