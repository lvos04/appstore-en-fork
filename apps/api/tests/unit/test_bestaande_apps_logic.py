"""Herkenning (nooit gegokt), Hub-antwoordcontrole, adoptiemanifest (nooit installeerbaar) en het decommission-plan
(voert niets uit, nooit data verwijderen). Unit-niveau, zonder database of netwerk."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from appstore.bestaande_apps import load_register, match_app, normalize_repository, register_entries
from appstore.catalog import adoption_manifest
from appstore.catalog.adoption import adopt, measured_from, release, shared_storage
from appstore.catalog.decisions import (
    DecisionError,
    Evidence,
    _check,
    build_plan,
    open_checks,
    record_decision,
    update_check,
)
from appstore.catalog.hubinventory import HubAppDetail, HubInventoryClient, HubInventoryState, HubListPage, _StopError
from appstore.manifest.validator import validate_data
from appstore.models import DecisionRecord, ExistingApp
from tests.conftest import REPO_ROOT
from tests.fakehub import hub_app

LOADED = load_register()
REG = LOADED.register
ENTRIES = register_entries(REG)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32
EXECUTOR_ALLOWLIST = {  # contract 1.2 §8 (executor-allowlist v1)
    "app.restart",
    "app.stop",
    "app.start",
    "app.install",
    "app.update",
    "app.rollback",
    "app.uninstall",
    "zfs.snapshot.create",
    "zfs.snapshot.clone_to_sandbox",
    "notification.send",
}


# ------------------------------------------------------------------------------------------------ herkenning
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("docker.io/library/nginx:1.27@sha256:" + "0" * 64, "nginx"),
        ("index.docker.io/jellyfin/jellyfin", "jellyfin/jellyfin"),
        ("ghcr.io/linuxserver/plex:1.2.3", "lscr.io/linuxserver/plex"),
        ("linuxserver/emby", "lscr.io/linuxserver/emby"),
        ("LSCR.IO/LinuxServer/Bazarr", "lscr.io/linuxserver/bazarr"),
        ("registry:5000/team/app:1", "registry:5000/team/app"),
    ],
)
def test_normalize_repository(raw: str, expected: str) -> None:
    assert normalize_repository(raw) == expected


def test_catalog_app_and_image_are_the_only_bases() -> None:
    m = match_app(install_method="catalog_app", catalog_app="plex", repositories=[], entries=ENTRIES)
    assert (m.status, m.app_id, m.basis) == ("kandidaat", "plex", "catalogusapp")
    m = match_app(
        install_method="custom_app", catalog_app="ix-app", repositories=["ghcr.io/jellyfin/jellyfin"], entries=ENTRIES
    )
    assert (m.status, m.app_id, m.basis) == ("kandidaat", "jellyfin", "image")
    # LinuxServer-spiegel telt als dezelfde image
    m = match_app(install_method="custom_app", catalog_app=None, repositories=["linuxserver/plex"], entries=ENTRIES)
    assert m.app_id == "plex"
    # eigen platformdienst (contract §2)
    m = match_app(
        install_method="custom_app",
        catalog_app="ix-app",
        repositories=["ghcr.io/lvos04/ai-server-manager"],
        entries=ENTRIES,
    )
    assert m.app_id == "ai-server-manager"


def test_never_guessed_on_name_or_ix_app() -> None:
    # een Custom App met de naam 'plex' maar een onbekende image: onbekend (de naam telt nooit; match_app kent hem niet)
    m = match_app(
        install_method="custom_app", catalog_app="ix-app", repositories=["ghcr.io/iemand/plex-achtig"], entries=ENTRIES
    )
    assert (m.status, m.app_id, m.candidates) == ("onbekend", None, ())
    # 'ix-app' is de standaard Custom App, geen catalogusherkenning
    m = match_app(install_method="catalog_app", catalog_app="ix-app", repositories=[], entries=ENTRIES)
    assert m.status == "onbekend"
    # catalogusnaam telt alleen bij installatiewijze catalog_app
    m = match_app(install_method="custom_app", catalog_app="plex", repositories=[], entries=ENTRIES)
    assert m.status == "onbekend"
    # A06: de catalogusapp 'snapotter' uit de community-trein is niet per se Snapotter van de gebruiker
    m = match_app(
        install_method="catalog_app",
        catalog_app="snapotter",
        repositories=["ghcr.io/snapotter-hq/snapotter"],
        entries=ENTRIES,
    )
    assert m.status == "onbekend"


def test_ambiguous_stays_unknown_with_candidates() -> None:
    m = match_app(
        install_method="custom_app",
        catalog_app=None,
        repositories=["plexinc/pms-docker", "emby/embyserver"],
        entries=ENTRIES,
    )
    assert m.status == "onbekend" and m.app_id is None and m.candidates == ("emby", "plex")


# ------------------------------------------------------------------------------------------------ Hub-antwoord
def test_hub_detail_model_ignores_extra_and_rejects_bad_types() -> None:
    data = hub_app("plex", installatiewijze="catalog_app", repository="plexinc/pms-docker", tag="1.0", digest=DIGEST)
    detail = HubAppDetail.model_validate(data)
    assert detail.runtime_id == "plex" and detail.images[0].digest == DIGEST
    with pytest.raises(ValueError):
        HubAppDetail.model_validate({**data, "aanwezig": "ja"})
    with pytest.raises(ValueError):
        HubAppDetail.model_validate({**data, "id": "geen-uuid"})
    with pytest.raises(ValueError):
        HubListPage.model_validate({"items": [], "next_cursor": None})  # inventaris ontbreekt


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"status": "ok", "observed_at": None, "verouderd": False}, "inventaris_niet_gemeten"),
        ({"status": "unknown", "observed_at": NOW, "verouderd": True, "reason_code": "stale"}, "inventaris_verouderd"),
        ({"status": "ok", "observed_at": NOW - timedelta(minutes=16), "verouderd": False}, "inventaris_verouderd"),
        ({"status": "ok", "observed_at": NOW + timedelta(minutes=3), "verouderd": False}, "inventaris_in_toekomst"),
    ],
)
def test_inventory_freshness_is_fail_closed(state: dict[str, Any], reason: str) -> None:
    with pytest.raises(_StopError) as exc:
        HubInventoryClient._check_fresh(HubInventoryState.model_validate(state), NOW)
    assert exc.value.status == "unknown" and exc.value.reason == reason


def test_inventory_fresh_ok() -> None:
    state = HubInventoryState.model_validate({"status": "ok", "observed_at": NOW - timedelta(minutes=5)})
    assert HubInventoryClient._check_fresh(state, NOW) == NOW - timedelta(minutes=5)


# ------------------------------------------------------------------------------------------------ rijen (zonder DB)
def _row(runtime: str, data: dict[str, Any], *, status: str = "kandidaat", app_id: str | None = None) -> ExistingApp:
    d = HubAppDetail.model_validate(data)
    measured = measured_from(d)
    return ExistingApp(
        id=uuid.uuid4(),
        runtime_name=runtime,
        hub_app_uuid=d.id,
        status=status,
        known_app_id=app_id,
        match_basis="catalogusapp" if status != "onbekend" else None,
        candidates=[app_id] if app_id else [],
        match_reason_nl="test",
        hub_recognition=d.app_id,
        install_method=d.installatiewijze or "onbekend",
        measured=measured,
        measured_sha256="x",
        image_history=[],
        present=True,
        first_seen=NOW,
        last_seen=NOW,
        gone_since=None,
        owner=None,
        history=[],
        created_at=NOW,
        updated_at=NOW,
        row_version=1,
    )


PLEX = hub_app(
    "plex",
    installatiewijze="catalog_app",
    repository="plexinc/pms-docker",
    tag="testtag-1",
    digest=DIGEST,
    catalogus={"app": "plex", "trein": "stable", "versie": "9.9.9", "app_versie": "test-1"},
    poorten=[{"container_poort": 32400, "host_poort": 32400, "host_ip": "0.0.0.0", "protocol": "tcp"}],
    opslag=[
        {"bron": "/mnt/p1/media", "doel": "/data", "type": "bind", "dataset": "p1/media", "soort": "hostpad"},
        {"bron": "/mnt/.ix-apps/app_mounts/plex/config", "doel": "/config", "type": "bind", "soort": "ixvolume"},
    ],
)
JELLYFIN = hub_app(
    "jf",
    repository="ghcr.io/jellyfin/jellyfin",
    tag="testtag-2",
    digest=None,
    readback={"status": "ok", "reden": None, "digests": [DIGEST]},
    opslag=[
        {"bron": "/mnt/p1/media/films", "doel": "/media", "type": "bind", "dataset": "p1/media", "soort": "hostpad"}
    ],
)


def test_adopt_requires_confirmation_and_manual_flag() -> None:
    row = _row("plex", PLEX, app_id="plex")
    adopt(row, app_id="plex", manual=False, note_nl=None, entries=ENTRIES, actor="user:beheerder", now=NOW)
    assert row.status == "adopted" and row.owner is not None
    assert (
        row.owner["soort"] == "derde" and row.owner["repo"] is None and row.owner["bevestigd_door"] == "user:beheerder"
    )
    from appstore.catalog.adoption import AdoptionError

    with pytest.raises(AdoptionError) as exc:
        adopt(row, app_id="plex", manual=False, note_nl=None, entries=ENTRIES, actor="u", now=NOW)
    assert exc.value.code == "al_adopted"
    other = _row("iets", hub_app("iets", repository="ghcr.io/iemand/iets", tag="1"), status="onbekend")
    with pytest.raises(AdoptionError) as exc:
        adopt(other, app_id="plex", manual=False, note_nl=None, entries=ENTRIES, actor="u", now=NOW)
    assert exc.value.code == "handmatige_koppeling_niet_bevestigd"
    with pytest.raises(AdoptionError) as exc:
        adopt(other, app_id="bestaat-niet", manual=True, note_nl=None, entries=ENTRIES, actor="u", now=NOW)
    assert exc.value.code == "onbekende_app_id"
    adopt(other, app_id="teeltboek", manual=True, note_nl="zelf gecontroleerd", entries=ENTRIES, actor="u", now=NOW)
    assert other.match_basis == "gebruiker" and other.owner and other.owner["soort"] == "eigen_los"
    release(other, reason_nl="verkeerd gekoppeld", entries=ENTRIES, actor="u", now=NOW)
    assert other.status == "onbekend" and other.owner is None and other.history[-1]["actie"] == "losgelaten"


def test_shared_storage_is_measured_overlap() -> None:
    plex, jf = _row("plex", PLEX, app_id="plex"), _row("jf", JELLYFIN, app_id="jellyfin")
    assert shared_storage(plex, [plex, jf]) == [
        {"runtime_name": "jf", "app_id": "jellyfin", "paden": ["/mnt/p1/media"]}
    ]


# ------------------------------------------------------------------------------------------------ adoptiemanifest
def test_adoption_manifest_follows_current_install_method_and_is_never_installable() -> None:
    entry = next(e for e in ENTRIES if e.app_id == "plex")
    row = _row("plex", PLEX, app_id="plex")
    with pytest.raises(adoption_manifest.AdoptionManifestError):
        adoption_manifest.build(row, entry, REG.get("plex"))  # nog niet adopted
    adopt(row, app_id="plex", manual=False, note_nl=None, entries=ENTRIES, actor="user:a", now=NOW)
    doc = adoption_manifest.build(row, entry, REG.get("plex"))
    assert adoption_manifest.schema_errors(doc) == []
    assert doc["install"] == {"method": "catalog_app"} and "image" not in doc
    assert doc["catalog"] == {"train": "stable", "app": "plex", "version": "9.9.9"}  # gemeten, niet aangenomen
    assert doc["installable"] is False and doc["kind"] == "adoption"
    assert [s["source_kind"] for s in doc["storage"]] == ["ixvolume", "hostpad"]
    report, model = validate_data(doc)
    assert not report.valid and model is None and report.codes() == ["adoption_manifest_not_installable"]

    jf_entry = next(e for e in ENTRIES if e.app_id == "jellyfin")
    jf = _row("jf", JELLYFIN, app_id="jellyfin")
    adopt(jf, app_id="jellyfin", manual=False, note_nl=None, entries=ENTRIES, actor="user:a", now=NOW)
    doc = adoption_manifest.build(jf, jf_entry, REG.get("jellyfin"))
    assert doc["install"] == {"method": "custom_app"} and "catalog" not in doc
    # effectieve digest uit de readback van de Hub (geen digest in het tagveld)
    assert doc["image"] == {"repository": "ghcr.io/jellyfin/jellyfin", "tag": "testtag-2", "digest": DIGEST}
    assert doc["adoption"]["digest_readback"] == {"status": "ok", "reden": None, "digests": [DIGEST]}
    sha, text = adoption_manifest.render(doc)
    assert sha.startswith("sha256:") and "NIET installeerbaar" in text


@pytest.mark.parametrize("method", ["custom_compose", "onbekend"])
def test_no_manifest_for_compose_or_unknown_install(method: str) -> None:
    data = hub_app("x", installatiewijze=method, repository="plexinc/pms-docker", tag="1")
    row = _row("x", data, app_id="plex")
    row.install_method = method
    adopt(row, app_id="plex", manual=False, note_nl=None, entries=ENTRIES, actor="user:a", now=NOW)
    with pytest.raises(adoption_manifest.AdoptionManifestError) as exc:
        adoption_manifest.build(row, next(e for e in ENTRIES if e.app_id == "plex"), REG.get("plex"))
    assert exc.value.code in ("installatiewijze_niet_ondersteund", "installatiewijze_onbekend")


def test_adoption_schema_copy_is_identical() -> None:
    pkg = REPO_ROOT / "apps" / "api" / "src" / "appstore" / "manifest" / "adoption-manifest-v1.schema.json"
    assert pkg.read_bytes() == (REPO_ROOT / "schemas" / "adoption-manifest-v1.schema.json").read_bytes()


# ------------------------------------------------------------------------------------------------ beslisregister
def _record(app_id: str) -> DecisionRecord:
    from appstore.bestaande_apps import checks_for

    app = REG.get(app_id)
    assert app is not None
    return DecisionRecord(
        app_id=app_id,
        excel_id=app.excel_id,
        register_sha256=LOADED.sha256,
        intended={},
        checks=[_check(t) for t in checks_for(app)],
        decision=None,
        history=[],
        created_at=NOW,
        updated_at=NOW,
        row_version=1,
    )


def _tick_all(rec: DecisionRecord) -> None:
    for c in list(rec.checks):
        if c["status"] == "open" and c["id"] != "gereed":
            update_check(
                rec,
                c["id"],
                status="afgevinkt",
                evidence=Evidence("Bewijs vastgelegd in de test.", None, ()),
                reason_nl=None,
                actor="user:o",
                now=NOW,
            )


def test_check_rules() -> None:
    rec = _record("plex")
    with pytest.raises(DecisionError) as exc:
        update_check(rec, "inventaris-1", status="afgevinkt", evidence=None, reason_nl=None, actor="u", now=NOW)
    assert exc.value.code == "bewijs_vereist"
    with pytest.raises(DecisionError) as exc:
        update_check(rec, "inventaris-1", status="nvt", evidence=None, reason_nl=None, actor="u", now=NOW)
    assert exc.value.code == "reden_vereist"
    with pytest.raises(DecisionError) as exc:
        update_check(rec, "bestaat-niet", status="open", evidence=None, reason_nl=None, actor="u", now=NOW)
    assert exc.value.status == 404
    before = rec.row_version
    update_check(
        rec,
        "inventaris-1",
        status="afgevinkt",
        evidence=Evidence("x" * 12, "ref", ("e1",)),
        reason_nl=None,
        actor="u",
        now=NOW,
    )
    assert rec.row_version == before + 1 and rec.history[-1]["check_id"] == "inventaris-1"
    assert "inventaris-1" not in open_checks(rec)


def test_decision_needs_full_checklist_and_plan() -> None:
    rec = _record("plex")
    with pytest.raises(DecisionError) as exc:
        record_decision(rec, code="behouden", motivation_nl="m" * 12, plan=None, plan_id=None, actor="u", now=NOW)
    assert exc.value.code == "controlelijst_onvolledig" and "acceptatie" in exc.value.details["open_checks"]
    _tick_all(rec)
    with pytest.raises(DecisionError) as exc:
        record_decision(rec, code="uitschakelen", motivation_nl="m" * 12, plan=None, plan_id=None, actor="u", now=NOW)
    assert exc.value.code == "plan_vereist"
    with pytest.raises(DecisionError) as exc:
        record_decision(
            rec, code="uitschakelen", motivation_nl="m" * 12, plan={"app_id": "emby"}, plan_id="p", actor="u", now=NOW
        )
    assert exc.value.code == "plan_hoort_niet_bij_app"
    decision = record_decision(
        rec,
        code="uitschakelen",
        motivation_nl="m" * 12,
        plan={"app_id": "plex", "plan_hash": "h"},
        plan_id="p",
        actor="u",
        now=NOW,
    )
    assert decision["data_verwijderen"] == "niet_aangeboden" and "voert niets uit" in decision["uitvoering_nl"]
    assert rec.decision == decision


def test_plan_never_executes_and_never_removes_data() -> None:
    rec = _record("readarr")
    readarr = hub_app(
        "readarr",
        installatiewijze="catalog_app",
        repository="ghcr.io/home-operations/readarr",
        tag="t",
        catalogus={"app": "readarr", "trein": "community", "versie": "1", "app_versie": "x"},
        opslag=[
            {"bron": "/mnt/p1/boeken", "doel": "/books", "type": "bind", "dataset": "p1/boeken", "soort": "hostpad"}
        ],
    )
    row = _row("readarr", readarr, app_id="readarr")
    adopt(row, app_id="readarr", manual=False, note_nl=None, entries=ENTRIES, actor="user:a", now=NOW)
    app = REG.get("readarr")
    assert app is not None
    plan = build_plan(rec, app, [row], [row], LOADED, now=NOW)
    assert plan["uitgevoerd"] is False and plan["data_verwijderen"]["aangeboden"] is False
    actions = {s["action_id"] for s in plan["stappen"] if s["action_id"]}
    assert actions <= EXECUTOR_ALLOWLIST and "app.uninstall_data" not in actions
    uninstall = next(s for s in plan["stappen"] if s["action_id"] == "app.uninstall")
    assert uninstall["parameters"]["remove_ix_volumes"] is False and uninstall["risico_min"] == "R2"
    snapshot = next(s for s in plan["stappen"] if s["action_id"] == "zfs.snapshot.create")
    assert snapshot["parameters"]["datasets"] == ["p1/boeken"]
    gates = {g["id"]: g["status"] for g in plan["gates"]}
    assert gates["backup_gate"] == "unknown" and gates["geen_gebruik"] == "unknown"  # nooit groen zonder meting
    assert gates["eigenaar_bevestigd"] == "ok" and gates["voorbereiding_afgevinkt"] == "open"
    assert plan["voorbereiding_compleet"] is False
    assert "I26-26-06" in plan["i26"]
    again = build_plan(rec, app, [row], [row], LOADED, now=NOW + timedelta(minutes=1))
    assert again["plan_hash"] == plan["plan_hash"] and again["gemaakt_op"] != plan["gemaakt_op"]


def test_plan_for_kept_app_is_flagged_as_deviation() -> None:
    app = REG.get("immich")
    assert app is not None
    plan = build_plan(_record("immich"), app, [], [], LOADED, now=NOW)
    gates = {g["id"]: g["status"] for g in plan["gates"]}
    assert gates["voorgenomen_besluit"] == "afwijking" and gates["eigenaar_bevestigd"] == "open"
    assert plan["instanties"] == [] and "geen_gebruik" not in gates
