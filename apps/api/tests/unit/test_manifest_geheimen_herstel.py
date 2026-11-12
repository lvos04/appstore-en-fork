"""Herstel na de review van S-P2-520 (ADR-0018, naast ADR-0016 en ADR-0017).

Elke test hoort bij een bevinding van de review (R-VEILIG-1 tot en met -6, R-TECH-1 tot en met -4, R-MANIFEST-1 tot en
met -3) en is eerst rood gezien op de code van vóór het herstel. Alleen testwaarden; geen melding, uitvoer of
vertaling noemt een testwaarde (V-094).
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from appstore.manifest import rules, validate_data
from appstore.manifest.cli import main
from appstore.manifest.findings import Finding, Severity
from appstore.manifest.model import Manifest
from appstore.manifest.rules import (
    has_password_hash,
    is_account_name,
    is_secret_name,
    secret_text_reasons,
    t2_safety_net_reasons,
    t2_setting_reasons,
)
from appstore.manifest.translate import Target, filled_value_findings, translate
from appstore.manifest.validator import validate_text
from tests.conftest import REPO_ROOT
from tests.geheimen_proeven import (
    EXTRA_PARAM,
    HASH,
    PROEFSLEUTEL,
    PROEFWACHTWOORD,
    TESTWAARDEN,
    WITRUIMTE,
    catalogus_basis,
    custom_basis,
    flows,
    hub_geheim,
    met_env,
    met_waarden,
    wizard,
)

CODE = "account_gegevens_verboden"
ENV = "custom_app.env[2]"
VERBORGEN = "<verborgen>"
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.thuis.test:30999",
    "timezone": "Europe/Amsterdam",
}
TARGET = Target(truenas_version="25.10.1", ix_app_version="1.4.8")
CATALOG_PARAMS = {"timezone": "Etc/UTC", "data_path": "/mnt/p1/sso"}
CATALOG_TARGET = Target(truenas_version="25.10.1")
#: 256 KiB: de grootte van een heel manifest (``loader.MAX_MANIFEST_BYTES``)
GROOT = 256 * 1024


def _errors(data: dict[str, Any]) -> list[Finding]:
    report, _ = validate_data(data)
    return [f for f in report.findings if f.severity is Severity.ERROR]


def _at(findings: list[Finding], path: str) -> list[str]:
    return [f.code for f in findings if f.path == path]


def _secret_codes(findings: list[Finding], path: str) -> list[str]:
    return [c for c in _at(findings, path) if c in ("secret_literal_forbidden", "secret_in_form", CODE)]


def _no_echo(text: str, *extra: str) -> None:
    for waarde in (*TESTWAARDEN, *extra):
        assert waarde not in text, "een testwaarde staat in de uitvoer"


def _model(data: dict[str, Any]) -> Manifest:
    report, model = validate_data(data)
    assert model is not None, report.as_text()
    return model


def _forged(data: dict[str, Any], change: Any) -> Manifest:
    """Buiten de validator om (verdediging in de diepte): de vertaling toetst zelf opnieuw."""
    dumped = _model(data).model_dump(mode="json")
    change(dumped)
    return Manifest.model_validate(dumped)


def _refused(data: dict[str, Any]) -> list[Finding]:
    """Ongeldig, dus ook geen vertaling (``translate``/``preview`` weigeren vóór de vertaling)."""
    report, model = validate_data(copy.deepcopy(data))
    assert not report.valid and model is None, report.as_text()
    return [f for f in report.findings if f.severity is Severity.ERROR]


# ---------------------------------------------------------------------------------------------- R-VEILIG-1
@pytest.mark.parametrize("soort", sorted(WITRUIMTE))
def test_whitespace_only_value_counts_at_every_place(soort: str) -> None:
    """Een tekst van alleen spaties, U+00A0, U+2003 of de stuurtekens U+001C/U+001F is geen lege waarde (ADR-0018):
    bash, nopt en de apps zelf zien die tekst als waarde. Elke plek uit R-VEILIG-1 geeft een fout, zonder vertaling."""
    ws = WITRUIMTE[soort]
    env_lit = met_env({"name": "GOTIFY_DEFAULTUSER_PASS", "value": ws})
    assert _secret_codes(_refused(env_lit), ENV) == ["secret_literal_forbidden"]
    env_key = met_env({"name": "APP_SECRET_KEY", "value": ws})
    assert _secret_codes(_refused(env_key), ENV) == ["secret_literal_forbidden"]
    sleutel = met_waarden({"bootstrap_password": ws})
    assert _secret_codes(_refused(sleutel), "values.voorbeeld.bootstrap_password") == ["secret_literal_forbidden"]
    paar = met_waarden({"additional_envs": [{"name": "AUTHENTIK_BOOTSTRAP_PASSWORD", "value": ws}]})
    assert _secret_codes(_refused(paar), "values.voorbeeld.additional_envs[0].value") == ["secret_literal_forbidden"]
    default = met_env(
        {"name": "X_WAARDE", "value_from": {"param": "app_token"}},
        {**wizard("app_token"), "source": "default", "default": ws},
    )
    assert _secret_codes(_refused(default), f"parameters[{EXTRA_PARAM}].default") == ["secret_literal_forbidden"]
    json_vorm = met_env({"name": "FLOWS", "value": flows('-D settings={"credentialSecret":"' + ws + '"}')})
    assert CODE in _at(_refused(json_vorm), f"{ENV}.value")
    escaped = "".join(f"\\u{ord(ch):04x}" for ch in ws)
    json_escaped = met_env({"name": "FLOWS", "value": flows('-D settings={"credentialSecret":"' + escaped + '"}')})
    assert CODE in _at(_refused(json_escaped), f"{ENV}.value")
    if soort != "spaties":  # bash splitst op een spatie: dan heeft -D credentialSecret= geen waarde
        define = met_env({"name": "FLOWS", "value": flows("-D credentialSecret=" + ws)})
        assert CODE in _at(_refused(define), f"{ENV}.value")
        los = met_waarden({"config": "credentialSecret=" + ws})
        assert CODE in _at(_refused(los), "values.voorbeeld.config")


@pytest.mark.parametrize("soort", sorted(WITRUIMTE))
def test_whitespace_only_value_from_the_wizard_gives_no_payload(soort: str) -> None:
    """W3: dezelfde tekst als wizardwaarde: geen payload (de stuurtekens weigert de parametercontrole al)."""
    ws = WITRUIMTE[soort]
    model = _model(met_env({"name": "WELKOM", "value_from": {"param": "welkomtekst"}}, wizard("welkomtekst")))
    for value in ('-D settings={"credentialSecret":"' + ws + '"}', "-D credentialSecret=" + ws):
        if soort == "spaties" and not value.endswith("}"):
            continue
        result = translate(model, dict(PARAMS, welkomtekst=value), TARGET)
        assert result.payload is None and not result.ready_for_install and result.preview_hash is None
        assert result.blocking()
    forged = filled_value_findings({"envs": [{"name": "APP_DB_PASSWORD", "value": ws}]}, {}, custom_app=True)
    assert [(f.path, f.code) for f in forged] == [("values.envs[0].value", "secret_in_form")]


def test_empty_text_still_does_not_count() -> None:
    """Alleen de lege tekst telt niet als waarde (en ja/nee en ``null``, zoals voorheen)."""
    report, _ = validate_data(met_env({"name": "APP_SECRET_KEY", "value": ""}))
    assert "secret_literal_forbidden" not in report.codes(), report.as_text()


def test_safety_net_stops_only_at_ifs_whitespace() -> None:
    """Het vangnet ziet een ``naam=`` met U+00A0 of U+001F erachter als waarde; alleen spatie, tab en regeleinde
    (de IFS van bash) beëindigen het woord."""
    for ws in (" ", " ", "\x1c", "\x1f"):
        assert t2_safety_net_reasons(f"x credentialSecret={ws}{ws} y"), repr(ws)
        assert t2_safety_net_reasons(f'{{"credentialSecret":"{ws}"}}'), repr(ws)
    for ws in (" ", "\t", "\n"):
        assert t2_safety_net_reasons(f"x credentialSecret={ws}y") == [], repr(ws)


# ---------------------------------------------------------------------------------------------- R-VEILIG-2
@pytest.mark.parametrize("stuk", ["$", '"\\', "users."], ids=["dollar", "aanhalingsteken-backslash", "users-punt"])
def test_text_rules_take_linear_time(stuk: str) -> None:
    """Teksten van 256 KiB zijn in minder dan 1 s getoetst, ook zonder de grens op de tekstlengte (de regels zelf
    zijn lineair; vóór het herstel duurde dit minuten)."""
    text = (stuk * (GROOT // len(stuk) + 1))[:GROOT]
    for regel in (has_password_hash, t2_setting_reasons, t2_safety_net_reasons):
        start = time.perf_counter()
        regel(text)
        assert time.perf_counter() - start < 1.0, regel.__name__
    data = met_waarden({"tekst": text})
    start = time.perf_counter()
    validate_data(data)
    assert time.perf_counter() - start < 1.0


def test_too_long_text_is_refused_fail_closed() -> None:
    """Een tekst langer dan ``MAX_SECRET_TEXT`` wordt niet getoetst maar geweigerd (fail-closed)."""
    MAX_SECRET_TEXT = rules.MAX_SECRET_TEXT
    assert secret_text_reasons("a" * MAX_SECRET_TEXT, settings=True) == []
    assert secret_text_reasons("a" * (MAX_SECRET_TEXT + 1), settings=True)
    assert secret_text_reasons("a" * (MAX_SECRET_TEXT + 1), settings=False)
    lang = met_waarden({"tekst": "a" * (MAX_SECRET_TEXT + 1)})
    assert _at(_errors(lang), "values.voorbeeld.tekst") == [CODE]
    beschrijving = custom_basis()
    beschrijving["description_nl"] = "b" * (MAX_SECRET_TEXT + 1)
    assert CODE in _at(_errors(beschrijving), "description_nl")


# ---------------------------------------------------------------------------------------------- R-VEILIG-3
@pytest.mark.parametrize("naam", ["paß", "Reisepaß", "ß", "PAß_WORD"])
def test_sharp_s_has_no_length_difference(naam: str) -> None:
    """``"paß".upper()`` is ``"PASS"`` (langer): geen uitzondering, en zo'n naam is geen geheimnaam."""
    assert is_secret_name(naam) is False
    assert is_account_name(naam) is False


