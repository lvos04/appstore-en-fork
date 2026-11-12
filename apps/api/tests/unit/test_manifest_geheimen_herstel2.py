"""Herstelronde 2 na de herreview van S-P2-520 (ADR-0019, naast ADR-0016, ADR-0017 en ADR-0018).

Elke test hoort bij een bevinding van de herreview (R-VEILIG-7, R-TECH-5 tot en met -8, R-MANIFEST-4) of bij het
vervolgpunt van R-VEILIG-2 (grens op de vertaling) en is eerst rood gezien op de code van vóór dit herstel. Alleen
testwaarden; geen melding, uitvoer of vertaling noemt een testwaarde (V-094).
"""

from __future__ import annotations

import copy
import gc
import json
import os
import time
from typing import Any

import pytest

from appstore.manifest import translate as vertaling
from appstore.manifest import validate_data
from appstore.manifest.canonical import canonical_json
from appstore.manifest.findings import CODES, Finding, Severity
from appstore.manifest.model import Manifest
from appstore.manifest.rules import has_account_word, is_account_name, t2_safety_net_reasons
from appstore.manifest.translate import Target, filled_value_findings, translate
from tests.conftest import REPO_ROOT
from tests.geheimen_proeven import (
    PROEFWACHTWOORD,
    TESTWAARDEN,
    catalogus_basis,
    custom_basis,
    hub_geheim,
    met_env,
    met_waarden,
    wizard,
)

CODE = "account_gegevens_verboden"
TE_GROOT = "vertaling_te_groot"
VERBORGEN = "<verborgen>"
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
#: Plekken met een parameter in ``values`` van de fixture ``valid-catalog-1-3`` (TZ, db_password, pad, poort).
FIXTURE_PLEKKEN = 4
_PAGE = os.sysconf("SC_PAGE_SIZE")


def _rss_mib() -> float:
    with open("/proc/self/statm", encoding="ascii") as fh:
        return int(fh.read().split()[1]) * _PAGE / 2**20


def _at(findings: list[Finding], path: str) -> list[str]:
    return [f.code for f in findings if f.path == path]


def _secret_codes(findings: list[Finding], path: str) -> list[str]:
    return [c for c in _at(findings, path) if c in ("secret_literal_forbidden", "secret_in_form", CODE)]


def _model(data: dict[str, Any]) -> Manifest:
    report, model = validate_data(copy.deepcopy(data))
    assert model is not None, report.as_text()
    return model


def _refused(data: dict[str, Any]) -> list[Finding]:
    report, model = validate_data(copy.deepcopy(data))
    assert not report.valid and model is None, report.as_text()
    return [f for f in report.findings if f.severity is Severity.ERROR]


def _forged(data: dict[str, Any], change: Any) -> Manifest:
    """Buiten de validator om (verdediging in de diepte): de vertaling toetst zelf opnieuw."""
    dumped = _model(data).model_dump(mode="json")
    change(dumped)
    return Manifest.model_validate(dumped)


def _no_echo(text: str, *extra: str) -> None:
    for waarde in (*TESTWAARDEN, *extra):
        assert waarde not in text, "een testwaarde staat in de uitvoer"


# ---------------------------------------------------------------------------------------------- R-VEILIG-7, R-TECH-5
def test_long_keys_do_not_stay_in_memory() -> None:
    """300 validaties, elk met een ander onbekend veld en een andere sleutel in ``values`` van 250 000 tekens: daarna
    houdt het proces minder dan 10 MiB extra vast. Er is geen cache op tekst van willekeurige lengte (ADR-0019); met
    de cache van ADR-0018 bleef elke sleutel staan (honderden MiB)."""
    validate_data(met_waarden({"opwarmen": "x"}))
    gc.collect()
    voor = _rss_mib()
    for i in range(300):
        data = met_waarden({f"v{i:06d}" + "y" * 249_993: "z"})
        data[f"k{i:06d}" + "x" * 249_993] = 1
        validate_data(data)
        del data
    gc.collect()
    groei = _rss_mib() - voor
    assert groei < 10, f"{groei:.0f} MiB blijft staan"


