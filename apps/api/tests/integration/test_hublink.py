"""Koppelen met het env-token, herstart halverwege, intrekking, token- en sleutelrotatie en de eenmalige overzetting
uit de database (contract 1.2 §3.2–§3.8, §4.5). Tegen echte PostgreSQL met de Hub-simulator (Sim-getest)."""

from __future__ import annotations

import dataclasses
import json
import stat
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from appstore.api.common import AppContext
from appstore.app import create_app
from appstore.config import Settings
from appstore.hubkeys import sha256_hex
from appstore.hublink import ensure_link_file
from appstore.hublink_store import LinkState, Pending
from appstore.models import HubLink, OutboxEvent
from appstore.secrets_store import LEGACY_TOKEN_AAD
from appstore.timeutil import iso, utcnow
from appstore.worker import build_context, push_outbox, run_once, startup
from tests.fakehub import HUB_URL, FakeHub, hub_jwk
from tests.integration.conftest import pair

pytestmark = pytest.mark.integration


@pytest.fixture
def env_settings(settings: Settings, hub: FakeHub) -> Settings:
    return dataclasses.replace(settings, hub_url=HUB_URL, enrollment_token=hub.add_token())


@pytest.fixture
def ctx(env_settings: Settings, hub: FakeHub) -> Iterator[AppContext]:
    c = build_context(env_settings, hub.client_factory())
    startup(c)
    yield c
    c.engine.dispose()


def _restart(c: AppContext, hub: FakeHub, **changes: Any) -> AppContext:
    """Nieuw workerproces op dezelfde /appdata (zoals een herstart van de container)."""
    c.engine.dispose()
    new = build_context(dataclasses.replace(c.settings, **changes), hub.client_factory())
    startup(new)
    return new


def _file(c: AppContext) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((c.settings.secrets_dir / "hub-link.json").read_text())
    return data


# ---------------------------------------------------------------------------------------------- env-token (§3.2, §3.6)
def test_env_enrollment_exact_request_pins_and_confirmation(ctx: AppContext, hub: FakeHub) -> None:
    run_once(ctx)
    req = hub.enroll_requests[0]
    assert set(req) == {"app_id", "enrollment_token", "version", "base_url", "public_key_jwk"}  # exact §3.3
    assert req["enrollment_token"] == ctx.settings.enrollment_token and req["base_url"] is None
    jwk = req["public_key_jwk"]
    assert set(jwk) == {"kty", "crv", "x", "kid"} and "use" not in jwk and "alg" not in jwk
    st = ctx.store.read()
    assert st.state == "gekoppeld" and st.via == "token" and st.confirmed_at and st.service_id == hub.service_id
    assert st.pins is not None and st.pins.keyset_fingerprint == hub.fingerprint()
    assert [p.source for p in st.pins.jwt + st.pins.policy] == ["enroll", "enroll"]
    assert st.used_enrollment_token_sha256 == [sha256_hex(ctx.settings.enrollment_token or "")]
    path = ctx.settings.secrets_dir / "hub-link.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    n = len(hub.enroll_requests)
    run_once(ctx)
    assert len(hub.enroll_requests) == n  # afgehandeld token wordt nooit opnieuw gebruikt


def test_expected_fingerprint_from_env(env_settings: Settings, hub: FakeHub) -> None:
    for fp, state in (("0000-0000-0000-0000", "nooit_gekoppeld"), (hub.fingerprint().lower(), "gekoppeld")):
        token = hub.add_token()
        c = build_context(
            dataclasses.replace(env_settings, enrollment_token=token, hub_key_fingerprint=fp), hub.client_factory()
        )
        try:
            startup(c)
            run_once(c)
            st = c.store.read()
            assert st.state == state, st
            if state != "gekoppeld":
                assert st.last_error is not None and st.last_error.code == "hub_vingerafdruk_onjuist"
                assert sha256_hex(token) in st.used_enrollment_token_sha256  # definitief voor dit token
        finally:
            c.engine.dispose()


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "hub_weigerde"), (404, "enroll_endpoint_ontbreekt"), (422, "hub_incompatibel"), (403, "hub_weigerde")],
)
def test_definitive_errors_are_never_retried(ctx: AppContext, hub: FakeHub, status: int, code: str) -> None:
    hub.enroll_status = status
    run_once(ctx)
    run_once(ctx)
    assert len(hub.enroll_requests) == 1
    st = ctx.store.read()
    assert st.state == "nooit_gekoppeld" and st.pending is None
    assert st.last_error is not None and st.last_error.code == code


