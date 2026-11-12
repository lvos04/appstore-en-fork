"""Geheimen en accountgegevens worden overal geweigerd (ADR-0016 W2 en W3; ADR-0017).

Naamregels N1 ``secret_literal_forbidden``, N2 ``secret_in_form`` en N3 ``account_gegevens_verboden``; tekstregels T1
(wachtwoordhash) en T2 (instellingen zoals Node-RED ze leest, met een vangnet op de tekst zelf); W3 na het invullen in
de vertaling. Elke proef staat in ``tests/geheimen_proeven.py`` (alleen testwaarden). Geen melding, geen uitvoer van
``jl-manifest`` en geen vertaling noemt een testwaarde (V-094).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from appstore.manifest import validate_data
from appstore.manifest.cli import main
from appstore.manifest.findings import CODES, Finding, Severity
from appstore.manifest.model import Manifest
from appstore.manifest.translate import Target, translate
from tests.conftest import REPO_ROOT, load_fixture
from tests.geheimen_proeven import (
    BCRYPT_VORMEN,
    EXTRA_PARAM,
    FLOWS_ANONIEM_LEZEN,
    FLOWS_DICHT,
    HASH,
    HASH_MET_SLASH_ESCAPED,
    N4_LETTERSALT,
    NAAMPROEVEN,
    NODE_RED_DIGEST,
    NOPT_UITKOMST,
    PHC_VORMEN,
    PROEFSLEUTEL,
    PROEFWACHTWOORD,
    TEKSTPROEVEN,
    TESTWAARDEN,
    bcrypt_proef,
    catalogus_basis,
    custom_basis,
    flows,
    hub_geheim,
    json_escape_dollar,
    met_env,
    met_waarden,
    wizard,
)

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


def _errors(data: dict[str, Any]) -> list[Finding]:
    report, _ = validate_data(data)
    return [f for f in report.findings if f.severity is Severity.ERROR]


def _at(findings: list[Finding], path: str) -> list[str]:
    return [f.code for f in findings if f.path == path]


def _no_echo(text: str) -> None:
    for waarde in TESTWAARDEN:
        assert waarde not in text, "een testwaarde staat in de uitvoer"


def _flows_manifest(value: str) -> dict[str, Any]:
    return met_env({"name": "FLOWS", "value": value})


def _model(data: dict[str, Any]) -> Manifest:
    report, model = validate_data(data)
    assert model is not None, report.as_text()
    return model


# ---------------------------------------------------------------------------------------------- codes
def test_account_code_is_documented() -> None:
    assert CODE in CODES
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    assert f"| `{CODE}` |" in doc


# ---------------------------------------------------------------------------------------------- naamregels
@pytest.mark.parametrize("proef", sorted(NAAMPROEVEN))
def test_name_rules_refuse_every_way(proef: str) -> None:
    """p1 tot en met p7 (ADR-0016 "Gevolgen per repo" punt 2): elke weg geeft precies zijn eigen code op zijn plek."""
    data, expected = NAAMPROEVEN[proef]
    report, model = validate_data(copy.deepcopy(data))
    assert not report.valid and model is None, report.as_text()
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    for path, code in expected.items():
        secret_codes = [c for c in _at(errors, path) if c in ("secret_literal_forbidden", "secret_in_form", CODE)]
        assert secret_codes == [code], f"{proef} {path}: {secret_codes}\n{report.as_text()}"
    _no_echo(report.model_dump_json())
    _no_echo(report.as_text())


def test_p6_vault_way_is_closed_even_with_accepted_findings() -> None:
    """Besluit gebruiker (C-12, "strikt zonder"): ook een geheim van de Hub onder een accountnaam is een fout, en die
    is niet te accepteren; er komt geen waarschuwing ``secret_in_form`` voor die plek."""
    data, _ = NAAMPROEVEN["p6"]
    report, _ = validate_data(copy.deepcopy(data))
    assert not report.valid
    assert [f.code for f in report.findings if f.path == ENV] == [CODE]
    assert "secret_in_form" not in report.codes()
    assert all(not f.accepted for f in report.findings if f.code == CODE)


def test_p5b_stays_secret_literal_forbidden() -> None:
    data, _ = NAAMPROEVEN["p5b"]
    assert _at(_errors(copy.deepcopy(data)), ENV) == ["secret_literal_forbidden"]


@pytest.mark.parametrize("name", ["SECRETS_DIR", "TOKENS", "PASSWORDLESS", "X_NIVEAU", "secretsDir"])
def test_names_that_only_look_like_a_secret_stay_valid(name: str) -> None:
    report, _ = validate_data(met_env({"name": name, "value": "aan"}))
    assert report.valid and report.codes() == [], report.as_text()


@pytest.mark.parametrize(
    ("name", "secret", "account"),
    [
        ("APP_SECRET_KEY", True, False),
        ("DB_PASSWORD", True, False),
        ("db_password", True, False),
        ("secret_key", True, False),
        ("secretKey", True, False),
        ("passwordHash", True, False),
        ("apiKeyValue", True, False),
        ("credentialSecret", True, False),
        ("WACHTWOORD", True, False),
        ("adminAuth.users[0].password", True, False),
        ('settings["credentialSecret"]', True, False),
        ("GOTIFY_DEFAULTUSER_PASS", True, True),
        ("AUTHENTIK_BOOTSTRAP_PASSWORD", True, True),
        ("AUTHENTIK_BOOTSTRAP_TOKEN", True, True),
        ("beheer_wachtwoord", True, True),
        ("adminPassword", True, True),
        ("SERVICE_ACCOUNT_TOKEN", True, True),
        ("SECRETS_DIR", False, False),
        ("TOKENS", False, False),
        ("PASSWORDLESS", False, False),
        ("ADMIN_EMAIL", False, False),
        ("HUB_URL", False, False),
        ("HUB_KEY_FINGERPRINT", False, False),
    ],
)
def test_secret_and_account_names(name: str, secret: bool, account: bool) -> None:
    from appstore.manifest.rules import is_account_name, is_secret_name

    assert is_secret_name(name) is secret
    assert is_account_name(name) is account


def test_hub_secret_under_a_plain_secret_name_stays_a_warning() -> None:
    """``db_password``/``secret_key`` van Authentik naar een geheim van de Hub: geen fout, de bestaande waarschuwing."""
    data = catalogus_basis()
    data["parameters"].append({**hub_geheim("secret_key"), "type": "secret_ref"})
    data["values"]["voorbeeld"]["secret_key"] = {"param": "secret_key"}
    report, _ = validate_data(data)
    assert report.valid and report.strict_valid, report.as_text()
    assert sorted(f.path for f in report.findings if f.code == "secret_in_form") == [
        "values.voorbeeld.db_password",
        "values.voorbeeld.secret_key",
    ]
    assert all(f.accepted for f in report.findings if f.code == "secret_in_form")


def test_secret_name_parameter_with_default_or_example_is_refused() -> None:
    """N1 op een parameter: een geheimnaam zonder geheimmarkering met een niet-lege ``default`` of ``example``."""
    with_example = met_env({"name": "X_WAARDE", "value_from": {"param": "api_token"}})
    with_example["parameters"].append({**wizard("api_token"), "example": PROEFSLEUTEL})
    errors = _errors(with_example)
    assert _at(errors, f"parameters[{EXTRA_PARAM}].example") == ["secret_literal_forbidden"]
    marked = met_env({"name": "X_WAARDE", "value_from": {"param": "api_token"}}, hub_geheim("api_token"))
    report, _ = validate_data(marked)
    assert report.valid, report.as_text()


def test_number_under_a_secret_key_in_values_is_a_literal_secret() -> None:
    data = met_waarden({"smtp_password": 12345678})
    assert _at(_errors(data), "values.voorbeeld.smtp_password") == ["secret_literal_forbidden"]


# ---------------------------------------------------------------------------------------------- tekstregels
def test_safe_node_red_flows_stay_valid() -> None:
    """De echte (veilige) vormen van ``FLOWS`` geven geen fout: geen gebruiker, geen geheim."""
    for value in (FLOWS_DICHT, FLOWS_ANONIEM_LEZEN):
        report, _ = validate_data(_flows_manifest(value))
        assert report.valid and CODE not in report.codes(), report.as_text()


@pytest.mark.parametrize("proef", sorted(TEKSTPROEVEN))
def test_text_rules_refuse_every_form(proef: str) -> None:
    """n1 tot en met n10, ``--d=``, ``--de ``, het losse woord, camelCase, c01 tot en met c15, kr1 en kr2."""
    report, model = validate_data(_flows_manifest(TEKSTPROEVEN[proef]))
    assert model is None and not report.valid
    assert CODE in _at([f for f in report.findings if f.severity is Severity.ERROR], f"{ENV}.value"), report.as_text()
    _no_echo(report.model_dump_json())


def test_candidate_forms_are_recorded_with_the_measured_digest() -> None:
    """T2 stap 3: geen optielijst; de vijftien kandidaatvormen zijn vastgelegd met de digest waarop hun uitkomst bij
    ``nopt`` is gemeten, zodat de rooktest van jelly-leon-platform ze bij een nieuwe digest opnieuw kan meten."""
    assert NODE_RED_DIGEST == "sha256:2ccd38b4066648bd5e17430c50a2f8102c566a82fd4f2fc5c342e61664d6896f"
    assert sorted(NOPT_UITKOMST) == [f"c{i:02d}" for i in range(1, 16)]
    assert all(c in TEKSTPROEVEN for c in NOPT_UITKOMST)


def test_rules_has_no_list_of_option_forms() -> None:
    """ADR-0016 besluit 4 en "Gevolgen per repo" punt 2: T2 krijgt geen lijst van optievormen in ``rules.py``."""
    source = (REPO_ROOT / "apps" / "api" / "src" / "appstore" / "manifest" / "rules.py").read_text(encoding="utf-8")
    for vorm in ("--define", "--def=", "-D=", "--no-define", "-vD"):
        assert vorm not in source


def test_n4_with_a_letter_salt_also_hits_interpolation_and_n4b_only_the_new_rule() -> None:
    """R-EERLIJK-3 (S-P2-510): een salt die met een letter begint valt toevallig ook onder ``interpolation_forbidden``;
    met een salt die met ``.`` begint (n4b) ziet alleen de nieuwe regel hem."""
    letter = _at(_errors(_flows_manifest(N4_LETTERSALT)), f"{ENV}.value")
    assert sorted(letter) == [CODE, "interpolation_forbidden"]
    dot = _at(_errors(_flows_manifest(TEKSTPROEVEN["n4b"])), f"{ENV}.value")
    assert dot == [CODE]


@pytest.mark.parametrize("vorm", sorted(BCRYPT_VORMEN))
def test_every_bcrypt_form_is_a_hash_in_any_text(vorm: str) -> None:
    """T1 op elke tekst (hier ``description_nl``), met de vormen van ADR-0017 die de bcrypt van Node-RED aanneemt."""
    data = custom_basis()
    data["description_nl"] = f"Proefbeschrijving met {BCRYPT_VORMEN[vorm]} erin."
    errors = _errors(data)
    assert _at(errors, "description_nl") == [CODE]


@pytest.mark.parametrize("vorm", sorted(PHC_VORMEN))
def test_phc_hashes_are_refused(vorm: str) -> None:
    data = custom_basis()
    data["custom_app"]["env"].append({"name": "X_HASH", "value": PHC_VORMEN[vorm]})
    assert CODE in _at(_errors(data), f"{ENV}.value")


def test_hash_in_a_parameter_default_and_in_post_install_check() -> None:
    data = met_env({"name": "X_WAARDE", "value_from": {"param": "instelling"}})
    data["parameters"].append({**wizard("instelling"), "source": "default", "default": f"x={HASH}"})
    data["post_install_checks"] = [{"id": "proef", "soort": "handmatig", "beschrijving_nl": f"Zie {HASH} hier."}]
    errors = _errors(data)
    assert _at(errors, f"parameters[{EXTRA_PARAM}].default") == [CODE]
    assert _at(errors, "post_install_checks[0].beschrijving_nl") == [CODE]


def test_escaped_slash_does_not_hide_a_hash() -> None:
    """c13: ``\\/`` in een hash breekt T1 niet; na het oplossen van de escapes wordt de hash herkend."""
    from appstore.manifest.rules import has_password_hash

    assert not has_password_hash(HASH_MET_SLASH_ESCAPED)
    report, _ = validate_data(_flows_manifest(flows(f'-D extra="{HASH_MET_SLASH_ESCAPED}"')))
    assert CODE in report.codes()


def test_hash_with_escaped_dollars_in_values_text() -> None:
    data = met_waarden({"config": '{"wachtwoordHash":"' + json_escape_dollar(HASH) + '"}'})
    assert CODE in _at(_errors(data), "values.voorbeeld.config")


@pytest.mark.parametrize("proef", ["n7", "n9"])
def test_safety_net_alone_catches_n7_and_n9(proef: str) -> None:
    """T2 stap 2: het vangnet vindt de gebruiker met de ge-escapete hash ook zonder stap 1."""
    from appstore.manifest.rules import t2_safety_net_reasons

    assert t2_safety_net_reasons(TEKSTPROEVEN[proef])


@pytest.mark.parametrize("proef", ["n7", "n9"])
def test_safety_net_catches_a_form_that_step_one_does_not_see(proef: str) -> None:
    """Een scheidingsteken dat bash niet kent (verticale tab) maakt van ``-D adminAuth=…`` één woord zonder instelling;
    alleen het vangnet ziet de gebruiker dan, en de validator weigert toch."""
    from appstore.manifest.rules import t2_setting_reasons

    text = (
        TEKSTPROEVEN[proef]
        .replace("-D adminAuth=", "-D\x0badminAuth=")
        .replace("--define adminAuth=", "-D\x0badminAuth=")
    )
    assert t2_setting_reasons(text.split(" -D nodesIncludes")[0]) == []
    assert CODE in _at(_errors(_flows_manifest(text)), f"{ENV}.value")


def test_step_one_without_safety_net_catches_the_settings() -> None:
    """T2 stap 1 los: elke instelling met een geheimnaam, gebruikers of ``@`` wordt herkend."""
    from appstore.manifest.rules import t2_setting_reasons

    for proef in ("n2", "n5", "n6", "n6b", "n8", "n10", "c01", "c08", "c11", "c12", "c15", "camelcase", "n3"):
        assert t2_setting_reasons(TEKSTPROEVEN[proef]), proef
    assert t2_setting_reasons(FLOWS_DICHT) == []


# ---------------------------------------------------------------------------------------------- ADR-0017
@pytest.mark.parametrize(
    "midden",
    [
        "-D k=users -D adminAuth[msg.k][0].username=proef",
        '-D k=users -D adminAuth[msg["k"]][0].username=proef',
        "-D nodesIncludes[0]=05-junction.js",
        '-D adminAuth["0"]=x',
        "-D a.0.b=x",
    ],
)
def test_cross_reference_or_index_in_a_define_key_is_refused(midden: str) -> None:
    """ADR-0017: een kruisverwijzing (``[msg.``, ``[msg[``) of een index in de sleutel van een instelling, ook zonder
    hash en zonder het woord ``users`` of ``password`` in de tekst."""
    from appstore.manifest.rules import t2_setting_reasons

    text = flows(midden)
    assert t2_setting_reasons(text)
    assert CODE in _at(_errors(_flows_manifest(text)), f"{ENV}.value")


def test_kr1_needs_adr_0017_and_kr2_is_caught_by_t1() -> None:
    """kr1 (``$2x$``) en kr2 (``$2y$``): zonder de uitbreiding van ADR-0017 ging kr1 door (herreview van ADR-0016)."""
    from appstore.manifest.rules import has_password_hash

    assert has_password_hash(bcrypt_proef("$2x$10$")) and has_password_hash(bcrypt_proef("$2y$10$"))
    for proef in ("kr1", "kr2"):
        assert CODE in validate_data(_flows_manifest(TEKSTPROEVEN[proef]))[0].codes()


# ---------------------------------------------------------------------------------------------- bestaande gevallen
def test_existing_fixtures_keep_their_outcome() -> None:
    """Eis 8: de fixtures weigeren of accepteren zoals voorheen (compose-forbidden en ex47 blijven falen)."""
    expected = {
        "compose-forbidden.manifest.yaml": False,
        "ex47-privileged-hostmount.manifest.yaml": False,
        "valid-catalog-1-3.manifest.yaml": True,
        "valid-catalog.manifest.yaml": True,
        "valid-custom-upstream.manifest.yaml": True,
    }
    for name, valid in expected.items():
        report, _ = validate_data(load_fixture(name))
        assert report.valid is valid, name
        assert CODE not in report.codes(), name


# ---------------------------------------------------------------------------------------------- schemafouten
def test_schema_errors_do_not_echo_a_recognised_secret() -> None:
    """ADR-0016 W2: ``invalid_format``/``invalid_value`` noemen de waarde niet als W2 haar herkent."""
    data = custom_basis()
    data["custom_app"]["env"].append({"name": f"DB_PASSWORD={PROEFWACHTWOORD}", "value": "x"})
    data["parameters"][0]["type"] = HASH
    data["image"]["digest"] = HASH
    report, _ = validate_data(data)
    assert "invalid_format" in _at(report.findings, "custom_app.env[2].name")
    assert "invalid_value" in _at(report.findings, "parameters[0].type")
    assert "invalid_format" in _at(report.findings, "image.digest")
    _no_echo(report.model_dump_json())


def test_schema_errors_still_name_an_ordinary_value() -> None:
    data = custom_basis()
    data["parameters"][0]["type"] = "tekstje"
    report, _ = validate_data(data)
    (finding,) = [f for f in report.findings if f.path == "parameters[0].type"]
    assert "tekstje" in finding.message


# ---------------------------------------------------------------------------------------------- W3: vertaling
def _wizard_text_manifest() -> dict[str, Any]:
    return met_env({"name": "WELKOM", "value_from": {"param": "welkomtekst"}}, wizard("welkomtekst"))


@pytest.mark.parametrize(
    "value",
    [HASH, f"-D credentialSecret={PROEFSLEUTEL}", '{"users":[{"username":"x"}]}'],
    ids=["hash", "instelling", "gebruikers"],
)
def test_wizard_value_with_a_hash_or_setting_is_refused_at_translation(value: str) -> None:
    """W3: na het invullen opnieuw T1 en T2; geen payload en de waarde staat nergens in het resultaat."""
    model = _model(_wizard_text_manifest())
    result = translate(model, dict(PARAMS, welkomtekst=value), TARGET)
    assert result.payload is None and not result.ready_for_install and result.preview_hash is None
    assert [f.path for f in result.findings if f.code == CODE] == ["values.envs[2].value"]
    assert value not in result.model_dump_json()
    ok = translate(model, dict(PARAMS, welkomtekst="Welkom thuis"), TARGET)
    assert ok.ready_for_install, [f.as_text() for f in ok.findings]


def _forged(data: dict[str, Any], change: Any) -> Manifest:
    """Buiten de validator om (verdediging in de diepte): de vertaling toetst zelf opnieuw."""
    dumped = _model(data).model_dump(mode="json")
    change(dumped)
    return Manifest.model_validate(dumped)


def test_translation_refuses_a_secret_name_from_a_plain_parameter() -> None:
    def change(d: dict[str, Any]) -> None:
        d["custom_app"]["env"][1]["name"] = "APP_DB_PASSWORD"

    result = translate(_forged(custom_basis(), change), PARAMS, TARGET)
    assert result.payload is None
    assert [(f.path, f.code) for f in result.blocking()] == [("values.envs[1].value", "secret_in_form")]
    assert PARAMS["public_url"] not in json.dumps([f.message for f in result.findings])


def test_translation_refuses_an_account_name_even_as_vault_reference() -> None:
    data = met_env({"name": "APP_KEY", "value_from": {"param": "api_key"}}, hub_geheim("api_key"))

    def change(d: dict[str, Any]) -> None:
        d["custom_app"]["env"][2]["name"] = "ADMIN_PASSWORD"

    result = translate(_forged(data, change), PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    assert [(f.path, f.code) for f in result.blocking()] == [("values.envs[2].value", CODE)]


def test_translation_refuses_account_values_in_catalog_values() -> None:
    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(wizard("welkom"))
        d["values"]["voorbeeld"]["bootstrap_password"] = {"param": "welkom"}

    result = translate(
        _forged(catalogus_basis(), change),
        dict(CATALOG_PARAMS, welkom=PROEFWACHTWOORD),
        CATALOG_TARGET,
        trusted_app_id=True,
    )
    assert result.payload is None
    assert [(f.path, f.code) for f in result.blocking()] == [("values.voorbeeld.bootstrap_password", CODE)]
    assert PROEFWACHTWOORD not in result.model_dump_json()


def test_hub_secret_vault_reference_still_translates() -> None:
    """Geen fout: de eigen vault-verwijzing van de vertaling onder ``db_password`` (geen accountnaam)."""
    result = translate(_model(catalogus_basis()), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.findings]


def test_w3_lets_only_the_enrollment_token_through() -> None:
    """Contract kop 3.1: alleen ``HUB_ENROLLMENT_TOKEN`` mag; ``HUB_TOKEN``, ``HUB_SECRET`` en elke andere geheimnaam
    niet. De vertaling zet ``HUB_*`` (nog) niet zelf, dus de uitzondering wordt op een ingevulde ``envs``-lijst getoetst.
    ADR-0018: de uitzondering geldt alleen op een plek waar de vertaling de naam zelf zette (``hub_at``), en een
    ``HUB_*``-naam die zij niet zette, geeft ``reserved_env``."""
    from appstore.manifest.translate import filled_value_findings

    envs = [
        {"name": "HUB_ENROLLMENT_TOKEN", "value": PROEFSLEUTEL},
        {"name": "HUB_TOKEN", "value": PROEFSLEUTEL},
        {"name": "HUB_SECRET", "value": PROEFSLEUTEL},
        {"name": "hub_enrollment_token", "value": PROEFSLEUTEL},
        {"name": "HUB_URL", "value": "http://hub.thuis.test:30400"},
        {"name": "HUB_KEY_FINGERPRINT", "value": "SHA256:proef"},
        {"name": "APP_TOKEN", "value": PROEFSLEUTEL},
    ]
    gezet = frozenset(("values", "envs", idx, "value") for idx in range(len(envs)))
    findings = filled_value_findings({"envs": envs}, {}, custom_app=True, hub_at=gezet)
    assert [(f.path, f.code) for f in findings] == [
        ("values.envs[1].value", "secret_in_form"),
        ("values.envs[2].value", "secret_in_form"),
        ("values.envs[3].value", "secret_in_form"),
        ("values.envs[6].value", "secret_in_form"),
    ]
    _no_echo(json.dumps([f.model_dump() for f in findings]))
    zelf_niet = filled_value_findings({"envs": envs}, {}, custom_app=True)
    assert [f.path for f in zelf_niet if f.code == "reserved_env"] == [f"values.envs[{i}].name" for i in range(6)]
    assert ("values.envs[0].value", "secret_in_form") in {(f.path, f.code) for f in zelf_niet}
    pairs = {"extra": [{"name": "HUB_ENROLLMENT_TOKEN", "value": PROEFSLEUTEL}, {"name": "HUB_SECRET", "value": "x"}]}
    gezet_paar = frozenset({("values", "extra", 0, "value"), ("values", "extra", 1, "value")})
    assert [f.path for f in filled_value_findings(pairs, {}, custom_app=False, hub_at=gezet_paar)] == [
        "values.extra[1].value"
    ]


# ---------------------------------------------------------------------------------------------- jl-manifest
@pytest.mark.parametrize("proef", ["p1", "p2", "p5b", "p7"])
def test_cli_never_prints_the_test_value(proef: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Bij ``validate`` en ``translate`` (zonder payload) staat de testwaarde nergens in de uitvoer."""
    data, _ = NAAMPROEVEN[proef]
    file = tmp_path / "proef.manifest.yaml"
    file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    assert main(["validate", "--strict", "--format", "json", str(file)]) == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["result"] == "ongeldig"
    _no_echo(out.out + out.err)
    params = tmp_path / "params.json"
    params.write_text(json.dumps({**PARAMS, **CATALOG_PARAMS, "beheer_wachtwoord": PROEFWACHTWOORD}), encoding="utf-8")
    code = main(
        ["translate", str(file), "--params", str(params), "--truenas-version", "25.10.1", "--ix-app-version", "1.4.8"]
    )
    out = capsys.readouterr()
    assert code == 1 and "Vertalen geweigerd" in out.err and "payload" not in out.out
    _no_echo(out.out + out.err)


def test_cli_translate_refuses_a_wizard_hash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = tmp_path / "proef.manifest.yaml"
    file.write_text(yaml.safe_dump(_wizard_text_manifest(), sort_keys=False), encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(json.dumps(dict(PARAMS, welkomtekst=HASH)), encoding="utf-8")
    code = main(
        ["translate", str(file), "--params", str(params), "--truenas-version", "25.10.1", "--ix-app-version", "1.4.8"]
    )
    out = capsys.readouterr()
    printed = json.loads(out.out)
    assert code == 1 and printed["payload"] is None and printed["preview_hash"] is None
    assert CODE in [f["code"] for f in printed["findings"]]
    _no_echo(out.out + out.err)
