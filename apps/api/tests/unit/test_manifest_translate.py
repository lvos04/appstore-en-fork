"""Vertaling naar TrueNAS app.create (standaard Custom App = ix-app, of catalogusapp)."""

from __future__ import annotations

import json
import logging
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from appstore.manifest import validate_data
from appstore.manifest.model import Manifest
from appstore.manifest.translate import Target, TranslationResult, VaultRef, translate
from appstore.manifest.truenas import AppCreatePayload
from tests.conftest import FIXTURES, load_fixture

FIELDS = json.loads((FIXTURES / "truenas" / "ix-app-1.4.8.fields.json").read_text())["fields"]
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.thuis.test:30999",
    "timezone": "Europe/Amsterdam",
}
TARGET = Target(truenas_version="25.10.1", ix_app_version="1.4.8")


def _model(data: dict[str, Any]) -> Manifest:
    report, model = validate_data(data)
    assert model is not None, report.as_text()
    return model


def _flatten(value: Any, path: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            sub = f"{path}.{k}" if path else k
            out.append((sub, v))
            if not (path.endswith("nvidia_gpu_selection")):
                out.extend(_flatten(v, sub))
    elif isinstance(value, list):
        for v in value:
            out.append((f"{path}[]", v))
            out.extend(_flatten(v, f"{path}[]"))
    return out


_TYPES = {
    "string": str,
    "text": str,
    "path": str,
    "hostpath": str,
    "int": int,
    "boolean": bool,
    "dict": dict,
    "list": list,
}


def test_custom_app_values_match_ix_app_1_4_8_form(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), PARAMS, TARGET)
    assert result.ready_for_install, [f.as_text() for f in result.findings]
    assert result.payload is not None
    payload = result.payload
    assert payload.custom_app is False
    assert (payload.catalog_app, payload.train, payload.version) == ("ix-app", "stable", "1.4.8")
    assert "custom_compose_config" not in payload.model_dump()
    for path, value in _flatten(payload.values):
        assert path in FIELDS, f"{path} bestaat niet in het ix-app 1.4.8-formulier"
        expected = _TYPES[FIELDS[path]["type"]]
        assert isinstance(value, expected), f"{path}: {type(value).__name__} ≠ {FIELDS[path]['type']}"
        if "enum" in FIELDS[path]:
            assert value in FIELDS[path]["enum"], f"{path}: {value!r} niet in {FIELDS[path]['enum']}"

    def shown(meta: dict[str, Any]) -> bool:  # show_if [[veld, "=", waarde]] uit questions.yaml
        return all(payload.values.get(var) == val for var, op, val in meta.get("show_if", []) if op == "=")

    required_top = [k for k, v in FIELDS.items() if "." not in k and "[" not in k and v.get("required") and shown(v)]
    for key in required_top:
        assert key in payload.values, f"verplicht formulierveld {key} ontbreekt"


def test_custom_app_values_content(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), PARAMS, TARGET)
    assert result.payload is not None
    v = result.payload.values
    assert v["image"] == {
        "repository": "docker.io/library/nginx",
        "tag": "1.27.3@sha256:" + "1" * 64,
        "pull_policy": "missing",
    }
    assert v["ports"] == [
        {"bind_mode": "published", "port_number": 30999, "container_port": 8080, "protocol": "tcp", "host_ips": []}
    ]
    assert v["storage"][0] == {
        "type": "host_path",
        "read_only": False,
        "mount_path": "/config",
        "host_path_config": {"acl_enable": False, "path": "/mnt/p1/apps/voorbeeld"},
    }
    assert v["storage"][1]["read_only"] is True
    assert v["storage"][2] == {
        "type": "tmpfs",
        "read_only": False,
        "mount_path": "/cache",
        "tmpfs_config": {"size": 256},
    }
    assert v["envs"] == [
        {"name": "LOG_LEVEL", "value": "info"},
        {"name": "PUBLIC_URL", "value": "http://nas.thuis.test:30999"},
    ]
    assert v["TZ"] == "Europe/Amsterdam"
    assert v["privileged"] is False and v["host_network"] is False
    assert v["run_as_custom_user"] is True and v["run_as"] == {"user": 568, "group": 568}
    assert v["resources"] == {
        "enable_resource_limits": True,
        "limits": {"cpus": 2, "memory": 1024},
        "gpus": {"use_all_gpus": False, "nvidia_gpu_selection": {}},  # geen GPU-waarde uit de wizard → none
    }
    assert v["portals"] == [
        {"name": "Web UI", "scheme": "http", "use_node_ip": True, "host": "", "port": 30999, "path": "/"}
    ]
    labels = {lbl["key"]: lbl["value"] for lbl in v["labels"]}
    assert labels["nl.lvos04.platform.app_id"] == "voorbeeld-web"
    assert "max_retry_count" not in v and "run_as" in v
    assert result.preview_hash and result.preview_hash.startswith("sha256:")


