"""B08-002 + APP-01…APP-20 via de echte API (PostgreSQL) en de Hub-simulator (Sim-getest, geen echte NAS of Hub).

Focus van de review: de import is idempotent, raakt niets aan (geen enkele schrijvende aanroep naar de Hub of de NAS)
en verzint geen versies of eigenaren; onbekende apps blijven 'onbekend'.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from appstore.api.common import AppContext
from appstore.catalog import hubinventory
from appstore.catalog.adoption import IMPORT_LOCK
from appstore.models import AuditEvent, EvidenceRecord, ExistingApp, OutboxEvent
from appstore.worker import periodic_import, run_once, startup
from tests.fakehub import FakeHub, hub_app
from tests.integration.conftest import idem, pair

pytestmark = pytest.mark.integration
IMPORT = "/catalog/v1/existing-apps/import"
DIGEST = "sha256:" + "cd" * 32


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hubinventory, "BACKOFF_BASE", 0.0)


@pytest.fixture
def linked(client: TestClient, hub: FakeHub, worker_ctx: AppContext) -> TestClient:
    pair(client, hub)
    startup(worker_ctx)
    run_once(worker_ctx)  # ingebouwde catalogus (o.a. voorbeeld-web = nginx) + bevestigen
    return client


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def seed(hub: FakeHub) -> None:
    """Kunstmatige testinventaris (geen gegevens van een echte NAS)."""
    seen = _now()
    hub.catalog_apps = [
        hub_app(
            "plex",
            app_id="plex",
            installatiewijze="catalog_app",
            repository="plexinc/pms-docker",
            tag="testtag-1",
            digest=DIGEST,
            catalogus={"app": "plex", "trein": "stable", "versie": "9.9.9", "app_versie": "test-1"},
            poorten=[{"container_poort": 32400, "host_poort": 32400, "host_ip": "0.0.0.0", "protocol": "tcp"}],
            opslag=[
                {"bron": "/mnt/p1/media", "doel": "/data", "type": "bind", "dataset": "p1/media", "soort": "hostpad"}
            ],
            laatst_gezien=seen,
        ),
        hub_app(
            "jellyfin",
            app_id="jellyfin",
            repository="ghcr.io/jellyfin/jellyfin",
            tag="testtag-2",
            readback={"status": "ok", "reden": None, "digests": [DIGEST]},
            opslag=[
                {
                    "bron": "/mnt/p1/media/films",
                    "doel": "/media",
                    "type": "bind",
                    "dataset": "p1/media",
                    "soort": "hostpad",
                }
            ],
            laatst_gezien=seen,
        ),
        hub_app(
            "mediaguard",
            app_id="mediaguard",
            repository="ghcr.io/lvos04/mediaguard-core",
            tag="t-aio",
            laatst_gezien=seen,
        ),
        # onbekend: eigen tool, naam die op een bekende app lijkt, Snapotter uit de catalogus, dubbelzinnig
        hub_app("mijn-tool", repository="ghcr.io/iemand/mijn-tool", tag="1.0", laatst_gezien=seen),
        hub_app("plex-oud", app_id="plex", repository="ghcr.io/iemand/eigen-plexbuild", tag="2", laatst_gezien=seen),
        hub_app(
            "snapotter",
            app_id=None,
            installatiewijze="catalog_app",
            repository="ghcr.io/snapotter-hq/snapotter",
            tag="t",
            catalogus={"app": "snapotter", "trein": "community", "versie": "1.0.0", "app_versie": "x"},
            laatst_gezien=seen,
        ),
        hub_app(
            "web-en-plex",
            repository="docker.io/library/nginx",
            tag="1.27.3",
            extra_images=[{"repository": "plexinc/pms-docker", "tag": "x", "digest": None}],
            laatst_gezien=seen,
        ),
        hub_app(
            "yaml-app",
            installatiewijze="custom_compose",
            repository="emby/embyserver",
            tag="t",
            catalogus={"app": None, "trein": None, "versie": None, "app_versie": None},
            laatst_gezien=seen,
        ),
    ]


def run_import(client: TestClient, hub: FakeHub, **auth: Any) -> dict[str, Any]:
    resp = client.post(IMPORT, headers={**hub.auth(roles=["operator"], **auth), **idem()})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def rows(ctx: AppContext) -> dict[str, ExistingApp]:
    with ctx.sessions() as db:
        return {r.runtime_name: r for r in db.scalars(select(ExistingApp))}


def by_name(client: TestClient, hub: FakeHub, *, present_only: bool = True) -> dict[str, dict[str, Any]]:
    resp = client.get(
        f"/catalog/v1/existing-apps?limit=200&alleen_aanwezig={'true' if present_only else 'false'}",
        headers=hub.auth(roles=["viewer"]),
    )
    assert resp.status_code == 200, resp.text
    return {i["runtime_name"]: i for i in resp.json()["items"]}


# ------------------------------------------------------------------------------------------------ import
def test_import_registers_never_guesses_and_is_idempotent(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext
) -> None:
    seed(hub)
    first = run_import(linked, hub)
    assert first["status"] == "ok" and first["executed"] is False and first["nas_gewijzigd"] is False
    assert first["counts"]["nieuw"] == 8 and first["gewijzigd"] is True
    apps = by_name(linked, hub)
    assert apps["plex"]["status"] == "kandidaat" and apps["plex"]["match"]["basis"] == "catalogusapp"
    assert apps["jellyfin"]["status"] == "kandidaat" and apps["jellyfin"]["match"]["basis"] == "image"
    assert apps["mediaguard"]["known_app_id"] == "mediaguard" and apps["mediaguard"]["excel_id"] == "APP-18"
    # nooit gegokt: naam of Hub-herkenning telt niet, Snapotter-catalogusapp niet, dubbelzinnig niet
    for name in ("mijn-tool", "plex-oud", "snapotter"):
        assert apps[name]["status"] == "onbekend" and apps[name]["known_app_id"] is None, name
    assert apps["plex-oud"]["match"]["hub_herkenning"] == "plex"  # alleen ter informatie
    assert apps["web-en-plex"]["status"] == "onbekend"
    assert sorted(apps["web-en-plex"]["match"]["kandidaten"]) == ["plex", "voorbeeld-web"]
    # gemeten velden komen 1-op-1 uit de Hub (geen verzonnen versies)
    assert apps["plex"]["catalog"] == {"app": "plex", "train": "stable", "version": "9.9.9", "app_version": "test-1"}
    assert apps["plex"]["image"]["digest"] == DIGEST and apps["plex"]["image"]["digest_bron"] == "tagveld"
    # zonder digest in het tagveld: de effectieve digest uit de readback van de Hub (app.image.query), gemeten
    jf_img = apps["jellyfin"]["image"]
    assert (jf_img["digest"], jf_img["digest_tagveld"], jf_img["digest_bron"]) == (DIGEST, None, "app.image.query")
    assert jf_img["readback"]["digests"] == [DIGEST]
    assert apps["mediaguard"]["image"]["digest"] is None  # niet gemeten → leeg, nooit verzonnen
    assert apps["plex"]["datasets"] == ["p1/media"] and apps["plex"]["ports"][0]["host_port"] == 32400
    assert apps["plex"]["owner"] is None  # eigenaar pas na bevestiging
    assert apps["plex"]["last_used"]["status"] == "unknown"
    assert all(a["planned_actions"] == [] and a["data_policy"] == "untouched" for a in apps.values())
    before = {n: r.row_version for n, r in rows(worker_ctx).items()}
    second = run_import(linked, hub)
    assert second["counts"] == {**second["counts"], "nieuw": 0, "bijgewerkt": 0, "ongewijzigd": 8, "verdwenen": 0}
    assert second["gewijzigd"] is False
    assert {n: r.row_version for n, r in rows(worker_ctx).items()} == before
    # alleen GET's op de appcatalogus, met het service-token (dienst → Hub, §4.4); nooit iets schrijvends
    app_calls = [(m, p) for m, p in hub.requests if p.startswith("/api/v1/apps")]
    assert app_calls and all(m == "GET" for m, _p in app_calls)
    assert set(hub.apps_auth) == {f"Bearer {hub.last_service_token}"}
    allowed = {"/api/v1/services/enroll", "/api/v1/services/self", "/api/v1/events/inbox", "/api/v1/auth/key-rotations"}
    assert {p for m, p in hub.requests if m != "GET"} <= allowed


def test_import_records_evidence_audit_and_event(linked: TestClient, hub: FakeHub, worker_ctx: AppContext) -> None:
    seed(hub)
    body = run_import(linked, hub)
    with worker_ctx.sessions() as db:
        ev = db.get(EvidenceRecord, __import__("uuid").UUID(body["evidence_id"]))
        assert ev is not None and ev.kind == "hub_import" and ev.summary["status"] == "ok"
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "catalog.import.run"))
        types = list(db.scalars(select(OutboxEvent.event_type)))
    assert "catalog.import.completed" in types
    listed = linked.get("/catalog/v1/existing-apps", headers=hub.auth(roles=["viewer"])).json()
    assert listed["laatste_import"]["status"] == "ok" and listed["laatste_import"]["evidence_id"] == body["evidence_id"]
    # connector: items van het nieuwe type (Hub kan meelezen)
    items = linked.get("/connector/v1/items?type=existing_app&limit=200", headers=hub.service_auth()).json()
    assert {i["runtime_name"] for i in items["items"]} >= {"plex", "mijn-tool"}


@pytest.mark.parametrize(
    ("mode", "status", "reason"),
    [
        ("alleen_gebruikers", "capability_unavailable", "hub_inventaris_niet_voor_diensten"),
        ("ontbreekt", "capability_unavailable", "hub_kent_appcatalogus_niet"),
    ],
)
def test_hub_without_service_access_is_capability_unavailable(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext, mode: str, status: str, reason: str
) -> None:
    seed(hub)
    run_import(linked, hub)
    before = {n: r.row_version for n, r in rows(worker_ctx).items()}
    hub.apps_mode = mode
    body = run_import(linked, hub)
    assert body["status"] == status and body["reason_code"] == reason and body["counts"] is None
    assert body["gewijzigd"] is False and body["melding_nl"]
    assert {n: r.row_version for n, r in rows(worker_ctx).items()} == before  # niets gewijzigd, niets 'verdwenen'
    with worker_ctx.sessions() as db:
        assert "catalog.import.unavailable" in list(db.scalars(select(OutboxEvent.event_type)))


def test_not_linked_is_capability_unavailable(client: TestClient, hub: FakeHub) -> None:
    resp = client.post(IMPORT, headers={**hub.auth(roles=["operator"]), **idem()})
    # niet gekoppeld: er zijn geen gepinde sleutels, dus elke Hub-JWT wordt al geweigerd (fail-closed)
    assert resp.status_code == 401 and resp.json()["details"]["reason"] == "not_linked"


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"status": "unknown", "reason_code": "stale", "verouderd": True}, "inventaris_verouderd"),
        ({"observed_at": "2026-01-01T00:00:00Z"}, "inventaris_verouderd"),
        ({"observed_at": "2099-01-01T00:00:00Z"}, "inventaris_in_toekomst"),
        ({"status": "unknown", "reason_code": "not_configured", "observed_at": None}, "inventaris_niet_gemeten"),
    ],
)
def test_stale_or_unmeasured_inventory_changes_nothing(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext, state: dict[str, Any], reason: str
) -> None:
    seed(hub)
    run_import(linked, hub)
    before = {n: (r.row_version, r.present) for n, r in rows(worker_ctx).items()}
    hub.catalog_apps = []  # een verouderde lege lijst mag nooit tot 'verdwenen' leiden
    hub.inventory_state = state
    body = run_import(linked, hub)
    assert body["status"] == "unknown" and body["reason_code"] == reason
    assert {n: (r.row_version, r.present) for n, r in rows(worker_ctx).items()} == before


def test_outage_retries_then_unknown_and_redirect_is_not_followed(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext
) -> None:
    seed(hub)
    hub.apps_failures = 2  # twee keer 503, derde poging lukt (max. 3 pogingen, §5)
    assert run_import(linked, hub)["status"] == "ok"
    hub.apps_failures = 0
    hub.apps_status = 500
    body = run_import(linked, hub)
    assert (body["status"], body["reason_code"], body["hub_calls"]) == ("unknown", "hub_onbereikbaar", 3)
    hub.apps_status = 302
    body = run_import(linked, hub)
    assert (body["status"], body["reason_code"]) == ("unknown", "hub_redirect")
    assert not any("elders.test" in u for u in hub.all_urls)
    hub.apps_status = None
    hub.down = True
    assert run_import(linked, hub)["reason_code"] == "hub_onbereikbaar"


def test_invalid_or_inconsistent_hub_answer_is_never_used(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext
) -> None:
    seed(hub)
    hub.apps_body_patch = lambda b: {k: v for k, v in b.items() if k != "inventaris"}
    body = run_import(linked, hub)
    assert (body["status"], body["reason_code"]) == ("unknown", "hub_antwoord_ongeldig")
    hub.apps_body_patch = None
    hub.detail_status = {"jellyfin": 404}  # verdween tussen lijst en detail
    body = run_import(linked, hub)
    assert (body["status"], body["reason_code"]) == ("unknown", "inventaris_gewijzigd_tijdens_lezen")
    assert rows(worker_ctx) == {}


def test_401_checks_self_before_revocation(linked: TestClient, hub: FakeHub, worker_ctx: AppContext) -> None:
    seed(hub)
    hub.apps_status = 401  # alleen de appcatalogus weigert; services/self is in orde → geen intrekking
    body = run_import(linked, hub)
    assert (body["status"], body["reason_code"]) == ("unknown", "hub_weigert_token")
    assert worker_ctx.store.read().state == "gekoppeld"
    hub.apps_status = None
    hub.revoke_all()  # nu weigert ook services/self → ingetrokken (contract 1.2 §3.8)
    resp = linked.post(IMPORT, headers={**hub.service_auth(), **idem()})
    # de dienst-JWT wordt nog geaccepteerd tot de intrekking is vastgesteld; daarna fail-closed
    assert resp.status_code == 200 and resp.json()["reason_code"] == "niet_gekoppeld"
    assert worker_ctx.store.read().state == "ingetrokken"


def test_pagination_is_followed_and_capped(linked: TestClient, hub: FakeHub, worker_ctx: AppContext) -> None:
    seed(hub)
    hub.apps_page_size = 3
    body = run_import(linked, hub)
    assert body["status"] == "ok" and body["counts"]["nieuw"] == 8
    hub.apps_page_size = 1
    monkey = hubinventory.MAX_PAGES
    hubinventory.MAX_PAGES = 4
    try:
        body = run_import(linked, hub)
    finally:
        hubinventory.MAX_PAGES = monkey
    assert (body["status"], body["reason_code"]) == ("unknown", "inventaris_te_groot")


def test_gone_apps_are_never_deleted_and_image_updates_are_tracked(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext
) -> None:
    seed(hub)
    run_import(linked, hub)
    plex = by_name(linked, hub)["plex"]
    adopted = linked.post(
        f"/catalog/v1/existing-apps/{plex['id']}/adopt",
        headers={**hub.auth(roles=["admin"]), **idem(), "If-Match": plex["etag"]},
        json={"app_id": "plex", "eigenaar_bevestigd": True},
    )
    assert adopted.status_code == 200, adopted.text
    # nieuwe tag + digest → bijgewerkt, historie, adoptie blijft
    new_digest = "sha256:" + "ef" * 32
    for a in hub.catalog_apps:
        if a["runtime_id"] == "plex":
            a["images"][0].update({"tag": "testtag-9", "digest": new_digest})
            a["image"].update({"tag": "testtag-9", "digest": new_digest})  # zoals de Hub: lijst én detail
    body = run_import(linked, hub)
    assert body["counts"]["bijgewerkt"] == 1
    detail = linked.get(f"/catalog/v1/existing-apps/{plex['id']}", headers=hub.auth(roles=["viewer"])).json()
    assert detail["status"] == "adopted" and detail["image"]["tag"] == "testtag-9"
    assert [h["tag"] for h in detail["image_history"]] == ["testtag-1", "testtag-9"]
    # verdwenen uit de Hub-lijst → present=false, nooit verwijderd; adoptie en eigenaar blijven
    hub.catalog_apps = [a for a in hub.catalog_apps if a["runtime_id"] not in ("plex", "mijn-tool")]
    body = run_import(linked, hub)
    assert body["counts"]["verdwenen"] == 2
    assert "plex" not in by_name(linked, hub)
    gone = by_name(linked, hub, present_only=False)
    assert gone["plex"]["present"] is False and gone["plex"]["gone_since"] and gone["plex"]["status"] == "adopted"
    assert gone["plex"]["owner"]["bevestigd_door"].startswith("user:")
    # de Hub meldt een verdwenen app zelf (aanwezig=false) → zelfde uitkomst
    for a in hub.catalog_apps:
        if a["runtime_id"] == "jellyfin":
            a["aanwezig"] = False
    run_import(linked, hub)
    assert by_name(linked, hub, present_only=False)["jellyfin"]["present"] is False
    assert len(rows(worker_ctx)) == 8


def test_concurrent_import_is_refused_not_merged(linked: TestClient, hub: FakeHub, database_url: str) -> None:
    seed(hub)
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn, conn.begin():
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": IMPORT_LOCK})
            resp = linked.post(IMPORT, headers={**hub.auth(roles=["operator"]), **idem()})
        assert resp.status_code == 409 and resp.json()["code"] == "import_bezig"
    finally:
        engine.dispose()


def test_import_permissions(linked: TestClient, hub: FakeHub) -> None:
    seed(hub)
    assert linked.post(IMPORT, headers=idem()).status_code == 401
    assert linked.post(IMPORT, headers={**hub.auth(roles=["viewer"]), **idem()}).status_code == 403
    assert linked.post(IMPORT, headers={**hub.auth(roles=["kind"]), **idem()}).status_code == 403
    assert linked.post(IMPORT, headers=hub.auth(roles=["operator"])).json()["code"] == "idempotency_key_required"
    # de Hub zelf (dienst-JWT) mag een import aanvragen (alleen lezen)
    resp = linked.post(IMPORT, headers={**hub.service_auth(), **idem()})
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
    # zelfde Idempotency-Key → zelfde antwoord, geen tweede ronde naar de Hub
    key = idem()
    first = linked.post(IMPORT, headers={**hub.auth(roles=["operator"], sub="op-1"), **key})
    calls = len(hub.apps_auth)
    again = linked.post(IMPORT, headers={**hub.auth(roles=["operator"], sub="op-1"), **key})
    assert again.headers.get("Idempotent-Replayed") == "true" and again.json() == first.json()
    assert len(hub.apps_auth) == calls


def test_worker_periodic_import_only_when_linked_and_confirmed(
    client: TestClient, hub: FakeHub, worker_ctx: AppContext
) -> None:
    seed(hub)
    assert periodic_import(worker_ctx) is None  # niet gekoppeld: niets
    pair(client, hub)
    startup(worker_ctx)
    run_once(worker_ctx)
    result = periodic_import(worker_ctx)
    assert result is not None and result["status"] == "ok" and result["counts"]["nieuw"] == 8
    with worker_ctx.sessions() as db:
        ev = db.scalar(select(EvidenceRecord).where(EvidenceRecord.kind == "hub_import"))
        assert ev is not None and ev.actor == "appstore-en-fork"  # systeem, geen gebruiker


# ------------------------------------------------------------------------------------------------ adopteren
@pytest.fixture
def imported(linked: TestClient, hub: FakeHub) -> Iterator[dict[str, dict[str, Any]]]:
    seed(hub)
    run_import(linked, hub)
    yield by_name(linked, hub)


def adopt_req(
    client: TestClient, hub: FakeHub, app: dict[str, Any], body: dict[str, Any], roles: list[str] | None = None
) -> Any:
    return client.post(
        f"/catalog/v1/existing-apps/{app['id']}/adopt",
        headers={**hub.auth(roles=roles or ["admin"]), **idem(), "If-Match": app["etag"]},
        json=body,
    )


def test_adopt_kandidaat_with_owner_from_register(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext, imported: dict[str, dict[str, Any]]
) -> None:
    mg = imported["mediaguard"]
    resp = adopt_req(linked, hub, mg, {"app_id": "mediaguard", "eigenaar_bevestigd": True, "toelichting_nl": "ok"})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["status"] == "adopted" and out["status_nl"] == "Geïmporteerd"
    assert out["owner"]["soort"] == "bestaand" and out["owner"]["repo"] == "lvos04/media-guard-ultimate"
    assert out["owner"]["bron"].startswith("APP_REGISTER.md") and out["owner"]["bevestigd_door"].startswith("user:")
    assert resp.headers["ETag"] == out["etag"] != mg["etag"]
    again = adopt_req(linked, hub, out, {"app_id": "mediaguard", "eigenaar_bevestigd": True})
    assert again.status_code == 409 and again.json()["code"] == "al_adopted"
    with worker_ctx.sessions() as db:
        assert "catalog.app.adopted" in list(db.scalars(select(OutboxEvent.event_type)))
    # een import daarna laat de adoptie staan (idempotent)
    run_import(linked, hub)
    assert by_name(linked, hub)["mediaguard"]["status"] == "adopted"


def test_adopt_rules_and_permissions(linked: TestClient, hub: FakeHub, imported: dict[str, dict[str, Any]]) -> None:
    plex = imported["plex"]
    body = {"app_id": "plex", "eigenaar_bevestigd": True}
    for roles in (["viewer"], ["operator"], ["kind"]):
        assert adopt_req(linked, hub, plex, body, roles).status_code == 403
    svc = linked.post(
        f"/catalog/v1/existing-apps/{plex['id']}/adopt",
        headers={**hub.service_auth(), **idem(), "If-Match": plex["etag"]},
        json=body,
    )
    assert svc.status_code == 403  # de Hub/AI kan nooit zelf een eigenaar bevestigen
    no_match = linked.post(
        f"/catalog/v1/existing-apps/{plex['id']}/adopt", headers={**hub.auth(roles=["admin"]), **idem()}, json=body
    )
    assert no_match.status_code == 428
    stale = adopt_req(linked, hub, {**plex, "etag": 'W/"999"'}, body)
    assert stale.status_code == 412
    assert adopt_req(linked, hub, plex, {"app_id": "plex", "eigenaar_bevestigd": False}).status_code == 422
    assert adopt_req(linked, hub, plex, {"app_id": "plex"}).status_code == 422
    # kandidaat voor plex, maar als emby adopteren vraagt een expliciete handmatige koppeling
    wrong = adopt_req(linked, hub, plex, {"app_id": "emby", "eigenaar_bevestigd": True})
    assert wrong.status_code == 409 and wrong.json()["code"] == "handmatige_koppeling_niet_bevestigd"
    unknown = adopt_req(linked, hub, imported["mijn-tool"], {"app_id": "geen-app", "eigenaar_bevestigd": True})
    assert unknown.status_code == 422 and unknown.json()["code"] == "onbekende_app_id"
    manual = adopt_req(
        linked, hub, imported["mijn-tool"], {"app_id": "uren", "eigenaar_bevestigd": True, "handmatig": True}
    )
    assert manual.status_code == 200 and manual.json()["match"]["basis"] == "gebruiker"
    assert manual.json()["owner"]["soort"] == "eigen_los"


def test_release_returns_to_recognition(linked: TestClient, hub: FakeHub, imported: dict[str, dict[str, Any]]) -> None:
    plex = adopt_req(linked, hub, imported["plex"], {"app_id": "plex", "eigenaar_bevestigd": True}).json()
    resp = linked.post(
        f"/catalog/v1/existing-apps/{plex['id']}/release",
        headers={**hub.auth(roles=["admin"]), **idem(), "If-Match": plex["etag"]},
        json={"reden_nl": "Toch niet zeker van de eigenaar"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["status"] == "kandidaat" and out["owner"] is None
    detail = linked.get(f"/catalog/v1/existing-apps/{plex['id']}", headers=hub.auth(roles=["viewer"])).json()
    assert [h["actie"] for h in detail["history"]] == ["adopted", "losgelaten"]


def test_adoption_manifest_is_measured_and_refused_for_installation(
    linked: TestClient, hub: FakeHub, imported: dict[str, dict[str, Any]]
) -> None:
    viewer = hub.auth(roles=["viewer"])
    assert linked.get(f"/catalog/v1/existing-apps/{imported['plex']['id']}/manifest", headers=viewer).status_code == 409
    for name, app_id in (("plex", "plex"), ("jellyfin", "jellyfin")):
        assert adopt_req(linked, hub, imported[name], {"app_id": app_id, "eigenaar_bevestigd": True}).status_code == 200
    plex = linked.get(
        f"/catalog/v1/existing-apps/{imported['plex']['id']}/manifest", headers=hub.auth(roles=["viewer"])
    )
    assert plex.status_code == 200
    doc = plex.json()["manifest"]
    assert doc["install"]["method"] == "catalog_app" and doc["catalog"]["version"] == "9.9.9"
    assert doc["adoption"]["excel_id"] == "APP-01" and doc["installable"] is False
    jf = linked.get(
        f"/catalog/v1/existing-apps/{imported['jellyfin']['id']}/manifest", headers=hub.auth(roles=["viewer"])
    )
    jf_doc = jf.json()["manifest"]
    assert jf_doc["install"]["method"] == "custom_app" and jf_doc["image"]["digest"] == DIGEST  # uit de readback
    assert jf_doc["adoption"]["digest_readback"]["status"] == "ok"
    mg = adopt_req(linked, hub, imported["mediaguard"], {"app_id": "mediaguard", "eigenaar_bevestigd": True})
    assert mg.status_code == 200
    mg_doc = linked.get(
        f"/catalog/v1/existing-apps/{imported['mediaguard']['id']}/manifest", headers=hub.auth(roles=["viewer"])
    )
    assert mg_doc.json()["manifest"]["image"]["digest"] is None  # niet gemeten → leeg, nooit verzonnen
    # de installatieroute weigert een adoptiemanifest vóór uitvoering
    validated = linked.post(
        "/catalog/v1/validate", headers=hub.auth(roles=["viewer"]), json={"manifest_yaml": jf.json()["manifest_yaml"]}
    ).json()
    assert validated["valid"] is False and validated["findings"][0]["code"] == "adoption_manifest_not_installable"
    preview = linked.post(
        "/catalog/v1/preview",
        headers=hub.auth(roles=["operator"]),
        json={"manifest_yaml": plex.json()["manifest_yaml"]},
    ).json()
    assert preview["ready_for_install"] is False and preview["translation"] is None and preview["executed"] is False
    # Install via YAML (compose): wel adopteren, nooit een manifest
    yaml_app = imported["yaml-app"]
    assert yaml_app["status"] == "kandidaat" and yaml_app["install_method"] == "custom_compose"
    assert adopt_req(linked, hub, yaml_app, {"app_id": "emby", "eigenaar_bevestigd": True}).status_code == 200
    resp = linked.get(f"/catalog/v1/existing-apps/{yaml_app['id']}/manifest", headers=hub.auth(roles=["viewer"]))
    assert resp.status_code == 409 and resp.json()["code"] == "installatiewijze_niet_ondersteund"


# ------------------------------------------------------------------------------------------------ beslisregister
def _decision(client: TestClient, hub: FakeHub, app_id: str) -> dict[str, Any]:
    resp = client.get(f"/catalog/v1/decisions/{app_id}", headers=hub.auth(roles=["viewer"]))
    assert resp.status_code == 200, resp.text
    out: dict[str, Any] = resp.json()
    return out


def _check(
    client: TestClient, hub: FakeHub, app_id: str, check_id: str, body: dict[str, Any], etag: str, roles: list[str]
) -> Any:
    return client.post(
        f"/catalog/v1/decisions/{app_id}/checks/{check_id}",
        headers={**hub.auth(roles=roles), **idem(), "If-Match": etag},
        json=body,
    )


def test_decision_register_lists_app_01_to_20(linked: TestClient, hub: FakeHub, imported: dict[str, Any]) -> None:
    resp = linked.get("/catalog/v1/decisions", headers=hub.auth(roles=["viewer"])).json()
    assert [i["excel_id"] for i in resp["items"]] == [f"APP-{i:02d}" for i in range(1, 21)]
    plex = next(i for i in resp["items"] if i["app_id"] == "plex")
    assert plex["voorgenomen_besluit"]["code"] == "kandidaat_uitschakelen" and plex["besluit"] is None
    assert plex["checks"] == {"open": 13, "afgevinkt": 0, "nvt": 0}
    assert [x["runtime_name"] for x in plex["instanties"]] == ["plex"]
    immich = next(i for i in resp["items"] if i["app_id"] == "immich")
    assert immich["checks"]["nvt"] == 1 and immich["instanties"] == []  # a3 n.v.t.; niet op de (test)NAS
    detail = _decision(linked, hub, "plex")
    assert len(detail["controlelijst"]) == 13
    hints = detail["gemeten_hints"]
    assert hints["inventaris-1"][0]["image"]["digest"] == DIGEST and hints["inventaris-1"][0]["container_id"] is None
    assert hints["inventaris-4"]["gedeelde_opslag_gemeten"][0]["gedeeld_met"][0]["runtime_name"] == "jellyfin"
    assert hints["inventaris-5"]["status"] == "unknown"
    assert {d["app_id"] for d in detail["afhankelijk_van_deze_app"]} == {"jellyplex-watched"}
    assert linked.get("/catalog/v1/decisions/bestaat-niet", headers=hub.auth(roles=["viewer"])).status_code == 404


def test_checks_need_evidence_and_a_user(linked: TestClient, hub: FakeHub, imported: dict[str, Any]) -> None:
    etag = _decision(linked, hub, "plex")["etag"]
    ok_body = {"status": "afgevinkt", "bewijs": {"omschrijving_nl": "Tag en digest gecontroleerd in de Hub-UI."}}
    assert _check(linked, hub, "plex", "inventaris-1", ok_body, etag, ["viewer"]).status_code == 403
    svc = linked.post(
        "/catalog/v1/decisions/plex/checks/inventaris-1",
        headers={**hub.service_auth(), **idem(), "If-Match": etag},
        json=ok_body,
    )
    assert svc.status_code == 403  # alleen een mens vinkt af
    no_ev = _check(linked, hub, "plex", "inventaris-1", {"status": "afgevinkt"}, etag, ["operator"])
    assert no_ev.status_code == 422 and no_ev.json()["code"] == "bewijs_vereist"
    short = _check(
        linked,
        hub,
        "plex",
        "inventaris-1",
        {"status": "afgevinkt", "bewijs": {"omschrijving_nl": "ok"}},
        etag,
        ["operator"],
    )
    assert short.status_code == 422
    resp = _check(linked, hub, "plex", "inventaris-1", ok_body, etag, ["operator"])
    assert resp.status_code == 200 and resp.json()["check"]["bewijs"]["omschrijving_nl"].startswith("Tag")
    assert _check(linked, hub, "plex", "inventaris-2", ok_body, etag, ["operator"]).status_code == 412  # oude ETag
    assert _check(linked, hub, "plex", "onbekend", ok_body, resp.json()["etag"], ["operator"]).status_code == 404


def test_decision_flow_with_plan_never_executes(
    linked: TestClient, hub: FakeHub, worker_ctx: AppContext, imported: dict[str, Any]
) -> None:
    plex = adopt_req(linked, hub, imported["plex"], {"app_id": "plex", "eigenaar_bevestigd": True}).json()
    calls_before = list(hub.requests)
    detail = _decision(linked, hub, "plex")
    early = linked.post(
        "/catalog/v1/decisions/plex/decision",
        headers={**hub.auth(roles=["admin"]), **idem(), "If-Match": detail["etag"]},
        json={"code": "behouden", "motivatie_nl": "Plex blijft voorlopig draaien."},
    )
    assert early.status_code == 409 and early.json()["code"] == "controlelijst_onvolledig"
    plan = linked.post(
        "/catalog/v1/decisions/plex/decommission-plan", headers={**hub.auth(roles=["operator"]), **idem()}
    )
    assert plan.status_code == 201, plan.text
    p = plan.json()
    assert p["uitgevoerd"] is False and p["data_verwijderen"]["aangeboden"] is False
    assert [i["runtime_name"] for i in p["instanties"]] == [plex["runtime_name"]]
    assert {g["id"]: g["status"] for g in p["gates"]}["backup_gate"] == "unknown"
    assert p["stappen"][2]["parameters"]["datasets"] == ["p1/media"]
    ev = linked.get(f"/connector/v1/evidence/{p['plan_id']}", headers=hub.service_auth())
    assert ev.status_code == 200
    etag = detail["etag"]
    for c in detail["controlelijst"]:
        if c["status"] == "open" and c["id"] != "gereed":
            r = _check(
                linked,
                hub,
                "plex",
                c["id"],
                {
                    "status": "afgevinkt",
                    "bewijs": {"omschrijving_nl": f"Bewijs voor {c['id']} (test).", "evidence_ids": [p["plan_id"]]},
                },
                etag,
                ["operator"],
            )
            assert r.status_code == 200, r.text
            etag = r.json()["etag"]
    no_plan = linked.post(
        "/catalog/v1/decisions/plex/decision",
        headers={**hub.auth(roles=["admin"]), **idem(), "If-Match": etag},
        json={"code": "uitschakelen", "motivatie_nl": "Parity bewezen; proefstop geslaagd."},
    )
    assert no_plan.status_code == 409 and no_plan.json()["code"] == "plan_vereist"
    as_operator = linked.post(
        "/catalog/v1/decisions/plex/decision",
        headers={**hub.auth(roles=["operator"]), **idem(), "If-Match": etag},
        json={
            "code": "uitschakelen",
            "motivatie_nl": "Parity bewezen; proefstop geslaagd.",
            "decommission_plan_id": p["plan_id"],
        },
    )
    assert as_operator.status_code == 403
    done = linked.post(
        "/catalog/v1/decisions/plex/decision",
        headers={**hub.auth(roles=["admin"]), **idem(), "If-Match": etag},
        json={
            "code": "uitschakelen",
            "motivatie_nl": "Parity bewezen; proefstop geslaagd.",
            "decommission_plan_id": p["plan_id"],
        },
    )
    assert done.status_code == 200, done.text
    assert done.json()["besluit"]["data_verwijderen"] == "niet_aangeboden"
    # plan van een andere app wordt geweigerd
    emby_plan = linked.post(
        "/catalog/v1/decisions/emby/decommission-plan", headers={**hub.auth(roles=["operator"]), **idem()}
    )
    assert emby_plan.status_code == 201
    # niets naar de Hub of de NAS gestuurd door plan of besluit (alleen eventueel de outbox-push van de worker)
    assert [r for r in hub.requests if r not in calls_before and r[1] != "/api/v1/events/inbox"] == []
    run_once(worker_ctx)
    types = [e["event_type"] for e in hub.inbox]
    assert {"catalog.app.adopted", "catalog.decommission.planned", "catalog.decision.recorded"} <= set(types)
    assert all(len(t.split(".")) == 3 for t in types)
