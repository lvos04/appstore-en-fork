"""Conflictanalyse tegen een gemeten inventaris."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from appstore.manifest import validate_data
from appstore.manifest.conflicts import Inventory, analyse
from appstore.manifest.translate import Target, translate

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
PARAMS = {
    "config_path": "/mnt/p1/apps/voorbeeld",
    "media_path": "/mnt/p1/media",
    "public_url": "http://nas.test:1",
    "timezone": "Etc/UTC",
}


def _plan(data: dict[str, Any]) -> tuple[Any, Any]:
    _, model = validate_data(data)
    assert model is not None
    res = translate(model, PARAMS, Target(truenas_version="25.10.1", ix_app_version="1.4.8"))
    return model, res.payload


def _inv(apps: list[dict[str, Any]], age: timedelta = timedelta(minutes=1)) -> Inventory:
    return Inventory.model_validate({"observed_at": NOW - age, "source": "simulator", "apps": apps})


def test_no_inventory_is_unknown_never_ok(custom_manifest: dict[str, Any]) -> None:
    model, payload = _plan(custom_manifest)
    report = analyse(model, payload, None, now=NOW)
    assert report.status == "unknown"
    assert [f.code for f in report.findings] == ["inventory_missing"]


def test_clean_inventory_ok(custom_manifest: dict[str, Any]) -> None:
    model, payload = _plan(custom_manifest)
    report = analyse(model, payload, _inv([{"name": "andere", "used_ports": [], "volumes": []}]), now=NOW)
    assert report.status == "ok", report.findings


def test_stale_inventory_is_unknown(custom_manifest: dict[str, Any]) -> None:
    model, payload = _plan(custom_manifest)
    report = analyse(model, payload, _inv([], age=timedelta(hours=2)), now=NOW)
    assert report.status == "unknown" and "inventory_stale" in [f.code for f in report.findings]


def test_port_path_name_conflicts(custom_manifest: dict[str, Any]) -> None:
    model, payload = _plan(custom_manifest)
    apps = [
        {"name": "voorbeeld-web"},
        {
            "name": "jellyfin",
            "used_ports": [
                {"container_port": 8096, "protocol": "tcp", "host_ports": [{"host_port": 30999, "host_ip": "0.0.0.0"}]}
            ],
            "volumes": [{"source": "/mnt/p1/media", "destination": "/media", "mode": "rw", "type": "bind"}],
        },
        {"name": "backup", "volumes": [{"source": "/mnt/p1/apps", "destination": "/b", "mode": "ro", "type": "bind"}]},
    ]
    report = analyse(model, payload, _inv(apps), now=NOW)
    codes = [f.code for f in report.findings]
    assert report.status == "conflict"
    assert {"app_name_taken", "port_in_use", "host_path_in_use", "host_path_overlap"} <= set(codes)


def test_platform_port_warning_and_dependencies(custom_manifest: dict[str, Any]) -> None:
    custom_manifest["parameters"][0]["default"] = 30400
    custom_manifest["dependencies"] = [
        {"app_id": "ai-server-manager", "kind": "required", "min_version": "0.2.0", "reason_nl": "koppeling"},
        {"app_id": "gotify", "kind": "optional", "reason_nl": "meldingen"},
    ]
    model, payload = _plan(custom_manifest)
    apps = [{"name": "ai-server-manager", "platform_app_id": "ai-server-manager", "version": "0.1.0"}]
    report = analyse(model, payload, _inv(apps), now=NOW)
    codes = [f.code for f in report.findings]
    assert {"port_reserved_platform", "dependency_version_too_low", "dependency_optional_missing"} <= set(codes)
    report = analyse(model, payload, _inv([]), now=NOW)
    assert "dependency_missing" in [f.code for f in report.findings]
