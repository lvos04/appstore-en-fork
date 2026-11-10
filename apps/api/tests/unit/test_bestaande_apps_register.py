"""Register bekende bestaande apps (APP-01…APP-20): elke tekst is letterlijk herleidbaar naar APP_REGISTER/A-docs/I26
en de Excel-taken, en het register bevat geen enkel NAS-gegeven (geen versies, poorten, paden of adressen)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from appstore.bestaande_apps import (
    INVENTORY_ITEMS,
    PHASES,
    READY_TEXT,
    REGISTER_PATH,
    checks_for,
    load_register,
    match_app,
    normalize_repository,
    register_entries,
)
from tests.conftest import REPO_ROOT

DOCS = REPO_ROOT / "docs" / "platform-v2"
APPS_DIR = DOCS / "04_BESTAANDE_APPS"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def _doc(name: str) -> str:
    for folder in (APPS_DIR, DOCS / "03_INSTALLATIE"):
        path = folder / name
        if path.exists():
            return _norm(path.read_text(encoding="utf-8"))
    raise AssertionError(f"document {name} niet gevonden")


REG = load_register().register


def test_register_covers_exactly_the_excel_app_tasks() -> None:
    tasks = json.loads((DOCS / "EXCEL_TAKEN.json").read_text(encoding="utf-8"))
    excel = {t["ID"]: t for t in tasks if t["ID"].startswith("APP-")}
    assert sorted(excel) == [f"APP-{i:02d}" for i in range(1, 21)]
    assert sorted(a.excel_id for a in REG.apps) == sorted(excel)
    for app in REG.apps:
        task = excel[app.excel_id]
        assert task["Project"] == app.naam
        assert task["Document"] == f"04_BESTAANDE_APPS/{app.a_doc}"
        assert _norm(task["Acceptatie"]) == _norm(app.belangrijkste_controle_nl)
        assert _norm(app.waarom_nl).rstrip(".") in _norm(task["Uitvoering"])


@pytest.mark.parametrize("app", REG.apps, ids=lambda a: a.excel_id)
def test_texts_are_literal_from_the_documents(app: object) -> None:
    from appstore.bestaande_apps import KnownApp

    assert isinstance(app, KnownApp)
    a_doc = _doc(app.a_doc)
    register = _doc("APP_REGISTER.md")
    assert f"## Besluit {_norm(app.voorgenomen_besluit.tekst_nl)}" in a_doc
    assert f"## Waarom {_norm(app.waarom_nl)}" in a_doc
    assert f"**Acceptatie:** {app.belangrijkste_controle_nl}".replace("**", "") in a_doc
    row = f"| {app.naam} | {app.voorgenomen_besluit.tekst_nl.rstrip('.')} | {app.belangrijkste_controle_nl} |"
    assert _norm(row) in register
    for item_id, text in INVENTORY_ITEMS:
        assert _norm(text) in a_doc, (app.a_doc, item_id)
    for _cid, label, text in PHASES:
        assert _norm(f"{label}: {text}") in a_doc, (app.a_doc, label)
    assert _norm(f"Gereed wanneer: {READY_TEXT}") in a_doc
    for dep in app.afhankelijk_van:
        assert _norm(dep.reden_nl) in _doc(dep.bron), (app.app_id, dep.app_id)


def test_check_list_shape_and_default_not_applicable() -> None:
    plex, immich = REG.get("plex"), REG.get("immich")
    assert plex is not None and immich is not None
    ids = [c.id for c in checks_for(plex)]
    assert ids == [
        "inventaris-1",
        "inventaris-2",
        "inventaris-3",
        "inventaris-4",
        "inventaris-5",
        "a0-meten",
        "a1-integreren",
        "a2-verbeteren",
        "a3-proefstop",
        "a4-besluit",
        "aandachtspunt",
        "acceptatie",
        "gereed",
    ]
    assert next(c for c in checks_for(plex) if c.id == "a3-proefstop").default_nvt_nl is None  # kandidaat
    assert next(c for c in checks_for(immich) if c.id == "a3-proefstop").default_nvt_nl  # behouden: n.v.t.
    candidates = sorted(a.app_id for a in REG.apps if a.decommission_candidate)
    assert candidates == ["emby", "flaresolverr", "jellyplex-watched", "plex", "readarr"]


def test_register_contains_no_nas_values() -> None:
    """Geen IP-adressen, hostpaden, poolnamen, poorten, tags of digests: die komen alleen uit de meting."""
    raw = REGISTER_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    flat = json.dumps({"apps": data["apps"], "platform_apps": data["platform_apps"]})  # bronverwijzingen niet
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", flat)
    assert "/mnt/" not in flat and "tank" not in flat.lower()
    assert "sha256:" not in flat and "@" not in flat
    for app in data["apps"]:
        assert set(app) <= {
            "app_id",
            "excel_id",
            "naam",
            "a_doc",
            "voorgenomen_besluit",
            "waarom_nl",
            "belangrijkste_controle_nl",
            "eigenaar",
            "i26_stappen",
            "proefstop_uren",
            "zonder_gebruik_dagen",
            "herkenning",
            "afhankelijk_van",
            "platform_wijzigt_nooit",
        }
        for repo in app["herkenning"]["image_repositories"]:
            assert ":" not in repo.rsplit("/", 1)[-1], f"{repo}: geen tag in een herkenningsregel"


def test_recognition_is_unambiguous_and_never_by_name() -> None:
    owners: dict[str, str] = {}
    cats: dict[str, str] = {}
    for app in REG.apps:
        for repo in app.herkenning.image_repositories:
            norm = normalize_repository(repo)
            assert norm not in owners, f"{repo} hoort al bij {owners.get(norm)}"
            owners[norm] = app.app_id
        for cat in app.herkenning.catalogus_apps:
            assert cat not in cats and cat != "ix-app"
            cats[cat] = app.app_id
    for p in REG.platform_apps:
        for repo in p.image_repositories:
            assert normalize_repository(repo) not in owners
            owners[normalize_repository(repo)] = p.app_id
    # A06: geen afleiding uit de appnaam → Snapotter, Teeltboek en Uren hebben geen enkele herkenningsregel
    for app_id in ("snapotter", "teeltboek", "uren"):
        app = REG.get(app_id)
        assert app is not None
        assert app.herkenning.catalogus_apps == [] and app.herkenning.image_repositories == []
        assert app.herkenning.toelichting_nl
    # eigen bestaande apps: de alles-in-één image uit platformcontract 1.2 §2.3 staat er altijd in; de overige namen
    # zijn de gepubliceerde images uit de release-workflow van die repo (oudere installaties met meerdere containers)
    for app_id, aio in (
        ("mediaguard", "ghcr.io/lvos04/mediaguard-core"),
        ("media-source-optimizer", "ghcr.io/lvos04/media-source-optimizer"),
        ("my_house", "ghcr.io/lvos04/my_house"),
    ):
        app = REG.get(app_id)
        assert app is not None and app.herkenning.image_repositories[0] == aio
        assert all(r.startswith("ghcr.io/lvos04/") for r in app.herkenning.image_repositories)
        contract = (DOCS / "PLATFORM_CONTRACTEN.md").read_text(encoding="utf-8")
        assert f"`{aio}:<versie>" in contract


def test_platform_apps_follow_contract_table() -> None:
    text = (DOCS / "PLATFORM_CONTRACTEN.md").read_text(encoding="utf-8")
    for p in REG.platform_apps:
        assert f"`{p.app_id}`" in text
        for repo in p.image_repositories:
            assert f"`{repo}`" in text, repo


def test_register_hash_is_stable(tmp_path: Path) -> None:
    copy = tmp_path / "register.yaml"
    copy.write_bytes(REGISTER_PATH.read_bytes())
    load_register.cache_clear()
    try:
        assert load_register(copy).sha256 == load_register(REGISTER_PATH).sha256
    finally:
        load_register.cache_clear()


def test_unknown_dependency_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(REGISTER_PATH.read_text(encoding="utf-8"))
    data["apps"][0]["afhankelijk_van"] = [{"app_id": "bestaat-niet", "soort": "vereist", "reden_nl": "x", "bron": "y"}]
    bad = tmp_path / "register.yaml"
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    load_register.cache_clear()
    try:
        with pytest.raises(ValueError, match="onbekende app"):
            load_register(bad)
    finally:
        load_register.cache_clear()


def test_emby_is_a_third_party_adoption_item_the_platform_never_changes() -> None:
    """Contract 1.3 §2.3 regels 3, 7, 8 en §9 (C1.3-ASF-12): Emby is een adoptie-item met ``owner.soort: derde``,
    alleen herkend op de exacte catalogusappnaam of image-repository, en het platform wijzigt hem nooit."""
    emby = REG.get("emby")
    assert emby is not None and emby.excel_id == "APP-02" and emby.eigenaar.soort == "derde"
    assert emby.platform_wijzigt_nooit is True
    assert [a.app_id for a in REG.apps if a.platform_wijzigt_nooit] == ["emby"]
    assert emby.herkenning.catalogus_apps == ["emby"]
    entries = register_entries(REG)
    by_name = match_app(
        install_method="custom_app", catalog_app=None, repositories=["ghcr.io/iemand/emby"], entries=entries
    )
    assert by_name.status == "onbekend"  # 'emby' in de naam van een andere image telt nooit
    exact = match_app(install_method="custom_app", catalog_app=None, repositories=["emby/embyserver"], entries=entries)
    assert (exact.status, exact.app_id) == ("kandidaat", "emby")
    cat = match_app(install_method="catalog_app", catalog_app="emby", repositories=[], entries=entries)
    assert (cat.status, cat.app_id) == ("kandidaat", "emby")
