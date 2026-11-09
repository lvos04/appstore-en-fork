"""§11 van het platformcontract (A4): EVIDENCE.json gebruikt alleen de vaste statuslijsten en vaste validatiewaarden,
nooit ``Gereed``/``Geslaagd``; toelichting hoort in ``opmerking``. Plus: de contractkopie is versie 1.2."""

from __future__ import annotations

import hashlib
import json
import re

from tests.conftest import REPO_ROOT

EVIDENCE = REPO_ROOT / "docs" / "platform-v2" / "EVIDENCE.json"
CONTRACT = REPO_ROOT / "docs" / "platform-v2" / "PLATFORM_CONTRACTEN.md"
#: sha256 van ai-server-manager docs/platform-v2/PLATFORM_CONTRACTEN.md (contracts 1.2, bron-commit 8b54f39)
CONTRACT_SHA256 = "6a9d29bd94fd991ac5d7876c1a5e51455d471c8bb2833dbed5c52c915b7df424"

STATUS_WORK = {"Niet gestart", "Bezig", "Geblokkeerd", "N.v.t."}  # 'Gereed' nooit in deze bouwomgeving
STATUS_TEST = {"Niet gestart", "Bezig", "Mislukt", "Geblokkeerd", "N.v.t."}  # 'Geslaagd' nooit
STATUS_DECISION = {"Open", "Ingevuld", "Getest", "Geblokkeerd", "N.v.t."}
VALIDATION = {
    "Sim-getest",
    "Unit-getest",
    "NIET GEVALIDEERD (echte NAS)",
    "NIET GEVALIDEERD (hardware)",
    "NIET GEVALIDEERD (account)",
    "NIET GEVALIDEERD (Mac/Xcode)",
}
VALIDATION_TEST = {"Sim-geslaagd", "Sim-mislukt"}
FIELDS = {"id", "status", "validatie", "bewijs", "blokkade", "opmerking", "repo", "bijgewerkt"}


def test_evidence_follows_section_11() -> None:
    items = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert isinstance(items, list) and items
    for item in items:
        assert set(item) == FIELDS, item["id"]
        assert item["repo"] == "appstore-en-fork" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["bijgewerkt"])
        ident = item["id"]
        if re.fullmatch(r"EX\d{2}", ident):
            statuses, values = STATUS_TEST, VALIDATION | VALIDATION_TEST
        elif re.fullmatch(r"D\d{2}", ident):
            statuses, values = STATUS_DECISION, VALIDATION
        else:
            statuses, values = STATUS_WORK, VALIDATION
        assert item["status"] in statuses, (ident, item["status"])
        if item["validatie"]:
            parts = item["validatie"].split("; ")
            assert all(part in values for part in parts), (ident, item["validatie"])
        assert "Gereed" not in item["status"] and "Geslaagd" not in item["status"]


def test_contract_copy_is_version_1_2_and_byte_equal_to_the_source() -> None:
    data = CONTRACT.read_bytes()
    assert b"Versie: `contracts 1.2`" in data
    assert hashlib.sha256(data).hexdigest() == CONTRACT_SHA256
