"""Manifeststandaard volgens contract 1.3 §9 (C1.3-ASF-08): install.reason, HUB_* gereserveerd, geheimen die de Hub
maakt, accepted_findings met --strict, stopped_snapshot, kind: checklist, post_install_checks, same_as_host en het
monitorplan. Elke regel heeft een positieve én een negatieve test."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from appstore.manifest import validate_data
from appstore.manifest.checklist import schema as checklist_schema
from appstore.manifest.checklist import validate_checklist_data, validate_checklist_text
from appstore.manifest.cli import main
from appstore.manifest.translate import Target, translate
from tests.conftest import FIXTURES, REPO_ROOT, load_fixture

CATALOG_13 = "valid-catalog-1-3.manifest.yaml"
UPSTREAM = "valid-custom-upstream.manifest.yaml"
CHECKLIST = FIXTURES / "checklists" / "I01_TRUENAS_BASIS.checklist.yaml"


def codes(data: dict[str, Any]) -> list[str]:
    report, _ = validate_data(data)
    return report.codes()


def fixture(name: str) -> dict[str, Any]:
    return copy.deepcopy(load_fixture(name))


def test_new_catalog_fixture_is_strict_valid_with_one_accepted_finding() -> None:
    report, model = validate_data(fixture(CATALOG_13))
    assert report.valid and report.strict_valid and report.accepted == 1, report.as_text()
    assert model is not None and model.install.reason == "meerdere_containers"
    assert model.network.ports[0].container_port == "same_as_host"
    assert model.post_install_checks[0].id == "eerste_beheerder" and model.backup.db_export.method == "stopped_snapshot"


def test_catalog_app_without_reason_warns_and_fails_strict() -> None:
    data = fixture(CATALOG_13)
    del data["install"]["reason"]
    report, _ = validate_data(data)
    assert report.valid and not report.strict_valid and "install_reason_ontbreekt" in report.codes()


@pytest.mark.parametrize(
    ("where", "name"),
    [("param", "hub_url"), ("param", "HUB_token".lower()), ("env", "HUB_URL"), ("env", "Hub_Key_Fingerprint")],
)
def test_hub_names_are_reserved(where: str, name: str) -> None:
    data = fixture(UPSTREAM)
    if where == "param":
        data["parameters"].append(
            {"name": name, "type": "url", "source": "wizard", "required": False, "description_nl": "Adres van de Hub."}
        )
        data["custom_app"]["env"].append({"name": "X_ADRES", "value_from": {"param": name}})
    else:
        data["custom_app"]["env"].append({"name": name, "value": "x"})
    report, _ = validate_data(data)
    assert not report.valid and "reserved_env" in report.codes()


def test_hub_names_in_catalog_values_are_reserved() -> None:
    data = fixture(CATALOG_13)
    data["values"]["additional_envs"] = [{"name": "HUB_ENROLLMENT_TOKEN", "value": "x"}]
    assert "reserved_env" in codes(data)


def test_hub_generated_secret_is_an_acceptable_warning_but_other_secrets_are_not() -> None:
    data = fixture(CATALOG_13)
    data["accepted_findings"] = []
    report, _ = validate_data(data)
    assert report.valid and not report.strict_valid and report.codes() == ["secret_in_form"]
    # geheim met source 'secret' (de gebruiker vult het in): de waarschuwing is niet te accepteren
    data = fixture(CATALOG_13)
    for p in data["parameters"]:
        if p["name"] == "db_password":
            p["source"] = "secret"
    report, _ = validate_data(data)
    assert "accepted_finding_unused" in report.codes() and not report.strict_valid


def test_secret_flag_needs_generated_or_secret_source_and_no_example() -> None:
    data = fixture(CATALOG_13)
    data["parameters"][0]["secret"] = True  # timezone, source wizard
    assert "secret_param_invalid" in codes(data)
    data = fixture(CATALOG_13)
    data["parameters"][2]["example"] = "geheim123"
    assert "secret_literal_forbidden" in codes(data)


def test_generated_secret_in_custom_app_env_is_a_warning_not_an_error() -> None:
    data = fixture(UPSTREAM)
    data["parameters"].append(
        {
            "name": "api_key",
            "type": "string",
            "source": "generated",
            "secret": True,
            "required": True,
            "description_nl": "Sleutel die de Hub maakt.",
        }
    )
    data["custom_app"]["env"].append({"name": "APP_KEY", "value_from": {"param": "api_key"}})
    report, _ = validate_data(data)
    assert report.valid and report.codes() == ["secret_in_form"]
    data["accepted_findings"] = [{"code": "secret_in_form", "reden_nl": "De Hub maakt deze sleutel in zijn vault."}]
    report, _ = validate_data(data)
    assert report.strict_valid and report.findings[0].accepted
    data["parameters"][-1]["secret"] = False  # generated zonder secret: blijft een fout in het formulier
    data["accepted_findings"] = []
    report, _ = validate_data(data)
    assert not report.valid and "secret_in_form" in report.codes()


@pytest.mark.parametrize(
    ("code", "reden"),
    [("backup_db_export_none", "Geen export mogelijk."), ("digest_pending_release", "Nog geen release van de image.")],
)
def test_accepted_code_that_does_not_occur_fails_strict(code: str, reden: str) -> None:
    data = fixture(UPSTREAM)
    data["accepted_findings"] = [{"code": code, "reden_nl": reden}]
    report, _ = validate_data(data)
    assert report.valid and not report.strict_valid and report.codes() == ["accepted_finding_unused"]


def test_only_the_fixed_list_can_be_accepted_and_errors_never() -> None:
    data = fixture(UPSTREAM)
    data["accepted_findings"] = [{"code": "imprecise_version_tag", "reden_nl": "Dit mag niet geaccepteerd worden."}]
    assert not validate_data(data)[0].valid  # schema: alleen de vaste lijst
    data = fixture(UPSTREAM)
    data["accepted_findings"] = [{"code": "digest_pending_release", "reden_nl": "Probeer een fout te verbergen."}]
    data["image"]["digest"] = None  # upstream zonder digest = fout digest_missing, nooit te accepteren
    report, _ = validate_data(data)
    assert not report.valid and "digest_missing" in report.codes()
    data = fixture(UPSTREAM)
    data["accepted_findings"] = [
        {"code": "backup_db_export_none", "reden_nl": "Eerste keer met een reden."},
        {"code": "backup_db_export_none", "reden_nl": "Tweede keer dezelfde code."},
    ]
    assert "duplicate_name" in codes(data)


def test_backup_db_export_none_is_acceptable_with_reason() -> None:
    data = fixture(UPSTREAM)
    data["upgrade"] = {"migrations": True, "tag_only_rollback_safe": False}
    report, _ = validate_data(data)
    assert report.codes() == ["backup_db_export_none"] and not report.strict_valid
    data["accepted_findings"] = [{"code": "backup_db_export_none", "reden_nl": "SQLite zonder exportfunctie."}]
    assert validate_data(data)[0].strict_valid


def test_stopped_snapshot_needs_a_dataset() -> None:
    data = fixture(CATALOG_13)
    data["backup"]["snapshot_datasets"] = []
    assert "stopped_snapshot_without_dataset" in codes(data)


def test_same_as_host_only_for_catalog_apps() -> None:
    data = fixture(UPSTREAM)
    data["network"]["ports"][0]["container_port"] = "same_as_host"
    assert "same_as_host_not_allowed" in codes(data)
    data["network"]["ports"][0]["container_port"] = "anders"
    assert not validate_data(data)[0].valid  # alleen een poortnummer of precies same_as_host


def test_monitor_plan_is_expected_and_typed() -> None:
    data = fixture(UPSTREAM)
    del data["healthcheck"]["monitor"]
    report, _ = validate_data(data)
    assert report.valid and not report.strict_valid and report.codes() == ["healthcheck_monitor_missing"]
    data["healthcheck"]["monitor"] = {"pad": "geen-pad", "verwachte_status": 200, "timeout_s": 5, "interval_s": 60}
    assert not validate_data(data)[0].valid
    data["healthcheck"]["monitor"] = {"pad": "/x", "verwachte_status": 700, "timeout_s": 5, "interval_s": 60}
    assert not validate_data(data)[0].valid


def test_post_install_checks_ids_unique_and_shown_in_requirements() -> None:
    data = fixture(CATALOG_13)
    data["post_install_checks"].append(dict(data["post_install_checks"][0]))
    assert "duplicate_name" in codes(data)
    data = fixture(CATALOG_13)
    data["post_install_checks"][0]["soort"] = "automatisch"
    assert not validate_data(data)[0].valid


def test_upstream_custom_app_names_the_no_new_privileges_risk() -> None:
    data = fixture(UPSTREAM)
    data["privilege_reason"] = "Hardware-transcodering via de Intel-GPU (/dev/dri)."
    report, _ = validate_data(data)
    assert report.valid and report.codes() == ["restrisico_nnp_ontbreekt"] and not report.strict_valid


def test_hub_generated_secret_is_never_translated_into_values_yet() -> None:
    """C1.3-ASF-14 (P2, nog niet gebouwd): de vertaling zet alleen een vault-verwijzing, nooit een waarde. Tot dan
    weigert de vertaling een geheime waarde eerlijk; er komt nooit een geheim in de payload."""
    _, model = validate_data(fixture(CATALOG_13))
    assert model is not None
    secret = "geheim-" + "x" * 32
    result = translate(
        model,
        {"timezone": "Etc/UTC", "web_port": 30995, "data_path": "/mnt/p1/sso", "db_password": secret},
        Target(truenas_version="25.10.1"),
    )
    assert result.payload is None and not result.ready_for_install
    assert "parameter_value_not_allowed" in [f.code for f in result.findings]
    assert secret not in result.model_dump_json()


# ---------------------------------------------------------------------------------------------- kind: checklist
def test_checklist_is_refused_by_the_manifest_validator_and_valid_as_checklist() -> None:
    data = yaml.safe_load(CHECKLIST.read_text(encoding="utf-8"))
    report, model = validate_data(data)
    assert model is None and report.codes() == ["checklist_not_installable"]
    assert validate_checklist_data(data).valid


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda d: d.pop("kind"), "required_field_missing"),
        (lambda d: d.update({"install": {"method": "custom_app"}}), "invalid_value"),
        (lambda d: d.update({"mutaties_door_platform": True}), "invalid_value"),
        (lambda d: d["stappen"][0].update({"id": "I02-01-01"}), "invalid_value"),
        (lambda d: d["stappen"][1].update({"id": d["stappen"][0]["id"]}), "duplicate_name"),
        (lambda d: d["stappen"][0].update({"besluiten": ["D18"]}), "decision_not_listed"),
        (
            lambda d: d["stappen"][0]["truenas_methoden"].append({"naam": "app.update", "versies": ["25.04"]}),
            "install_method_forbidden",
        ),
        (
            lambda d: d["stappen"][0].update({"uitvoeren_nl": "Kopieer naar /mnt/tank/apps op 192.168.1.10."}),
            "example_value_as_real",
        ),
    ],
)
def test_checklist_rules_reject_wrong_documents(change: Any, code: str) -> None:
    data = yaml.safe_load(CHECKLIST.read_text(encoding="utf-8"))
    change(data)
    report = validate_checklist_data(data)
    assert not report.valid and code in report.codes(), report.as_text()


def test_checklist_schema_copies_are_byte_equal() -> None:
    pkg = Path(__file__).resolve().parents[2] / "src" / "appstore" / "manifest" / "checklist-v1.schema.json"
    repo = REPO_ROOT / "schemas" / "checklist-v1.schema.json"
    assert pkg.read_bytes() == repo.read_bytes()
    assert checklist_schema()["properties"]["kind"] == {"const": "checklist"}


def test_cli_checklist_and_strict(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["checklist", str(CHECKLIST)]) == 0
    capsys.readouterr()
    assert main(["checklist", "--format", "json", str(CHECKLIST)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is True and out["result"] == "geldig" and out["app_id"] == "I01"
    assert main(["validate", str(CHECKLIST)]) == 1
    assert main(["validate", "--strict", str(FIXTURES / "manifests" / CATALOG_13)]) == 0
    assert validate_checklist_text(b"- geen object").valid is False
