"""W1 van ADR-0016, gebouwd in stap S-P3-490 (ADR-0023 besluit 1): het optionele veld ``accounts``.

Het manifest verklaart welke beheerder of welk dienstaccount er moet komen, nooit een waarde. Getest: de geldige vorm,
elke weigerregel (``account_ongeldig``, ``account_controle_ontbreekt``, ``account_controle_te_lang``,
``duplicate_name`` en de schemafouten), een geheime waarde in de voorziening, in ``custom_app.env`` en in een
parameter-``default``, en dat ``accounts`` niet in de vertaling, de eisen of de uitvoer van ``jl-manifest`` komt.
Alleen testwaarden.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from appstore.catalog.service import requirements
from appstore.manifest import validate_data
from appstore.manifest.cli import main
from appstore.manifest.findings import CODES, Finding, Severity
from appstore.manifest.translate import Target, translate
from tests.conftest import REPO_ROOT
from tests.geheimen_proeven import (
    HASH,
    PROEFSLEUTEL,
    PROEFWACHTWOORD,
    TESTWAARDEN,
    catalogus_basis,
    custom_basis,
    flows,
)

CATALOG_PARAMS = {"timezone": "Etc/UTC", "data_path": "/mnt/p1/sso"}
CATALOG_TARGET = Target(truenas_version="25.10.1")
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.thuis.test:30999",
    "timezone": "Europe/Amsterdam",
}
TARGET = Target(truenas_version="25.10.1", ix_app_version="1.4.8")
#: Een merkteken in de namen en teksten van de accounts: het mag in geen payload, eis of uitvoer staan.
MERK = "proefmerkaccount"
DIENST_CONTROLE = {
    "id": "dienst_aanmaken",
    "soort": "handmatig",
    "beschrijving_nl": "Maak na je eigen aanmelding het dienstaccount voor de afnemer aan, met de rechten uit het manifest.",
}


def beheerder(**extra: Any) -> dict[str, Any]:
    return {
        "id": f"{MERK}_beheer",
        "rol": "beheerder",
        "aanmaak": "eerste_gebruik",
        "controle": "eerste_beheerder",
        "beschrijving_nl": f"De beheerder zet zijn eigen wachtwoord in het eerste-gebruik-venster ({MERK}).",
        **extra,
    }


def dienst(**extra: Any) -> dict[str, Any]:
    return {
        "id": f"{MERK}_dienst",
        "rol": "dienst",
        "aanmaak": "door_beheerder",
        "afnemer": "home-control-hub",
        "rechten": ["flows.read", "status:lezen"],
        "controle": "dienst_aanmaken",
        "beschrijving_nl": f"Dienstaccount voor de afnemer; de beheerder voert de aanmelding één keer in ({MERK}).",
        **extra,
    }


def met_accounts(*accounts: dict[str, Any], basis: dict[str, Any] | None = None) -> dict[str, Any]:
    data = copy.deepcopy(basis) if basis is not None else catalogus_basis()
    checks = data.setdefault("post_install_checks", [])
    if not any(c["id"] == "eerste_beheerder" for c in checks):
        checks.append(
            {"id": "eerste_beheerder", "soort": "handmatig", "beschrijving_nl": "Zet het eerste beheerdersaccount."}
        )
    checks.append(copy.deepcopy(DIENST_CONTROLE))
    data["accounts"] = copy.deepcopy(list(accounts))
    return data


def _errors(data: dict[str, Any]) -> list[Finding]:
    report, model = validate_data(copy.deepcopy(data))
    assert not report.valid and model is None, report.as_text()
    return [f for f in report.findings if f.severity is Severity.ERROR]


def _no_echo(text: str, *extra: str) -> None:
    for waarde in (*TESTWAARDEN, *extra):
        assert waarde not in text, "een testwaarde staat in de uitvoer"


# ---------------------------------------------------------------------------------------------- geldige vorm
def test_a_manifest_with_an_administrator_and_a_service_account_is_valid() -> None:
    data = met_accounts(beheerder(), dienst())
    report, model = validate_data(data)
    assert report.valid and report.strict_valid, report.as_text()
    assert model is not None and [a.rol for a in model.accounts] == ["beheerder", "dienst"]
    assert model.accounts[1].rechten == ["flows.read", "status:lezen"]
    # dezelfde bevindingen als zonder accounts: de voorziening voegt geen melding toe
    zonder = validate_data(catalogus_basis())[0]
    assert [(f.code, f.path) for f in report.findings] == [(f.code, f.path) for f in zonder.findings]


def test_accounts_is_optional_and_a_manifest_without_it_is_unchanged() -> None:
    report, model = validate_data(catalogus_basis())
    assert report.valid and model is not None and model.accounts == []
    assert "accounts" not in catalogus_basis()


def test_the_same_check_may_be_named_by_more_accounts() -> None:
    report, _ = validate_data(met_accounts(beheerder(), dienst(controle="eerste_beheerder")))
    assert report.valid, report.as_text()


# ---------------------------------------------------------------------------------------------- weigerregels
def _account_codes(data: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted(
        (f.path, f.code)
        for f in _errors(data)
        if f.code in ("account_ongeldig", "account_controle_ontbreekt", "account_controle_te_lang", "duplicate_name")
    )


ONGELDIG = [
    (
        "dienst_zonder_afnemer",
        (beheerder(), {k: v for k, v in dienst().items() if k != "afnemer"}),
        "accounts[1].afnemer",
    ),
    (
        "dienst_zonder_rechten",
        (beheerder(), {k: v for k, v in dienst().items() if k != "rechten"}),
        "accounts[1].rechten",
    ),
    ("beheerder_met_afnemer", (beheerder(afnemer="home-control-hub"),), "accounts[0].afnemer"),
    ("beheerder_met_rechten", (beheerder(rechten=["flows.read"]),), "accounts[0].rechten"),
    ("eerste_gebruik_bij_dienst", (beheerder(), dienst(aanmaak="eerste_gebruik")), "accounts[1].aanmaak"),
    ("door_beheerder_bij_beheerder", (beheerder(aanmaak="door_beheerder"),), "accounts[0].aanmaak"),
    ("twee_beheerders", (beheerder(), beheerder(id=f"{MERK}_tweede")), "accounts[1].rol"),
    ("afnemer_is_de_app_zelf", (beheerder(), dienst(afnemer="voorbeeld-sso")), "accounts[1].afnemer"),
    ("dienst_zonder_beheerder", (dienst(),), "accounts[0].rol"),
]


@pytest.mark.parametrize(("accounts", "pad"), [(a, p) for _, a, p in ONGELDIG], ids=[n for n, _, _ in ONGELDIG])
def test_a_combination_the_table_forbids_is_account_ongeldig(accounts: tuple[dict[str, Any], ...], pad: str) -> None:
    errors = _errors(met_accounts(*accounts))
    assert [(f.path, f.code) for f in errors] == [(pad, "account_ongeldig")]
    assert MERK not in json.dumps([f.model_dump() for f in errors]), "een melding noemt de waarde van een veld"


def test_an_unknown_check_is_account_controle_ontbreekt() -> None:
    assert _account_codes(met_accounts(beheerder(controle="bestaat_niet"))) == [
        ("accounts[0].controle", "account_controle_ontbreekt")
    ]


def test_a_duplicate_id_is_duplicate_name() -> None:
    data = met_accounts(beheerder(), dienst(id=f"{MERK}_beheer"))
    assert _account_codes(data) == [("accounts[1].id", "duplicate_name")]


def test_the_text_of_a_check_named_by_an_account_is_at_most_500_characters() -> None:
    """De grens van ``validate_manifest_meta`` in het beleid van de Hub (ADR-0016): 500 geldig, 501 fout, en dezelfde
    tekst van 501 tekens bij een controle zonder account blijft geldig."""
    for lengte, geldig in ((500, True), (501, False)):
        data = met_accounts(beheerder())
        data["post_install_checks"][0]["beschrijving_nl"] = "x" * lengte
        report, _ = validate_data(data)
        assert report.valid is geldig, report.as_text()
        if not geldig:
            assert [(f.path, f.code) for f in report.findings if f.severity is Severity.ERROR] == [
                ("post_install_checks[0].beschrijving_nl", "account_controle_te_lang")
            ]
    data = met_accounts(beheerder())
    data["post_install_checks"][1]["beschrijving_nl"] = "x" * 501  # dienst_aanmaken: geen account noemt hem
    assert validate_data(data)[0].valid
    # twee accounts die dezelfde te lange controle noemen: één melding
    data = met_accounts(beheerder(), dienst(controle="eerste_beheerder"))
    data["post_install_checks"][0]["beschrijving_nl"] = "x" * 501
    assert _account_codes(data) == [("post_install_checks[0].beschrijving_nl", "account_controle_te_lang")]


VERPLICHT = ("id", "rol", "aanmaak", "controle", "beschrijving_nl")


@pytest.mark.parametrize("veld", VERPLICHT)
def test_an_account_without_a_required_field_fails(veld: str) -> None:
    account = {k: v for k, v in beheerder().items() if k != veld}
    errors = _errors(met_accounts(account))
    assert ("required_field_missing", f"accounts[0].{veld}") in {(f.code, f.path) for f in errors}


SCHEMAFOUTEN = [
    ("rechten_ster", dienst(rechten=["*"]), "invalid_format"),
    ("rechten_leeg", dienst(rechten=[]), "out_of_range"),
    ("rechten_dubbel", dienst(rechten=["flows.read", "flows.read"]), "duplicate_name"),
    ("rechten_te_veel", dienst(rechten=[f"recht{i}" for i in range(33)]), "out_of_range"),
    ("rol_onbekend", beheerder(rol="eigenaar"), "invalid_value"),
    ("aanmaak_onbekend", beheerder(aanmaak="door_de_app"), "invalid_value"),
    ("afnemer_geen_app_id", dienst(afnemer="Home Control Hub"), "invalid_format"),
    ("id_geen_naam", beheerder(id="Beheer!"), "invalid_format"),
    ("beschrijving_te_kort", beheerder(beschrijving_nl="kort"), "out_of_range"),
]


@pytest.mark.parametrize(("account", "code"), [(a, c) for _, a, c in SCHEMAFOUTEN], ids=[n for n, _, _ in SCHEMAFOUTEN])
def test_schema_errors_under_accounts_do_not_show_the_value(account: dict[str, Any], code: str) -> None:
    report, model = validate_data(
        met_accounts(beheerder(), account) if account["rol"] == "dienst" else met_accounts(account)
    )
    assert not report.valid and model is None
    assert code in report.codes(), report.as_text()
    for verboden in ("'*'", "Home Control Hub", "Beheer!", "eigenaar", "door_de_app"):
        assert verboden not in report.as_text() and verboden not in report.model_dump_json()


def test_at_most_16_accounts() -> None:
    accounts = [beheerder()] + [dienst(id=f"dienst{i}") for i in range(16)]
    assert "out_of_range" in validate_data(met_accounts(*accounts))[0].codes()
    assert validate_data(met_accounts(*accounts[:16]))[0].valid


# ---------------------------------------------------------------------------------------------- geen waarde (bouw 3)
#: (plek, soort waarde) -> (manifest, pad, verwachte code). Negatief (a) van de stap.
def _in_voorziening(veld: str, waarde: str) -> dict[str, Any]:
    return met_accounts(beheerder(**{veld: waarde}))


def _in_env(naam: str, waarde: str) -> dict[str, Any]:
    data = custom_basis()
    data["custom_app"]["env"].append({"name": naam, "value": waarde})
    return data


def _in_default(naam: str, waarde: str) -> dict[str, Any]:
    data = custom_basis()
    data["parameters"].append(
        {
            "name": naam,
            "type": "string",
            "source": "default",
            "default": waarde,
            "required": True,
            "description_nl": "Proef.",
        }
    )
    return data


GEHEIME_WAARDEN = [
    (
        "voorziening_wachtwoord",
        _in_voorziening("wachtwoord", PROEFWACHTWOORD),
        "accounts[0].wachtwoord",
        "unknown_field",
    ),
    (
        "voorziening_bcrypt",
        _in_voorziening("wachtwoord_hash", HASH),
        "accounts[0].wachtwoord_hash",
        "account_gegevens_verboden",
    ),
    (
        "voorziening_credentialsecret",
        _in_voorziening("credentialSecret", PROEFSLEUTEL),
        "accounts[0].credentialSecret",
        "unknown_field",
    ),
    (
        "voorziening_bcrypt_in_tekst",
        met_accounts(beheerder(beschrijving_nl=f"Hash {HASH} van de beheerder.")),
        "accounts[0].beschrijving_nl",
        "account_gegevens_verboden",
    ),
    ("env_wachtwoord", _in_env("APP_ADMIN_PASSWORD", PROEFWACHTWOORD), "custom_app.env[2]", "secret_literal_forbidden"),
    ("env_bcrypt", _in_env("APP_HASH", HASH), "custom_app.env[2].value", "account_gegevens_verboden"),
    (
        "env_credentialsecret",
        _in_env("FLOWS", flows(f"-D credentialSecret={PROEFSLEUTEL}")),
        "custom_app.env[2].value",
        "account_gegevens_verboden",
    ),
    (
        "default_wachtwoord",
        _in_default("beheer_wachtwoord", PROEFWACHTWOORD),
        "parameters[6].default",
        "secret_literal_forbidden",
    ),
    ("default_bcrypt", _in_default("hash_waarde", HASH), "parameters[6].default", "account_gegevens_verboden"),
    (
        "default_credentialsecret",
        _in_default("instellingen", f"credentialSecret={PROEFSLEUTEL}"),
        "parameters[6].default",
        "account_gegevens_verboden",
    ),
]


@pytest.mark.parametrize(
    ("data", "pad", "code"), [(d, p, c) for _, d, p, c in GEHEIME_WAARDEN], ids=[n for n, _, _, _ in GEHEIME_WAARDEN]
)
def test_a_secret_value_in_the_provision_env_or_default_is_refused_without_echo(
    data: dict[str, Any], pad: str, code: str
) -> None:
    report, model = validate_data(copy.deepcopy(data))
    assert not report.valid and model is None
    assert (pad, code) in {(f.path, f.code) for f in report.findings if f.severity is Severity.ERROR}, report.as_text()
    _no_echo(report.as_text() + report.model_dump_json(), PROEFWACHTWOORD, PROEFSLEUTEL)


# ---------------------------------------------------------------------------------------------- niets in de vertaling
def test_accounts_is_in_no_payload_requirement_or_vault_ref_of_a_catalog_app() -> None:
    data = met_accounts(beheerder(), dienst())
    report, model = validate_data(data)
    assert model is not None, report.as_text()
    zonder = validate_data(catalogus_basis())[1]
    assert zonder is not None
    result = translate(model, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    oud = translate(zonder, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install and result.payload is not None
    assert result.payload == oud.payload and result.vault_refs == oud.vault_refs
    uitvoer = result.model_dump_json()
    assert MERK not in uitvoer and "accounts" not in uitvoer and "home-control-hub" not in uitvoer
    eisen = json.dumps(requirements(model))
    assert "accounts" not in eisen and MERK not in eisen and "home-control-hub" not in eisen
    assert set(requirements(model)) == set(requirements(zonder))


def test_accounts_is_in_no_payload_of_a_custom_app() -> None:
    data = met_accounts(beheerder(), dienst(), basis=custom_basis())
    report, model = validate_data(data)
    assert model is not None, report.as_text()
    result = translate(model, PARAMS, TARGET, trusted_app_id=True)
    assert result.ready_for_install and result.payload is not None, [f.code for f in result.findings]
    uitvoer = result.model_dump_json()
    assert MERK not in uitvoer and "accounts" not in uitvoer


def test_the_translate_cli_shows_no_account(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pad = tmp_path / "proef.manifest.yaml"
    pad.write_text(yaml.safe_dump(met_accounts(beheerder(), dienst()), allow_unicode=True), encoding="utf-8")
    params = tmp_path / "p.json"
    params.write_text(json.dumps(CATALOG_PARAMS), encoding="utf-8")
    assert main(["validate", "--strict", str(pad)]) == 0
    assert main(["translate", str(pad), "--params", str(params), "--truenas-version", "25.10.1"]) == 0
    uit = capsys.readouterr().out
    assert "db_password" in uit and MERK not in uit and "home-control-hub" not in uit


# ---------------------------------------------------------------------------------------------- codes en docs
def test_the_new_codes_are_documented_errors() -> None:
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    for code in ("account_ongeldig", "account_controle_ontbreekt", "account_controle_te_lang"):
        assert code in CODES and f"| `{code}` |" in doc
    assert "| `accounts` |" in doc
