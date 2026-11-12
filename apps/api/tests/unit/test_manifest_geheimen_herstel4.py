"""Herstelronde 4 na de derde review van S-P2-520 (ADR-0021, naast ADR-0016 tot en met ADR-0020).

Elke test hoort bij een bevinding van die review (R-VEILIG-8 en -9, R-TECH-9, -10 en -11, R-MANIFEST-5) en is eerst
rood gezien op de code van vóór dit herstel (78e24a6). De geheugentests meten de PIEK (tracemalloc), niet alleen wat
blijft staan, en draaien onder een eigen grens op het adresgeheugen: komt de fout terug, dan faalt de test met een
MemoryError in plaats van het geheugen van de machine te vullen. Alleen testwaarden; geen melding, uitvoer of vertaling
noemt een testwaarde (V-094).
"""

from __future__ import annotations

import contextlib
import copy
import gc
import os
import random
import resource
import time
import tracemalloc
from collections.abc import Callable, Iterator
from functools import partial
from typing import Any

import pytest

from appstore.manifest import validate_data
from appstore.manifest.findings import MAX_SHOWN_PATH, Finding, Severity
from appstore.manifest.model import Manifest
from appstore.manifest.rules import (
    ACCOUNT_WORDS,
    SHOWN_KEY_MAX,
    PathNode,
    has_account_word,
    is_account_name,
    path_segments,
    render_shown,
    secret_text_findings,
    shown_key,
    shown_path,
)
from appstore.manifest.translate import Target, filled_value_findings, translate
from tests.conftest import REPO_ROOT
from tests.geheimen_proeven import (
    TESTWAARDEN,
    catalogus_basis,
    custom_basis,
    hub_geheim,
    met_env,
    met_waarden,
    wizard,
)

CODE = "account_gegevens_verboden"
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
ACC = [{"code": "secret_in_form", "reden_nl": "Geheim dat de Hub maakt (proef)."}]
_PAGE = os.sysconf("SC_PAGE_SIZE")
#: De vorm uit R-VEILIG-8: een sleutel van 65536 tekens met één teken buiten het BMP (Python gebruikt dan 4 bytes per
#: teken) en daaronder een lijst van 98 000 nullen; als compacte JSON net onder de grens van 256 KiB.
LANGE_SLEUTEL = "k" * 65535 + "\U0001f600"
VEEL = 98_000
PIEK_GRENS_MIB = 50
#: Een tweede vorm: 26 geneste sleutels van elk 256 tekens (die staan voluit in een pad). Het pad naar de lijst is dan
#: ruim 6600 tekens; per lijstitem vooraf opgebouwd is dat 98 000 keer 26 KiB, ongeveer 2,5 GiB.
GENESTE_SLEUTELS = [f"{i:02d}" + "n" * 253 + "\U0001f600" for i in range(26)]


def _genest(blad: Any) -> dict[str, Any]:
    """``blad`` onder de 26 geneste sleutels van :data:`GENESTE_SLEUTELS`."""
    for sleutel in reversed(GENESTE_SLEUTELS):
        blad = {sleutel: blad}
    assert isinstance(blad, dict)
    return blad


#: (naam, inhoud van ``values.voorbeeld``): één lange sleutel of veel geneste sleutels, boven een lange lijst.
_VORMEN: list[tuple[str, Callable[[Any], dict[str, Any]]]] = [
    ("lange-sleutel", lambda lijst: {LANGE_SLEUTEL: lijst}),
    ("geneste-sleutels", lambda lijst: _genest({"lijst": lijst})),
]


