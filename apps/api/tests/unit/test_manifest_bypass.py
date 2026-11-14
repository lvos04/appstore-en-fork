"""Omzeilpogingen op de validator (review fase P0): elk gevonden lek heeft hier een regressietest.

Gevonden en gerepareerd (zie ADR-0007):

* hostpaden met ``//`` (POSIX laat ``//mnt/.ix-apps`` ongemoeid, waardoor de prefixvergelijking faalde), verborgen
  mappen (``.zfs``, ``.system``), ``ix-applications``, hoofdletters op hoofdletterongevoelige datasets, newline/
  spatie/backslash in een parameterwaarde;
* apparaten met ``/dev/./sda`` of ``/dev/../…`` en een denylist die watchdog, IPMI, Intel ME, vfio, eMMC, consoles,
  … doorliet → nu een allowlist;
* koppelpad ``//etc`` in de container;
* NET_ADMIN samen met host network;
* tags als ``alpine-latest``, ``stable-alpine``, ``alpine`` (geen versie);
* ``$VAR``/``$(cmd)``/``$$`` in omgevingswaarden (TrueNAS zet ze in een compose-bestand);
* catalogusapp ``ix-app`` met ruwe waarden, en ``privileged: "true"``/``1``, ``pid``/``ipc``/``network_mode``,
  ``security_opt: unconfined``, capabilities, apparaten, ``command``, verstopte ``docker_compose``/``compose_file``
  en een tekstparameter op een hostpad-veld in catalogus-``values``;
* YAML: merge-sleutel ``<<`` zonder anker, lijst/object/bool als sleutel (crash), diepe nesting (RecursionError).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from appstore.manifest import validate_data
from appstore.manifest.findings import CODES, Severity
from appstore.manifest.loader import load_manifest_text
from appstore.manifest.model import Manifest
from appstore.manifest.rules import (
    catalog_value_findings,
    device_problem,
    host_path_problem,
    mount_path_problem,
    mutable_tag_reason,
    normalize_abs_path,
)
from appstore.manifest.translate import Target, translate
from appstore.manifest.validator import validate_text

TARGET = Target(truenas_version="25.10.1", ix_app_version="1.4.8")
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.thuis.test:30999",
    "timezone": "Europe/Amsterdam",
}


def _errors(data: dict[str, Any]) -> set[str]:
    report, model = validate_data(data)
    errors = {f.code for f in report.findings if f.severity is Severity.ERROR}
    assert (model is None) == bool(errors), report.as_text()
    return errors


def _model(data: dict[str, Any]) -> Manifest:
    report, model = validate_data(data)
    assert model is not None, report.as_text()
    return model


# ---------------------------------------------------------------------------------------------- hostpaden
BAD_HOST_PATHS = [
    "//mnt/.ix-apps/docker",
    "//mnt/.ix-apps/app_configs",
    "///etc/passwd",
    "//var/run/docker.sock",
    "/mnt//p1/apps",
    "/mnt/p1/.zfs/snapshot",
    "/mnt/p1/.system",
    "/mnt/p1/apps/.ix-apps",
    "/mnt/p1/ix-applications",
    "/mnt/p1/IX-Applications/k3s",
    "/mnt/.IX-APPS/docker",
    "/MNT/.ix-apps",
    "/mnt/p1/apps\n",
    "/mnt/p1/apps\r",
    "/mnt/p1/apps\t",
    "/mnt/p1/ apps",
    "/mnt/p1/apps ",
    "/mnt/p1/apps\u202e",
    "/mnt/p1/apps\u200b",
    "/mnt/p1/x\\..\\..\\etc",
    "/mnt/p1/./apps",
    "/mnt/p1/apps/../../../etc",
    "/mnt/p1",
    "/mnt/",
    "mnt/p1/apps",
    "",
]


@pytest.mark.parametrize("path", BAD_HOST_PATHS, ids=repr)
def test_host_path_bypass_refused(path: str) -> None:
    assert host_path_problem(path) is not None, path


@pytest.mark.parametrize("path", BAD_HOST_PATHS, ids=repr)
def test_host_path_bypass_refused_at_translation(custom_manifest: dict[str, Any], path: str) -> None:
    """De validator kent de waarde niet (komt uit de datasetkiezer); de vertaling moet hem dus weigeren."""
    result = translate(_model(custom_manifest), {**PARAMS, "config_path": path}, TARGET)
    assert not result.ready_for_install and result.payload is None
    assert "parameter_value_invalid" in {f.code for f in result.blocking()}


@pytest.mark.parametrize(
    "path", ["/mnt/p1/apps/voorbeeld", "/mnt/p1/apps/voorbeeld/", "/mnt/p1/media/Films en series", "/mnt/p1/a.b_c-d"]
)
def test_legitimate_dataset_paths_still_accepted(custom_manifest: dict[str, Any], path: str) -> None:
    assert host_path_problem(path) is None
    result = translate(_model(custom_manifest), {**PARAMS, "config_path": path}, TARGET)
    assert result.ready_for_install, [f.as_text() for f in result.findings]


def test_normalize_abs_path_is_strict() -> None:
    assert normalize_abs_path("/") == "/"
    assert normalize_abs_path("/mnt/p1/x/") == "/mnt/p1/x"
    for bad in ("//etc", "/a//b", "/a/./b", "/a/../b", "/a\x00b", "/a\nb", "/a\\b", "a/b", "/a /b"):
        assert normalize_abs_path(bad) is None, bad


# ---------------------------------------------------------------------------------------------- koppelpaden
@pytest.mark.parametrize("mount", ["//etc", "//proc/1", "/etc/", "/./etc", "/dev/../etc", "//var/run/docker.sock"])
def test_container_mount_bypass_refused(custom_manifest: dict[str, Any], mount: str) -> None:
    assert mount_path_problem(mount) is not None
    custom_manifest["storage"][0]["mount"] = mount
    assert "forbidden_mount_path" in _errors(custom_manifest)


# KLEIN-82: het koppelpad is gelijk aan de regel van de Hub (ai-server-manager, asm_policy.params: CONTAINERPAD_RE en
# _mount_path_problem, KLEIN-80 en KLEIN-81). Alles wat de Hub als mount_path weigert, is hier al forbidden_mount_path,
# zodat de appstore geen manifest goedkeurt waarvan de Hub de payload weigert. Vroeger lieten mount_path_problem en
# daarmee de catalogus-waarden ":", ",", "=", een spatie, niet-ASCII en een afsluitende "/" door, en was storage.mount
# "/data/" geldig (het schema weigert de andere tekens daar al).
HUB_MOUNT_ONGELDIG = [
    "/cache:exec,mode=1777,=",
    "/cache:exec,suid,dev,mode=4777,uid=0,gid=0,=",
    "/cache,exec",
    "/proc:=",
    "/sys:=",
    "/dev:exec,dev,mode=0777,=",
    "/data:rw,z",
    "/proc/.",
    "/./proc",
    "/sys/.",
    "/dev/.",
    "/a/./b",
    "/a/../proc",
    "/..",
    "//proc",
    "/a//b",
    "/data/",
    "/da ta",
    "/data=x",
    "/data\x00",
    "/data\n",
    "/dаta",  # Cyrillische a
    "data",
    "/",
    "/proc",
    "/var/run/docker.sock",
    "",
    "/data~",
    "/data#x",
    "/café",
    "/proc/sys",
    "/proc/sys/kernel",
    "/proc/1",
    "/sys/fs/cgroup",
    "/sys/kernel",
    "/dev/shm",
    "/dev/mqueue",
    "/dev/pts",
    "/var/run/docker.sock/x",
    "/run/docker.sock/x",
]
#: Geldig bij de Hub en hier: alle koppelpaden uit de manifesten van de repos en de tegenproeven van de Hub. Niet hier:
#: /run en /var/run, die deze validator strenger al weigert (FORBIDDEN_MOUNT_PATHS).
HUB_MOUNT_GELDIG = [
    "/app/data", "/appdata", "/beszel_data", "/cache", "/certs", "/config", "/data", "/media", "/templates",
    "/var/lib/beszel-agent", "/var/lib/postgresql", "/media/films", "/groot", "/data_1", "/x-y", "/a.b", "/a@b+c",
    "/procfs", "/devices", "/system", "/dev2/x", "/host", "/process", "/procfs/sys", "/proc_data", "/devices/shm",
    "/dev2/shm", "/sysadmin", "/systeem/fs", "/PROC", "/PROC/sys", "/Proc/sys", "/DEV/shm", "/Sys/fs/cgroup",
    "/var/run/docker", "/var/lib/proc", "/data/dev/shm", "/.verborgen", "/a..b",
]  # fmt: skip


@pytest.mark.parametrize("mount", HUB_MOUNT_ONGELDIG)
def test_mount_path_problem_weigert_wat_de_hub_weigert(mount: str) -> None:
    assert mount_path_problem(mount) is not None


@pytest.mark.parametrize("mount", HUB_MOUNT_GELDIG)
def test_mount_path_problem_laat_door_wat_de_hub_doorlaat(mount: str) -> None:
    assert mount_path_problem(mount) is None


@pytest.mark.parametrize("mount", ["/data/", "/config/", "/var/lib/postgresql/"])
def test_storage_mount_met_afsluitende_schuine_streep_geweigerd(custom_manifest: dict[str, Any], mount: str) -> None:
    custom_manifest["storage"][0]["mount"] = mount
    assert "forbidden_mount_path" in _errors(custom_manifest)


@pytest.mark.parametrize("mount", ["/cache:exec,mode=1777,=", "/cache,exec", "/da ta", "/data=x", "/data/", "/café"])
@pytest.mark.parametrize("key", ["mount_path", "mountPath"])
def test_catalogus_koppelpad_gelijk_aan_de_hub(catalog_manifest: dict[str, Any], key: str, mount: str) -> None:
    catalog_manifest["values"] = {**catalog_manifest["values"], "extra": {key: mount}}
    assert "forbidden_mount_path" in _errors(catalog_manifest)


def test_geldige_koppelpaden_blijven_geldig_in_manifest_en_catalogus(
    custom_manifest: dict[str, Any], catalog_manifest: dict[str, Any]
) -> None:
    """Tegenproef: de koppelpaden die het schema toestaat, blijven als storage.mount en als catalogus-waarde geldig."""
    other_mounts = {decl["mount"] for decl in custom_manifest["storage"][1:]}
    for mount in HUB_MOUNT_GELDIG:
        if "@" in mount or "+" in mount or mount in other_mounts:
            continue  # het schema van storage.mount laat "@" en "+" niet toe (strenger dan de Hub); geen dubbel pad
        data = copy.deepcopy(custom_manifest)
        data["storage"][0]["mount"] = mount
        assert _errors(data) == set(), mount
    for mount in HUB_MOUNT_GELDIG:
        data = copy.deepcopy(catalog_manifest)
        data["values"] = {**data["values"], "extra": {"mount_path": mount}}
        assert _errors(data) == set(), mount


# ---------------------------------------------------------------------------------------------- apparaten
BAD_DEVICES = [
    "/dev/./sda",
    "/dev/../dev/sda",
    "/dev/../etc/shadow",
    "/dev//sda",
    "/dev/watchdog",
    "/dev/watchdog0",
    "/dev/mei0",
    "/dev/ipmi0",
    "/dev/vfio/vfio",
    "/dev/mmcblk0",
    "/dev/nbd0",
    "/dev/tty0",
    "/dev/ttyS0",
    "/dev/kvm",
    "/dev/nvram",
    "/dev/sr0",
    "/dev/ram0",
    "/dev/xvda",
    "/dev/input/event0",
    "/dev/uinput",
    "/dev/hidraw0",
    "/dev/hpet",
    "/dev/rtc0",
    "/dev/i2c-0",
    "/dev/sda",
    "/dev/nvme0n1",
    "/dev/zfs",
    "/dev/dri/renderD128",
]


@pytest.mark.parametrize("device", BAD_DEVICES)
def test_device_bypass_refused(custom_manifest: dict[str, Any], device: str) -> None:
    assert device_problem(device) is not None
    custom_manifest["privileges"]["devices"] = [{"host_device": device, "container_device": "/dev/ttyZigbee"}]
    assert "device_forbidden" in _errors(custom_manifest)


@pytest.mark.parametrize("container", ["/dev/./sda", "/dev/../etc/x", "/dev//x", "/dev/zfs", "/dev/dri"])
def test_container_device_bypass_refused(custom_manifest: dict[str, Any], container: str) -> None:
    custom_manifest["privileges"]["devices"] = [{"host_device": "/dev/ttyUSB0", "container_device": container}]
    assert "device_forbidden" in _errors(custom_manifest)


@pytest.mark.parametrize(
    "device",
    [
        "/dev/ttyUSB0",
        "/dev/ttyACM1",
        "/dev/serial/by-id/usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus-if00-port0",
        "/dev/video0",
        "/dev/dvb/adapter0/frontend0",
        "/dev/apex_0",
    ],
)
def test_allowlisted_devices_accepted_with_reason(custom_manifest: dict[str, Any], device: str) -> None:
    custom_manifest["privileges"]["devices"] = [{"host_device": device, "container_device": "/dev/ttyZigbee"}]
    assert _errors(custom_manifest) == set()


# ---------------------------------------------------------------------------------------------- capabilities
def test_net_admin_with_host_network_refused(custom_manifest: dict[str, Any]) -> None:
    custom_manifest["privileges"]["cap_add"] = ["NET_ADMIN"]
    assert _errors(custom_manifest) == set()  # zonder host network: toegestaan mét reden
    custom_manifest["network"].update(host_network=True, host_network_reason="mDNS-discovery")
    assert "capability_forbidden" in _errors(custom_manifest)


@pytest.mark.parametrize("cap", ["cap_sys_admin", "SYS_ADMIN ", "sys_admin", "ALL", "CAP_SYS_ADMIN"])
def test_capability_spellings_refused(custom_manifest: dict[str, Any], cap: str) -> None:
    custom_manifest["privileges"]["cap_add"] = [cap]
    assert _errors(custom_manifest) & {"invalid_format", "capability_forbidden"}


# ---------------------------------------------------------------------------------------------- tags
@pytest.mark.parametrize(
    "tag",
    ["alpine-latest", "latest-alpine", "LATEST", "stable-alpine", "nightly-2024", "main-abc123", "alpine", "bookworm"],
)
def test_mutable_tag_variants_refused(custom_manifest: dict[str, Any], tag: str) -> None:
    assert mutable_tag_reason(tag) is not None
    custom_manifest["image"]["tag"] = tag
    assert "mutable_tag_forbidden" in _errors(custom_manifest)


@pytest.mark.parametrize("tag", ["1.27.3", "v2.3.4", "2.479.1-lts-jdk17", "2.0.0-rc.1", "RELEASE.2024-11-07T00-52-20Z"])
def test_pinned_tags_accepted(tag: str) -> None:
    assert mutable_tag_reason(tag) is None


# ---------------------------------------------------------------------------------------------- env-interpolatie
@pytest.mark.parametrize("value", ["$HOME", "${HOST_PATH}", "$(id)", "$$HOME", "a $VAR b", "`id`$_"])
def test_env_interpolation_refused(custom_manifest: dict[str, Any], value: str) -> None:
    custom_manifest["custom_app"]["env"][0]["value"] = value
    assert "interpolation_forbidden" in _errors(custom_manifest)


def test_env_interpolation_via_parameter_refused(custom_manifest: dict[str, Any]) -> None:
    result = translate(_model(custom_manifest), {**PARAMS, "public_url": "http://nas.test/$(id)"}, TARGET)
    assert not result.ready_for_install
    assert "parameter_value_invalid" in {f.code for f in result.blocking()}


def test_literal_timezone_must_be_iana(custom_manifest: dict[str, Any]) -> None:
    custom_manifest["custom_app"]["timezone"] = "../../etc/localtime"
    assert "invalid_value" in _errors(custom_manifest)


# ---------------------------------------------------------------------------------------------- catalogus-values
CATALOG_VALUE_BYPASSES: list[tuple[str, dict[str, Any], str]] = [
    ("privileged als tekst", {"privileged": "true"}, "privileged_forbidden"),
    ("privileged als 1", {"privileged": 1}, "privileged_forbidden"),
    ("Privileged hoofdletter", {"Privileged": True}, "privileged_forbidden"),
    ("pid host", {"pid": "host"}, "catalog_value_forbidden"),
    ("ipc host", {"ipc": "host"}, "catalog_value_forbidden"),
    ("pid_mode genest", {"container": {"pid_mode": "host"}}, "catalog_value_forbidden"),
    ("network_mode host", {"network_mode": "host"}, "catalog_value_forbidden"),
    ("userns host", {"userns_mode": "host"}, "catalog_value_forbidden"),
    ("security_opt unconfined", {"security_opt": ["seccomp=unconfined"]}, "catalog_value_forbidden"),
    ("Security-Opt spelling", {"Security-Opt": ["apparmor=unconfined"]}, "catalog_value_forbidden"),
    ("unconfined als waarde", {"labels": [{"key": "x", "value": "seccomp:unconfined"}]}, "catalog_value_forbidden"),
    ("capabilities add", {"capabilities": {"add": ["SYS_ADMIN"]}}, "catalog_value_forbidden"),
    ("cap_add", {"cap_add": ["NET_ADMIN"]}, "catalog_value_forbidden"),
    ("sysctls", {"sysctls": {"net.ipv4.ip_forward": "1"}}, "catalog_value_forbidden"),
    ("command sh -c", {"command": ["sh", "-c", "id"]}, "catalog_value_forbidden"),
    ("entrypoint", {"entrypoint": "/bin/sh"}, "catalog_value_forbidden"),
    ("apparaat schijf", {"devices": [{"host_device": "/dev/sda", "container_device": "/dev/sda"}]}, "device_forbidden"),
    ("apparaat compose-stijl", {"devices": ["/dev/mem:/dev/mem"]}, "device_forbidden"),
    ("docker_compose verstopt", {"docker_compose": "services: {}"}, "compose_forbidden"),
    ("compose_file genest", {"extra": {"compose_file": "x.yaml"}}, "compose_forbidden"),
    ("Custom-Compose-Config", {"Custom-Compose-Config": {"services": {}}}, "compose_forbidden"),
    ("host_network als tekst", {"host_network": "true"}, "host_network_without_reason"),
    ("hostNetwork genest", {"network": {"hostNetwork": 1}}, "host_network_without_reason"),
    ("koppelpad //etc", {"extra": {"mount_path": "//etc"}}, "forbidden_mount_path"),
    (
        "hostpad //mnt/.ix-apps",
        {"storage": {"x": {"host_path_config": {"path": "//mnt/.ix-apps"}}}},
        "forbidden_host_path",
    ),
    ("interpolatie", {"extra": "$HOME"}, "interpolation_forbidden"),
    ("tag alpine-latest", {"image": {"repository": "x/y", "tag": "alpine-latest"}}, "mutable_tag_forbidden"),
]


@pytest.mark.parametrize(
    ("label", "values", "code"), CATALOG_VALUE_BYPASSES, ids=[c[0] for c in CATALOG_VALUE_BYPASSES]
)
def test_catalog_value_bypass_refused(
    catalog_manifest: dict[str, Any], label: str, values: dict[str, Any], code: str
) -> None:
    catalog_manifest["values"] = {**catalog_manifest["values"], **values}
    assert code in _errors(catalog_manifest), label


def test_catalog_ix_app_with_raw_values_refused(catalog_manifest: dict[str, Any]) -> None:
    catalog_manifest["catalog"]["app"] = "ix-app"
    assert "install_method_forbidden" in _errors(catalog_manifest)


def test_catalog_text_parameter_on_host_path_field_refused(catalog_manifest: dict[str, Any]) -> None:
    """Een vrije tekstparameter op een hostpad-veld zou ``/etc`` uit de wizard doorlaten."""
    catalog_manifest["parameters"].append(
        {"name": "vrij", "type": "string", "source": "wizard", "required": True, "description_nl": "vrije tekst"}
    )
    catalog_manifest["values"]["storage"]["extra"] = {"host_path_config": {"path": {"param": "vrij"}}}
    assert "parameter_type_mismatch" in _errors(catalog_manifest)


def test_catalog_device_from_parameter_refused(catalog_manifest: dict[str, Any]) -> None:
    catalog_manifest["parameters"].append(
        {"name": "apparaat", "type": "string", "source": "wizard", "required": True, "description_nl": "apparaat"}
    )
    catalog_manifest["values"]["devices"] = [{"host_device": {"param": "apparaat"}, "container_device": "/dev/x"}]
    assert "catalog_value_forbidden" in _errors(catalog_manifest)


def test_resolved_catalog_values_are_checked_again(catalog_manifest: dict[str, Any]) -> None:
    params = {"timezone": "Europe/Amsterdam", "library_path": "//mnt/.ix-apps/docker"}
    result = translate(_model(catalog_manifest), params, TARGET)
    assert not result.ready_for_install and result.payload is None
    ok = translate(_model(catalog_manifest), {**params, "library_path": "/mnt/p1/fotos"}, TARGET)
    assert ok.ready_for_install, [f.as_text() for f in ok.findings]


def test_catalog_value_findings_resolved_mode_allows_measured_paths() -> None:
    values = {"storage": {"x": {"host_path_config": {"path": "/mnt/p1/fotos"}}}, "url": "http://192.168.1.2"}
    assert catalog_value_findings(values, host_network_reason=None, resolved=True) == []
    unresolved = {f.code for f in catalog_value_findings(values, host_network_reason=None, resolved=False)}
    assert unresolved == {"literal_host_path", "example_value_as_real"}


def test_safe_catalog_flags_still_accepted(catalog_manifest: dict[str, Any]) -> None:
    catalog_manifest["values"].update(
        {"privileged": False, "capabilities": {"add": []}, "command": [], "network": {"host_network": False}}
    )
    assert _errors(catalog_manifest) == set()


# ---------------------------------------------------------------------------------------------- YAML
def _yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _codes(text: str) -> list[str]:
    return [f.code for f in load_manifest_text(text)[1]]


def test_merge_key_without_anchor_refused(custom_manifest: dict[str, Any]) -> None:
    text = _yaml(custom_manifest).replace("privileges:\n", "privileges:\n  <<: {privileged: true}\n", 1)
    text = text.replace("  privileged: false\n", "", 1)
    assert _codes(text) == ["yaml_merge_key_forbidden"]
    assert _codes("<<: {compose: {services: {}}}\n" + _yaml(custom_manifest)) == ["yaml_merge_key_forbidden"]


@pytest.mark.parametrize("prefix", ["[a, b]: 1\n", "? {a: 1}\n: 1\n", "1: x\n", "on: x\n", "null: x\n"])
def test_non_string_keys_refused_without_crash(custom_manifest: dict[str, Any], prefix: str) -> None:
    text = prefix + _yaml(custom_manifest)
    assert _codes(text) == ["yaml_key_not_string"]
    report, model, _ = validate_text(text)
    assert not report.valid and model is None


@pytest.mark.parametrize("depth", [33, 5000, 100_000])
def test_deep_nesting_refused_without_recursion_error(depth: int) -> None:
    text = "x: " + "[" * depth + "]" * depth + "\n"
    assert _codes(text) == ["yaml_too_deep"]


def test_nested_hidden_compose_in_custom_app_refused(custom_manifest: dict[str, Any]) -> None:
    for where in ((), ("custom_app",), ("install",)):
        data = copy.deepcopy(custom_manifest)
        target: Any = data
        for part in where:
            target = target[part]
        target["custom_compose_config_string"] = "services: {}"
        assert "compose_forbidden" in _errors(data), where


@pytest.mark.parametrize(
    ("where", "key"),
    [
        (("custom_app",), "security_opt"),
        (("custom_app",), "pid"),
        (("custom_app",), "ipc"),
        (("custom_app",), "command"),
        (("privileges",), "pid_mode"),
        (("network",), "network_mode"),
        (("custom_app", "run_as"), "privileged"),
        ((), "Compose"),
    ],
)
def test_unknown_dangerous_fields_refused(custom_manifest: dict[str, Any], where: tuple[str, ...], key: str) -> None:
    target: Any = custom_manifest
    for part in where:
        target = target[part]
    target[key] = "host"
    assert "unknown_field" in _errors(custom_manifest)


def test_new_codes_are_documented() -> None:
    for code in (
        "yaml_merge_key_forbidden",
        "yaml_key_not_string",
        "yaml_too_deep",
        "interpolation_forbidden",
        "install_method_forbidden",
        "catalog_value_forbidden",
    ):
        assert code in CODES