def test_values_are_typed_not_templated(custom_manifest: dict[str, Any]) -> None:
    hostile = dict(PARAMS, public_url='http://x"; rm -rf /; echo "')
    result = translate(_model(custom_manifest), hostile, TARGET)
    assert result.payload is not None
    env = result.payload.values["envs"][1]
    assert env == {"name": "PUBLIC_URL", "value": 'http://x"; rm -rf /; echo "'}  # letterlijk, nooit uitgevoerd
    assert "${" not in json.dumps(result.payload.values)


def test_host_port_override_must_be_int(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), dict(PARAMS, web_port="30999"), TARGET)
    assert not result.ready_for_install and result.payload is None
    assert "parameter_value_invalid" in [f.code for f in result.findings]


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("config_path", "/"),
        ("config_path", "/etc"),
        ("config_path", "/var/run/docker.sock"),
        ("config_path", "/mnt"),
        ("config_path", "/mnt/<POOL>/apps"),
        ("timezone", "Mars/Olympus"),
        ("public_url", "ftp://x"),
        ("public_url", "http://gebruiker:wachtwoord@nas:1"),
    ],
)
def test_invalid_parameter_values_block(custom_manifest: dict[str, Any], param: str, value: str) -> None:
    result = translate(_model(custom_manifest), dict(PARAMS, **{param: value}), TARGET)
    assert not result.ready_for_install
    assert result.payload is None
    assert "parameter_value_invalid" in [f.code for f in result.findings]


def test_missing_wizard_values_are_never_assumed(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), {}, TARGET)
    assert not result.ready_for_install and result.payload is None
    missing = [f.path for f in result.findings if f.code == "parameter_value_missing"]
    assert len(missing) == 4  # config_path, media_path, public_url, timezone; web_port heeft een default


def test_unknown_parameter_value_refused(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), dict(PARAMS, extra="x"), TARGET)
    assert "parameter_value_unknown" in [f.code for f in result.findings]
    assert not result.ready_for_install


def test_unknown_versions_block(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), PARAMS, Target())
    codes = [f.code for f in result.findings]
    assert {"truenas_version_unknown", "ix_app_version_unknown"} <= set(codes)
    assert result.compatibility.status == "unknown"
    assert result.payload is None and not result.ready_for_install


@pytest.mark.parametrize(
    ("target", "code", "status"),
    [
        (Target(truenas_version="24.04.2", ix_app_version="1.4.8"), "truenas_version_incompatible", "incompatible"),
        (Target(truenas_version="25.10.1", ix_app_version="1.1.15"), "ix_app_version_unsupported", "incompatible"),
        (Target(truenas_version="25.10.1", ix_app_version="2.0.0"), "ix_app_version_unsupported", "incompatible"),
        (Target(truenas_version="onzin", ix_app_version="1.4.8"), "truenas_version_invalid", "unknown"),
    ],
)
def test_compatibility(custom_manifest: dict[str, Any], target: Target, code: str, status: str) -> None:
    result = translate(_model(custom_manifest), PARAMS, target)
    assert code in [f.code for f in result.findings]
    assert result.compatibility.status == status
    assert not result.ready_for_install


def test_unverified_ix_app_version_warns_but_translates(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), PARAMS, Target(truenas_version="25.10.1", ix_app_version="1.4.9"))
    assert "ix_app_version_unverified" in [f.code for f in result.findings]
    assert result.ready_for_install


def test_own_image_without_digest_not_ready(own_manifest_text: str) -> None:
    from appstore.manifest import validate_text

    report, model, _ = validate_text(own_manifest_text)
    assert model is not None
    result = translate(
        model,
        {"appdata_path": "/mnt/p1/apps/appstore", "timezone": "Etc/UTC"},
        TARGET,
        manifest_sha256=report.manifest_sha256,
    )
    assert "digest_not_pinned" in [f.code for f in result.findings]
    assert result.payload is not None  # preview mag, installeren niet
    assert not result.ready_for_install
    assert result.payload.values["image"]["tag"] == "0.1.0"