def test_unknown_token_gets_invalid_enrollment(settings: Settings, hub: FakeHub) -> None:
    s = dataclasses.replace(settings, hub_url=HUB_URL, enrollment_token="enr_" + "A" * 43)
    c = build_context(s, hub.client_factory())
    try:
        startup(c)
        run_once(c)
        assert c.store.read().last_error.code == "hub_weigerde"  # type: ignore[union-attr]
        assert hub.enroll_responses == []
    finally:
        c.engine.dispose()


def test_expired_env_token_is_definitive(settings: Settings, hub: FakeHub) -> None:
    """Verlopen enrollment-token (> 60 min na aanmaak bij de Hub): 401 ``invalid_enrollment`` → definitief, de hash
    gaat in ``used_enrollment_token_sha256`` en het token wordt nooit opnieuw geprobeerd (§3.3, §3.6)."""
    token = hub.add_token(age=3601)
    c = build_context(dataclasses.replace(settings, hub_url=HUB_URL, enrollment_token=token), hub.client_factory())
    try:
        startup(c)
        run_once(c)
        run_once(c)
        st = c.store.read()
        assert len(hub.enroll_requests) == 1 and hub.enroll_responses == []
        assert st.state == "nooit_gekoppeld" and st.pending is None and st.pins is None
        assert st.last_error is not None and st.last_error.code == "hub_weigerde"
        assert sha256_hex(token) in st.used_enrollment_token_sha256
        c2 = _restart(c, hub)  # ook na een herstart niet opnieuw
        try:
            run_once(c2)
            assert len(hub.enroll_requests) == 1
        finally:
            c2.engine.dispose()
    finally:
        c.engine.dispose()


def test_reused_env_token_is_definitive(settings: Settings, hub: FakeHub) -> None:
    """Hergebruikt token: al gebruikt door een andere installatie (andere identiteitssleutel) → 401, definitief; er wordt
    niets opgeslagen en de andere registratie wordt niet overgenomen."""
    from appstore.hubkeys import enroll_jwk

    token = hub.add_token()
    other = {
        "app_id": "appstore-en-fork",
        "enrollment_token": token,
        "version": "0.1.0",
        "base_url": None,
        "public_key_jwk": enroll_jwk(Ed25519PrivateKey.generate().public_key()),
    }
    with hub.client_factory()() as http:
        assert http.post(f"{HUB_URL}/api/v1/services/enroll", json=other).status_code == 200
    c = build_context(dataclasses.replace(settings, hub_url=HUB_URL, enrollment_token=token), hub.client_factory())
    try:
        startup(c)
        run_once(c)
        run_once(c)
        st = c.store.read()
        assert len(hub.enroll_requests) == 2  # de andere installatie + precies één poging van deze dienst
        assert st.state == "nooit_gekoppeld" and st.service_token is None
        assert st.last_error is not None and st.last_error.code == "hub_weigerde"
        assert sha256_hex(token) in st.used_enrollment_token_sha256
    finally:
        c.engine.dispose()


def test_malformed_env_token_is_not_sent(settings: Settings, hub: FakeHub) -> None:
    s = dataclasses.replace(settings, hub_url=HUB_URL, enrollment_token="eenmalig-token-1234567890")
    c = build_context(s, hub.client_factory())
    try:
        startup(c)
        run_once(c)
        assert hub.enroll_requests == []
        assert c.store.read().last_error.code == "token_ongeldig"  # type: ignore[union-attr]
    finally:
        c.engine.dispose()


