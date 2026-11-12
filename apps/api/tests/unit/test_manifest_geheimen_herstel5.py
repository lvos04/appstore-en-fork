"""Herstelronde 5 na de vierde review van S-P2-520 (ADR-0022, naast ADR-0016 tot en met ADR-0021).

Elke test hoort bij een bevinding of besluit van die review: R-VEILIG-10 met besluit B-1 (hooguit 1000 meldingen per
rapport), besluit B-2 (een parameter met een geheimmarkering en een accountwoord in de eigen naam), R-TECH-12 (T2 op de
``default`` en het ``example`` van een parameter) en R-MANIFEST-6 (de zin over ``secret_in_form``). De tests zijn eerst
rood gezien op de code van vóór dit herstel (3227fe1). De geheugentests meten de PIEK (tracemalloc) en draaien onder een
eigen grens op het adresgeheugen: zonder de grens op het aantal meldingen faalt de test met een MemoryError of een te
hoge piek, en vult hij niet het geheugen van de machine. Alleen testwaarden; geen melding, uitvoer of vertaling noemt
een testwaarde (V-094).
"""

from __future__ import annotations

import contextlib
import copy
import gc
import json
import os
import resource
import tracemalloc
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from functools import cache
from typing import Any

import pytest

from appstore.manifest import validate_data, validate_text
from appstore.manifest.conflicts import Inventory, analyse
from appstore.manifest.findings import CODES, MAX_FINDINGS, TOO_MANY, Finding, Severity, error
from appstore.manifest.loader import MAX_DEPTH, MAX_MANIFEST_BYTES, data_limit_findings
from appstore.manifest.model import Manifest
from appstore.manifest.rules import has_account_word, marked_account_param, secret_text_reasons
from appstore.manifest.translate import Target, translate
from appstore.manifest.truenas import AppCreatePayload
from tests.conftest import REPO_ROOT, load_fixture
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
PIEK_GRENS_MIB = 50
#: Het antwoord van een begrensd rapport: 1001 meldingen met elk hooguit een pad van 1025 tekens, als JSON plus tekst.
ANTWOORD_GRENS = 4 * 2**20
#: De vorm van R-VEILIG-4 (``test_rveilig4_api.py::test_a``): onder ``values.voorbeeld`` 28 geneste sleutels van 256
#: tekens met elk één teken buiten het BMP; daaronder een lange lijst, zo lang als in 256 KiB compacte JSON past.
DIEPE_SLEUTELS = [f"{i:02d}" + "n" * 253 + "\U0001f600" for i in range(MAX_DEPTH - 4)]


@contextlib.contextmanager
def _geheugengrens(extra_mib: int = 1024) -> Iterator[None]:
    """Zachte grens op het adresgeheugen van dit proces: wat er nu is plus ``extra_mib``. Zonder de grens op het aantal
    meldingen vraagt één manifest van deze vormen honderden MiB tot gigabytes; dan een MemoryError, geen volle machine."""
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


def _grootte(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())


def _diep(basis: dict[str, Any], blad_sleutel: str, item: Any, aantal: int) -> dict[str, Any]:
    data = copy.deepcopy(basis)
    node = data["values"]["voorbeeld"] if "voorbeeld" in data["values"] else data["values"]
    # een object als item is zelf een niveau: dan één sleutel minder (hooguit MAX_DEPTH niveaus)
    for sleutel in DIEPE_SLEUTELS[: len(DIEPE_SLEUTELS) - isinstance(item, dict | list)]:
        node[sleutel] = {}
        node = node[sleutel]
    node[blad_sleutel] = [copy.deepcopy(item)] * aantal
    return data


@cache
def _zo_veel_als_past(fixture: str, blad_sleutel: str, item_json: str) -> int:
    """Het grootste aantal items onder de diepe sleutels dat als compacte JSON binnen 256 KiB past."""
    basis, item = load_fixture(fixture), json.loads(item_json)
    laag, hoog = 1, 400_000
    while laag < hoog:
        midden = (laag + hoog + 1) // 2
        if _grootte(_diep(basis, blad_sleutel, item, midden)) <= MAX_MANIFEST_BYTES:
            laag = midden
        else:
            hoog = midden - 1
    return laag


