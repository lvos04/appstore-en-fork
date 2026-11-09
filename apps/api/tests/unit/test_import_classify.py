"""B08-002 — bestaande apps herkennen. Acceptatie (Excel): *nog niet ondersteunde app blijft unmanaged zonder
dataverlies.* De classificatie voert nooit iets uit en plant geen acties; alleen platformlabels gelden als beheerd."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from appstore.catalog.importer import CatalogCandidate, classify_inventory
from appstore.manifest.conflicts import Inventory

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
CATALOG = [
    CatalogCandidate(
        app_id="voorbeeld-web",
        install_method="custom_app",
        manifest_sha256=SHA,
        image_repository="docker.io/library/nginx",
    ),
    CatalogCandidate(
        app_id="voorbeeld-catalogus", install_method="catalog_app", manifest_sha256=SHA, catalog_app="voorbeeldapp"
    ),
    CatalogCandidate(
        app_id="ingetrokken",
        install_method="custom_app",
        manifest_sha256=SHA,
        image_repository="ghcr.io/x/oud",
        valid=False,
    ),
]


def _inv(apps: list[dict[str, Any]], age: timedelta = timedelta(minutes=1)) -> Inventory:
    return Inventory.model_validate({"observed_at": NOW - age, "source": "simulator", "apps": apps})


def _one(app: dict[str, Any], catalog: list[CatalogCandidate] = CATALOG) -> Any:
    report = classify_inventory(_inv([app]), catalog, now=NOW)
    assert report.status == "ok" and report.executed is False
    assert report.owner_confirmation == "capability_unavailable"
    (result,) = report.apps
    return result


def _labels(app_id: str, sha: str | None = SHA, managed_by: str = "appstore-en-fork") -> dict[str, str]:
    out = {"nl.lvos04.platform.app_id": app_id, "nl.lvos04.platform.managed_by": managed_by}
    if sha:
        out["nl.lvos04.platform.manifest_sha256"] = sha
    return out


@pytest.mark.parametrize(
    "app",
    [
        {"name": "plex", "catalog_app": "plex", "images": ["plexinc/pms-docker:1.41.0"]},
        {"name": "eigen-ding", "catalog_app": "ix-app", "images": ["ghcr.io/iemand/eigen:2.0.0"]},
        {"name": "zonder-alles"},
        {"name": "oud", "images": ["ghcr.io/x/oud:1.0.0"]},  # manifest bestaat, maar is ingetrokken
        {"name": "vreemd-label", "labels": _labels("bestaat-niet")},
    ],
    ids=["catalogusapp-onbekend", "custom-app-onbekend", "niets", "manifest-ingetrokken", "label-onbekend-manifest"],
)
def test_unsupported_app_stays_unmanaged_without_actions(app: dict[str, Any]) -> None:
    """Excel-acceptatie B08-002."""
    result = _one(app)
    assert result.status == "unmanaged"
    assert result.planned_actions == [] and result.data_policy == "untouched"
    assert result.requires_owner_confirmation is False
    assert any("niet aangeraakt" in r for r in result.reasons_nl)


def test_platform_labels_mean_managed() -> None:
    result = _one(
        {"name": "voorbeeld-web", "labels": _labels("voorbeeld-web"), "images": ["docker.io/library/nginx:1"]}
    )
    assert (result.status, result.match_basis, result.matched_app_id) == ("managed", "label", "voorbeeld-web")
    assert result.manifest_current is True and result.planned_actions == []


def test_label_with_old_manifest_is_managed_but_not_current() -> None:
    result = _one({"name": "voorbeeld-web", "labels": _labels("voorbeeld-web", sha="sha256:" + "b" * 64)})
    assert result.status == "managed" and result.manifest_current is False
    assert result.planned_actions == []  # een update gaat via preview + goedkeuring, nooit via de import


def test_label_without_managed_by_is_not_trusted() -> None:
    result = _one(
        {"name": "x", "labels": _labels("voorbeeld-web", managed_by="iemand-anders"), "images": ["nginx:1.27.3"]}
    )
    assert result.status == "candidate" and result.match_basis == "image"  # alleen een kandidaat, geen beheer


@pytest.mark.parametrize(
    "image", ["nginx:1.27.3", "library/nginx:1.27.3", "docker.io/library/nginx@sha256:" + "c" * 64]
)
def test_image_match_is_only_a_candidate(image: str) -> None:
    result = _one({"name": "web", "catalog_app": "ix-app", "images": [image]})
    assert (result.status, result.match_basis, result.matched_app_id) == ("candidate", "image", "voorbeeld-web")
    assert result.requires_owner_confirmation is True and result.planned_actions == []


def test_catalog_app_match_is_only_a_candidate() -> None:
    result = _one({"name": "fotos", "catalog_app": "voorbeeldapp", "version": "1.6.12"})
    assert (result.status, result.match_basis) == ("candidate", "catalog_app")
    assert result.requires_owner_confirmation is True


def test_ambiguous_match_stays_unmanaged() -> None:
    catalog = [
        *CATALOG,
        CatalogCandidate(
            app_id="ander-nginx", install_method="custom_app", manifest_sha256=SHA, image_repository="nginx"
        ),
    ]
    result = _one({"name": "web", "images": ["nginx:1.27.3"]}, catalog)
    assert result.status == "unmanaged" and result.candidates == ["ander-nginx", "voorbeeld-web"]


@pytest.mark.parametrize("age", [timedelta(hours=2), timedelta(hours=-1)], ids=["verouderd", "toekomst"])
def test_stale_or_future_inventory_is_unknown(age: timedelta) -> None:
    report = classify_inventory(
        _inv([{"name": "voorbeeld-web", "labels": _labels("voorbeeld-web")}], age=age), CATALOG, now=NOW
    )
    assert report.status == "unknown" and [a.status for a in report.apps] == ["unknown"]
    assert report.apps[0].planned_actions == []


def test_missing_inventory_is_unknown() -> None:
    report = classify_inventory(None, CATALOG, now=NOW)
    assert report.status == "unknown" and report.apps == []


def test_counts_and_no_actions_anywhere() -> None:
    apps = [
        {"name": "voorbeeld-web", "labels": _labels("voorbeeld-web")},
        {"name": "fotos", "catalog_app": "voorbeeldapp"},
        {"name": "plex", "catalog_app": "plex"},
    ]
    report = classify_inventory(_inv(apps), CATALOG, now=NOW)
    assert report.counts == {"managed": 1, "candidate": 1, "unmanaged": 1}
    assert all(a.planned_actions == [] and a.data_policy == "untouched" for a in report.apps)