def test_catalog_app_translation(catalog_manifest: dict[str, Any]) -> None:
    result = translate(
        _model(catalog_manifest),
        {"timezone": "Etc/UTC", "library_path": "/mnt/p1/fotos"},
        Target(truenas_version="25.10.1"),
    )
    assert result.ready_for_install, [f.as_text() for f in result.findings]
    payload = result.payload
    assert isinstance(payload, AppCreatePayload)
    assert (payload.catalog_app, payload.train, payload.version, payload.custom_app) == (
        "voorbeeldapp",
        "community",
        "1.6.12",
        False,
    )
    assert payload.values["network"]["web_port"]["port_number"] == 30998  # int, geen tekst
    assert payload.values["storage"]["library"]["host_path_config"]["path"] == "/mnt/p1/fotos"


def test_catalog_secret_param_is_a_vault_reference_not_a_value(catalog_manifest: dict[str, Any]) -> None:
    """C1.3-ASF-14: een geheime parameter wordt nooit ingevuld; de payload krijgt alleen de vault-verwijzing."""
    catalog_manifest["parameters"].append(
        {
            "name": "db_pw",
            "type": "secret_ref",
            "source": "generated",
            "required": True,
            "description_nl": "databasewachtwoord",
        }
    )
    catalog_manifest["values"]["db_password"] = {"param": "db_pw"}
    model = _model(catalog_manifest)
    result = translate(
        model,
        {"timezone": "Etc/UTC", "library_path": "/mnt/p1/fotos"},
        Target(truenas_version="25.10.1"),
        trusted_app_id=True,
    )
    assert "parameter_value_not_allowed" not in [f.code for f in result.findings]
    assert result.payload is not None and result.ready_for_install
    assert result.payload.values["db_password"] == "vault://app/voorbeeld-catalogus/db_pw"
    assert [r.model_dump() for r in result.vault_refs] == [
        {"path": "values.db_password", "parameter": "db_pw", "ref": "vault://app/voorbeeld-catalogus/db_pw"}
    ]


# ---------------------------------------------------------------------------------------------- C1.3-ASF-14
CATALOG_13 = "valid-catalog-1-3.manifest.yaml"
CATALOG_13_PARAMS = {"timezone": "Etc/UTC", "data_path": "/mnt/p1/sso"}
CATALOG_13_TARGET = Target(truenas_version="25.10.1")
OWN_REF = "vault://app/voorbeeld-sso/db_password"
#: Testwaarde die op een geheim lijkt; hij mag nergens in de uitvoer terugkomen (V-094).
SECRET = "Geheim-" + "Zq7" * 12


def _catalog_13() -> dict[str, Any]:
    return load_fixture(CATALOG_13)


def _with_env_secret(custom_manifest: dict[str, Any], **param: Any) -> dict[str, Any]:
    """Custom App met een omgevingsvariabele naar een geheim dat de Hub maakt (besluit gebruiker 2026-11-11)."""
    custom_manifest["parameters"].append(
        {
            "name": "api_key",
            "type": "string",
            "source": "generated",
            "secret": True,
            "required": True,
            "description_nl": "Sleutel die de Hub maakt (vault).",
            **param,
        }
    )
    custom_manifest["custom_app"]["env"].append({"name": "APP_KEY", "value_from": {"param": "api_key"}})
    return custom_manifest