def _vol(blad_sleutel: str, item: Any, fixture: str = "valid-catalog-1-3.manifest.yaml") -> dict[str, Any]:
    """Een manifest van de vorm van R-VEILIG-4, zo groot als de lader aanneemt (geen ``manifest_too_large``)."""
    aantal = _zo_veel_als_past(fixture, blad_sleutel, json.dumps(item))
    data = _diep(load_fixture(fixture), blad_sleutel, item, aantal)
    assert _grootte(data) <= MAX_MANIFEST_BYTES and not data_limit_findings(data)
    return data


def _begrensd(findings: list[Finding]) -> None:
    """ADR-0022: precies de eerste MAX_FINDINGS meldingen en één vaste fout ``te_veel_meldingen``."""
    assert len(findings) == MAX_FINDINGS + 1, len(findings)
    vast = [f for f in findings if f.code == TOO_MANY]
    assert len(vast) == 1 and vast[0].severity is Severity.ERROR and vast[0].path == "(root)"
    assert str(MAX_FINDINGS) in vast[0].message


def _no_echo(text: str, *extra: str) -> None:
    for waarde in (*TESTWAARDEN, *extra):
        assert waarde not in text, "een testwaarde staat in de uitvoer"


def _at(findings: list[Finding], path: str) -> list[str]:
    return [f.code for f in findings if f.path == path]


def _secret_codes(findings: list[Finding], path: str) -> list[str]:
    return [c for c in _at(findings, path) if c in ("secret_literal_forbidden", "secret_in_form", CODE)]


def _refused(data: dict[str, Any]) -> list[Finding]:
    report, model = validate_data(copy.deepcopy(data))
    assert not report.valid and not report.strict_valid and model is None, report.as_text()
    return [f for f in report.findings if f.severity is Severity.ERROR]


def _model(data: dict[str, Any]) -> Manifest:
    report, model = validate_data(copy.deepcopy(data))
    assert model is not None, report.as_text()
    return model