def test_transient_errors_back_off_and_stop_after_60_minutes(ctx: AppContext, hub: FakeHub) -> None:
    hub.enroll_status = 503
    run_once(ctx)
    assert len(hub.enroll_requests) == 1
    st = ctx.store.read()
    assert st.pending is not None and st.pending.kind == "token" and st.pending.attempts == 1
    assert st.last_error is not None and st.last_error.code == "hub_onbereikbaar"
    run_once(ctx)
    assert len(hub.enroll_requests) == 1  # backoff: start 5 s
    ctx.hub._next_env_attempt = 0.0
    run_once(ctx)
    assert len(hub.enroll_requests) == 2 and ctx.store.read().pending.attempts == 2  # type: ignore[union-attr]
    # 60 min na de eerste poging: stoppen, definitief
    ctx.store.update(
        lambda s: s.model_copy(
            update={"pending": s.pending.model_copy(update={"started_at": iso(utcnow() - timedelta(minutes=61))})}
        )
    )  # type: ignore[union-attr]
    ctx.hub._next_env_attempt = 0.0
    run_once(ctx)
    st = ctx.store.read()
    assert len(hub.enroll_requests) == 2 and st.pending is None
    assert st.last_error is not None and st.last_error.code == "token_verlopen_na_60_min"


def test_rate_limited_waits_for_retry_after(ctx: AppContext, hub: FakeHub) -> None:
    hub.enroll_status = 429
    hub.failed_attempts.clear()
    run_once(ctx)
    assert ctx.store.read().last_error.code == "rate_limited"  # type: ignore[union-attr]
    assert ctx.hub._next_env_attempt > 0
    run_once(ctx)
    assert len(hub.enroll_requests) == 1


def test_simulator_rate_limits_after_ten_failures(hub: FakeHub, settings: Settings) -> None:
    """Hub-simulator §3.3: na 10 mislukte pogingen binnen 15 min krijgt elke volgende 429 + Retry-After."""
    import httpx

    with hub.client_factory()() as client:
        body = {
            "app_id": "appstore-en-fork",
            "enrollment_token": "enr_" + "B" * 43,
            "version": "0.1.0",
            "base_url": None,
            "public_key_jwk": {"kty": "OKP", "crv": "Ed25519", "x": hub_jwk(Ed25519PrivateKey.generate())["x"]},
        }
        codes = [client.post(f"{HUB_URL}/api/v1/services/enroll", json=body).status_code for _ in range(11)]
        assert codes == [401] * 10 + [429]
        resp = client.post(f"{HUB_URL}/api/v1/services/enroll", json=body)
        assert resp.status_code == 429 and resp.headers["Retry-After"]
        bad = client.post(f"{HUB_URL}/api/v1/services/enroll", json={**body, "capabilities": ["read"]})
        assert bad.status_code == 429  # ook dan; zonder limiet zou het 422 zijn
        hub.failed_attempts.clear()
        assert (
            client.post(f"{HUB_URL}/api/v1/services/enroll", json={**body, "capabilities": ["read"]}).status_code == 422
        )
        jwk = {**body["public_key_jwk"], "use": "sig", "alg": "EdDSA"}
        assert client.post(f"{HUB_URL}/api/v1/services/enroll", json={**body, "public_key_jwk": jwk}).status_code == 422
        assert isinstance(client, httpx.Client)


def _patch(**changes: Any) -> Any:
    def patch(out: dict[str, Any]) -> dict[str, Any]:
        out = {**out, **changes}
        return {k: v for k, v in out.items() if v is not ...}

    return patch


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        (_patch(jwks=...), "hub_antwoord_ongeldig"),
        (_patch(schema_version="2.0"), "hub_antwoord_ongeldig"),
        (_patch(app_id="iets-anders"), "hub_antwoord_ongeldig"),
        (_patch(service_id="geen-uuid"), "hub_antwoord_ongeldig"),
        (_patch(service_token="svc_te-kort"), "hub_antwoord_ongeldig"),
        (_patch(token_type="Basic"), "hub_antwoord_ongeldig"),
        (_patch(enrollment_replayed="nee"), "hub_antwoord_ongeldig"),
        (_patch(keyset_fingerprint="0000-0000-0000-0000"), "hub_vingerafdruk_onjuist"),
        (
            lambda o: {**o, "policy_public_key": {k: v for k, v in o["policy_public_key"].items() if k != "use"}},
            "hub_antwoord_ongeldig",
        ),
        (lambda o: {**o, "policy_public_key": {**o["policy_public_key"], "d": "geheim"}}, "hub_antwoord_ongeldig"),
        (lambda o: {**o, "jwks": {"keys": [o["jwks"]["keys"][0]] * 2}}, "hub_antwoord_ongeldig"),  # dubbele kid
    ],
)
def test_invalid_responses_store_nothing(ctx: AppContext, hub: FakeHub, patch: Any, code: str) -> None:
    hub.response_patch = patch
    run_once(ctx)
    st = ctx.store.read()
    assert st.state == "nooit_gekoppeld" and st.service_token is None and st.pins is None and st.hub_url is None
    assert st.last_error is not None and st.last_error.code == code