def test_vault_ref_marker_has_exactly_these_fields() -> None:
    """V-046: de markering in de uitvoer heeft precies deze velden, niet meer en niet minder."""
    assert set(VaultRef.model_fields) == {"path", "parameter", "ref"}
    assert set(TranslationResult.model_fields) == {
        "ready_for_install",
        "method",
        "payload",
        "preview_hash",
        "compatibility",
        "digest_readback",
        "vault_refs",
        "findings",
    }
    result = translate(_model(_catalog_13()), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    dumped = result.model_dump(mode="json")
    assert [set(r) for r in dumped["vault_refs"]] == [{"path", "parameter", "ref"}]


def test_catalog_hub_secret_becomes_only_a_vault_reference() -> None:
    result = translate(_model(_catalog_13()), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert result.ready_for_install, [f.as_text() for f in result.findings]
    assert result.payload is not None
    assert result.payload.values["voorbeeld"] == {"db_password": OWN_REF}
    assert [r.model_dump() for r in result.vault_refs] == [
        {"path": "values.voorbeeld.db_password", "parameter": "db_password", "ref": OWN_REF}
    ]
    # de preview-hash bindt de verwijzing, nooit een waarde
    assert result.preview_hash and result.preview_hash.startswith("sha256:")


def test_vault_reference_uses_manifest_app_id_not_the_app_name() -> None:
    result = translate(
        _model(_catalog_13()), CATALOG_13_PARAMS, CATALOG_13_TARGET, app_name="sso-thuis", trusted_app_id=True
    )
    assert result.payload is not None and result.payload.app_name == "sso-thuis"
    assert result.payload.values["voorbeeld"]["db_password"] == OWN_REF
    assert [r.ref for r in result.vault_refs] == [OWN_REF]


@pytest.mark.parametrize(
    "change",
    [
        {"type": "string", "source": "generated", "secret": True},
        {"type": "string", "source": "secret", "secret": True},
        {"type": "secret_ref", "source": "generated", "secret": True},
        {"type": "secret_ref", "source": "secret"},
        {"type": "string", "source": "generated", "secret": False},
    ],
)
def test_every_kind_of_secret_parameter_becomes_a_reference(change: dict[str, Any]) -> None:
    """Bouwpunt 1: source generated, source secret en type secret_ref leveren alleen de verwijzing."""
    data = _catalog_13()
    for p in data["parameters"]:
        if p["name"] == "db_password":
            p.update(change)
    result = translate(_model(data), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert result.payload is not None, [f.as_text() for f in result.findings]
    assert result.payload.values["voorbeeld"]["db_password"] == OWN_REF
    assert [r.parameter for r in result.vault_refs] == ["db_password"]


def test_supplying_exactly_the_own_reference_is_the_same_translation() -> None:
    without = translate(_model(_catalog_13()), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    with_ref = translate(
        _model(_catalog_13()), dict(CATALOG_13_PARAMS, db_password=OWN_REF), CATALOG_13_TARGET, trusted_app_id=True
    )
    assert with_ref.ready_for_install and with_ref.payload == without.payload
    assert with_ref.preview_hash == without.preview_hash and with_ref.vault_refs == without.vault_refs


@pytest.mark.parametrize(
    "value",
    [
        SECRET,
        "hunter2hunter2",
        "vault://app/andere-app/db_password",  # verwijzing naar een andere app
        "vault://app/voorbeeld-sso-x/db_password",  # lijkt op de eigen app-ID
        "vault://app/voorbeeld-sso/secret_key",  # andere parameter van dezelfde app
        "vault://voorbeeld-sso/db_password",  # oude vorm met twee delen
        "vault://app/voorbeeld-sso/db_password/extra",
        "vault://app/voorbeeld-sso/db_password\n",
        " vault://app/voorbeeld-sso/db_password",
        "VAULT://APP/voorbeeld-sso/db_password",
        "",
        12345678,
        True,
        None,
        {"param": "db_password"},
        [OWN_REF],
    ],
)
def test_value_in_place_of_the_reference_is_refused_without_echo(value: Any) -> None:
    """Bouwpunt 2: alles behalve precies de eigen verwijzing wordt geweigerd; de melding noemt alleen de parameter."""
    result = translate(
        _model(_catalog_13()), dict(CATALOG_13_PARAMS, db_password=value), CATALOG_13_TARGET, trusted_app_id=True
    )
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    refused = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert len(refused) == 1 and refused[0].path == "values.voorbeeld.db_password"
    assert "'db_password'" in refused[0].message
    if isinstance(value, str) and value.strip() and value.strip() != OWN_REF:  # de eigen verwijzing is geen geheim
        assert value.strip() not in result.model_dump_json()
        assert value.strip() not in " ".join(f.as_text() for f in result.findings)


def test_reference_to_another_app_names_the_reason_not_the_value() -> None:
    foreign = "vault://app/andere-app/db_password"
    result = translate(
        _model(_catalog_13()), dict(CATALOG_13_PARAMS, db_password=foreign), CATALOG_13_TARGET, trusted_app_id=True
    )
    (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert "andere app" in refused.message and "andere-app" not in refused.message
    assert "andere-app" not in result.model_dump_json()


def test_custom_app_env_hub_secret_becomes_only_a_vault_reference(custom_manifest: dict[str, Any]) -> None:
    """Besluit gebruiker 2026-11-11: ook een Custom App-env naar een geheim dat de Hub maakt krijgt de verwijzing."""
    result = translate(
        _model(_with_env_secret(custom_manifest)), PARAMS, TARGET, app_name="web-thuis", trusted_app_id=True
    )
    assert result.ready_for_install, [f.as_text() for f in result.findings]
    assert result.payload is not None
    assert result.payload.values["envs"][2] == {"name": "APP_KEY", "value": "vault://app/voorbeeld-web/api_key"}
    assert [r.model_dump() for r in result.vault_refs] == [
        {"path": "values.envs[2].value", "parameter": "api_key", "ref": "vault://app/voorbeeld-web/api_key"}
    ]
    for path, _value in _flatten(result.payload.values):  # het formulier van ix-app 1.4.8 blijft kloppen
        assert path in FIELDS, path


def test_custom_app_env_secret_value_or_foreign_reference_is_refused(custom_manifest: dict[str, Any]) -> None:
    model = _model(_with_env_secret(custom_manifest))
    for value in (SECRET, "vault://app/andere-app/api_key"):
        result = translate(model, dict(PARAMS, api_key=value), TARGET, trusted_app_id=True)
        assert result.payload is None and not result.ready_for_install and result.vault_refs == []
        (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
        assert refused.path == "custom_app.env[2].value_from" and "'api_key'" in refused.message
        assert value not in result.model_dump_json()
    ok = translate(model, dict(PARAMS, api_key="vault://app/voorbeeld-web/api_key"), TARGET, trusted_app_id=True)
    assert ok.ready_for_install and [r.ref for r in ok.vault_refs] == ["vault://app/voorbeeld-web/api_key"]


def test_secret_without_text_place_is_still_refused(custom_manifest: dict[str, Any]) -> None:
    """Een verwijzing alleen op een tekstplek: een geheime poort, tijdzone of hostpad krijgt nooit een verwijzing."""
    for p in custom_manifest["parameters"]:
        if p["name"] == "web_port":
            p.update(source="generated", secret=True)
            p.pop("default")
    result = translate(_model(custom_manifest), PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert refused.path == "network.ports[0].host_port"
    assert "vault://" not in result.model_dump_json()


def test_secret_of_a_non_text_type_gets_no_reference() -> None:
    data = _catalog_13()
    for p in data["parameters"]:
        if p["name"] == "web_port":
            p.update(source="generated", secret=True)
            p.pop("default")
    result = translate(_model(data), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    refused = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert [f.path for f in refused] == ["values.network.web_port.port_number"]


def test_custom_app_env_only_takes_a_secret_that_the_hub_makes(custom_manifest: dict[str, Any]) -> None:
    """Verdediging in de diepte: de validator weigert al een ander geheim in het formulier; de vertaling ook."""
    model = _model(_with_env_secret(custom_manifest))
    data = model.model_dump(mode="json")
    for p in data["parameters"]:
        if p["name"] == "api_key":
            p["source"] = "secret"
    forged = Manifest.model_validate(data)  # buiten de validator om
    result = translate(forged, PARAMS, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    assert [f.path for f in result.findings if f.code == "parameter_value_not_allowed"] == [
        "custom_app.env[2].value_from"
    ]


@pytest.mark.parametrize(
    "literal",
    [
        "vault://app/andere-app/db_password",
        OWN_REF,
        "VAULT://app/andere-app/geheim",
        "prefix vault://app/andere-app/geheim",
    ],
)
def test_literal_vault_reference_in_catalog_values_is_refused(literal: str) -> None:
    """Alleen de vertaling maakt een verwijzing: een letterlijke verwijzing in het manifest zou de Hub het geheim van
    een andere app laten invullen."""
    data = _catalog_13()
    data["values"]["voorbeeld"]["andere"] = literal
    result = translate(_model(data), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    refused = [f for f in result.findings if f.code == "vault_ref_not_allowed"]
    assert [f.path for f in refused] == ["values.voorbeeld.andere"]
    assert literal not in " ".join(f.message for f in result.findings)


def test_literal_vault_reference_in_custom_app_env_is_refused(custom_manifest: dict[str, Any]) -> None:
    custom_manifest["custom_app"]["env"].append({"name": "OTHER", "value": "vault://app/andere-app/db_password"})
    result = translate(_model(custom_manifest), PARAMS, TARGET)
    assert result.payload is None and result.vault_refs == []
    assert [f.path for f in result.findings if f.code == "vault_ref_not_allowed"] == ["values.envs[2].value"]


def test_vault_reference_in_a_normal_parameter_is_refused(custom_manifest: dict[str, Any]) -> None:
    """Een gewone (wizard)parameter kan geen verwijzing binnensmokkelen."""
    result = translate(_model(custom_manifest), dict(PARAMS, public_url="http://x/vault://app/a/b"), TARGET)
    assert result.payload is None
    (invalid,) = [f for f in result.findings if f.code == "parameter_value_invalid"]
    assert invalid.path == "custom_app.env[1].value_from" and "vault://app/a/b" not in invalid.message


def test_secret_never_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    translate(
        _model(_catalog_13()), dict(CATALOG_13_PARAMS, db_password=SECRET), CATALOG_13_TARGET, trusted_app_id=True
    )
    assert SECRET not in caplog.text


def test_cli_translate_never_prints_the_secret(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from appstore.manifest.cli import main

    params = tmp_path / "params.json"
    cmd = [
        "translate",
        str(FIXTURES / "manifests" / CATALOG_13),
        "--params",
        str(params),
        "--truenas-version",
        "25.10.1",
    ]
    params.write_text(json.dumps(dict(CATALOG_13_PARAMS, db_password=SECRET)), encoding="utf-8")
    code = main(cmd)
    out = capsys.readouterr()
    assert code != 0 and SECRET not in out.out and SECRET not in out.err
    assert "parameter_value_not_allowed" in out.out
    params.write_text(json.dumps(CATALOG_13_PARAMS), encoding="utf-8")
    assert main(cmd) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["vault_refs"] == [
        {"path": "values.voorbeeld.db_password", "parameter": "db_password", "ref": OWN_REF}
    ]


# ---------------------------------------------------------------------------------------------- herstel review S-P2-500
def test_untrusted_app_id_gets_no_vault_reference(custom_manifest: dict[str, Any]) -> None:
    """R-VEILIG-1: de app_id van een los manifest staat niet vast. Zonder ``trusted_app_id`` (bijvoorbeeld
    ``POST /catalog/v1/preview`` met ``manifest_yaml``) krijgt een geheim geen verwijzing, ook niet als het manifest
    zich de app_id van een andere app geeft."""
    custom_manifest["app_id"] = "voorbeeld-catalogus"  # de app_id van een andere catalogusapp
    model = _model(_with_env_secret(custom_manifest))
    result = translate(model, PARAMS, TARGET)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert refused.path == "custom_app.env[2].value_from" and "app_id" in refused.message
    assert "vault://" not in result.model_dump_json()
    catalog = translate(_model(_catalog_13()), CATALOG_13_PARAMS, CATALOG_13_TARGET)
    assert catalog.payload is None and catalog.vault_refs == []
    assert [f.path for f in catalog.findings if f.code == "parameter_value_not_allowed"] == [
        "values.voorbeeld.db_password"
    ]


def test_supplied_value_for_an_unused_secret_is_refused() -> None:
    """R-TECH-2: ook een geheime parameter die nergens gebruikt wordt, krijgt nooit een waarde mee."""
    data = _catalog_13()
    data["parameters"].append(
        {
            "name": "extra_secret",
            "type": "string",
            "source": "generated",
            "secret": True,
            "required": True,
            "description_nl": "Ongebruikt geheim.",
        }
    )
    model = _model(data)
    result = translate(model, dict(CATALOG_13_PARAMS, extra_secret=SECRET), CATALOG_13_TARGET, trusted_app_id=True)
    assert result.payload is None and not result.ready_for_install and result.vault_refs == []
    (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert refused.path == "parameters.extra_secret" and "'extra_secret'" in refused.message
    assert SECRET not in result.model_dump_json()
    without = translate(model, CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert without.ready_for_install and [r.parameter for r in without.vault_refs] == ["db_password"]


def test_text_secret_on_a_place_without_reference_is_refused(custom_manifest: dict[str, Any]) -> None:
    """R-TECH-3: een tekstgeheim op een plek die een waarde vraagt (hier de tijdzone) wordt geweigerd met een bevinding,
    nooit met een uitzondering; de validator sluit dit al uit, dus buiten de validator om gemaakt."""
    data = _model(custom_manifest).model_dump(mode="json")
    for p in data["parameters"]:
        if p["name"] == "timezone":
            p.update(type="string", source="generated", secret=True)
    forged = Manifest.model_validate(data)
    params = {k: v for k, v in PARAMS.items() if k != "timezone"}
    result = translate(forged, params, TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert refused.path == "custom_app.timezone" and "plek vraagt een waarde" in refused.message
    assert "vault://" not in result.model_dump_json()


def test_literal_reference_under_a_key_that_looks_like_the_marked_path_is_refused() -> None:
    """R-VEILIG-2 (A en B): een sleutel met '.' of '[i]' geeft hetzelfde pad als de gemarkeerde plek; de controle
    vergelijkt de echte plek, dus een letterlijke verwijzing daar wordt geweigerd."""
    flat = _catalog_13()
    flat["values"]["voorbeeld"] = {"conn": {"param": "db_password"}}
    flat["values"]["voorbeeld.conn"] = OWN_REF
    listed = _catalog_13()
    listed["values"]["voorbeeld"] = {}
    listed["values"]["lijst"] = [{"param": "db_password"}]
    listed["values"]["lijst[0]"] = OWN_REF
    for data in (flat, listed):
        result = translate(_model(data), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
        assert result.payload is None and not result.ready_for_install and result.vault_refs == []
        assert [f.code for f in result.blocking()] == ["vault_ref_not_allowed"]


def test_reference_under_an_ambiguous_key_is_refused() -> None:
    """R-VEILIG-2 (C): een verwijzing onder een sleutel met '.', '[' of ']' zou een dubbelzinnig pad krijgen (de
    executor kan dan het geheim in een ander, openbaar veld zetten): geweigerd."""
    data = _catalog_13()
    data["values"]["voorbeeld"] = {"hostname": "openbaar.test"}
    data["values"]["voorbeeld.hostname"] = {"param": "db_password"}
    result = translate(_model(data), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert refused.path == "values.voorbeeld.hostname" and "dubbelzinnig" in refused.message


@pytest.mark.parametrize(
    "where",
    [
        ("image", "repository"),
        ("TZ",),
        ("notes",),
        ("voorbeeld", "images", "main", "repository"),
        ("labels", 0, "value"),
    ],
)
def test_reference_on_a_public_place_is_refused(where: tuple[Any, ...]) -> None:
    """R-VEILIG-3: geen verwijzing op een plek die openbaar is of gelogd wordt (image, tijdzone, notities, labels)."""
    data = _catalog_13()
    data["values"]["voorbeeld"] = {}
    node: Any = data["values"]
    for here, nxt in pairwise(where):
        if isinstance(node, list):
            node = node[here]
            continue
        node = node.setdefault(here, [{}] if isinstance(nxt, int) else {})
    node[where[-1]] = {"param": "db_password"}
    result = translate(_model(data), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    (refused,) = [f for f in result.findings if f.code == "parameter_value_not_allowed"]
    assert "openbare plek" in refused.message
    assert "vault://" not in result.model_dump_json()


def test_vault_reference_in_a_key_is_refused() -> None:
    """R-MANIFEST-1: ook een sleutel met ``vault://`` in de values wordt geweigerd, zonder de sleutel te herhalen."""
    data = _catalog_13()
    data["values"]["voorbeeld"]["vault://app/andere-app/x"] = "y"
    result = translate(_model(data), CATALOG_13_PARAMS, CATALOG_13_TARGET, trusted_app_id=True)
    assert result.payload is None and result.vault_refs == []
    (refused,) = [f for f in result.findings if f.code == "vault_ref_not_allowed"]
    assert refused.path == "values.voorbeeld" and "andere-app" not in refused.message


# ---------------------------------------------------------------------------------------------- contract 1.2 (M2, M3, M5)
def test_ports_come_only_from_network_ports(custom_manifest: dict[str, Any]) -> None:
    """M2: zonder custom_app.ports bouwt de vertaling de formulierpoorten uit network.ports."""
    assert "ports" not in custom_manifest["custom_app"]
    result = translate(_model(custom_manifest), PARAMS, TARGET)
    assert result.payload is not None
    assert [p["port_number"] for p in result.payload.values["ports"]] == [30999]
    assert result.payload.values["portals"][0]["port"] == 30999


def test_legacy_manifest_translates_identically(custom_manifest: dict[str, Any]) -> None:
    """Overgang: een 1.1-manifest met gelijke custom_app.ports levert precies dezelfde formulierwaarden."""
    legacy = json.loads(json.dumps(custom_manifest))
    legacy["custom_app"]["ports"] = [dict(legacy["network"]["ports"][0])]
    legacy["network"]["joins_platform_network"] = False
    new = translate(_model(custom_manifest), PARAMS, TARGET).payload
    old = translate(_model(legacy), PARAMS, TARGET).payload
    assert new is not None and old is not None
    assert new.values["ports"] == old.values["ports"]


@pytest.mark.parametrize(
    ("gpu_value", "measured", "code", "ready", "all_gpus"),
    [
        ("none", None, None, True, False),
        ("intel_render", None, "gpu_not_measured", False, None),
        ("intel_render", "absent", "gpu_not_measured", False, None),
        ("intel_render", "measured", "gpu_all_render_devices", True, True),
    ],
)
def test_gpu_parameter_requires_measured_render_node(
    custom_manifest: dict[str, Any],
    gpu_value: str,
    measured: str | None,
    code: str | None,
    ready: bool,
    all_gpus: bool | None,
) -> None:
    """M3: GPU als optionele parameter; intel_render alleen met een door D08 gemeten render-node."""
    target = Target(truenas_version="25.10.1", ix_app_version="1.4.8", gpu_render_node=measured)  # type: ignore[arg-type]
    result = translate(_model(custom_manifest), dict(PARAMS, gpu=gpu_value), target)
    codes = [f.code for f in result.findings]
    if code:
        assert code in codes
    assert result.ready_for_install is ready
    if all_gpus is None:
        assert result.payload is None
    else:
        assert result.payload is not None
        assert result.payload.values["resources"]["gpus"]["use_all_gpus"] is all_gpus


def test_gpu_parameter_value_is_typed(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), dict(PARAMS, gpu="nvidia"), TARGET)
    assert "parameter_value_invalid" in [f.code for f in result.findings]
    assert result.payload is None


def test_literal_gpu_without_measurement_blocks(custom_manifest: dict[str, Any]) -> None:
    custom_manifest["custom_app"]["gpu"] = "intel_render"
    result = translate(_model(custom_manifest), PARAMS, TARGET)
    assert "gpu_not_measured" in [f.code for f in result.findings]
    assert not result.ready_for_install


def test_digest_in_tag_with_readback_step(custom_manifest: dict[str, Any]) -> None:
    """M5: tag = <tag>@<digest> plus een verplichte digest-readback (app.image.query, repo_digests)."""
    result = translate(_model(custom_manifest), PARAMS, TARGET)
    assert result.payload is not None
    rb = result.digest_readback
    assert rb is not None
    assert result.payload.values["image"]["tag"] == rb.form_tag == "1.27.3@sha256:" + "1" * 64
    assert (rb.method, rb.field, rb.expected_digest, rb.fallback_tag) == (
        "app.image.query",
        "repo_digests",
        "sha256:" + "1" * 64,
        "1.27.3",
    )
    assert rb.on_mismatch == "stop_en_incident" and "NIET GEVALIDEERD" in rb.note_nl


def test_no_readback_without_digest(own_manifest_text: str) -> None:
    from appstore.manifest import validate_text

    _, model, _ = validate_text(own_manifest_text)
    assert model is not None
    result = translate(model, {"appdata_path": "/mnt/p1/apps/appstore", "timezone": "Etc/UTC"}, TARGET)
    assert result.digest_readback is None and not result.ready_for_install


def test_version_availability_not_measured_warns(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), PARAMS, TARGET)
    assert "version_availability_unknown" in [f.code for f in result.findings]
    assert result.ready_for_install  # waarschuwing; de executor controleert vóór uitvoering


@pytest.mark.parametrize(
    ("ix_app_version", "available", "blocked"),
    [
        ("1.4.8", ["1.4.8", "1.4.9"], False),
        ("1.4.8", ["1.4.9"], True),  # gemeten versie bestaat niet (meer)
        ("1.4.9", ["1.4.9"], True),  # geen geteste versie meer in de catalogus → eerst testen
        ("1.4.9", ["1.4.8", "1.4.9"], False),  # geteste versie nog beschikbaar; 1.4.9 geeft alleen 'unverified'
    ],
)
def test_ix_app_version_unavailable(
    custom_manifest: dict[str, Any], ix_app_version: str, available: list[str], blocked: bool
) -> None:
    target = Target(truenas_version="25.10.1", ix_app_version=ix_app_version, available_versions=available)
    result = translate(_model(custom_manifest), PARAMS, target)
    codes = [f.code for f in result.findings]
    assert ("version_unavailable" in codes) is blocked
    assert result.ready_for_install is not blocked
    if blocked:
        assert result.compatibility.status == "incompatible"
        assert any("Voorstel" in r for r in result.compatibility.reasons_nl)


def test_catalog_version_unavailable_never_picks_newer(catalog_manifest: dict[str, Any]) -> None:
    target = Target(truenas_version="25.10.1", available_versions=["1.7.0"])
    result = translate(_model(catalog_manifest), {"timezone": "Etc/UTC", "library_path": "/mnt/p1/fotos"}, target)
    assert "version_unavailable" in [f.code for f in result.findings]
    assert not result.ready_for_install
    assert result.payload is None or result.payload.version == "1.6.12"  # nooit stil 1.7.0
    ok = translate(
        _model(catalog_manifest),
        {"timezone": "Etc/UTC", "library_path": "/mnt/p1/fotos"},
        Target(truenas_version="25.10.1", available_versions=["1.6.12", "1.7.0"]),
    )
    assert ok.ready_for_install and "version_unavailable" not in [f.code for f in ok.findings]
