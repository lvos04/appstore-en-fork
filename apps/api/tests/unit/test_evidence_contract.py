"""Contract 1.3 §11 (EVIDENCE.json) en de kop van het contract (byte-gelijke kopieën met een drifttest).

EVIDENCE.json:

- ``status`` uit de Excel-lijst van het blad; ``Gereed`` en ``Geslaagd`` worden in deze bouwomgeving nooit gezet.
- ``validatie``: één of meer waarden uit de vaste lijst, gescheiden door ``"; "``; **nooit leeg**; toelichting hoort in
  ``opmerking``. ``Niet getest`` alleen bij status ``Niet gestart`` (nieuw in 1.3).
- Testmatrix-ID's (``EX..``) gebruiken ``Sim-geslaagd``/``Sim-mislukt`` (naast ``NIET GEVALIDEERD (…)``).
- Elke ``NIET GEVALIDEERD (…)`` die in ``blokkade`` staat, staat ook in ``validatie`` (nieuw in 1.3).
- Naast Excel-ID's mogen contractpunten (``C1.3-ASF-<nn>``, ``C1.2-<code>``) en reviews (``REVIEW-appstore-en-fork-…``)
  een eigen regel hebben, met de statussen van het blad Werkpakketten.

Kopieën (kop van het contract, C1.3-ALG-01): ``PLATFORM_CONTRACTEN.md`` en ``CONTRACT_1.3_CONFORMITEIT.md`` zijn
byte-gelijk aan de bron ``lvos04/ai-server-manager`` op een vaste bron-commit; nooit lokaal aanpassen.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pytest

from tests.conftest import REPO_ROOT

DOCS = REPO_ROOT / "docs" / "platform-v2"
EVIDENCE = DOCS / "EVIDENCE.json"
CONTRACT = DOCS / "PLATFORM_CONTRACTEN.md"
CONFORMITY = DOCS / "CONTRACT_1.3_CONFORMITEIT.md"
#: bron-commit in lvos04/ai-server-manager (branch ccr-c133d7a0-kuz1cv) waarop beide kopieën zijn genomen
SOURCE_COMMIT = "02b119cb84be9a307a88532f6692ea8a4bcb12f1"
#: sha256 van docs/platform-v2/PLATFORM_CONTRACTEN.md (contracts 1.3) op SOURCE_COMMIT
CONTRACT_SHA256 = "15b30cf17f91b606451ef9c41f02327076ef76ce655ee94e40e3538106962310"
#: sha256 van docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md op SOURCE_COMMIT
CONFORMITY_SHA256 = "f0a2352ddb27088ae668013501c62a1eaaf879efe742a2d4422ffe8d33b43466"

STATUS_WORK = {"Niet gestart", "Bezig", "Geblokkeerd", "N.v.t."}  # 'Gereed' nooit in deze bouwomgeving
STATUS_TEST = {"Niet gestart", "Bezig", "Mislukt", "Geblokkeerd", "N.v.t."}  # 'Geslaagd' nooit
STATUS_DECISION = {"Open", "Ingevuld", "Getest", "Geblokkeerd", "N.v.t."}
NOT_VALIDATED = {
    "NIET GEVALIDEERD (echte NAS)",
    "NIET GEVALIDEERD (hardware)",
    "NIET GEVALIDEERD (account)",
    "NIET GEVALIDEERD (Mac/Xcode)",
}
VALIDATION_WORK = {"Sim-getest", "Unit-getest", "Intel-iGPU-getest (ontwikkelmachine)", "Niet getest", *NOT_VALIDATED}
VALIDATION_TEST = {"Sim-geslaagd", "Sim-mislukt", "Niet getest", *NOT_VALIDATED}
FIELDS = {"id", "status", "validatie", "bewijs", "blokkade", "opmerking", "repo", "bijgewerkt"}
CONTRACT_POINT = re.compile(r"C1\.3-ASF-\d{2}|C1\.3-ALG-\d{2}|C1\.2-[A-Z0-9]+")
REVIEW = re.compile(r"REVIEW-appstore-en-fork-[a-z0-9][a-z0-9.-]*")
NIET_GEVALIDEERD = re.compile(r"NIET GEVALIDEERD \([^)]*\)")


def _items() -> list[dict[str, Any]]:
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
    return data


def _kind(ident: str) -> str:
    if re.fullmatch(r"EX\d{2}", ident):
        return "test"
    if re.fullmatch(r"D\d{2}", ident):
        return "decision"
    return "work"


def check_item(item: dict[str, Any]) -> list[str]:
    """Alle regels van §11 voor één regel; geeft de overtredingen (leeg = in orde). Ook gebruikt voor negatieve tests."""
    problems: list[str] = []
    if set(item) != FIELDS:
        return [f"velden {sorted(set(item) ^ FIELDS)} kloppen niet"]
    ident = str(item["id"])
    if item["repo"] != "appstore-en-fork":
        problems.append("repo moet appstore-en-fork zijn")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item["bijgewerkt"])):
        problems.append("bijgewerkt is geen datum")
    kind = _kind(ident)
    statuses = {"test": STATUS_TEST, "decision": STATUS_DECISION, "work": STATUS_WORK}[kind]
    if item["status"] not in statuses:
        problems.append(f"status {item['status']!r} hoort niet bij dit blad")
    if item["status"] in ("Gereed", "Geslaagd"):
        problems.append("Gereed/Geslaagd wordt in deze bouwomgeving nooit gezet")
    raw = str(item["validatie"])
    if not raw.strip():
        problems.append("een lege validatie is nooit toegestaan")
        return problems
    values = raw.split("; ")
    allowed = VALIDATION_TEST if kind == "test" else VALIDATION_WORK
    if not all(v in allowed for v in values):
        problems.append(f"validatie {raw!r} bevat een waarde buiten de vaste lijst")
    if len(values) != len(set(values)):
        problems.append("validatie noemt een waarde dubbel")
    if "Niet getest" in values and item["status"] != "Niet gestart":
        problems.append("'Niet getest' alleen bij status 'Niet gestart'")
    for found in NIET_GEVALIDEERD.findall(str(item["blokkade"])):
        if found not in NOT_VALIDATED:
            problems.append(f"{found!r} is geen waarde uit de vaste lijst")
        elif found not in values:
            problems.append(f"{found!r} staat in blokkade maar niet in validatie")
    if ident.startswith("C1.") and not CONTRACT_POINT.fullmatch(ident):
        problems.append(f"contractpunt {ident!r} heeft niet de vaste vorm")
    if ident.startswith("REVIEW-") and not REVIEW.fullmatch(ident):
        problems.append(f"review {ident!r} heeft niet de vaste vorm")
    return problems


@pytest.mark.parametrize("item", _items(), ids=lambda e: str(e.get("id")))
def test_item_follows_section_11(item: dict[str, Any]) -> None:
    assert check_item(item) == []


def test_ids_are_unique() -> None:
    ids = [e["id"] for e in _items()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"validatie": ""}, "lege validatie"),
        ({"status": "Gereed"}, "status"),
        ({"status": "Bezig", "validatie": "Niet getest"}, "'Niet getest'"),
        ({"blokkade": "NIET GEVALIDEERD (echte NAS): echte TrueNAS nodig", "validatie": "Sim-getest"}, "blokkade"),
        ({"validatie": "Sim-getest; Klaar"}, "vaste lijst"),
        ({"id": "C1.3-HCH-01"}, "vaste vorm"),
        ({"id": "REVIEW-andere-repo-x"}, "vaste vorm"),
    ],
)
def test_section_11_rules_reject_wrong_items(change: dict[str, str], expected: str) -> None:
    """Negatief: elke regel van §11 valt aantoonbaar om."""
    base = {
        "id": "C1.3-ASF-01",
        "status": "Bezig",
        "validatie": "Sim-getest",
        "bewijs": "x",
        "blokkade": "",
        "opmerking": "",
        "repo": "appstore-en-fork",
        "bijgewerkt": "2026-11-10",
    }
    assert check_item(base) == []
    problems = check_item({**base, **change})
    assert any(expected in p for p in problems), problems


def test_test_matrix_ids_use_sim_results() -> None:
    item = {
        "id": "EX16",
        "status": "Bezig",
        "validatie": "Sim-getest",
        "bewijs": "x",
        "blokkade": "",
        "opmerking": "",
        "repo": "appstore-en-fork",
        "bijgewerkt": "2026-11-10",
    }
    assert any("vaste lijst" in p for p in check_item(item))
    assert check_item({**item, "validatie": "Sim-geslaagd"}) == []


def test_contract_copy_is_version_1_3_and_byte_equal_to_the_source() -> None:
    data = CONTRACT.read_bytes()
    assert b"Versie: `contracts 1.3`" in data
    assert hashlib.sha256(data).hexdigest() == CONTRACT_SHA256, (
        f"PLATFORM_CONTRACTEN.md wijkt af van ai-server-manager@{SOURCE_COMMIT[:12]}; nooit lokaal aanpassen"
    )


def test_conformity_list_copy_is_byte_equal_to_the_source() -> None:
    data = CONFORMITY.read_bytes()
    assert data.startswith(b"# Conformiteit met contract 1.3")
    assert hashlib.sha256(data).hexdigest() == CONFORMITY_SHA256, (
        f"CONTRACT_1.3_CONFORMITEIT.md wijkt af van ai-server-manager@{SOURCE_COMMIT[:12]}; nooit lokaal aanpassen"
    )


def test_conformity_list_names_this_repo() -> None:
    text = CONFORMITY.read_text(encoding="utf-8")
    assert "## 3. appstore-en-fork" in text
    for n in range(1, 15):
        assert f"C1.3-ASF-{n:02d}" in text


def test_provenance_file_pins_the_same_source() -> None:
    """``CONTRACT_HERKOMST.json`` (gebruikt door ``tools/contract_kopie.py`` in CI) pint dezelfde commit en sha256."""
    pin = json.loads((DOCS / "CONTRACT_HERKOMST.json").read_text(encoding="utf-8"))
    assert pin["bron_repo"] == "lvos04/ai-server-manager"
    assert pin["bron_commit"] == SOURCE_COMMIT
    assert pin["bestanden"]["PLATFORM_CONTRACTEN.md"]["sha256"] == CONTRACT_SHA256
    assert pin["bestanden"]["CONTRACT_1.3_CONFORMITEIT.md"]["sha256"] == CONFORMITY_SHA256


def test_contract_copy_tool_detects_a_local_change(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negatief: één gewijzigde byte in een kopie laat de offline controle falen."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("contract_kopie", REPO_ROOT / "tools" / "contract_kopie.py")
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("PLATFORM_CONTRACTEN.md", "CONTRACT_1.3_CONFORMITEIT.md", "CONTRACT_HERKOMST.json"):
        (docs / name).write_bytes((DOCS / name).read_bytes())
    monkeypatch.setattr(tool, "DOCS", docs)
    monkeypatch.setattr(tool, "HERKOMST", docs / "CONTRACT_HERKOMST.json")
    assert tool.controleer() == []
    (docs / "PLATFORM_CONTRACTEN.md").write_bytes(CONTRACT.read_bytes().replace(b"contracts 1.3", b"contracts 1.4", 1))
    assert any("PLATFORM_CONTRACTEN.md" in f for f in tool.controleer())