@pytest.mark.parametrize("naam", ["paß", "Reisepaß", "ß"])
def test_sharp_s_at_every_place_gives_a_fixed_outcome(naam: str) -> None:
    """Als sleutel in ``values``, als naam van een naam/waarde-paar, als sleutel van een instelling in een tekst, als
    JSON-sleutel, als naam van een omgevingsvariabele (schemafout) en na het invullen: een vaste uitkomst."""
    for data in (
        met_waarden({naam: "x"}),
        met_waarden({"lijst": [{"name": naam, "value": "x"}]}),
        met_env({"name": "FLOWS", "value": f"flows.json -D {naam}=x"}),
        met_waarden({"tekst": json.dumps({naam: "x"})}),
    ):
        report, model = validate_data(data)
        assert report.valid and model is not None, report.as_text()
    schema = met_env({"name": naam, "value": "x"})
    assert "invalid_format" in _at(validate_data(schema)[0].findings, f"{ENV}.name")
    model = _model(
        met_waarden(
            {"extra": [{"name": {"param": "envnaam"}, "value": {"param": "envwaarde"}}]},
            wizard("envnaam"),
            wizard("envwaarde"),
        )
    )
    result = translate(model, dict(CATALOG_PARAMS, envnaam=naam, envwaarde="x"), CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.findings]


