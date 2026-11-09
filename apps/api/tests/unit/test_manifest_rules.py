"""Semantische veiligheidsregels: elke regel wordt minstens één keer afgedwongen."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from appstore.manifest import validate_data
from appstore.manifest.findings import Severity
from appstore.manifest.rules import example_reason, host_path_problem

Mutator = Callable[[dict[str, Any]], None]


def _set(path: str, value: Any) -> Mutator:
    def mutate(d: dict[str, Any]) -> None:
        cur: Any = d
        parts = path.split(".")
        for part in parts[:-1]:
            cur = cur[int(part)] if part.isdigit() else cur[part]
        last = parts[-1]
        if last.isdigit():
            cur[int(last)] = value
        else:
            cur[last] = value

    return mutate


def _both_ports(value: Any) -> Mutator:
    """Contract 1.2 §9: network.ports is de enige poortbron (custom_app.ports is vervallen)."""

    def mutate(d: dict[str, Any]) -> None:
        d["network"]["ports"][0]["host_port"] = value

    return mutate


def _legacy_ports(**change: Any) -> Mutator:
    """Vervallen custom_app.ports (vorm van contract 1.1) als kopie van network.ports, eventueel gewijzigd."""

    def mutate(d: dict[str, Any]) -> None:
        d["custom_app"]["ports"] = [{**dict(d["network"]["ports"][0]), **change}]

    return mutate


def _add_param(**param: Any) -> Mutator:
    def mutate(d: dict[str, Any]) -> None:
        d["parameters"].append({"required": True, "description_nl": "testparameter", **param})

    return mutate


def _host_path(value: Any) -> Mutator:
    return _set("custom_app.storage.0.host_path", value)


ERRORS: list[tuple[str, Mutator, str]] = [
    ("latest", _set("image.tag", "latest"), "mutable_tag_forbidden"),
    ("stable", _set("image.tag", "stable"), "mutable_tag_forbidden"),
    ("geen tag", _set("image.tag", ""), "invalid_format"),
    ("upstream zonder digest", _set("image.digest", None), "digest_missing"),
    ("privileged", _set("privileges.privileged", True), "privileged_forbidden"),
    ("docker-socket hostpad", _host_path("/var/run/docker.sock"), "docker_socket_forbidden"),
    ("containerd-socket", _host_path("/run/containerd/containerd.sock"), "docker_socket_forbidden"),
    ("hostpad /", _host_path("/"), "forbidden_host_path"),
    ("hostpad /etc", _host_path("/etc"), "forbidden_host_path"),
    ("hostpad /var/run", _host_path("/var/run"), "forbidden_host_path"),
    ("hostpad /root", _host_path("/root/.ssh"), "forbidden_host_path"),
    ("hostpad /mnt", _host_path("/mnt"), "forbidden_host_path"),
    ("hostpad ix-apps", _host_path("/mnt/.ix-apps/app_mounts"), "forbidden_host_path"),
    ("hostpad buiten /mnt", _host_path("/srv/data"), "forbidden_host_path"),
    ("hostpad met ..", _host_path("/mnt/p/../../etc"), "forbidden_host_path"),
    ("letterlijk datasetpad", _host_path("/mnt/p1/apps/x"), "literal_host_path"),
    ("letterlijk voorbeeldpool", _host_path("/mnt/tank/apps/x"), "literal_host_path"),
    ("host network zonder reden", _set("network.host_network", True), "host_network_without_reason"),
    ("gedeeld netwerk", _set("network.joins_platform_network", True), "platform_network_not_supported"),
    (
        "cap zonder reden",
        lambda d: (d["privileges"]["cap_add"].append("NET_ADMIN"), d.update(privilege_reason=None)),
        "capability_without_reason",
    ),
    ("cap SYS_ADMIN", lambda d: d["privileges"]["cap_add"].append("SYS_ADMIN"), "capability_forbidden"),
    ("cap onbekend", lambda d: d["privileges"]["cap_add"].append("SUPER_POWER"), "capability_unknown"),
    (
        "apparaat /dev/sda",
        lambda d: d["privileges"]["devices"].append({"host_device": "/dev/sda", "container_device": "/dev/sda"}),
        "device_forbidden",
    ),
    (
        "apparaat /dev/dri handmatig",
        lambda d: d["privileges"]["devices"].append({"host_device": "/dev/dri", "container_device": "/dev/dri"}),
        "device_forbidden",
    ),
    (
        "apparaat /dev/zfs",
        lambda d: d["privileges"]["devices"].append({"host_device": "/dev/zfs", "container_device": "/dev/zfs"}),
        "device_forbidden",
    ),
    (
        "apparaat zonder reden",
        lambda d: (
            d["privileges"]["devices"].append({"host_device": "/dev/ttyUSB0", "container_device": "/dev/ttyUSB0"}),
            d.update(privilege_reason=None),
        ),
        "device_without_reason",
    ),
    ("gpu zonder reden", _set("privilege_reason", None), "gpu_without_reason"),
    ("gpu boven het maximum", _set("privileges.gpu", "none"), "gpu_exceeds_privileges"),
    (
        "gpu letterlijk boven het maximum",
        lambda d: (d["custom_app"].update(gpu="intel_render"), d["privileges"].update(gpu="none")),
        "gpu_exceeds_privileges",
    ),
    ("gpu zonder D08", _set("decisions_required", ["D02", "D03"]), "gpu_decision_missing"),
    ("gpu-parameter uit default", _set("parameters.5.source", "default"), "gpu_param_invalid"),
    ("gpu-parameter verplicht", _set("parameters.5.required", True), "gpu_param_invalid"),
    ("gpu-parameter zonder D08", _set("parameters.5.decision", "D02"), "gpu_param_invalid"),
    ("gpu naar verkeerd parametertype", _set("custom_app.gpu", {"param": "timezone"}), "parameter_type_mismatch"),
    ("gpu naar onbekende parameter", _set("custom_app.gpu", {"param": "bestaat_niet"}), "unknown_parameter"),
    ("gpu-waarde onbekend", _set("custom_app.gpu", "nvidia"), "invalid_value"),
    ("vervallen poorten wijken af", _legacy_ports(container_port=9090), "ports_inconsistent"),
    (
        "portal naar vervallen poortnaam",
        lambda d: (_legacy_ports(name="oud")(d), d["custom_app"]["portal"].update(port="oud")),
        "portal_unknown_port",
    ),
    ("onbekende parameter", _both_ports({"param": "bestaat_niet"}), "unknown_parameter"),
    ("parametertype", _both_ports({"param": "timezone"}), "parameter_type_mismatch"),
    ("default ontbreekt", lambda d: d["parameters"][0].pop("default"), "parameter_default_missing"),
    ("wizard met default", _set("parameters.3.default", "http://nas:1"), "parameter_default_not_allowed"),
    (
        "voorbeeld-IP in default",
        _add_param(name="hub", type="url", source="default", default="http://192.168.1.10:30400"),
        "example_value_as_real",
    ),
    (
        "voorbeeld-IP in env",
        lambda d: d["custom_app"]["env"].append({"name": "UPSTREAM", "value": "http://192.168.1.10:8096"}),
        "example_value_as_real",
    ),
    (
        "plaatshouder in env",
        lambda d: d["custom_app"]["env"].append({"name": "NAS", "value": "<NAS-IP>"}),
        "example_value_as_real",
    ),
    (
        "geheim letterlijk",
        lambda d: d["custom_app"]["env"].append({"name": "DB_PASSWORD", "value": "geheim123"}),
        "secret_literal_forbidden",
    ),
    (
        "geheim in formulier",
        lambda d: (
            d["parameters"].append(
                {
                    "name": "db_pw",
                    "type": "secret_ref",
                    "source": "secret",
                    "required": True,
                    "description_nl": "databasewachtwoord",
                }
            ),
            d["custom_app"]["env"].append({"name": "DB_PASSWORD", "value_from": {"param": "db_pw"}}),
        ),
        "secret_in_form",
    ),
    (
        "DOCKER_HOST",
        lambda d: d["custom_app"]["env"].append({"name": "DOCKER_HOST", "value": "unix:///var/run/docker.sock"}),
        "docker_socket_forbidden",
    ),
    ("besluit niet gelijst", _set("decisions_required", ["D02"]), "decision_not_listed"),
    ("hostpad-parameter verkeerde bron", _set("parameters.1.source", "wizard"), "host_path_param_wrong_source"),
    ("datasetrol inconsistent", _set("parameters.1.dataset_role", "iets_anders"), "dataset_role_inconsistent"),
    ("datasetrol ontbreekt", _set("storage.0.required_dataset_role", None), "dataset_role_missing"),
    ("opslag zonder declaratie", _set("custom_app.storage.0.name", "onbekend"), "storage_unknown_reference"),
    ("verboden koppelpad", _set("storage.0.mount", "/etc"), "forbidden_mount_path"),
    (
        "tmpfs voor data",
        _set("custom_app.storage.0", {"name": "config", "type": "tmpfs", "tmpfs_size_mib": 64}),
        "invalid_value",
    ),
    (
        "back-up onbekende opslag",
        _set("backup.snapshot_datasets", ["config", "bestaat_niet"]),
        "backup_unknown_dataset",
    ),
    ("risico te laag", _set("risk.update", "R1"), "risk_below_policy_minimum"),
    ("tag-only rollback met migraties", _set("upgrade.migrations", True), "tag_only_rollback_with_migrations"),
    ("healthcheck poort", _set("healthcheck.port", "admin"), "healthcheck_unknown_port"),
    ("portal poort", _set("custom_app.portal.port", "admin"), "portal_unknown_port"),
    (
        "afhankelijk van zichzelf",
        _set("dependencies", [{"app_id": "voorbeeld-web", "kind": "required", "reason_nl": "zelf"}]),
        "dependency_self",
    ),
    ("dubbele parameter", lambda d: d["parameters"].append(dict(d["parameters"][0])), "duplicate_name"),
    ("compose-veld", _set("compose", {"services": {}}), "compose_forbidden"),
    ("custom_compose_config", _set("custom_compose_config", {"services": {}}), "compose_forbidden"),
    ("install via yaml", _set("install", {"method": "install_via_yaml"}), "compose_forbidden"),
    (
        "catalogus-velden bij custom_app",
        _set("catalog", {"train": "stable", "app": "x", "version": "1.0.0"}),
        "forbidden_combination",
    ),
    ("ontbrekende backup", lambda d: d.pop("backup"), "required_field_missing"),
    ("ontbrekende upgrade", lambda d: d.pop("upgrade"), "required_field_missing"),
    ("ontbrekende risk", lambda d: d.pop("risk"), "required_field_missing"),
    ("onbekend veld", _set("extra_veld", 1), "unknown_field"),
    ("root als gebruiker", _set("custom_app.run_as", {"mode": "custom", "user": 0, "group": 0}), "out_of_range"),
]


@pytest.mark.parametrize(("label", "mutate", "code"), ERRORS, ids=[e[0] for e in ERRORS])
def test_rule_refuses(custom_manifest: dict[str, Any], label: str, mutate: Mutator, code: str) -> None:
    mutate(custom_manifest)
    report, model = validate_data(custom_manifest)
    assert not report.valid, f"{label}: verwacht ongeldig\n{report.as_text()}"
    assert model is None
    errors = [f.code for f in report.findings if f.severity is Severity.ERROR]
    assert code in errors, f"{label}: verwacht {code}\n{report.as_text()}"


WARNINGS: list[tuple[str, Mutator, str]] = [
    ("onnauwkeurige tag", _set("image.tag", "1.27"), "imprecise_version_tag"),
    (
        "eigen image zonder digest",
        lambda d: d["image"].update(repository="ghcr.io/lvos04/voorbeeld", digest=None),
        "digest_pending_release",
    ),
    (
        "host network met reden",
        lambda d: d["network"].update(host_network=True, host_network_reason="mDNS-discovery"),
        "host_network_requested",
    ),
    ("hostpoort < 1024", _both_ports(80), "port_privileged_range"),
    ("vervallen custom_app.ports (gelijk)", _legacy_ports(), "deprecated_field"),
    ("vervallen joins_platform_network", _set("network.joins_platform_network", False), "deprecated_field"),
    ("ongebruikte parameter", _add_param(name="ongebruikt", type="string", source="wizard"), "parameter_unused"),
    ("geclaimde status", _set("validation_status", "validated_on_nas"), "validation_status_claim_ignored"),
    ("geen compatibiliteit", lambda d: d.pop("compatibility"), "compatibility_missing"),
    (
        "upstream als imagegebruiker",
        _set("custom_app.run_as", {"mode": "image_default"}),
        "run_as_image_default_upstream",
    ),
    ("vaste tijdzone", _set("custom_app.timezone", "Europe/Amsterdam"), "example_value_as_real"),
    ("geen tijdzone", lambda d: d["custom_app"].pop("timezone"), "timezone_missing"),
    ("config niet in snapshot", _set("backup.snapshot_datasets", []), "backup_dataset_not_covered"),
]


@pytest.mark.parametrize(("label", "mutate", "code"), WARNINGS, ids=[w[0] for w in WARNINGS])
def test_rule_warns(custom_manifest: dict[str, Any], label: str, mutate: Mutator, code: str) -> None:
    mutate(custom_manifest)
    report, _ = validate_data(custom_manifest)
    assert code in [f.code for f in report.findings if f.severity is Severity.WARNING], report.as_text()


def test_catalog_rules(catalog_manifest: dict[str, Any]) -> None:
    catalog_manifest["catalog"]["version"] = "latest"
    catalog_manifest["values"]["privileged"] = True
    catalog_manifest["values"]["storage"]["sock"] = {"host_path_config": {"path": "/var/run/docker.sock"}}
    catalog_manifest["values"]["DB_PASSWORD"] = "geheim"
    catalog_manifest["values"]["upstream"] = "http://10.0.0.5:80"
    catalog_manifest["values"]["custom_compose_config"] = {"services": {}}
    catalog_manifest["values"]["image"] = {"tag": "latest"}
    report, _ = validate_data(catalog_manifest)
    errors = {f.code for f in report.findings if f.severity is Severity.ERROR}
    assert {
        "mutable_version_forbidden",
        "privileged_forbidden",
        "docker_socket_forbidden",
        "secret_literal_forbidden",
        "example_value_as_real",
        "compose_forbidden",
        "mutable_tag_forbidden",
    } <= errors, report.as_text()


def test_catalog_requires_dataset_role_and_param(catalog_manifest: dict[str, Any]) -> None:
    catalog_manifest["storage"][0]["required_dataset_role"] = None
    report, _ = validate_data(catalog_manifest)
    assert "dataset_role_missing" in report.codes()
    catalog_manifest["storage"][0]["required_dataset_role"] = "onbekende_rol"
    report, _ = validate_data(catalog_manifest)
    assert "dataset_role_inconsistent" in report.codes()


def test_catalog_literal_host_path_and_unknown_param(catalog_manifest: dict[str, Any]) -> None:
    catalog_manifest["values"]["storage"]["library"]["host_path_config"]["path"] = "/mnt/p1/fotos"
    catalog_manifest["values"]["extra"] = {"param": "bestaat_niet"}
    report, _ = validate_data(catalog_manifest)
    assert {"literal_host_path", "unknown_parameter"} <= set(report.codes())


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("/mnt/p1/apps/x", True),
        ("/mnt/p1/media", True),
        ("/mnt/p1", False),
        ("/mnt", False),
        ("/", False),
        ("/etc/passwd", False),
        ("/var/run/docker.sock", False),
        ("relatief/pad", False),
        ("/mnt/p1/../etc", False),
        ("/mnt/.ix-apps/x", False),
        ("/home/admin", False),
    ],
)
def test_host_path_problem(path: str, ok: bool) -> None:
    assert (host_path_problem(path) is None) is ok


@pytest.mark.parametrize(
    ("value", "example"),
    [
        ("http://192.168.1.10:8096", True),
        ("/mnt/tank/apps", True),
        ("https://truenas.local", True),
        ("<POOL>", True),
        ("${HUB_URL}", True),
        ("http://100.64.1.1", True),
        ("info", False),
        ("https://github.com/lvos04", False),
        ("8.8.8.8", False),
    ],
)
def test_example_detection(value: str, example: bool) -> None:
    assert (example_reason(value) is not None) is example