@contextlib.contextmanager
def _geheugengrens(extra_mib: int = 1024) -> Iterator[None]:
    """Zachte grens op het adresgeheugen van dit proces: wat er nu is plus ``extra_mib``. Zo eindigt een terugkerende
    geheugenfout (tot 25 GiB voor één manifest) in een MemoryError en niet in een machine zonder geheugen."""
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    with open("/proc/self/statm", encoding="ascii") as fh:
        grens = int(fh.read().split()[0]) * _PAGE + extra_mib * 2**20
    if hard != resource.RLIM_INFINITY:
        grens = min(grens, hard)
    resource.setrlimit(resource.RLIMIT_AS, (grens, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _piek_mib(werk: Callable[[], Any]) -> tuple[float, Any]:
    """Piek van het geheugen (tracemalloc) tijdens ``werk``, in MiB, met de uitkomst van ``werk``."""
    gc.collect()
    tracemalloc.start()
    try:
        with _geheugengrens():
            uit = werk()
        return tracemalloc.get_traced_memory()[1] / 2**20, uit
    finally:
        tracemalloc.stop()


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
    assert not report.valid and not report.strict_valid and model is None, report.as_text()
    return [f for f in report.findings if f.severity is Severity.ERROR]


def _forged(data: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> Manifest:
    """Buiten de validator om (verdediging in de diepte): de vertaling toetst zelf opnieuw."""
    dumped = _model(data).model_dump(mode="json")
    change(dumped)
    return Manifest.model_validate(dumped)


def _no_echo(text: str, *extra: str) -> None:
    for waarde in (*TESTWAARDEN, *extra):
        assert waarde not in text, "een testwaarde staat in de uitvoer"


def _geheim(naam: str, type_: str = "string", source: str = "generated") -> dict[str, Any]:
    return {**hub_geheim(naam), "type": type_, "source": source}


# ---------------------------------------------------------------------------------------------- R-VEILIG-8
@pytest.mark.parametrize(("vorm", "maak"), _VORMEN, ids=[v[0] for v in _VORMEN])
def test_peak_memory_of_the_text_rules_over_a_long_list(vorm: str, maak: Callable[[Any], dict[str, Any]]) -> None:
    """R-VEILIG-8: ``secret_text_findings`` zet per lijstitem geen eigen kopie van het pad op de stapel. Het pad wordt
    pas bij een melding gemaakt (ADR-0021): één sleutel van 65536 tekens, of 26 geneste sleutels van 256 tekens, boven
    98 000 items blijft onder 50 MiB (met het pad vooraf per knooppunt: gigabytes)."""
    for item in (0, "a"):
        data = {"values": maak([item] * VEEL)}
        piek, uit = _piek_mib(partial(secret_text_findings, data))
        assert uit == [], vorm
        assert piek < PIEK_GRENS_MIB, f"{vorm}: piek {piek:.0f} MiB"


def test_peak_memory_of_validate_data_with_a_long_key_over_a_long_list() -> None:
    """R-VEILIG-8: hetzelfde manifest via ``validate_data`` (de weg van het JSON-veld van
    ``POST /catalog/v1/validate``, ook als het schema faalt): piek onder 50 MiB, geen MemoryError."""
    piek, (report, model) = _piek_mib(lambda: validate_data({LANGE_SLEUTEL: [0] * VEEL}))
    assert model is None and "unknown_field" in report.codes()
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"


_GELDIG = [
    ("lange-sleutel-getallen", _VORMEN[0][1], 0),
    ("lange-sleutel-teksten", _VORMEN[0][1], "a"),
    ("geneste-sleutels-getallen", _VORMEN[1][1], 0),
]


@pytest.mark.parametrize(("vorm", "maak", "item"), _GELDIG, ids=[g[0] for g in _GELDIG])
def test_peak_memory_of_validate_and_translate_of_a_valid_manifest(
    vorm: str, maak: Callable[[Any], dict[str, Any]], item: Any
) -> None:
    """R-VEILIG-8: een GELDIG catalogusmanifest met dezelfde sleutels en lijst in ``values``: valideren en vertalen
    blijven elk onder 50 MiB (``_substitute``, ``filled_value_findings`` en de controle op vault-verwijzingen bouwen
    het pad ook pas bij een melding)."""
    data = met_waarden(maak([item] * VEEL))
    piek, (report, model) = _piek_mib(lambda: validate_data(data))
    assert model is not None, report.as_text()[:2000]
    assert piek < PIEK_GRENS_MIB, f"validate: piek {piek:.0f} MiB"
    piek, result = _piek_mib(lambda: translate(model, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True))
    assert piek < PIEK_GRENS_MIB, f"translate: piek {piek:.0f} MiB"
    codes = {f.code for f in result.blocking()}
    assert codes <= {"vertaling_te_groot"}, codes  # de payload is rond de grens van 256 KiB; nooit een andere fout
    assert (result.payload is None) == bool(codes)


def test_peak_memory_of_the_filled_values_check() -> None:
    """R-VEILIG-8: W3 (``filled_value_findings``) rechtstreeks, met één melding onder de sleutels: het pad staat alleen
    in die ene melding, voor één lange sleutel (``<verborgen>``) en voor veel geneste sleutels (ingekort)."""
    lijst = [0] * VEEL + [{"password": "x"}]
    values = {"voorbeeld": {LANGE_SLEUTEL: lijst}}
    piek, findings = _piek_mib(lambda: filled_value_findings(values, {}, custom_app=False))
    assert [(f.path, f.code) for f in findings] == [
        (f"values.voorbeeld.{VERBORGEN}[{VEEL}].password", "secret_in_form")
    ]
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"
    genest = {"voorbeeld": _genest({"lijst": lijst})}
    piek, findings = _piek_mib(lambda: filled_value_findings(genest, {}, custom_app=False))
    pad = "values.voorbeeld." + ".".join(GENESTE_SLEUTELS) + f".lijst[{VEEL}].password"
    assert [(f.path, f.code) for f in findings] == [(pad[:MAX_SHOWN_PATH] + "…", "secret_in_form")]
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"


def test_the_linked_path_gives_the_same_text_as_the_path_built_up_front() -> None:
    """ADR-0021: het pad uit de gelinkte keten (``shown_path``) is teken voor teken het pad dat eerder vooraf werd
    opgebouwd (``render_shown``), ook met een lege sleutel, een sleutel met punten, een verborgen sleutel en een pad
    dat met een lijstpositie begint; ``path_segments`` geeft de plek terug zoals zij erin ging."""
    rng = random.Random(7)
    sleutels = ["", "a", "b.c", "x=y", "k" * 300, "password", ".", "[0]", "n" * 256]
    for _ in range(3000):
        segmenten = tuple(rng.choice([rng.choice(sleutels), rng.randrange(3)]) for _ in range(rng.randrange(0, 7)))
        node: PathNode | None = None
        for segment in segmenten:
            node = PathNode(node, segment, isinstance(segment, int))
        assert path_segments(node) == segmenten
        assert shown_path(node) == render_shown(segmenten), segmenten
    assert shown_path(PathNode(PathNode(None, "values"), 7)) == "values.7"  # een getal als sleutel is geen positie


def test_a_key_longer_than_the_limit_is_shown_in_one_fixed_form() -> None:
    """ADR-0021: een sleutel van meer dan ``SHOWN_KEY_MAX`` (256) tekens staat in een pad of melding als
    ``<verborgen>``; een sleutel van precies 256 tekens staat er zoals hij is. De regels zelf lezen de hele sleutel."""
    assert SHOWN_KEY_MAX == 256
    kort, lang = "a" * 256, "b" * 257
    assert shown_key(kort) == kort and shown_key(lang) == VERBORGEN
    report, _ = validate_data(met_waarden({kort: {"password": "x"}, lang: {"password": "x"}, "z" * 300: 1}))
    paden = {f.path for f in report.findings if f.code == "secret_literal_forbidden"}
    assert paden == {f"values.voorbeeld.{kort}.password", f"values.voorbeeld.{VERBORGEN}.password"}
    assert lang not in report.model_dump_json() and lang not in report.as_text()
    onbekend, _ = validate_data({lang: 1})
    assert (VERBORGEN, "unknown_field") in {(f.path, f.code) for f in onbekend.findings}
    assert lang not in onbekend.model_dump_json()
    # de naamregels lezen de lange sleutel wel: een geheimnaam van 300 tekens blijft een geheimplek
    geheim = "c" * 291 + "_password"
    fouten = _refused(met_waarden({geheim: "x"}))
    assert _secret_codes(fouten, f"values.voorbeeld.{VERBORGEN}") == ["secret_literal_forbidden"]


def test_answer_under_a_long_key_stays_small() -> None:
    """Vervolgpunt van R-VEILIG-8 (de grootte van het antwoord): een sleutel van 60 000 tekens met 3000 treffers
    eronder gaf een rapport plus tekst van 346 MiB, omdat elke melding het hele pad droeg. Nu staat de sleutel er als
    ``<verborgen>``: rapport en tekst samen blijven klein en de piek onder 50 MiB. ADR-0022: sinds de grens van 1000
    meldingen per rapport met 900 treffers (alle in het rapport) en een strengere grens op het antwoord: met
    ``<verborgen>`` is het ongeveer 0,7 MiB, met de sleutel voluit (dan alleen door de padgrens ingekort) ongeveer
    2,5 MiB; daarom onder 1 MiB, en de sleutel staat nergens in rapport of tekst."""
    sleutel = "s" * 60_000
    data = met_waarden({sleutel: {f"a{i}": "credentialSecret=x" for i in range(900)}})

    def werk() -> tuple[int, int, bool]:
        report, _ = validate_data(data)
        assert "te_veel_meldingen" not in report.codes()
        json_tekst, tekst = report.model_dump_json(), report.as_text()
        return report.errors, len(json_tekst) + len(tekst), "s" * 300 in json_tekst + tekst

    piek, (fouten, grootte, sleutel_zichtbaar) = _piek_mib(werk)
    assert fouten >= 900
    assert not sleutel_zichtbaar
    assert grootte < 2**20, f"antwoord {grootte / 2**20:.1f} MiB"
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"


def test_a_long_path_is_shortened_in_a_finding() -> None:
    """ADR-0021: ook met sleutels van hooguit 256 tekens kan een pad lang worden (32 niveaus). Een pad van meer dan
    ``MAX_SHOWN_PATH`` tekens staat ingekort in de melding (het begin en ``…``); zo is de grootte van één melding
    begrensd (het aantal meldingen per rapport begrenst ADR-0022; daarom 900 treffers). Een korter pad staat er zoals
    het is."""
    assert MAX_SHOWN_PATH == 1024
    values: dict[str, Any] = {}
    node = values
    for i in range(8):
        node[f"{i}" + "n" * 255] = {}
        node = node[f"{i}" + "n" * 255]
    node.update({f"a{i}": "credentialSecret=x" for i in range(900)})
    report, _ = validate_data(met_waarden({"diep": values}))
    paden = [f.path for f in report.findings if f.code == CODE]
    assert len(paden) == 900
    assert all(len(p) == MAX_SHOWN_PATH + 1 and p.endswith("…") for p in paden)
    assert all(p.startswith("values.voorbeeld.diep.0nnn") for p in paden)
    assert len(report.model_dump_json()) + len(report.as_text()) < 12 * 2**20
    kort, _ = validate_data(met_waarden({"n" * 256: {"password": "x"}}))
    assert f"values.voorbeeld.{'n' * 256}.password" in {f.path for f in kort.findings}


def test_long_keys_with_many_findings_stay_fast() -> None:
    """De rekentijd blijft lineair nu het pad per melding wordt gemaakt: 30 000 treffers onder 24 geneste sleutels van
    256 tekens zijn in minder dan 5 s getoetst (elke sleutel komt uit de cache of is direct ``<verborgen>``). ADR-0022:
    het rapport van ``validate_data`` stopt na 1000 meldingen; de tekstregels zelf (``secret_text_findings``, zonder
    de grens per rapport) maken nog alle 30 000 meldingen, en die tijd wordt gemeten."""
    values: dict[str, Any] = {}
    node = values
    for i in range(24):
        node[f"{i:02d}" + "n" * 254] = {}
        node = node[f"{i:02d}" + "n" * 254]
    node.update({f"a{i}": "credentialSecret=x" for i in range(30_000)})
    data = met_waarden({"diep": values})
    start = time.perf_counter()
    gevonden = secret_text_findings(data)
    assert time.perf_counter() - start < 5.0
    assert len(gevonden) >= 30_000
    start = time.perf_counter()
    report, _ = validate_data(data)
    assert time.perf_counter() - start < 5.0
    assert len(report.findings) == 1001 and "te_veel_meldingen" in report.codes()


# ---------------------------------------------------------------------------------------------- R-VEILIG-9, R-TECH-10
_BOOLEAN_GEHEIMEN = [
    ("B1", "catalogus", "init", _geheim("admin_password", "boolean")),
    ("B2", "custom", "INIT_VALUE", _geheim("admin_password", "boolean")),
    ("B3", "catalogus", "init", _geheim("beheer_wachtwoord", "boolean")),
    ("B4", "catalogus", "init", _geheim("root_token", "boolean", "secret")),
    ("B5", "catalogus", "admin.x", _geheim("db_password2", "boolean")),
]


def _met_plek(soort: str, plek: str, param: dict[str, Any]) -> tuple[dict[str, Any], str]:
    ref = {"param": param["name"]}
    if soort == "custom":
        return met_env({"name": plek, "value_from": ref}, param, accepted=ACC), ENV
    extra: Any = ref
    for deel in reversed(plek.split(".")):
        extra = {deel: extra}
    return met_waarden(extra, param), f"values.voorbeeld.{plek}"


@pytest.mark.parametrize(("vorm", "soort", "plek", "param"), _BOOLEAN_GEHEIMEN, ids=[b[0] for b in _BOOLEAN_GEHEIMEN])
def test_boolean_with_a_secret_marking_falls_under_the_name_rules(
    vorm: str, soort: str, plek: str, param: dict[str, Any]
) -> None:
    """R-VEILIG-9, R-TECH-10: de uitzondering voor type boolean geldt alleen zonder geheimmarkering. Een Hub-geheim van
    type boolean met een accountnaam (of met een geheimnaam op een plek met een accountwoord) is
    ``account_gegevens_verboden``: niet te accepteren, ook niet in ``--strict``."""
    data, pad = _met_plek(soort, plek, param)
    assert _secret_codes(_refused(data), pad) == [CODE], vorm
    report, _ = validate_data(data)
    assert not any(f.accepted for f in report.findings if f.path == pad), vorm


@pytest.mark.parametrize(("vorm", "soort", "plek", "param"), _BOOLEAN_GEHEIMEN, ids=[b[0] for b in _BOOLEAN_GEHEIMEN])
def test_boolean_with_a_secret_marking_gives_no_payload_with_the_same_code(
    vorm: str, soort: str, plek: str, param: dict[str, Any]
) -> None:
    """De vertaling geeft buiten de validator om geen payload en geen ``vault_refs``. Bij een accountnaam (B1 tot en
    met B4) is de code dezelfde als bij de validator, ``account_gegevens_verboden``; bij B5 (een geheimnaam op een plek
    met een accountwoord) weigert de vertaling het geheim al vóór het invullen (``parameter_value_not_allowed``: een
    vault-verwijzing kan niet bij type boolean), dus W3 ziet daar geen waarde."""
    data, _ = _met_plek(soort, plek, param)
    basis = custom_basis() if soort == "custom" else catalogus_basis()

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(copy.deepcopy(param))
        if soort == "custom":
            d["custom_app"]["env"].append(copy.deepcopy(data["custom_app"]["env"][-1]))
        else:
            d["values"] = copy.deepcopy(data["values"])

    model = _forged(basis, change)
    if soort == "custom":
        result = translate(model, PARAMS, TARGET, trusted_app_id=True)
    else:
        result = translate(model, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == [], vorm
    codes = {f.code for f in result.blocking()}
    assert codes == ({"parameter_value_not_allowed"} if vorm == "B5" else {CODE}), vorm


def test_boolean_without_a_secret_marking_keeps_the_exception() -> None:
    """Bewaker (keuze van de regisseur): een gewone boolean uit de wizard met een geheimwoord in de naam
    (``password_login``) blijft geldig en wordt vertaald."""
    param = {**wizard("password_login", "boolean"), "required": False}
    model = _model(met_waarden({"login_aan": {"param": "password_login"}}, param))
    result = translate(model, dict(CATALOG_PARAMS, password_login=True), CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.blocking()]


# ---------------------------------------------------------------------------------------------- R-TECH-9
_GEMARKEERD_OP_ACCOUNTPLEK = [
    ("env ADMIN_PW <- admin_pw", "custom", "ADMIN_PW", hub_geheim("admin_pw")),
    ("env ADMIN <- init_value", "custom", "ADMIN", hub_geheim("init_value")),
    ("env BEHEERDER_PW <- beheerder_pw", "custom", "BEHEERDER_PW", hub_geheim("beheerder_pw")),
    ("values admin.pw <- init_value", "catalogus", "admin.pw", hub_geheim("init_value")),
    ("values admin <- init_value", "catalogus", "admin", hub_geheim("init_value")),
    ("values administrator.login <- init_value", "catalogus", "administrator.login", hub_geheim("init_value")),
    ("values setup.sleutel <- init_value", "catalogus", "setup.sleutel", hub_geheim("init_value")),
    ("values admin.pw <- secret_ref", "catalogus", "admin.pw", {**_geheim("init_value", "secret_ref", "secret")}),
]


@pytest.mark.parametrize(
    ("vorm", "soort", "plek", "param"), _GEMARKEERD_OP_ACCOUNTPLEK, ids=[g[0] for g in _GEMARKEERD_OP_ACCOUNTPLEK]
)
def test_marked_secret_on_a_place_with_an_account_word_is_account_data(
    vorm: str, soort: str, plek: str, param: dict[str, Any]
) -> None:
    """R-TECH-9 (optie 1, volgt uit C-12 "strikt zonder"): een parameter met een geheimmarkering op een plek waarvan de
    eigen naam of een voorouder een accountwoord als heel naamdeel heeft, is de aanmelding van een beheerder of
    dienstaccount, ook als de naam van de parameter geen geheimwoord heeft: ``account_gegevens_verboden``, niet te
    accepteren."""
    data, pad = _met_plek(soort, plek, param)
    assert _secret_codes(_refused(data), pad) == [CODE], vorm
    report, _ = validate_data(data)
    assert not any(f.accepted for f in report.findings if f.path == pad), vorm
    _no_echo(report.model_dump_json())


def test_marked_secret_in_a_name_value_pair_with_an_account_word() -> None:
    """Dezelfde regel bij een naam/waarde-paar in ``values`` (de naam staat onder ``name``)."""
    extra = {"extra": [{"name": "ADMIN_PW", "value": {"param": "init_value"}}]}
    fouten = _refused(met_waarden(extra, hub_geheim("init_value")))
    assert _secret_codes(fouten, "values.voorbeeld.extra[0].value") == [CODE]


@pytest.mark.parametrize(
    ("vorm", "soort", "plek", "param"), _GEMARKEERD_OP_ACCOUNTPLEK, ids=[g[0] for g in _GEMARKEERD_OP_ACCOUNTPLEK]
)
def test_marked_secret_on_a_place_with_an_account_word_gives_no_payload(
    vorm: str, soort: str, plek: str, param: dict[str, Any]
) -> None:
    """W3: dezelfde vormen buiten de validator om geven geen payload en geen ``vault_refs`` (de vault-weg voor een
    beheerder is ook na het invullen dicht)."""
    data, _ = _met_plek(soort, plek, param)
    basis = custom_basis() if soort == "custom" else catalogus_basis()

    def change(d: dict[str, Any]) -> None:
        d["parameters"].append(copy.deepcopy(param))
        if soort == "custom":
            d["custom_app"]["env"].append(copy.deepcopy(data["custom_app"]["env"][-1]))
        else:
            d["values"] = copy.deepcopy(data["values"])

    model = _forged(basis, change)
    if soort == "custom":
        result = translate(model, PARAMS, TARGET, trusted_app_id=True)
        pad = "values.envs[2].value"
    else:
        result = translate(model, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
        pad = f"values.voorbeeld.{plek}"
    assert result.payload is None and not result.ready_for_install and result.vault_refs == [], vorm
    assert (pad, CODE) in {(f.path, f.code) for f in result.blocking()}, vorm


def test_marked_secret_without_an_account_word_keeps_the_acceptable_warning() -> None:
    """Bewakers bij R-TECH-9: een accountwoord als deel van een woord telt niet (``phpmyadmin.x``,
    ``PHPMYADMIN_DB``), en een gewone parameter onder ``admin`` is geen geheim. Die blijven geldig; het Hub-geheim gaat
    als vault-verwijzing mee met de te accepteren waarschuwing ``secret_in_form``."""
    data = met_waarden({"phpmyadmin": {"x": {"param": "init_value"}}}, hub_geheim("init_value"))
    report, model = validate_data(data)
    assert report.valid and report.strict_valid and model is not None, report.as_text()
    assert _at(report.findings, "values.voorbeeld.phpmyadmin.x") == ["secret_in_form"]
    result = translate(model, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.blocking()]
    assert "values.voorbeeld.phpmyadmin.x" in {v.path for v in result.vault_refs}

    env = met_env(
        {"name": "PHPMYADMIN_DB", "value_from": {"param": "init_value"}}, hub_geheim("init_value"), accepted=ACC
    )
    report, model = validate_data(env)
    assert report.valid and report.strict_valid and model is not None, report.as_text()
    assert _at(report.findings, ENV) == ["secret_in_form"]

    gewoon = met_waarden({"admin": {"x": {"param": "w"}}}, wizard("w"))
    report, model = validate_data(gewoon)
    assert report.valid and model is not None, report.as_text()
    assert _at(report.findings, "values.voorbeeld.admin.x") == []
    result = translate(model, dict(CATALOG_PARAMS, w="proeftekst"), CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.blocking()]


# ---------------------------------------------------------------------------------------------- R-TECH-11
def test_w3_has_no_name_rule_for_a_plain_boolean_on_a_place_with_an_account_word() -> None:
    """R-TECH-11 (1): een gewone boolean met een geheimwoord in de naam op een plek met een accountwoord wordt
    vertaald zonder W3-fout, net als bij de validator. In een omgevingsvariabele staat hij als tekst (``true``), dus
    W3 ziet daar een waarde."""
    param = {**wizard("password_login", "boolean"), "required": False}
    data = met_env({"name": "ADMIN_LOGIN", "value_from": {"param": "password_login"}}, param)
    model = _model(data)
    result = translate(model, dict(PARAMS, password_login=True), TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.blocking()]
    assert result.payload is not None
    assert result.payload.values["envs"][2] == {"name": "ADMIN_LOGIN", "value": "true"}


@pytest.mark.parametrize("voorouder", ["ADMINSettings", "ROOTDir", "OWNERName"])
def test_account_word_before_the_last_capital_of_a_run_counts(voorouder: str) -> None:
    """R-TECH-11 (2): de grens vóór de laatste hoofdletter van een reeks hoofdletters (ADR-0019 besluit 3,
    ``ADMINUser``) met vormen die niet zelf in de lijst staan."""
    assert voorouder.upper() not in ACCOUNT_WORDS
    assert has_account_word(voorouder) is True
    data = met_waarden({voorouder: {"db": {"param": "db_password"}}})
    assert _secret_codes(_refused(data), f"values.voorbeeld.{voorouder}.db") == [CODE]


def test_account_word_inside_a_longer_word_does_not_count_for_an_env_name() -> None:
    """R-TECH-11 (3): ook bij een omgevingsvariabele telt een accountwoord alleen als heel naamdeel:
    ``PHPMYADMIN_DB`` naar een Hub-geheim met een geheimnaam blijft geldig met de te accepteren waarschuwing."""
    data = met_env(
        {"name": "PHPMYADMIN_DB", "value_from": {"param": "db_password"}}, hub_geheim("db_password"), accepted=ACC
    )
    report, model = validate_data(data)
    assert report.valid and report.strict_valid and model is not None, report.as_text()
    assert _at(report.findings, ENV) == ["secret_in_form"]
    result = translate(model, PARAMS, TARGET, trusted_app_id=True)
    assert result.ready_for_install and [v.path for v in result.vault_refs] == ["values.envs[2].value"]


def test_vault_ref_finding_hides_a_key_that_can_carry_a_value() -> None:
    """R-TECH-11 (4): ``vault_ref_not_allowed`` onder een sleutel met een ``=`` toont ``<verborgen>`` in het pad, voor
    een waarde en voor een sleutel met een vault-verwijzing."""
    data = met_waarden({"a=b": {"x": "vault://app/andere-app/sleutel", "y": {"vault://app/andere-app/z": 1}}})
    result = translate(_model(data), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    paden = sorted(f.path for f in result.blocking() if f.code == "vault_ref_not_allowed")
    assert paden == [f"values.voorbeeld.{VERBORGEN}.x", f"values.voorbeeld.{VERBORGEN}.y"]
    assert "a=b" not in result.model_dump_json()


def test_missing_value_finding_hides_a_key_that_can_carry_a_value() -> None:
    """R-TECH-11 (5): ``parameter_value_missing`` onder een sleutel met een ``=`` in de ``values`` van een
    catalogusapp toont ``<verborgen>`` in het pad (rechtstreeks via ``translate``)."""
    data = met_waarden({"a=b": {"x": {"param": "w"}}}, wizard("w"))
    result = translate(_model(data), CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.payload is None
    assert [f.path for f in result.blocking() if f.code == "parameter_value_missing"] == [
        f"values.voorbeeld.{VERBORGEN}.x"
    ]
    assert "a=b" not in result.model_dump_json()


# ---------------------------------------------------------------------------------------------- R-MANIFEST-5
def test_account_name_looks_at_the_last_part_of_a_dotted_key_and_is_documented() -> None:
    """R-MANIFEST-5: bij een sleutel met punten of ``[n]`` telt voor een accountnaam het laatste deel, net als bij een
    geheimnaam. ``phpmyadmin.password`` (één sleutel) is dus geen accountnaam; ``phpmyadmin_password`` wel. De
    standaard zegt dat nu ook."""
    assert is_account_name("phpmyadmin.password") is False and is_account_name("groot.password") is False
    assert is_account_name("phpmyadmin_password") is True and is_account_name("x.phpmyadmin_password") is True
    report, _ = validate_data(met_waarden({"phpmyadmin.password": {"param": "db_password"}}))
    assert report.valid, report.as_text()
    assert _at(report.findings, "values.voorbeeld.phpmyadmin.password") == ["secret_in_form"]
    for sleutel in ("phpmyadmin_password", "admin.password"):  # de tweede via het accountwoord als heel naamdeel
        fouten = _refused(met_waarden({sleutel: {"param": "db_password"}}))
        assert _secret_codes(fouten, f"values.voorbeeld.{sleutel}") == [CODE]
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    sectie = doc.split("## Geheimen en accountgegevens weigeren", 1)[1].split("\n## ", 1)[0]
    accountnaam = sectie.split("- **Accountnaam:**", 1)[1].split("- **Accountwoord als heel naamdeel", 1)[0]
    assert "in het laatste deel" in " ".join(accountnaam.split())


def test_the_standard_documents_the_rules_of_this_round() -> None:
    """De standaard noemt de regels van ADR-0021: de vaste vorm van een lange sleutel en van een lang pad, de boolean
    met een geheimmarkering en het gemarkeerde geheim op een plek met een accountwoord."""
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    sectie = " ".join(doc.split("## Geheimen en accountgegevens weigeren", 1)[1].split("\n## ", 1)[0].split())
    for stuk in ("ADR-0021", "meer dan 256 tekens", "meer dan 1024 tekens", "zonder geheimmarkering"):
        assert stuk in sectie, stuk