# ---------------------------------------------------------------------------------------------- R-VEILIG-4
def _enrollment_model() -> Manifest:
    return _model(
        met_waarden(
            {"additional_envs": [{"name": {"param": "envnaam"}, "value": {"param": "envwaarde"}}]},
            wizard("envnaam"),
            wizard("envwaarde"),
        )
    )


@pytest.mark.parametrize(
    ("naam", "waarde"),
    [
        ("HUB_ENROLLMENT_TOKEN", HASH),
        ("HUB_ENROLLMENT_TOKEN", f"-D credentialSecret={PROEFSLEUTEL}"),
        ("HUB_ENROLLMENT_TOKEN", "proef-token-zonder-betekenis"),
        ("HUB_URL", "http://ander.thuis.test:30400"),
        ("HUB_KEY_FINGERPRINT", "SHA256:proef"),
        ("hub_url", "http://ander.thuis.test:30400"),
    ],
)
def test_hub_name_from_a_parameter_is_refused_after_filling(naam: str, waarde: str) -> None:
    """Een ``HUB_*``-naam die via een parameter binnenkomt, geeft na het invullen ``reserved_env``; T1 en T2 gelden
    ook onder ``HUB_ENROLLMENT_TOKEN``. Geen payload en de waarde staat nergens."""
    params = dict(CATALOG_PARAMS, envnaam=naam, envwaarde=waarde)
    result = translate(_enrollment_model(), params, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    codes = {(f.path, f.code) for f in result.blocking()}
    assert ("values.voorbeeld.additional_envs[0].name", "reserved_env") in codes
    if waarde in (HASH, f"-D credentialSecret={PROEFSLEUTEL}"):
        assert ("values.voorbeeld.additional_envs[0].value", CODE) in codes
    _no_echo(result.model_dump_json())


def test_w3_enrollment_exception_only_for_a_value_the_translation_set() -> None:
    """De uitzondering geldt alleen voor de geheimnaamregel en alleen op een plek waar de vertaling zelf
    ``HUB_ENROLLMENT_TOKEN`` zette; T1 en T2 blijven gelden en elke andere ``HUB_*``-naam blijft onder W3."""
    envs = [
        {"name": "HUB_ENROLLMENT_TOKEN", "value": PROEFSLEUTEL},
        {"name": "HUB_URL", "value": "http://hub.thuis.test:30400"},
        {"name": "HUB_TOKEN", "value": PROEFSLEUTEL},
        {"name": "HUB_ENROLLMENT_TOKEN", "value": HASH},
        {"name": "HUB_ENROLLMENT_TOKEN", "value": PROEFSLEUTEL},
    ]
    gezet = frozenset(("values", "envs", i, "value") for i in (0, 1, 2, 3))
    findings = filled_value_findings({"envs": envs}, {}, custom_app=True, hub_at=gezet)
    assert [(f.path, f.code) for f in findings] == [
        ("values.envs[2].value", "secret_in_form"),
        ("values.envs[3].value", CODE),
        ("values.envs[4].name", "reserved_env"),
        ("values.envs[4].value", "secret_in_form"),
    ]
    _no_echo(json.dumps([f.model_dump() for f in findings]))


# ---------------------------------------------------------------------------------------------- R-VEILIG-5
@pytest.mark.parametrize("veld", ["app_id", "version", "install.method"])
def test_recognised_value_in_a_header_field_is_never_shown(
    veld: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Een herkende waarde in ``app_id``, ``version`` of ``install.method`` staat niet in het rapport, de tekst of de
    uitvoer van ``jl-manifest``."""
    data = custom_basis()
    if veld == "install.method":
        data["install"]["method"] = HASH
    else:
        data[veld] = HASH
    report, _ = validate_data(data)
    assert not report.valid
    _no_echo(report.model_dump_json())
    _no_echo(report.as_text())
    file = tmp_path / "proef.manifest.yaml"
    file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    for fmt in ("json", "text"):
        assert main(["validate", "--format", fmt, str(file)]) == 1
        out = capsys.readouterr()
        _no_echo(out.out + out.err)


def test_ordinary_header_fields_stay_in_the_report() -> None:
    report, _ = validate_data(custom_basis())
    assert report.app_id == "voorbeeld-web" and report.version and report.install_method == "custom_app"


# ---------------------------------------------------------------------------------------------- R-VEILIG-6, R-TECH-1, R-MANIFEST-1
def test_object_under_a_secret_name_inherits_the_name() -> None:
    """Wat onder een geheim- of accountnaam ligt, erft die naam (net als een lijst)."""
    letterlijk = met_waarden({"admin_password": {"x": PROEFWACHTWOORD}})
    assert _secret_codes(_refused(letterlijk), "values.voorbeeld.admin_password.x") == ["secret_literal_forbidden"]
    wizardwaarde = met_waarden({"admin_password": {"x": {"param": "pw"}}}, wizard("pw"))
    assert _secret_codes(_refused(wizardwaarde), "values.voorbeeld.admin_password.x") == [CODE]
    gewoon = met_waarden({"smtp_password": {"waarde": {"param": "pw"}}}, wizard("pw"))
    assert _secret_codes(_refused(gewoon), "values.voorbeeld.smtp_password.waarde") == ["secret_in_form"]
    getal = met_waarden({"api_key": {"lengte": 32}})
    assert _secret_codes(_refused(getal), "values.voorbeeld.api_key.lengte") == ["secret_literal_forbidden"]


@pytest.mark.parametrize("voorouder", ["admin", "adminUser", "root", "beheer", "setup"])
def test_ancestor_with_an_account_word_makes_an_account_place(voorouder: str) -> None:
    """R-TECH-1: een geheim van de Hub onder ``admin.password`` is de aanmelding van een beheerder (C-12, "strikt
    zonder": geen vault-weg voor een beheerder of dienstaccount)."""
    data = met_waarden({voorouder: {"password": {"param": "adm_pw"}}}, hub_geheim("adm_pw"))
    errors = _refused(data)
    assert _secret_codes(errors, f"values.voorbeeld.{voorouder}.password") == [CODE]
    report, _ = validate_data(data)
    assert not any(f.code == "secret_in_form" for f in report.findings if f.path.endswith(".password"))


def test_ancestor_account_word_also_counts_after_filling() -> None:
    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(hub_geheim("adm_pw"))
        d["values"]["voorbeeld"]["admin"] = {"password": {"param": "adm_pw"}}

    result = translate(_forged(catalogus_basis(), change), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    assert ("values.voorbeeld.admin.password", CODE) in {(f.path, f.code) for f in result.blocking()}


def test_ancestor_without_account_word_keeps_the_existing_rules() -> None:
    """Een geheimnaam onder een gewone sleutel naar een geheim van de Hub blijft de te accepteren waarschuwing."""
    data = met_waarden({"database": {"password": {"param": "db_pw"}}}, hub_geheim("db_pw"))
    report, _ = validate_data(data)
    assert report.valid, report.as_text()
    assert [f.path for f in report.findings if f.code == "secret_in_form"] == [
        "values.voorbeeld.database.password",
        "values.voorbeeld.db_password",
    ]


@pytest.mark.parametrize(
    ("proef", "data", "pad", "code"),
    [
        (
            "A1",
            met_env({"name": "GOTIFY_INIT", "value_from": {"param": "beheer_wachtwoord"}}, wizard("beheer_wachtwoord")),
            ENV,
            CODE,
        ),
        (
            "A2",
            met_env({"name": "APP_INIT", "value_from": {"param": "admin_password"}}, hub_geheim("admin_password")),
            ENV,
            CODE,
        ),
        (
            "A3",
            met_env(
                {"name": "APP_INIT", "value_from": {"param": "admin_password"}},
                hub_geheim("admin_password"),
                accepted=[{"code": "secret_in_form", "reden_nl": "Proef: accepteren van de vault-weg."}],
            ),
            ENV,
            CODE,
        ),
        (
            "A4",
            met_waarden({"bootstrap": {"param": "bootstrap_password"}}, wizard("bootstrap_password")),
            "values.voorbeeld.bootstrap",
            CODE,
        ),
        (
            "B1",
            met_env({"name": "DB_X", "value_from": {"param": "db_password"}}, wizard("db_password")),
            ENV,
            "secret_in_form",
        ),
        (
            "B2",
            met_waarden({"smtp": {"param": "smtp_password"}}, wizard("smtp_password")),
            "values.voorbeeld.smtp",
            "secret_in_form",
        ),
    ],
)
def test_the_parameter_name_counts_too(proef: str, data: dict[str, Any], pad: str, code: str) -> None:
    """R-MANIFEST-1: een gebruikte parameter met een accountnaam geeft ``account_gegevens_verboden`` (ook als geheim
    van de Hub, niet te accepteren); een gewone parameter met een geheimnaam geeft de fout ``secret_in_form``."""
    errors = _refused(data)
    assert _secret_codes(errors, pad) == [code], proef
    report, _ = validate_data(copy.deepcopy(data))
    assert not any(f.accepted for f in report.findings if f.path == pad), proef


def test_the_parameter_name_counts_at_a_typed_place() -> None:
    """Ook op een plek met een getypte waarde (hier een hostpoort): de naam van de parameter telt."""
    data = custom_basis()
    data["parameters"][0]["name"] = "root_token"
    data["network"]["ports"][0]["host_port"] = {"param": "root_token"}
    assert _secret_codes(_refused(data), "network.ports[0].host_port") == [CODE]


def test_the_parameter_name_counts_after_filling() -> None:
    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(hub_geheim("admin_password"))
        d["custom_app"]["env"].append({"name": "APP_INIT", "value_from": {"param": "admin_password"}})

    result = translate(_forged(custom_basis(), change), PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    assert ("custom_app.env[2].value_from", CODE) in {(f.path, f.code) for f in result.blocking()}

    def plain(d: dict[str, Any]) -> None:
        d["parameters"].append(wizard("db_password"))
        d["custom_app"]["env"].append({"name": "DB_X", "value_from": {"param": "db_password"}})

    result = translate(_forged(custom_basis(), plain), dict(PARAMS, db_password=PROEFWACHTWOORD), TARGET)
    assert result.payload is None
    assert ("custom_app.env[2].value_from", "secret_in_form") in {(f.path, f.code) for f in result.blocking()}
    _no_echo(result.model_dump_json())


# ---------------------------------------------------------------------------------------------- R-MANIFEST-2
@pytest.mark.parametrize(
    ("naam", "account"),
    [("DB_PASSWORD2", False), ("ADMIN_TOKEN1", True), ("api_key2", False), ("password1", False), ("SALT0", False)],
)
def test_a_digit_after_the_secret_word_counts(naam: str, account: bool) -> None:
    assert is_secret_name(naam) is True
    assert is_account_name(naam) is account
    errors = _refused(met_env({"name": naam.upper(), "value": PROEFSLEUTEL}))
    assert _secret_codes(errors, ENV) == ["secret_literal_forbidden"]


@pytest.mark.parametrize("naam", ["TOKENS", "PASSWORDLESS", "SECRETS_DIR", "BYPASSES"])
def test_a_letter_after_the_secret_word_still_does_not_count(naam: str) -> None:
    assert is_secret_name(naam) is False


# ---------------------------------------------------------------------------------------------- R-TECH-4
def test_hash_as_a_key_in_values_is_refused_and_hidden() -> None:
    """T1 ook op een sleutel in ``values``; het pad toont zo'n sleutel niet."""
    data = met_waarden({HASH: "x"})
    errors = _refused(data)
    assert _at(errors, f"values.voorbeeld.{VERBORGEN}") == [CODE]
    _no_echo(json.dumps([f.model_dump() for f in errors]))


def test_key_with_an_equals_sign_is_hidden_in_path_and_message() -> None:
    data = met_waarden({f"credentialSecret={PROEFSLEUTEL}": "x"})
    report, _ = validate_data(data)
    assert _secret_codes(report.findings, f"values.voorbeeld.{VERBORGEN}") == ["secret_literal_forbidden"]
    _no_echo(report.model_dump_json())
    _no_echo(report.as_text())
    pair = met_waarden({"extra": [{"name": f"ADMIN_PASSWORD_={PROEFSLEUTEL}", "value": {"param": "pw"}}]}, wizard("pw"))
    report, _ = validate_data(pair)
    assert _secret_codes(report.findings, "values.voorbeeld.extra[0].value") == [CODE]
    _no_echo(report.model_dump_json())


def test_unknown_field_with_a_hash_is_hidden() -> None:
    data = custom_basis()
    data["onbekend_" + HASH] = 1
    report, _ = validate_data(data)
    assert _at(report.findings, VERBORGEN) == ["unknown_field"]
    _no_echo(report.model_dump_json())
    _no_echo(report.as_text())


def test_translation_hides_a_key_in_its_findings() -> None:
    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(wizard("welkom"))
        d["values"]["voorbeeld"][f"credentialSecret={PROEFSLEUTEL}"] = {"param": "welkom"}

    result = translate(
        _forged(catalogus_basis(), change), dict(CATALOG_PARAMS, welkom="Welkom"), CATALOG_TARGET, trusted_app_id=True
    )
    assert result.payload is None
    assert (f"values.voorbeeld.{VERBORGEN}", "secret_in_form") in {(f.path, f.code) for f in result.blocking()}
    _no_echo(json.dumps([f.model_dump() for f in result.findings]))


def test_unknown_parameter_name_with_a_hash_is_hidden() -> None:
    """Ook de naam van een opgegeven parameter is een padsegment: een herkende naam staat er niet."""
    result = translate(_model(custom_basis()), dict(PARAMS, **{HASH: "x"}), TARGET)
    assert ("parameters." + VERBORGEN, "parameter_value_unknown") in {(f.path, f.code) for f in result.findings}
    _no_echo(json.dumps([f.model_dump() for f in result.findings]))


def test_ordinary_keys_are_shown() -> None:
    report, _ = validate_data(met_waarden({"smtp_password": PROEFSLEUTEL}))
    assert _at(report.findings, "values.voorbeeld.smtp_password") == ["secret_literal_forbidden"]


# ---------------------------------------------------------------------------------------------- R-TECH-2, R-MANIFEST-3
def test_app_name_is_not_a_place_with_a_name() -> None:
    """Uitleg (c): de naam van de app (``name`` bovenaan) telt niet bij het verbergen van een waarde in een schemafout."""
    data = custom_basis()
    data["name"] = "Proef-Password"
    data["category"] = "tekstje"
    report, _ = validate_data(data)
    (finding,) = [f for f in report.findings if f.path == "category"]
    assert finding.code == "invalid_value" and "tekstje" in finding.message


def test_yes_no_and_null_are_not_a_value() -> None:
    """Uitleg (a): ja/nee en ``null`` tellen niet als waarde (een geheim is nooit een boolean)."""
    report, _ = validate_data(met_waarden({"root_password": True, "admin_token": None}))
    assert report.valid, report.as_text()
    data = custom_basis()
    data["parameters"].append(
        {
            "name": "root_password_reset",
            "type": "boolean",
            "source": "default",
            "default": True,
            "required": False,
            "description_nl": "Proefparameter.",
        }
    )
    report, _ = validate_data(data)
    assert report.valid and "secret_literal_forbidden" not in report.codes(), report.as_text()


def test_users_key_in_step_one() -> None:
    """T2 stap 1 ziet een sleutel ``users`` met een lijst ook zonder het vangnet."""
    assert t2_setting_reasons('-D adminAuth.users=[{"username":"x"}]')
    assert t2_setting_reasons("-D adminAuth.users=[]") == []


def test_index_forms_and_app_name_are_documented() -> None:
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    assert "`['0']`" in doc
    assert "naam van de app" in doc


# ---------------------------------------------------------------------------------------------- tekst als YAML
def test_yaml_with_control_characters_round_trips_to_the_same_refusal() -> None:
    """De stuurtekens komen via YAML (zoals ``jl-manifest`` en ``preview`` lezen) op dezelfde plek aan."""
    data = met_env({"name": "APP_SECRET_KEY", "value": WITRUIMTE["us"]})
    report, model, _ = validate_text(yaml.safe_dump(data, allow_unicode=True))
    assert model is None and _at(report.findings, ENV) == ["secret_literal_forbidden"]
