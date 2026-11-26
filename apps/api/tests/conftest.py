"""Gedeelde testhulpen."""

from __future__ import annotations

import copy
import errno
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


def acl_dataset(monkeypatch: pytest.MonkeyPatch, *, chmod: str, herkend: bool) -> None:
    """Bootst een ZFS-dataset met NFSv4-ACL na (TrueNAS-preset *Apps*; lokaal niet beschikbaar): elk nieuw bestand krijgt
    0770 uit de overgeërfde ACE's (``aclinherit=passthrough`` negeert de 0600 van open()). ``chmod``: ``werkt``
    (aclmode=passthrough), ``geweigerd`` (aclmode=restricted) of ``genegeerd``. ``herkend``: of de NFSv4-ACL zichtbaar
    is als xattr (``system.nfs4_acl_xdr``)."""
    from appstore import secrets_store

    real_open, real_fchmod, real_chmod = os.open, os.fchmod, os.chmod

    def open_acl(path: Any, flags: int, mode: int = 0o777, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, mode, *args, **kwargs)
        if flags & os.O_CREAT and stat.S_ISREG(os.fstat(fd).st_mode):
            real_fchmod(fd, 0o770)
        return fd

    def fchmod_acl(fd: int, mode: int) -> None:
        if chmod == "geweigerd":
            raise PermissionError(errno.EPERM, "Operation not permitted")
        if chmod == "werkt":
            real_fchmod(fd, mode)

    def chmod_acl(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        if chmod == "geweigerd":
            raise PermissionError(errno.EPERM, "Operation not permitted")
        if chmod == "werkt":
            real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_acl)
    monkeypatch.setattr(os, "fchmod", fchmod_acl)
    monkeypatch.setattr(os, "chmod", chmod_acl)
    monkeypatch.setattr(secrets_store, "nfs4_acl", lambda fd: herkend)


#: (chmod, NFSv4-ACL herkend) waarbij koppelen moet slagen
ACL_GEVALLEN = [("werkt", False), ("geweigerd", True), ("genegeerd", True)]


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


@pytest.fixture
def own_manifest_without_digest(own_manifest_text: str) -> str:
    """Kopie van het releasemanifest waarin alleen de image-digest ontbreekt."""
    return re.sub(r"^  digest: .+$", "  digest: null", own_manifest_text, count=1, flags=re.MULTILINE)