def _lange_sleutel(stuk: str, lengte: int, achter: str = "") -> str:
    return (stuk * (lengte // len(stuk) + 1))[: lengte - len(achter)] + achter


@pytest.mark.parametrize("stuk", ["k", '"\\'], ids=["letters", "aanhalingsteken-backslash"])
def test_long_keys_on_many_paths_stay_linear(stuk: str) -> None:
    """Zonder cache telt elke sleutel in een pad of melding één keer per doorloop (ADR-0019): een sleutel van 60 000
    tekens met duizenden treffers eronder, of een lange geheimnaam boven duizenden parameterplekken, is in minder dan
    2 s getoetst en vertaald (met het pad per treffer opnieuw opgebouwd duurde dit minuten)."""
    sleutel = _lange_sleutel(stuk, 60_000)
    treffers = met_waarden({sleutel: {f"a{i}": "credentialSecret=x" for i in range(3000)}})
    start = time.perf_counter()
    report, _ = validate_data(treffers)
    assert time.perf_counter() - start < 2.0
    assert report.errors >= 3000
    geheim = _lange_sleutel(stuk, 60_000, "_password")
    plekken = met_waarden({geheim: {f"a{i}": {"param": "w"} for i in range(3000)}}, wizard("w"))
    start = time.perf_counter()
    report, _ = validate_data(plekken)
    assert time.perf_counter() - start < 2.0
    assert report.errors >= 3000

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(wizard("w"))
        d["values"]["voorbeeld"][sleutel] = {f"a{i}": {"param": "w"} for i in range(2000)}

    model = _forged(catalogus_basis(), change)
    start = time.perf_counter()
    result = translate(model, dict(CATALOG_PARAMS, w="x"), CATALOG_TARGET, trusted_app_id=True)
    assert time.perf_counter() - start < 2.0
    assert result.payload is None  # meer dan MAX_FILLED_PLACES plekken


# ---------------------------------------------------------------------------------------------- R-TECH-6
_ACCOUNTVORMEN = [
    ("admin", {"admin": {"param": "db_password"}}, "values.voorbeeld.admin"),
    ("admin.credentials", {"admin": {"credentials": {"param": "db_password"}}}, "values.voorbeeld.admin.credentials"),
    ("adminUser.pw", {"adminUser": {"pw": {"param": "db_password"}}}, "values.voorbeeld.adminUser.pw"),
    (
        "paar ADMIN_PW",
        {"extra": [{"name": "ADMIN_PW", "value": {"param": "db_password"}}]},
        "values.voorbeeld.extra[0].value",
    ),
]


@pytest.mark.parametrize(("vorm", "extra", "pad"), _ACCOUNTVORMEN, ids=[v[0] for v in _ACCOUNTVORMEN])
def test_secret_parameter_name_under_an_account_word_is_an_account_place(
    vorm: str, extra: dict[str, Any], pad: str
) -> None:
    """R-TECH-6 (C-12, "strikt zonder"): de plek of een voorouder heeft een accountwoord en de parameter een geheimnaam.
    Dan is het de aanmelding van een beheerder: ``account_gegevens_verboden``, ook bij een geheim dat de Hub maakt, en
    niet te accepteren (de fixture accepteert ``secret_in_form``)."""
    errors = _refused(met_waarden(extra))
    assert _secret_codes(errors, pad) == [CODE], vorm
    report, _ = validate_data(met_waarden(extra))
    assert not any(f.accepted for f in report.findings if f.path == pad), vorm


@pytest.mark.parametrize(("vorm", "extra", "pad"), _ACCOUNTVORMEN, ids=[v[0] for v in _ACCOUNTVORMEN])
def test_secret_parameter_name_under_an_account_word_gives_no_payload(
    vorm: str, extra: dict[str, Any], pad: str
) -> None:
    """W3: dezelfde vormen buiten de validator om geven geen payload en geen ``vault_refs``."""

    def change(d: dict[str, Any]) -> None:
        d["values"]["voorbeeld"].update(copy.deepcopy(extra))

    result = translate(_forged(catalogus_basis(), change), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == [], vorm
    assert (pad, CODE) in {(f.path, f.code) for f in result.blocking()}, vorm


def test_secret_parameter_name_under_an_account_word_in_a_custom_app() -> None:
    """Dezelfde regel bij een omgevingsvariabele van een Custom App (ook een plek met een naam)."""
    data = met_env({"name": "ADMIN_PW", "value_from": {"param": "db_password"}}, hub_geheim("db_password"))
    assert _secret_codes(_refused(data), ENV) == [CODE]

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(hub_geheim("db_password"))
        d["custom_app"]["env"].append({"name": "ADMIN_PW", "value_from": {"param": "db_password"}})

    result = translate(_forged(custom_basis(), change), PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    assert ("values.envs[2].value", CODE) in {(f.path, f.code) for f in result.blocking()}


def test_parameter_without_a_secret_name_under_an_account_word_follows_its_marking() -> None:
    """Grens van de regel. Tot ADR-0021 bleef een geheim van de Hub zonder geheimnaam onder ``admin`` de te accepteren
    waarschuwing; sinds ADR-0021 (R-TECH-9, binnen C-12 "strikt zonder") telt de geheimmarkering van de parameter op
    zo'n plek net als een geheimnaam: ``account_gegevens_verboden``. Een gewone parameter (zonder geheimmarkering en
    zonder geheimnaam) onder ``admin`` blijft geldig, zonder melding op die plek."""
    errors = _refused(met_waarden({"admin": {"param": "sleutel_a"}}, hub_geheim("sleutel_a")))
    assert _secret_codes(errors, "values.voorbeeld.admin") == [CODE]
    report, _ = validate_data(met_waarden({"admin": {"param": "naam_a"}}, wizard("naam_a")))
    assert report.valid, report.as_text()
    assert _at(report.findings, "values.voorbeeld.admin") == []


# ---------------------------------------------------------------------------------------------- R-TECH-7
@pytest.mark.parametrize(
    "voorouder", ["phpmyadmin", "rootless", "groot", "administration", "webroot", "ADMINISTRATION", "ownership"]
)
def test_account_word_inside_a_longer_word_does_not_count(voorouder: str) -> None:
    """R-TECH-7 (keuze van de regisseur): een accountwoord telt alleen als heel naamdeel. Een geheim van de Hub onder
    zo'n sleutel blijft de te accepteren waarschuwing, zoals vóór ADR-0018 (oude valse treffers: geldig)."""
    assert has_account_word(voorouder) is False
    for extra, pad in (
        ({voorouder: {"db": {"password": {"param": "db_password"}}}}, f"values.voorbeeld.{voorouder}.db.password"),
        ({voorouder: {"param": "db_password"}}, f"values.voorbeeld.{voorouder}"),
    ):
        report, _ = validate_data(met_waarden(extra))
        assert report.valid, report.as_text()
        assert _at(report.findings, pad) == ["secret_in_form"]


@pytest.mark.parametrize(
    "voorouder",
    [
        "admin",
        "Admin",
        "adminUser",
        "ADMINUser",
        "superAdmin",
        "admin_ui",
        "ADMIN-UI",
        "root",
        "root user",
        "beheer",
        "setup",
        "initialSync",
        "admins",
        "admin2",
        "defaultUser",
        "serviceAccount",
        "dienst.x",
    ],
)
def test_account_word_as_a_whole_part_still_counts(voorouder: str) -> None:
    """De echte gevallen blijven geweigerd: een accountwoord als heel naamdeel (gesplitst op elk teken dat geen letter
    of cijfer is, op een camelCase-grens en tussen letter en cijfer; ook met een meervouds-s)."""
    assert has_account_word(voorouder) is True
    data = met_waarden({voorouder: {"password": {"param": "db_password"}}})
    assert _secret_codes(_refused(data), f"values.voorbeeld.{voorouder}.password") == [CODE]


@pytest.mark.parametrize(
    "naam", ["ADMINISTRATOR_PASSWORD", "beheerwachtwoord", "ADMINPASSWORD", "phpmyadmin_password", "BEHEERDER_TOKEN"]
)
def test_an_account_name_itself_keeps_the_word_anywhere(naam: str) -> None:
    """Scope van ADR-0019: alleen een accountwoord in een voorouder of in een naam zonder geheimwoord moet een heel
    naamdeel zijn. Een geheimnaam met een accountwoord (accountnaam) blijft zoals ADR-0016 hem noemt."""
    assert is_account_name(naam) is True
    data = met_waarden({naam.lower(): {"param": "adm"}}, hub_geheim("adm"))
    assert _secret_codes(_refused(data), f"values.voorbeeld.{naam.lower()}") == [CODE]


def _bool_param(naam: str) -> dict[str, Any]:
    return {**wizard(naam, "boolean"), "required": False}


def test_no_name_rule_for_a_boolean_parameter() -> None:
    """R-TECH-7 (uitleg a: een geheim is nooit een boolean): de naamregels voor de naam van een parameter gelden niet
    voor type boolean. De regels voor de plek zelf blijven gelden."""
    gevallen = [
        met_waarden({"login_aan": {"param": "password_login"}}, _bool_param("password_login")),
        met_waarden({"reset": {"param": "root_password_reset"}}, _bool_param("root_password_reset")),
        met_waarden({"admin": {"login": {"param": "password_login"}}}, _bool_param("password_login")),
        met_env(
            {"name": "LOGIN_AAN", "value_from": {"param": "root_password_reset"}}, _bool_param("root_password_reset")
        ),
    ]
    for data in gevallen:
        model = _model(data)
        params = {p.name: True for p in model.parameters if p.type == "boolean"}
        if model.install.method == "custom_app":
            result = translate(model, dict(PARAMS, **params), TARGET)
        else:
            result = translate(model, dict(CATALOG_PARAMS, **params), CATALOG_TARGET, trusted_app_id=True)
        assert result.ready_for_install, [f.as_text() for f in result.blocking()]
    tekst = met_waarden({"login_aan": {"param": "password_login"}}, wizard("password_login"))
    assert _secret_codes(_refused(tekst), "values.voorbeeld.login_aan") == ["secret_in_form"]
    plek = met_waarden({"admin_password": {"param": "aan"}}, _bool_param("aan"))
    assert _secret_codes(_refused(plek), "values.voorbeeld.admin_password") == [CODE]


# ---------------------------------------------------------------------------------------------- R-TECH-8
def test_safety_net_counts_a_json_text_that_does_not_close() -> None:
    """Een JSON-tekst die niet sluit, telt als waarde (fail-closed)."""
    assert t2_safety_net_reasons('{"password":"abc')
    assert t2_safety_net_reasons('{"password":""}') == []


def test_key_with_only_an_equals_sign_is_hidden_in_path_and_message() -> None:
    """Een sleutel met een ``=`` en zonder iets dat T1 of T2 herkent, staat toch als ``<verborgen>`` in pad en
    melding (ADR-0018 besluit 6)."""
    report, _ = validate_data(met_waarden({"foo=bar": {"password": PROEFWACHTWOORD}}))
    assert _secret_codes(report.findings, f"values.voorbeeld.{VERBORGEN}.password") == ["secret_literal_forbidden"]
    report2, _ = validate_data(met_waarden({"foo=bar.password": {"param": "w"}}, wizard("w")))
    (finding,) = [f for f in report2.findings if f.path == f"values.voorbeeld.{VERBORGEN}"]
    assert finding.code == "secret_in_form" and finding.message.startswith(VERBORGEN)
    for rep in (report, report2):
        assert "foo=bar" not in rep.model_dump_json() and "foo=bar" not in rep.as_text()
        _no_echo(rep.model_dump_json())


def test_w3_refuses_a_hub_key_in_values() -> None:
    """W3: een sleutel ``HUB_*`` in de ingevulde ``values`` geeft ``reserved_env`` (verdediging in de diepte)."""
    findings = filled_value_findings({"voorbeeld": {"HUB_X": "x"}}, {}, custom_app=False)
    assert [(f.path, f.code) for f in findings] == [("values.voorbeeld.HUB_X", "reserved_env")]


# ---------------------------------------------------------------------------------------------- R-MANIFEST-4
def test_object_under_a_camelcase_secret_name_stays_strict_and_is_documented() -> None:
    """Een object onder een geheimnaam in camelCase erft die naam (geheimwoord, geen accountwoord): dat blijft strikt
    en staat onder "Bekende valse treffers", met de vaste sleutels van iX bij een catalogusapp."""
    for extra, pad in (
        ({"tokenSettings": {"ttl": 3600}}, "values.voorbeeld.tokenSettings.ttl"),
        ({"passwordPolicy": {"minLength": 8}}, "values.voorbeeld.passwordPolicy.minLength"),
        ({"token": {"ttl": 3600}}, "values.voorbeeld.token.ttl"),
    ):
        assert _secret_codes(_refused(met_waarden(extra)), pad) == ["secret_literal_forbidden"]
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    valse = doc.split("Bekende valse treffers", 1)[1].split("Catalogussynchronisatie", 1)[0]
    for woord in ("`passwordPolicy`", "`tokenSettings`", "iX", "`phpmyadmin`", "boolean", "`initialSync`"):
        assert woord in valse, woord
    verbergen = doc.split("## Geheimen en accountgegevens weigeren", 1)[1].split("- **Geheimnaam:**", 1)[0]
    assert "of onder `accounts`" in verbergen


# ---------------------------------------------------------------------------------------------- vervolgpunt R-VEILIG-2
def test_too_many_filled_places_is_refused_fail_closed() -> None:
    """Meer dan ``MAX_FILLED_PLACES`` plekken met een parameter: fail-closed ``vertaling_te_groot``, zonder payload,
    en snel (de vertaling vult daarna niets meer in)."""
    assert TE_GROOT in CODES
    waarde = ("-D a=" * 1000)[:4096]
    plekken = vertaling.MAX_FILLED_PLACES - FIXTURE_PLEKKEN
    net = met_waarden({f"k{i}": {"param": "w"} for i in range(plekken)}, wizard("w"))
    result = translate(_model(net), dict(CATALOG_PARAMS, w="x"), CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.blocking()]
    te_veel = met_waarden({f"k{i}": {"param": "w"} for i in range(5000)}, wizard("w"))
    model = _model(te_veel)
    start = time.perf_counter()
    result = translate(model, dict(CATALOG_PARAMS, w=waarde), CATALOG_TARGET, trusted_app_id=True)
    assert time.perf_counter() - start < 5.0
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    assert result.preview_hash is None
    assert [f.path for f in result.blocking() if f.code == TE_GROOT] == ["parameters"]


def test_too_large_payload_is_refused_fail_closed() -> None:
    """Een payload groter dan ``MAX_PAYLOAD_BYTES`` (canonieke JSON): fail-closed ``vertaling_te_groot``."""
    waarde = "a" * 4096
    passend = vertaling.MAX_PAYLOAD_BYTES // 4096 - 4
    net = met_waarden({f"k{i}": {"param": "w"} for i in range(passend)}, wizard("w"))
    result = translate(_model(net), dict(CATALOG_PARAMS, w=waarde), CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install and result.payload is not None, [f.as_text() for f in result.blocking()]
    assert len(canonical_json(result.payload.model_dump(mode="json"))) <= vertaling.MAX_PAYLOAD_BYTES
    groot = met_waarden({f"k{i}": {"param": "w"} for i in range(passend + 8)}, wizard("w"))
    result = translate(_model(groot), dict(CATALOG_PARAMS, w=waarde), CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    assert [f.path for f in result.blocking() if f.code == TE_GROOT] == ["payload"]
    assert waarde not in json.dumps([f.model_dump() for f in result.findings])
