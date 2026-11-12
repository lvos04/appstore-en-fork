"""Herstelronde 3 van S-P2-520 (ADR-0020, aanvulling op ADR-0019): de vaste lijst van accountwoorden uitgebreid.

Besluit BN-1 van de regisseur (optie B, binnen C-12 "strikt zonder"): ``ADMINISTRATOR``, ``BEHEERDER``,
``DIENSTACCOUNT``, ``BEHEERACCOUNT``, ``SUPERADMIN``, ``ROOTUSER`` en ``ADMINUSER`` staan in ``ACCOUNT_WORDS``. Een geheim
van de Hub onder zo'n voorouder (ook met een meervouds-s) is weer een accountplek; de oude valse treffers
(``phpmyadmin``, ``rootless``, ``groot``, ``administration``) blijven geldig. Elke test is eerst rood gezien op de code
van vóór dit herstel (93350b9). Alleen testwaarden.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from appstore.manifest import validate_data
from appstore.manifest.findings import Finding, Severity
from appstore.manifest.model import Manifest
from appstore.manifest.rules import ACCOUNT_WORDS, has_account_word
from appstore.manifest.translate import Target, translate
from tests.conftest import REPO_ROOT
from tests.geheimen_proeven import TESTWAARDEN, catalogus_basis, custom_basis, hub_geheim, met_env, met_waarden

CODE = "account_gegevens_verboden"
ENV = "custom_app.env[2]"
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.thuis.test:30999",
    "timezone": "Europe/Amsterdam",
}
TARGET = Target(truenas_version="25.10.1", ix_app_version="1.4.8")
CATALOG_PARAMS = {"timezone": "Etc/UTC", "data_path": "/mnt/p1/sso"}
CATALOG_TARGET = Target(truenas_version="25.10.1")

#: De zeven woorden uit besluit BN-1 (ADR-0020).
NIEUWE_WOORDEN = ("ADMINISTRATOR", "BEHEERDER", "DIENSTACCOUNT", "BEHEERACCOUNT", "SUPERADMIN", "ROOTUSER", "ADMINUSER")
#: De lijst van ADR-0016 (blijft staan; ADR-0020 voegt alleen toe).
OUDE_WOORDEN = (
    "BOOTSTRAP",
    "DEFAULTUSER",
    "DEFAULT_USER",
    "ADMIN",
    "ROOT",
    "SUPERUSER",
    "OWNER",
    "INITIAL",
    "SETUP",
    "BEHEER",
    "SERVICE_ACCOUNT",
    "SERVICEACCOUNT",
    "DIENST",
)
#: De negen vormen uit de restproef van herstelronde 2 (onder 35bde31 geweigerd, onder 93350b9 geldig), plus
#: meervoudsvormen en schrijfwijzen.
VOOROUDERS = (
    "administrator",
    "Administrator",
    "beheerder",
    "beheerders",
    "dienstaccount",
    "beheeraccount",
    "superadmin",
    "rootuser",
    "adminuser",
    "ADMINISTRATORS",
    "superAdmins",
    "dienstaccounts",
    "beheeraccount_x",
    "ADMINUSER",
)
#: De oude valse treffers van ADR-0018: blijven geldig (ADR-0019 punt 3).
OUDE_VALSE_TREFFERS = ("phpmyadmin", "rootless", "groot", "administration")


def _secret_codes(findings: list[Finding], path: str) -> list[str]:
    return [
        f.code for f in findings if f.path == path and f.code in ("secret_literal_forbidden", "secret_in_form", CODE)
    ]


def _model(data: dict[str, Any]) -> Manifest:
    report, model = validate_data(copy.deepcopy(data))
    assert model is not None, report.as_text()
    return model


def _forged(data: dict[str, Any], change: Any) -> Manifest:
    """Buiten de validator om (verdediging in de diepte): de vertaling toetst zelf opnieuw (W3)."""
    dumped = _model(data).model_dump(mode="json")
    change(dumped)
    return Manifest.model_validate(dumped)


def test_the_fixed_list_has_the_new_words_and_keeps_the_old_ones() -> None:
    """ADR-0020: de lijst krijgt precies de zeven woorden van BN-1 erbij; niets van ADR-0016 verdwijnt."""
    assert set(ACCOUNT_WORDS) == set(OUDE_WOORDEN) | set(NIEUWE_WOORDEN)
    assert len(ACCOUNT_WORDS) == len(OUDE_WOORDEN) + len(NIEUWE_WOORDEN)


@pytest.mark.parametrize("voorouder", VOOROUDERS)
def test_compound_account_word_as_an_ancestor_is_an_account_place(voorouder: str) -> None:
    """BN-1 optie B: een geheim van de Hub onder zo'n voorouder geeft ``account_gegevens_verboden`` (niet te
    accepteren), net als onder ADR-0018, en niet meer de te accepteren waarschuwing ``secret_in_form``."""
    assert has_account_word(voorouder) is True
    pad = f"values.voorbeeld.{voorouder}.password"
    report, model = validate_data(met_waarden({voorouder: {"password": {"param": "db_password"}}}))
    assert not report.valid and model is None, report.as_text()
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    assert _secret_codes(errors, pad) == [CODE]
    assert not any(f.accepted for f in report.findings if f.path == pad)
    for waarde in TESTWAARDEN:
        assert waarde not in report.model_dump_json()


@pytest.mark.parametrize("voorouder", VOOROUDERS)
def test_compound_account_word_as_an_ancestor_gives_no_payload(voorouder: str) -> None:
    """W3: dezelfde vormen buiten de validator om geven geen payload en geen ``vault_refs``."""

    def change(d: dict[str, Any]) -> None:
        d["values"]["voorbeeld"][voorouder] = {"password": {"param": "db_password"}}

    result = translate(_forged(catalogus_basis(), change), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    assert (f"values.voorbeeld.{voorouder}.password", CODE) in {(f.path, f.code) for f in result.blocking()}


@pytest.mark.parametrize("naam", ["ADMINISTRATOR_PW", "BEHEERDER_PW", "DIENSTACCOUNT_PW", "ROOTUSER_PW"])
def test_compound_account_word_in_an_env_name_of_a_custom_app(naam: str) -> None:
    """Dezelfde regel bij een omgevingsvariabele van een Custom App met een geheim van de Hub (ADR-0019 punt 2)."""
    data = met_env({"name": naam, "value_from": {"param": "db_password"}}, hub_geheim("db_password"))
    report, model = validate_data(data)
    assert not report.valid and model is None, report.as_text()
    assert _secret_codes(report.findings, ENV) == [CODE]

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(hub_geheim("db_password"))
        d["custom_app"]["env"].append({"name": naam, "value_from": {"param": "db_password"}})

    result = translate(_forged(custom_basis(), change), PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    assert ("values.envs[2].value", CODE) in {(f.path, f.code) for f in result.blocking()}


@pytest.mark.parametrize("voorouder", OUDE_VALSE_TREFFERS)
def test_old_false_matches_stay_valid(voorouder: str) -> None:
    """De uitbreiding brengt de oude valse treffers niet terug: een geheim van de Hub daaronder blijft de te accepteren
    waarschuwing ``secret_in_form`` (``administration`` is geen ``administrator``)."""
    assert has_account_word(voorouder) is False
    pad = f"values.voorbeeld.{voorouder}.password"
    report, _ = validate_data(met_waarden({voorouder: {"password": {"param": "db_password"}}}))
    assert report.valid, report.as_text()
    assert _secret_codes(report.findings, pad) == ["secret_in_form"]


def test_the_list_stays_open_and_is_documented() -> None:
    """De lijst blijft open voor nieuwe samenstellingen (``sysadmin``, ``webadmin``, ``rootaccount`` tellen niet), en
    ``docs/MANIFESTSTANDAARD.md`` noemt elk woord van de lijst en die grens (ADR-0020)."""
    for naam in ("sysadmin", "webadmin", "rootaccount"):
        assert has_account_word(naam) is False, naam
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    lijst = doc.split("- **Accountnaam:**", 1)[1].split("- **Accountwoord als heel naamdeel", 1)[0]
    for woord in ACCOUNT_WORDS:
        assert f"`{woord}`" in lijst, woord
    niet_ziet = doc.split("Wat de validator niet ziet:", 1)[1].split("Bekende valse treffers", 1)[0]
    assert "`sysadmin`" in niet_ziet and "open" in niet_ziet
    assert "staan sinds ADR-0020 in de lijst en tellen weer" in " ".join(niet_ziet.split())