def test_unknown_extra_response_fields_are_ignored(ctx: AppContext, hub: FakeHub) -> None:
    hub.response_patch = _patch(nieuw_veld_van_later={"x": 1})
    run_once(ctx)
    assert ctx.store.read().state == "gekoppeld"


# ---------------------------------------------------------------------------------------------- herstart (§3.6 stap 5)
def test_crash_after_hub_issued_token_allows_replay(
    ctx: AppContext, hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dienst crasht nadat de Hub het token gaf maar vóór opslaan → na herstart toegestane herhaling."""

    def crash(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("stroom weg")

    monkeypatch.setattr(ctx.hub, "_succeed", crash)
    with pytest.raises(RuntimeError):
        run_once(ctx)
    first_token = hub.last_service_token
    st = ctx.store.read()
    assert st.state == "nooit_gekoppeld" and st.pending is not None and st.pending.kind == "token"
    c2 = _restart(ctx, hub)
    try:
        run_once(c2)
        st = c2.store.read()
        assert st.state == "gekoppeld" and st.pending is None and st.service_token != first_token
        assert hub.enroll_responses[-1][1]["enrollment_replayed"] is True
        assert hub.tokens[first_token or ""].revoked  # ongebruikt token van de eerste poging ingetrokken
    finally:
        c2.engine.dispose()


def test_restart_with_other_or_no_token_interrupts_pending(ctx: AppContext, hub: FakeHub) -> None:
    hub.enroll_status = 503
    run_once(ctx)
    assert ctx.store.read().pending is not None
    c2 = _restart(ctx, hub, enrollment_token=None)
    try:
        st = c2.store.read()
        assert st.pending is None and st.last_error is not None and st.last_error.code == "koppelen_onderbroken"
    finally:
        c2.engine.dispose()


def test_api_restart_clears_pending_koppelcode(settings: Settings, hub: FakeHub, worker_ctx: AppContext) -> None:
    worker_ctx.store.update(
        lambda s: s.model_copy(
            update={
                "pending": Pending(
                    kind="koppelcode", secret_sha256="ab" * 32, hub_url=HUB_URL, started_at=iso(utcnow()) or ""
                )
            }
        )
    )
    app = create_app(settings, http_factory=hub.client_factory())
    try:
        st = app.state.ctx.store.read()
        assert st.pending is None and st.last_error is not None and st.last_error.code == "koppelen_onderbroken"
        with TestClient(app) as c:
            assert "onderbroken" in c.get("/").text and 'action="/koppelen"' in c.get("/").text
    finally:
        app.state.ctx.engine.dispose()


def test_env_token_waits_while_a_pairing_code_attempt_runs(ctx: AppContext, hub: FakeHub) -> None:
    """Nooit twee enrollments tegelijk: een lopende koppelcodepoging (API-proces) gaat voor."""
    ctx.store.update(
        lambda s: s.model_copy(
            update={
                "pending": Pending(
                    kind="koppelcode", secret_sha256="cd" * 32, hub_url=HUB_URL, started_at=iso(utcnow()) or ""
                )
            }
        )
    )
    run_once(ctx)
    assert hub.enroll_requests == [] and ctx.store.read().pending.kind == "koppelcode"  # type: ignore[union-attr]
    # een koppelcodepoging ouder dan 2 min is onderbroken; daarna gaat het env-token alsnog
    ctx.store.update(
        lambda s: s.model_copy(
            update={"pending": s.pending.model_copy(update={"started_at": iso(utcnow() - timedelta(minutes=3))})}  # type: ignore[union-attr]
        )
    )
    c2 = _restart(ctx, hub)
    try:
        run_once(c2)
        assert c2.store.read().state == "gekoppeld" and len(hub.enroll_requests) == 1
    finally:
        c2.engine.dispose()


def test_orphaned_pairing_attempt_does_not_block_env_token_without_restart(ctx: AppContext, hub: FakeHub) -> None:
    """Een verweesde koppelcodepoging (ouder dan 2 min, bv. een afgebroken verzoek) houdt het env-token niet tot de
    volgende herstart tegen: de worker markeert hem als onderbroken en koppelt."""
    ctx.store.update(
        lambda s: s.model_copy(
            update={
                "pending": Pending(
                    kind="koppelcode",
                    secret_sha256="cd" * 32,
                    hub_url=HUB_URL,
                    started_at=iso(utcnow() - timedelta(minutes=3)) or "",
                )
            }
        )
    )
    run_once(ctx)
    st = ctx.store.read()
    assert st.state == "gekoppeld" and st.via == "token" and len(hub.enroll_requests) == 1


def test_linked_without_confirmation_only_confirms_again(ctx: AppContext, hub: FakeHub) -> None:
    hub.self_status = 503
    run_once(ctx)
    st = ctx.store.read()
    assert st.state == "gekoppeld" and st.confirmed_at is None
    hub.self_status = None
    c2 = _restart(ctx, hub)
    try:
        n = len(hub.enroll_requests)
        run_once(c2)
        assert c2.store.read().confirmed_at and len(hub.enroll_requests) == n  # geen nieuwe enrollment
    finally:
        c2.engine.dispose()


# ---------------------------------------------------------------------------------------------- opnieuw koppelen (§3.8)
def test_new_env_token_relinks_even_when_linked_or_revoked(ctx: AppContext, hub: FakeHub) -> None:
    run_once(ctx)
    first = ctx.store.read()
    hub.revoke_all()
    ctx.hub.confirm(trace_id="t")
    assert ctx.store.read().state == "ingetrokken"
    c2 = _restart(ctx, hub, enrollment_token=hub.add_token())
    try:
        run_once(c2)
        st = c2.store.read()
        assert st.state == "gekoppeld" and st.service_token != first.service_token
        assert st.first_linked_at == first.first_linked_at and st.previous_pins is not None
        assert len(st.used_enrollment_token_sha256) == 2
    finally:
        c2.engine.dispose()


def test_failed_relink_keeps_existing_link(ctx: AppContext, hub: FakeHub) -> None:
    run_once(ctx)
    before = ctx.store.read()
    c2 = _restart(ctx, hub, enrollment_token="enr_" + "C" * 43)  # onbekend token → 401
    try:
        run_once(c2)
        st = c2.store.read()
        assert st.state == "gekoppeld" and st.service_token == before.service_token and st.pins == before.pins
        assert st.last_error is not None and st.last_error.code == "hub_weigerde"
    finally:
        c2.engine.dispose()


# ---------------------------------------------------------------------------------------------- rotatie (§3.8)
def test_token_rotation_on_hub_request(ctx: AppContext, hub: FakeHub) -> None:
    run_once(ctx)
    old = ctx.store.read().service_token
    hub.rotation_required = True
    with ctx.sessions() as db:
        from appstore.events import emit

        emit(db, "catalog.test.event", "test", "1", {}, "00000000-0000-0000-0000-000000000000")
        db.commit()
    push_outbox(ctx)  # antwoord met Hub-Token-Rotation: required
    assert ctx.store.read().token_rotation_requested_at
    run_once(ctx)  # worker roteert, slaat op en bevestigt
    st = ctx.store.read()
    assert st.service_token != old and st.service_token == hub.last_service_token and st.confirmed_at
    assert st.token_rotation_requested_at is None and st.service_token_last4 == (st.service_token or "")[-4:]
    assert ("POST", "/api/v1/services/self/rotate") in hub.requests


def test_idle_service_rotates_within_five_minutes(ctx: AppContext, hub: FakeHub) -> None:
    """§3.8: na 'rotatie aanvragen' zet de Hub de header alleen op geauthenticeerde dienst-aanroepen, en de dienst
    roteert binnen 5 min. Een dienst zonder events doet uit zichzelf geen aanroep; daarom bevestigt de worker
    periodiek (≤ 4 min) met ``GET /api/v1/services/self``. (Gevonden met de echte Hub-code: rotatie bleef liggen.)"""
    from appstore.hublink import HEARTBEAT_INTERVAL

    assert HEARTBEAT_INTERVAL < 5 * 60
    run_once(ctx)
    old = ctx.store.read().service_token
    first_confirmed = ctx.store.read().confirmed_at
    hub.rotation_required = True
    run_once(ctx)  # binnen het interval: geen extra aanroep
    assert ctx.store.read().service_token == old
    n_self = hub.requests.count(("GET", "/api/v1/services/self"))
    ctx.hub._next_heartbeat = 0.0  # het interval is verstreken
    run_once(ctx)
    run_once(ctx)
    st = ctx.store.read()
    assert hub.requests.count(("GET", "/api/v1/services/self")) > n_self
    assert st.service_token != old and st.service_token == hub.last_service_token and st.confirmed_at
    assert st.token_rotation_requested_at is None
    assert first_confirmed is not None


def test_heartbeat_detects_revocation_and_ignores_outages(ctx: AppContext, hub: FakeHub) -> None:
    """Periodieke bevestiging: 5xx/netwerk is nooit een intrekking; 401 op services/self wel (§3.8)."""
    run_once(ctx)
    hub.self_status = 503
    ctx.hub._next_heartbeat = 0.0
    run_once(ctx)
    assert ctx.store.read().state == "gekoppeld"
    hub.self_status = None
    hub.down = True
    ctx.hub._next_heartbeat = 0.0
    run_once(ctx)
    assert ctx.store.read().state == "gekoppeld"
    hub.down = False
    hub.revoke_all()
    ctx.hub._next_heartbeat = 0.0
    run_once(ctx)
    st = ctx.store.read()
    assert st.state == "ingetrokken" and st.last_error is not None and st.last_error.code == "ingetrokken_door_hub"


def test_interrupted_rotation_rotates_again(ctx: AppContext, hub: FakeHub, monkeypatch: pytest.MonkeyPatch) -> None:
    """Crash na de rotatie bij de Hub maar vóór opslaan: oud token in gebruik, nieuwste ongebruikt → header → opnieuw."""
    run_once(ctx)
    old = ctx.store.read().service_token
    hub.rotation_required = True
    ctx.hub.confirm(trace_id="t")
    original_update = ctx.store.update
    calls = {"n": 0}

    def crash_on_token_write(change: Any) -> LinkState:
        probe = change(ctx.store.read())
        if probe is not None and probe.service_token not in (None, old):
            calls["n"] += 1
            raise RuntimeError("crash tijdens het opslaan van het nieuwe token")
        return original_update(change)

    monkeypatch.setattr(ctx.store, "update", crash_on_token_write)
    with pytest.raises(RuntimeError):
        ctx.hub.rotate_token(trace_id="t")
    monkeypatch.setattr(ctx.store, "update", original_update)
    abandoned = hub.last_service_token
    assert ctx.store.read().service_token == old and calls["n"] == 1
    ctx.store.update(lambda s: s.model_copy(update={"token_rotation_requested_at": None}))
    ctx.hub.confirm(trace_id="t")  # oud token gebruikt terwijl het nieuwste nooit gebruikt is → header
    assert ctx.store.read().token_rotation_requested_at
    run_once(ctx)
    st = ctx.store.read()
    assert st.service_token not in (old, abandoned) and hub.tokens[abandoned or ""].revoked


# ---------------------------------------------------------------------------------------------- sleutelrotatie (§4.5)
def test_signed_key_rotation_is_applied_in_order(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> None:
    pair(client, hub)
    old_key, new_key = hub.key, Ed25519PrivateKey.generate()
    old_kid = hub_jwk(old_key)["kid"]
    hub.key_rotation_items = [
        hub.rotation_statement(1, "jwt", [hub_jwk(new_key)], [], signer=old_key),
        hub.rotation_statement(2, "jwt", [], [old_kid], signer=new_key),
    ]
    assert worker_ctx.hub.poll_key_rotations(trace_id="t") == 2
    st = worker_ctx.store.read()
    assert st.pins is not None and st.pins.rotation_sequence == 2
    assert [p.kid for p in st.pins.jwt] == [hub_jwk(new_key)["kid"]] and st.pins.jwt[0].source == "rotation"
    hub.key = new_key
    assert st.pins.keyset_fingerprint == hub.fingerprint()
    assert client.get("/catalog/v1/sources", headers=hub.auth()).status_code == 200
    resp = client.get("/catalog/v1/sources", headers=hub.auth(key=old_key))
    assert resp.status_code == 401 and resp.json()["details"]["reason"] == "unknown_key"
    assert worker_ctx.hub.poll_key_rotations(trace_id="t") == 0  # niets nieuws na sequence 2


@pytest.mark.parametrize(
    "case",
    [
        "niet_gepinde_sleutel",
        "gat_in_volgorde",
        "laatste_sleutel",
        "ander_doel",
        "verkeerde_typ",
        "volgnummer_tekst",
        "volgnummer_bool",
        "geen_object",
    ],
)
def test_invalid_key_rotation_is_refused(client: TestClient, hub: FakeHub, worker_ctx: AppContext, case: str) -> None:
    pair(client, hub)
    new_key = Ed25519PrivateKey.generate()
    kid = hub_jwk(hub.key)["kid"]
    item = {
        "niet_gepinde_sleutel": lambda: hub.rotation_statement(1, "jwt", [hub_jwk(new_key)], [], signer=new_key),
        "gat_in_volgorde": lambda: hub.rotation_statement(2, "jwt", [hub_jwk(new_key)], [], signer=hub.key),
        "laatste_sleutel": lambda: hub.rotation_statement(1, "jwt", [], [kid], signer=hub.key),
        "ander_doel": lambda: hub.rotation_statement(1, "jwt", [hub_jwk(new_key)], [], signer=hub.policy_key),
        "verkeerde_typ": lambda: {
            "sequence": 1,
            "statement": __import__("jwt").encode({"x": 1}, hub.key, algorithm="EdDSA", headers={"kid": kid}),
        },
        "volgnummer_tekst": lambda: {
            **hub.rotation_statement(1, "jwt", [hub_jwk(new_key)], [], signer=hub.key),
            "sequence": "een",
        },
        "volgnummer_bool": lambda: {
            **hub.rotation_statement(1, "jwt", [hub_jwk(new_key)], [], signer=hub.key),
            "sequence": True,
        },
        "geen_object": lambda: ["sequence", 1],
    }[case]()
    hub.key_rotation_items = [item]
    before = worker_ctx.store.read().pins
    assert worker_ctx.hub.poll_key_rotations(trace_id="t") == 0
    st = worker_ctx.store.read()
    assert st.pins == before and st.key_rotation_blocked_sequence == 1
    with worker_ctx.sessions() as db:
        from sqlalchemy import func, select

        rejected = db.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "service.key_rotation.rejected")
        )
    worker_ctx.hub.poll_key_rotations(trace_id="t")
    with worker_ctx.sessions() as db:
        again = db.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "service.key_rotation.rejected")
        )
    assert rejected == 1 and again == 1  # één incident per geweigerd volgnummer, niet bij elke poll


def test_hub_without_key_rotation_endpoint(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> None:
    pair(client, hub)
    hub.key_rotation_status = 404
    assert worker_ctx.hub.poll_key_rotations(trace_id="t") == 0
    hub.key_rotation_status = 200
    assert worker_ctx.hub.poll_key_rotations(trace_id="t") == 0  # {"items": []}
    assert worker_ctx.store.read().pins.rotation_sequence == 0  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------- overzetten (§3.5)
def _legacy_row(worker_ctx: AppContext, hub: FakeHub, **changes: Any) -> None:
    token = "svc_" + "D" * 43
    nonce, ciphertext = worker_ctx.secrets.vault.encrypt(token, LEGACY_TOKEN_AAD)
    row = {
        "id": 1,
        "status": "linked",
        "hub_url": HUB_URL,
        "service_id": hub.service_id,
        "linked_via": "koppelcode",
        "linked_at": utcnow(),
        "token_nonce": nonce,
        "token_ciphertext": ciphertext,
        "token_hash": "x" * 64,
        "token_last4": token[-4:],
        "jwks": hub.jwks(),
        "jwks_fetched_at": utcnow(),
        "policy_public_jwk": hub.policy_jwk(),
        "used_enrollment_token_hash": "e" * 64,
        "attempts": 1,
        "updated_at": utcnow(),
    }
    row.update(changes)
    with worker_ctx.sessions() as db:
        db.add(HubLink(**row))
        db.commit()


def _fresh_store(worker_ctx: AppContext) -> None:
    (worker_ctx.settings.secrets_dir / "hub-link.json").unlink(missing_ok=True)


def test_legacy_link_is_transferred_once_and_removed(worker_ctx: AppContext, hub: FakeHub) -> None:
    _fresh_store(worker_ctx)
    _legacy_row(worker_ctx, hub)
    assert ensure_link_file(worker_ctx.store, worker_ctx.sessions, worker_ctx.secrets) == "overgezet"
    st = worker_ctx.store.read()
    assert st.state == "gekoppeld" and st.service_token == "svc_" + "D" * 43 and st.confirmed_at is None
    assert st.pins is not None and {p.source for p in st.pins.jwt + st.pins.policy} == {"overgezet"}
    assert st.pins.keyset_fingerprint == hub.fingerprint() and st.used_enrollment_token_sha256 == ["e" * 64]
    with worker_ctx.sessions() as db:
        assert db.get(HubLink, 1) is None  # daarna uit de database verwijderd
    # een database-herstel brengt de oude rij terug: het bestand wint, de rij wordt alleen verwijderd
    _legacy_row(worker_ctx, hub, status="revoked")
    assert ensure_link_file(worker_ctx.store, worker_ctx.sessions, worker_ctx.secrets) == "oude rij verwijderd"
    assert worker_ctx.store.read().state == "gekoppeld"


@pytest.mark.parametrize(
    ("changes", "state", "code"),
    [
        ({"policy_public_jwk": None}, "ontkoppeld", "overzetten_onvolledig"),
        ({"jwks": {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": "kapot"}]}}, "ontkoppeld", "overzetten_onvolledig"),
        ({"status": "revoked"}, "ingetrokken", "ingetrokken_door_hub"),
        ({"status": "failed", "linked_at": None}, "nooit_gekoppeld", None),
    ],
)
def test_legacy_link_edge_cases(
    worker_ctx: AppContext, hub: FakeHub, changes: dict[str, Any], state: str, code: str | None
) -> None:
    _fresh_store(worker_ctx)
    _legacy_row(worker_ctx, hub, **changes)
    ensure_link_file(worker_ctx.store, worker_ctx.sessions, worker_ctx.secrets)
    st = worker_ctx.store.read()
    assert st.state == state and not st.linked
    assert (st.last_error.code if st.last_error else None) == code
    assert (st.first_linked_at is None) is (state == "nooit_gekoppeld")


def test_transferred_link_is_confirmed_or_revoked(worker_ctx: AppContext, hub: FakeHub) -> None:
    _fresh_store(worker_ctx)
    _legacy_row(worker_ctx, hub)
    ensure_link_file(worker_ctx.store, worker_ctx.sessions, worker_ctx.secrets)
    run_once(worker_ctx)  # de Hub kent dit token niet (meer): services/self 401 → ingetrokken
    assert worker_ctx.store.read().state == "ingetrokken"