def _forged(data: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> Manifest:
    """Buiten de validator om (verdediging in de diepte): de vertaling toetst zelf opnieuw."""
    dumped = _model(data).model_dump(mode="json")
    change(dumped)
    return Manifest.model_validate(dumped)


# ---------------------------------------------------------------------------------------------- R-VEILIG-10, B-1
#: (naam, blad-sleutel, item): elk item geeft één melding met een pad van 1025 tekens (na de padgrens van ADR-0021).
_VEEL_MELDINGEN = [
    ("geheim-getal", "password", 1),  # secret_literal_forbidden (de vorm van R-VEILIG-4, test_a)
    ("interpolatie", "l", "$a"),  # interpolation_forbidden (al vóór S-P2-520 een MemoryError)
    ("tekstregel-t2", "l", "credentialSecret=x"),  # account_gegevens_verboden uit de tekstregels
    ("hub-naam", "l", {"name": "HUB_X"}),  # reserved_env
]


@pytest.mark.parametrize(("vorm", "blad", "item"), _VEEL_MELDINGEN, ids=[v[0] for v in _VEEL_MELDINGEN])
def test_validate_of_the_r_veilig_4_form_stops_at_the_maximum(vorm: str, blad: str, item: Any) -> None:
    """R-VEILIG-10, besluit B-1: een manifest van 256 KiB met tienduizenden meldingen onder 28 geneste sleutels van 256
    tekens (vorm ``geheim-getal``: 126 111 items, op 3227fe1 126 112 meldingen, een antwoord van 282 MiB en 2,8 GiB RSS)
    geeft nu de eerste 1000 meldingen en één vaste fout ``te_veel_meldingen``: ongeldig, geen model, het antwoord
    (JSON plus tekst) onder 4 MiB en de piek onder 50 MiB, zonder MemoryError."""
    data = _vol(blad, item)
    if vorm == "geheim-getal":  # N ongeveer 126 000, zoals bij R-VEILIG-4
        assert _zo_veel_als_past("valid-catalog-1-3.manifest.yaml", "password", "1") > 120_000

    def werk() -> tuple[Any, Any, int]:
        report, model = validate_data(data)
        return report, model, len(report.model_dump_json()) + len(report.as_text())

    piek, (report, model, antwoord) = _piek_mib(werk)
    assert model is None and not report.valid and not report.strict_valid, vorm
    _begrensd(report.findings)
    assert antwoord < ANTWOORD_GRENS, f"{vorm}: antwoord {antwoord / 2**20:.1f} MiB"
    assert piek < PIEK_GRENS_MIB, f"{vorm}: piek {piek:.0f} MiB"


def test_exactly_the_maximum_is_a_normal_report_and_one_more_stops() -> None:
    """De grens zelf: precies 1000 meldingen is een gewoon rapport (zonder ``te_veel_meldingen``); bij 1001 houdt het
    rapport de eerste 1000 en krijgt het de vaste fout."""
    basis = len(validate_data(met_waarden({"password": []}))[0].findings)
    assert 0 < basis < 10
    precies, model = validate_data(met_waarden({"password": [1] * (MAX_FINDINGS - basis)}))
    assert model is None and len(precies.findings) == MAX_FINDINGS
    assert TOO_MANY not in precies.codes()
    een_meer, model = validate_data(met_waarden({"password": [1] * (MAX_FINDINGS - basis + 1)}))
    assert model is None and not een_meer.valid
    _begrensd(een_meer.findings)


def test_validate_text_and_the_cli_path_stop_at_the_maximum() -> None:
    """Hetzelfde via ``validate_text`` (``manifest_yaml`` van ``POST /catalog/v1/validate``, de synchronisatie en
    ``jl-manifest validate``)."""
    tekst = json.dumps(_vol("password", 1), ensure_ascii=False, separators=(",", ":"))
    report, model, _ = validate_text(tekst)
    assert model is None and not report.valid
    _begrensd(report.findings)
    assert len(report.model_dump_json()) + len(report.as_text()) < ANTWOORD_GRENS


def test_schema_errors_are_capped_before_they_are_sorted() -> None:
    """Schemafouten: elke fout geeft minstens één melding, dus de validator haalt er niet meer dan 1001 op voordat hij
    sorteert. 120 000 verkeerde items in ``parameters`` gaven op 3227fe1 120 020 meldingen, een piek van 472 MiB en 9 s;
    nu de eerste 1000, de vaste fout en een piek onder 50 MiB."""
    data = {"parameters": [1] * 120_000}
    assert _grootte(data) <= MAX_MANIFEST_BYTES and not data_limit_findings(data)
    piek, (report, model) = _piek_mib(lambda: validate_data(data))
    assert model is None
    _begrensd(report.findings)
    assert set(report.codes()) <= {"wrong_type", "required_field_missing", TOO_MANY}
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"


def _vault_teksten(aantal: int) -> dict[str, Any]:
    """Een GELDIG catalogusmanifest zonder geheimen met ``aantal`` letterlijke vault-teksten in ``values``: de
    validator laat het door, de vertaling geeft per tekst ``vault_ref_not_allowed``."""
    data = load_fixture("valid-catalog.manifest.yaml")
    data["values"]["l"] = ["vault://x"] * aantal
    return data


_FOTO_PARAMS = {"timezone": "Etc/UTC", "library_path": "/mnt/p1/fotos"}


def test_translate_stops_at_the_maximum_without_payload() -> None:
    """De vertaling is een eigen rapport (ook in ``POST /catalog/v1/preview``, ook met een los manifest): 3000
    vault-teksten gaven op 3227fe1 3001 meldingen; nu de eerste 1000 en ``te_veel_meldingen``, zonder payload,
    ``preview_hash`` of ``vault_refs``."""
    model = _model(_vault_teksten(3000))
    for vertrouwd in (True, False):
        result = translate(model, _FOTO_PARAMS, CATALOG_TARGET, trusted_app_id=vertrouwd)
        _begrensd(result.findings)
        assert result.payload is None and result.preview_hash is None and result.vault_refs == []
        assert not result.ready_for_install and result.compatibility.status in ("ok", "unknown")
    klein = translate(_model(_vault_teksten(3)), _FOTO_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert TOO_MANY not in {f.code for f in klein.findings}
    assert [f.code for f in klein.blocking()] == ["vault_ref_not_allowed"] * 3


def test_translate_peak_stays_small_with_many_findings_under_deep_keys() -> None:
    """Dezelfde vertaling onder de 28 geneste sleutels, zo veel vault-teksten als in 256 KiB past: piek onder 50 MiB."""
    model = _model(_vol("l", "vault://x", fixture="valid-catalog.manifest.yaml"))
    piek, result = _piek_mib(lambda: translate(model, _FOTO_PARAMS, CATALOG_TARGET, trusted_app_id=True))
    _begrensd(result.findings)
    assert result.payload is None
    assert len(result.model_dump_json()) < ANTWOORD_GRENS
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"


def _veel_hostpaden(paden: int, volumes: int) -> tuple[Manifest, AppCreatePayload, Inventory]:
    model = Manifest.model_validate(custom_basis())
    payload = AppCreatePayload(
        app_name="proef",
        catalog_app="ix-app",
        train="stable",
        version="1.4.8",
        values={
            "storage": [
                {
                    "type": "host_path",
                    "read_only": False,
                    "mount_path": f"/m{i}",
                    "host_path_config": {"path": "/mnt/p1/d"},
                }
                for i in range(paden)
            ]
        },
    )
    inventaris = Inventory.model_validate(
        {
            "observed_at": datetime.now(UTC).isoformat(),
            "source": "simulator",
            "apps": [
                {
                    "name": "ander",
                    "version": "1.0.0",
                    "volumes": [
                        {"type": "bind", "source": "/mnt/p1/d", "destination": f"/d{j}"} for j in range(volumes)
                    ],
                }
            ],
        }
    )
    return model, payload, inventaris


def test_conflict_analysis_of_the_preview_stops_at_the_maximum() -> None:
    """De conflictanalyse van ``POST /catalog/v1/preview`` is ook een rapport: 200 hostpaden tegenover een inventaris
    van 8000 volumes op hetzelfde pad (0,6 MiB) gaven op 3227fe1 1,6 miljoen meldingen en ruim 1,1 GiB RSS. Nu de
    eerste 1000, ``te_veel_meldingen`` en status ``conflict``, met een piek onder 50 MiB."""
    model, payload, inventaris = _veel_hostpaden(200, 8000)
    piek, report = _piek_mib(lambda: analyse(model, payload, inventaris))
    assert report.status == "conflict" and report.inventory_observed_at is not None
    _begrensd(report.findings)
    assert piek < PIEK_GRENS_MIB, f"piek {piek:.0f} MiB"
    klein = analyse(*_veel_hostpaden(2, 3))
    assert [f.code for f in klein.findings] == ["host_path_in_use"] * 6


def test_the_limit_is_per_report_and_ends_with_the_report() -> None:
    """Na een begrensd rapport is er geen budget meer actief: een volgend rapport begint opnieuw bij nul, en meldingen
    buiten een rapport (bv. in een losse regel) tellen niet mee."""
    _begrensd(validate_data(met_waarden({"password": [1] * 3000}))[0].findings)
    report, model = validate_data(catalogus_basis())
    assert report.valid and model is not None
    los = [error("invalid_value", "x", "proef") for _ in range(3 * MAX_FINDINGS)]
    assert len(los) == 3 * MAX_FINDINGS


def test_the_limit_is_a_documented_constant_and_code() -> None:
    """``MAX_FINDINGS`` is 1000, ``te_veel_meldingen`` staat in de codes, en de standaard en ADR-0022 noemen de grens.
    De foute zinnen over het aantal meldingen ("hoeveel meldingen er ook zijn") staan niet meer in de standaard."""
    assert MAX_FINDINGS == 1000 and TOO_MANY == "te_veel_meldingen" and TOO_MANY in CODES
    doc = (REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8")
    plat = " ".join(doc.split())
    assert "hoeveel meldingen er ook zijn" not in plat
    assert "Hooguit 1000 meldingen per rapport (ADR-0022)" in plat and "| `te_veel_meldingen` |" in doc
    adr = REPO_ROOT / "docs" / "adr" / "ADR-0022-herstelronde-5-grens-op-meldingen-en-accountnaam-van-een-geheim.md"
    tekst = " ".join(adr.read_text(encoding="utf-8").split())
    for stuk in ("MAX_FINDINGS", "te_veel_meldingen", "tientallen MiB", "ADR-0021", "C-12"):
        assert stuk in tekst, stuk


# ---------------------------------------------------------------------------------------------- B-2
def _geheim(naam: str, type_: str = "string", source: str = "generated") -> dict[str, Any]:
    return {**hub_geheim(naam), "type": type_, "source": source}


_EIGEN_NAAM = [
    ("P1 x <- admin_pw (Hub-geheim)", "catalogus", "x", hub_geheim("admin_pw")),
    ("P2 x <- root_key (Hub-geheim)", "catalogus", "x", hub_geheim("root_key")),
    (
        "P3 x <- beheer_code (secret_ref, source secret)",
        "catalogus",
        "x",
        {"name": "beheer_code", "type": "secret_ref", "source": "secret", "required": True, "description_nl": "Proef."},
    ),
    ("P4 env X <- admin_pw (Hub-geheim)", "custom", "X", hub_geheim("admin_pw")),
    ("P5 x <- admin_password (accountnaam, al zo)", "catalogus", "x", hub_geheim("admin_password")),
]


def _met_plek(soort: str, plek: str, param: dict[str, Any]) -> tuple[dict[str, Any], str]:
    ref = {"param": param["name"]}
    if soort == "custom":
        return met_env({"name": plek, "value_from": ref}, param, accepted=ACC), ENV
    data = met_waarden({plek: ref}, param)
    data["accepted_findings"] = ACC
    return data, f"values.voorbeeld.{plek}"


@pytest.mark.parametrize(("vorm", "soort", "plek", "param"), _EIGEN_NAAM, ids=[e[0][:2] for e in _EIGEN_NAAM])
def test_marked_secret_with_an_account_word_in_its_own_name_is_account_data(
    vorm: str, soort: str, plek: str, param: dict[str, Any]
) -> None:
    """Besluit B-2 (dichtmaken, volgt uit C-12 "strikt zonder"): een parameter met een geheimmarkering waarvan de EIGEN
    naam een accountwoord als heel naamdeel heeft (``admin_pw``, ``root_key``, ``beheer_code``) is een accountgegeven,
    ook op een plek zonder accountwoord (``x``, ``X``): ``account_gegevens_verboden``, niet te accepteren. Op 3227fe1
    waren P1 tot en met P4 geldig (P1, P2 en P4 ook in ``--strict``) met een vault-verwijzing."""
    data, pad = _met_plek(soort, plek, param)
    assert _secret_codes(_refused(data), pad) == [CODE], vorm
    report, _ = validate_data(data)
    assert not any(f.accepted for f in report.findings if f.path == pad), vorm
    _no_echo(report.model_dump_json() + report.as_text())


@pytest.mark.parametrize(("vorm", "soort", "plek", "param"), _EIGEN_NAAM, ids=[e[0][:2] for e in _EIGEN_NAAM])
def test_marked_secret_with_an_account_word_in_its_own_name_gives_no_payload(
    vorm: str, soort: str, plek: str, param: dict[str, Any]
) -> None:
    """W3: dezelfde vormen buiten de validator om (met een vaste ``app_id``) geven geen payload en geen ``vault_refs``,
    met ``account_gegevens_verboden`` op de plek."""
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
    _no_echo(result.model_dump_json())


def test_marked_secret_with_an_account_word_in_its_own_name_on_another_place() -> None:
    """De naam van een gebruikte parameter telt overal (``param_name_rules``), dus ook voor een gemarkeerd geheim met
    een accountwoord in de eigen naam op een plek als de tijdzone van een Custom App (op 3227fe1 geldig)."""
    data = custom_basis()
    data["parameters"].append(_geheim("admin_tz", "timezone"))
    data["custom_app"]["timezone"] = {"param": "admin_tz"}
    assert _at(_refused(data), "custom_app.timezone") == [CODE]
    zonder = custom_basis()
    zonder["parameters"].append(_geheim("zone_tz", "timezone"))
    zonder["custom_app"]["timezone"] = {"param": "zone_tz"}
    report, _ = validate_data(zonder)
    assert CODE not in report.codes()


@pytest.mark.parametrize("naam", ["phpmyadmin_pw", "rootless_key", "groot_sleutel", "administration_key", "x_code"])
def test_marked_secret_without_an_account_word_as_a_whole_part_keeps_the_acceptable_warning(naam: str) -> None:
    """Bewakers bij B-2: een accountwoord als deel van een woord telt niet (``phpmyadmin``, ``rootless``, ``groot``,
    ``administration``). Het Hub-geheim blijft geldig (ook in ``--strict``, met de geaccepteerde waarschuwing
    ``secret_in_form``) en gaat als vault-verwijzing mee, als waarde en als omgevingsvariabele."""
    assert not has_account_word(naam)
    data, pad = _met_plek("catalogus", "x", hub_geheim(naam))
    report, model = validate_data(data)
    assert report.valid and report.strict_valid and model is not None, report.as_text()
    assert _at(report.findings, pad) == ["secret_in_form"]
    result = translate(model, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.blocking()]
    assert pad in {v.path for v in result.vault_refs}
    env, _ = _met_plek("custom", "X", hub_geheim(naam))
    report, model = validate_data(env)
    assert report.valid and report.strict_valid and model is not None, report.as_text()
    result = translate(model, PARAMS, TARGET, trusted_app_id=True)
    assert result.ready_for_install and [v.path for v in result.vault_refs] == ["values.envs[2].value"]


def test_existing_hub_secrets_and_plain_parameters_stay_valid() -> None:
    """Bewakers bij B-2: de Hub-geheimen ``db_password`` en ``secret_key`` (zoals in het manifest van Authentik) blijven
    geldig met de te accepteren waarschuwing en gaan als vault-verwijzing mee; een gewone parameter zonder
    geheimmarkering met de naam ``admin_pw`` valt niet onder B-2 (geen geheim, geen geheimnaam) en wordt vertaald."""
    data = met_waarden({"secret_key": {"param": "secret_key"}}, hub_geheim("secret_key"))
    report, model = validate_data(data)
    assert report.valid and report.strict_valid and model is not None, report.as_text()
    assert _at(report.findings, "values.voorbeeld.secret_key") == ["secret_in_form"]
    result = translate(model, CATALOG_PARAMS, CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install
    assert {v.parameter for v in result.vault_refs} == {"db_password", "secret_key"}
    gewoon = wizard("admin_pw")
    assert not marked_account_param(Manifest.model_validate(met_waarden({}, gewoon)).parameters[-1])
    model = _model(met_waarden({"x": {"param": "admin_pw"}}, gewoon))
    result = translate(model, dict(CATALOG_PARAMS, admin_pw="proeftekst"), CATALOG_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.blocking()]


# ---------------------------------------------------------------------------------------------- R-TECH-12
def test_t2_on_the_default_and_example_of_a_parameter() -> None:
    """R-TECH-12: T2 geldt ook op ``parameters[].default`` en ``parameters[].example`` (diepte 3). Een tekst die alleen
    T2 vindt (``credentialSecret=<testwaarde>``; T1 vindt er niets in) geeft daar ``account_gegevens_verboden``, en de
    testwaarde staat niet in het rapport. Zonder de regel voor diepte 3 is deze test rood (mutatie W08)."""
    tekst = f"credentialSecret={PROEFWACHTWOORD}"
    assert secret_text_reasons(tekst, settings=False) == []  # T1 vindt niets: alleen T2
    param = {
        "name": "flow_opties",
        "type": "string",
        "source": "default",
        "required": False,
        "description_nl": "Proefparameter.",
        "default": tekst,
        "example": tekst,
    }
    idx = len(catalogus_basis()["parameters"])
    report, model = validate_data(met_waarden({"opties": {"param": "flow_opties"}}, param))
    assert model is None
    fouten = {(f.path, f.code) for f in report.findings if f.severity is Severity.ERROR}
    assert fouten == {(f"parameters[{idx}].default", CODE), (f"parameters[{idx}].example", CODE)}
    _no_echo(report.model_dump_json() + report.as_text())


# ---------------------------------------------------------------------------------------------- R-MANIFEST-6
def test_the_sentence_about_secret_in_form_matches_the_code() -> None:
    """R-MANIFEST-6: elders is ``secret_in_form`` alleen te accepteren bij een geheim dat de Hub maakt. Een gemarkeerd
    geheim dat de Hub niet maakt (``source: secret``) geeft in ``values`` een NIET te accepteren waarschuwing en in een
    omgevingsvariabele een fout; de standaard zegt dat nu ook."""
    geheim = {**_geheim("init_value", source="secret")}
    data, pad = _met_plek("catalogus", "x", geheim)
    report, _ = validate_data(data)
    assert report.valid and not report.strict_valid
    assert [(f.code, f.severity, f.accepted) for f in report.findings if f.path == pad] == [
        ("secret_in_form", Severity.WARNING, False)
    ]
    env, _ = _met_plek("custom", "INIT_VALUE", geheim)
    assert _at(_refused(env), ENV) == ["secret_in_form"]
    hub, pad = _met_plek("catalogus", "x", hub_geheim("init_value"))
    report, _ = validate_data(hub)
    assert [f.accepted for f in report.findings if f.path == pad] == [True]
    doc = " ".join((REPO_ROOT / "docs" / "MANIFESTSTANDAARD.md").read_text(encoding="utf-8").split())
    zin = doc.split("Wat de validator niet ziet:", 1)[1].split("De samenstellingen", 1)[0]
    assert "(te accepteren alleen bij een geheim dat de Hub maakt)" in zin
    assert "elders de te accepteren waarschuwing" not in zin
