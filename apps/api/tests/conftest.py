"""Gedeelde testhulpen."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


def load_fixture(name: str) -> dict[str, Any]:
    data = yaml.safe_load((FIXTURES / "manifests" / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture
def custom_manifest() -> dict[str, Any]:
    return copy.deepcopy(load_fixture("valid-custom-upstream.manifest.yaml"))


@pytest.fixture
def catalog_manifest() -> dict[str, Any]:
    return copy.deepcopy(load_fixture("valid-catalog.manifest.yaml"))


@pytest.fixture
def own_manifest_text() -> str:
    return (REPO_ROOT / "deploy" / "manifest" / "appstore-en-fork.manifest.yaml").read_text(encoding="utf-8")
