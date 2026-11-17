"""Aanvulling C-12 van het contract (C1.3-ASF-15), gebouwd in stap S-P3-490 (ADR-0023 besluit 2 tot en met 4).

- Besluit 2 (BN-490-1, optie 1): ``INIT``, ``LOGIN`` en ``BASIC_AUTH`` in de vaste lijst van accountwoorden, en
  ``JUPYTER_TOKEN`` en ``PASSWORD`` als hele naam van een omgevingsvariabele, een naam/waarde-paar of een parameter.
  Een geheim van de Hub onder zo'n naam is ``account_gegevens_verboden``, nooit de waarschuwing ``secret_in_form``
  (de proeven van R-VEILIG-2 bij de review van S-P2-C12), in ``validate`` en in ``translate``.
- Besluit 3: een kluisverwijzing in een manifest is ook in ``validate`` de fout ``vault_ref_not_allowed``.
- Besluit 4 (BN-490-2, variant B): een geheim van de Hub in een omgevingsvariabele van een Custom App alleen onder een
  geheimnaam die geen accountnaam is; anders de fout ``secret_in_form``.

Alleen testwaarden.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from appstore.manifest import validate_data
from appstore.manifest.cli import main
from appstore.manifest.findings import Finding, Severity
from appstore.manifest.model import Manifest
from appstore.manifest.rules import ACCOUNT_WORDS, LOGIN_NAMES, is_account_name
from appstore.manifest.translate import Target, translate
from tests.conftest import REPO_ROOT
from tests.geheimen_proeven import TESTWAARDEN, catalogus_basis, custom_basis, hub_geheim, met_env, met_waarden

CODE = "account_gegevens_verboden"
ENV = "custom_app.env[2]"
ACC = [{"code": "secret_in_form", "reden_nl": "Proef: geheim dat de Hub maakt."}]
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.thuis.test:30999",
    "timezone": "Europe/Amsterdam",
}
TARGET = Target(truenas_version="25.10.1", ix_app_version="1.4.8")
CATALOG_PARAMS = {"timezone": "Etc/UTC", "data_path": "/mnt/p1/sso"}
CATALOG_TARGET = Target(truenas_version="25.10.1")


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


def _blocking(result: Any) -> set[tuple[str, str]]:
    return {(f.path, f.code) for f in result.blocking()}


# ---------------------------------------------------------------------------------------------- besluit 2
def test_the_fixed_list_has_the_three_words_and_the_two_login_names() -> None:
    assert {"INIT", "LOGIN", "BASIC_AUTH"} <= set(ACCOUNT_WORDS)
    assert frozenset({"JUPYTER_TOKEN", "PASSWORD"}) == LOGIN_NAMES


#: De namen uit de proef van R-VEILIG-2 (ADR-0031 besluit 9) plus de andere vormen van besluit 2.
ACCOUNTNAMEN = (
    "DOCKER_INFLUXDB_INIT_PASSWORD",
    "N8N_BASIC_AUTH_PASSWORD",
    "JUPYTER_TOKEN",
    "PASSWORD",
    "password",
    "Jupyter_Token",
    "LOGIN_TOKEN",
    "APP_LOGIN_PASSWORD",
    "INIT_SECRET",
)


@pytest.mark.parametrize("naam", ACCOUNTNAMEN)
def test_a_hub_secret_under_a_login_name_in_a_custom_app_env_is_refused(naam: str) -> None:
    """Eerst gaf dit de te accepteren waarschuwing ``secret_in_form``; nu ``account_gegevens_verboden``, ook met
    ``accepted_findings`` (fouten zijn nooit te accepteren)."""
    assert is_account_name(naam)
    data = met_env({"name": naam, "value_from": {"param": "toegang"}}, hub_geheim("toegang"), accepted=ACC)
    report, model = validate_data(data)
    assert not report.valid and model is None, report.as_text()
    assert _secret_codes(report.findings, ENV) == [CODE]
    assert not any(f.accepted for f in report.findings)

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(hub_geheim("toegang"))
        d["custom_app"]["env"].append({"name": naam, "value_from": {"param": "toegang"}})

    result = translate(_forged(custom_basis(), change), PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == [] and not result.ready_for_install
    assert ("values.envs[2].value", CODE) in _blocking(result)


@pytest.mark.parametrize("naam", ["JUPYTER_TOKEN", "PASSWORD", "N8N_BASIC_AUTH_PASSWORD", "LOGIN_TOKEN"])
def test_a_hub_secret_under_a_login_name_of_a_name_value_pair_is_refused(naam: str) -> None:
    pad = "values.voorbeeld.additional_envs[0].value"
    data = met_waarden({"additional_envs": [{"name": naam, "value": {"param": "db_password"}}]})
    report, model = validate_data(data)
    assert not report.valid and model is None, report.as_text()
    assert _secret_codes(report.findings, pad) == [CODE]

    def change(d: dict[str, Any]) -> None:
        d["values"]["voorbeeld"]["additional_envs"] = [{"name": naam, "value": {"param": "db_password"}}]

    result = translate(_forged(catalogus_basis(), change), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    assert (pad, CODE) in _blocking(result)


@pytest.mark.parametrize("naam", ["password", "jupyter_token"])
def test_a_parameter_with_a_login_name_is_an_account_name(naam: str) -> None:
    """De hele naam van een parameter telt ook: een geheim van de Hub met de naam ``password`` gaat nergens heen."""
    data = met_waarden({"wachtwoord_db": {"param": naam}}, hub_geheim(naam))
    report, model = validate_data(data)
    assert not report.valid and model is None, report.as_text()
    assert _secret_codes(report.findings, "values.voorbeeld.wachtwoord_db") == [CODE]

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(hub_geheim(naam))
        d["values"]["voorbeeld"]["wachtwoord_db"] = {"param": naam}

    result = translate(_forged(catalogus_basis(), change), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and ("values.voorbeeld.wachtwoord_db", CODE) in _blocking(result)


@pytest.mark.parametrize("voorouder", ["init", "login", "basic_auth", "Login", "initSync", "basic_auth_x"])
def test_the_new_words_as_a_whole_name_part_of_an_ancestor_make_an_account_place(voorouder: str) -> None:
    pad = f"values.voorbeeld.{voorouder}.db_password"
    report, model = validate_data(met_waarden({voorouder: {"db_password": {"param": "db_password"}}}))
    assert not report.valid and model is None, report.as_text()
    assert _secret_codes(report.findings, pad) == [CODE]


def test_a_marked_secret_with_login_in_its_own_name_is_an_account_secret_everywhere() -> None:
    """ADR-0022 met de nieuwe woorden: ``login_sleutel`` is een accountgeheim, ook onder een gewone sleutel."""
    report, model = validate_data(met_waarden({"x": {"param": "login_sleutel"}}, hub_geheim("login_sleutel")))
    assert not report.valid and model is None
    assert _secret_codes(report.findings, "values.voorbeeld.x") == [CODE]


#: Grenzen van besluit 2: een sleutel in ``values`` en het laatste deel van een naam met punten tellen niet als hele
#: naam (``PASSWORD`` als laatste sleuteldeel is een vervolgpunt; gemeten: 0 van 22 bestanden), en een samenstelling die
#: niet in de lijst staat ook niet. Daar blijft de te accepteren waarschuwing ``secret_in_form``.
GRENZEN = [
    ("sleutel_password", {"password": {"param": "db_password"}}, "values.voorbeeld.password"),
    ("sleutel_jupyter_token", {"jupyter_token": {"param": "db_password"}}, "values.voorbeeld.jupyter_token"),
    (
        "paar_met_punten",
        {"additional_envs": [{"name": "app.password", "value": {"param": "db_password"}}]},
        "values.voorbeeld.additional_envs[0].value",
    ),
    (
        "paar_webpassword",
        {"additional_envs": [{"name": "WEBPASSWORD", "value": {"param": "db_password"}}]},
        "values.voorbeeld.additional_envs[0].value",
    ),
]


@pytest.mark.parametrize(("extra", "pad"), [(e, p) for _, e, p in GRENZEN], ids=[n for n, _, _ in GRENZEN])
def test_keys_in_values_and_dotted_names_are_no_whole_name(extra: dict[str, Any], pad: str) -> None:
    report, _ = validate_data(met_waarden(extra))
    assert report.valid, report.as_text()
    assert _secret_codes(report.findings, pad) == ["secret_in_form"]


def test_init_inside_a_word_of_a_secret_name_is_a_known_false_match() -> None:
    """Fail-closed, zoals ``phpmyadmin_password`` (ADR-0019): ``AFFINITY_TOKEN`` en ``CATALOGINDEX_SECRET`` hebben
    ``INIT`` en ``LOGIN`` binnen een woord van een geheimnaam en zijn dus accountnamen."""
    for naam in ("AFFINITY_TOKEN", "CATALOGINDEX_SECRET"):
        assert is_account_name(naam), naam
    for naam in ("APP_SECRET", "DB_PASSWORD", "SECRET_KEY", "API_TOKEN"):
        assert not is_account_name(naam), naam


def test_the_manifest_standard_names_the_words_and_the_login_names() -> None:
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    lijst = doc.split("- **Accountnaam:**", 1)[1].split("- **Accountwoord als heel naamdeel", 1)[0]
    for naam in (*ACCOUNT_WORDS, *LOGIN_NAMES):
        assert f"`{naam}`" in lijst, naam
    assert "hele naam" in lijst


def test_no_test_value_in_the_reports() -> None:
    for naam in ACCOUNTNAMEN:
        data = met_env({"name": naam, "value_from": {"param": "toegang"}}, hub_geheim("toegang"))
        report, _ = validate_data(data)
        for waarde in TESTWAARDEN:
            assert waarde not in report.model_dump_json()


# ---------------------------------------------------------------------------------------------- besluit 4 (variant B)
@pytest.mark.parametrize("naam", ["APP_KEY", "X", "X_WAARDE", "PHPMYADMIN_DB", "GEDEELD"])
def test_a_hub_secret_in_a_custom_app_env_under_a_name_that_is_no_secret_name_is_refused(naam: str) -> None:
    """Fail-closed: de validator kan onder zo'n naam niet uitsluiten dat het een aanmelding is. Fout ``secret_in_form``,
    ook met ``accepted_findings``; de vertaling geeft dan geen payload."""
    data = met_env({"name": naam, "value_from": {"param": "toegang"}}, hub_geheim("toegang"), accepted=ACC)
    report, model = validate_data(data)
    assert not report.valid and model is None, report.as_text()
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    assert _secret_codes(errors, ENV) == ["secret_in_form"]

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(hub_geheim("toegang"))
        d["custom_app"]["env"].append({"name": naam, "value_from": {"param": "toegang"}})

    result = translate(_forged(custom_basis(), change), PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == [] and not result.ready_for_install
    assert ("values.envs[2].value", "secret_in_form") in _blocking(result)


@pytest.mark.parametrize("naam", ["APP_API_KEY", "APP_SECRET", "DB_PASSWORD", "WEBPASSWORD", "apiKeyValue"])
def test_a_hub_secret_under_a_secret_name_without_account_word_keeps_the_acceptable_warning(naam: str) -> None:
    """De tweede uitzondering van §3.1 blijft onder een geheimnaam die geen accountnaam is (``WEBPASSWORD`` ook: die
    aanmeldnaam staat niet in de lijst; daar blijft de review de controle)."""
    data = met_env({"name": naam, "value_from": {"param": "toegang"}}, hub_geheim("toegang"), accepted=ACC)
    report, model = validate_data(data)
    assert report.valid and report.strict_valid and model is not None, report.as_text()
    assert [(f.code, f.accepted) for f in report.findings if f.path == ENV] == [("secret_in_form", True)]
    result = translate(model, PARAMS, TARGET, trusted_app_id=True)
    assert result.ready_for_install and [v.path for v in result.vault_refs] == ["values.envs[2].value"]


def test_variant_b_is_only_for_custom_app_envs() -> None:
    """In de ``values`` van een catalogusapp (§3.1 a) legt de app van iX de sleutels vast; daar verandert niets."""
    report, model = validate_data(met_waarden({"x": {"param": "db_password"}}))
    assert report.valid and model is not None, report.as_text()
    assert _secret_codes(report.findings, "values.voorbeeld.x") == ["secret_in_form"]


# ---------------------------------------------------------------------------------------------- besluit 3 (vault)
VREEMD = "vault://app/andere-app/geheim"


def _met_ref(plek: str) -> tuple[dict[str, Any], str]:
    if plek == "env":
        return met_env({"name": "OTHER", "value": VREEMD}), "custom_app.env[2].value"
    if plek == "waarde":
        return met_waarden({"x": VREEMD}), "values.voorbeeld.x"
    if plek == "midden_in_tekst":
        return met_waarden({"x": f"zie {VREEMD.upper()} hier"}), "values.voorbeeld.x"
    if plek == "sleutel":
        return met_waarden({VREEMD: "y"}), "values.voorbeeld"
    if plek == "default":
        data = custom_basis()
        data["parameters"].append(
            {
                "name": "x",
                "type": "string",
                "source": "default",
                "default": VREEMD,
                "required": True,
                "description_nl": "Proef.",
            }
        )
        return data, "parameters[6].default"
    data = custom_basis()
    data["description_nl"] = f"Beschrijving met {VREEMD} erin."
    return data, "description_nl"


@pytest.mark.parametrize("plek", ["env", "waarde", "midden_in_tekst", "sleutel", "default", "beschrijving"])
def test_a_vault_reference_in_a_manifest_is_refused_by_validate(plek: str) -> None:
    """Proef p5 van de review van S-P2-C12: eerst "GELDIG" bij ``validate --strict``; nu ``vault_ref_not_allowed``,
    met het pad en zonder de verwijzing."""
    data, pad = _met_ref(plek)
    report, model = validate_data(data)
    assert not report.valid and model is None, report.as_text()
    assert ("vault_ref_not_allowed", Severity.ERROR) in {(f.code, f.severity) for f in report.findings if f.path == pad}
    uitvoer = report.as_text() + report.model_dump_json()
    assert "andere-app" not in uitvoer and "ANDERE-APP" not in uitvoer


def test_a_vault_reference_is_refused_also_when_the_schema_fails() -> None:
    data, pad = _met_ref("waarde")
    data["onbekend_veld"] = 1
    report, _ = validate_data(data)
    assert {"unknown_field", "vault_ref_not_allowed"} <= set(report.codes())
    assert ("vault_ref_not_allowed", pad) in {(f.code, f.path) for f in report.findings}


def test_the_cli_refuses_a_vault_reference(tmp_path: Any) -> None:
    data, _ = _met_ref("env")
    pad = tmp_path / "p5.manifest.yaml"
    pad.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert main(["validate", "--strict", str(pad)]) == 1
