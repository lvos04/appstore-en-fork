"""Vertaling naar TrueNAS app.create (standaard Custom App = ix-app, of catalogusapp)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from appstore.manifest import validate_data
from appstore.manifest.model import Manifest
from appstore.manifest.translate import Target, translate
from appstore.manifest.truenas import AppCreatePayload
from tests.conftest import FIXTURES

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


def test_catalog_secret_param_not_filled(catalog_manifest: dict[str, Any]) -> None:
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
        model, {"timezone": "Etc/UTC", "library_path": "/mnt/p1/fotos"}, Target(truenas_version="25.10.1")
    )
    assert "parameter_value_not_allowed" in [f.code for f in result.findings]
    assert result.payload is None


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
