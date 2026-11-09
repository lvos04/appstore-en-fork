"""Catalogusbronnen ophalen: ``bundled`` (in de image), ``git`` (https, vaste ref, optioneel vastgepinde commit) en
``http`` (index-JSON met sha256 per manifest). Alleen lezen; er wordt nooit iets uit een bron uitgevoerd.

Beveiliging: alleen ``https`` (behalve met ``APPSTORE_ALLOW_INSECURE_SOURCES`` in ontwikkeling/tests), geen
gebruikersnaam/wachtwoord in URL's, geen loopback/link-local/metadata-adressen, harde limieten op aantal en grootte.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import httpx

from ..manifest.loader import MAX_MANIFEST_BYTES

MAX_FILES = 500
MANIFEST_SUFFIXES = (".manifest.yaml", ".manifest.yml", ".manifest.json")
MAX_INDEX_BYTES = 512 * 1024


class SourceError(Exception):
    """Fout bij het ophalen; ``message`` is Nederlands en bevat geen geheimen."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FetchedFile:
    path: str
    content: bytes
    signature: str | None


@dataclass
class FetchResult:
    files: list[FetchedFile] = field(default_factory=list)
    revision: str | None = None  # git-commit of HTTP-ETag
    not_modified: bool = False


def check_remote_url(url: str, *, allow_insecure: bool, schemes: tuple[str, ...] = ("https",)) -> None:
    parts = urlsplit(url)
    allowed = (*schemes, "http") if allow_insecure else schemes
    if parts.scheme not in allowed:
        raise SourceError("source_url_forbidden", f"Alleen {' of '.join(schemes)}-adressen zijn toegestaan.")
    if parts.username or parts.password:
        raise SourceError("source_url_forbidden", "Een bronadres mag geen gebruikersnaam of wachtwoord bevatten.")
    if not parts.hostname:
        raise SourceError("source_url_forbidden", "Het bronadres mist een host.")
    if allow_insecure:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise SourceError("source_unreachable", f"Host {parts.hostname!r} is niet te vinden.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise SourceError("source_url_forbidden", f"Adres {ip} (loopback/link-local) is niet toegestaan als bron.")


def _safe_rel(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts) or "\\" in path:
        raise SourceError("source_path_forbidden", f"Ongeldig pad in de bron: {path!r}.")
    return str(p)


def _read_dir(root: Path) -> list[FetchedFile]:
    if not root.is_dir():
        raise SourceError("source_missing", "De map van de bron bestaat niet.")
    files: list[FetchedFile] = []
    root_resolved = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not path.name.endswith(MANIFEST_SUFFIXES):
            continue
        if not path.resolve().is_relative_to(root_resolved):
            continue
        if len(files) >= MAX_FILES:
            raise SourceError("source_too_large", f"De bron bevat meer dan {MAX_FILES} manifesten.")
        # te groot: de validator meldt manifest_too_large zonder het hele bestand te lezen
        data = b"x" * (MAX_MANIFEST_BYTES + 1) if path.stat().st_size > MAX_MANIFEST_BYTES else path.read_bytes()
        sig_path = path.with_name(path.name + ".sig")
        signature = (
            sig_path.read_text(encoding="utf-8").strip() if sig_path.is_file() and not sig_path.is_symlink() else None
        )
        files.append(FetchedFile(path=str(path.relative_to(root)), content=data, signature=signature))
    return files


def fetch_bundled(directory: Path, subpath: str | None) -> FetchResult:
    root = directory / _safe_rel(subpath) if subpath else directory
    files = _read_dir(root)
    digest = hashlib.sha256(b"".join(f.path.encode() + f.content for f in files)).hexdigest()
    return FetchResult(files=files, revision=f"bundled:{digest[:16]}")


def fetch_git(
    url: str, ref: str | None, pinned_commit: str | None, subpath: str | None, *, allow_insecure: bool
) -> FetchResult:
    from dulwich import porcelain
    from dulwich.repo import Repo

    is_local = allow_insecure and (url.startswith("file://") or url.startswith("/"))
    if not is_local:
        check_remote_url(url, allow_insecure=allow_insecure)
    with tempfile.TemporaryDirectory(prefix="appstore-git-") as tmp:
        target = os.path.join(tmp, "repo")
        source = url.removeprefix("file://") if is_local else url
        try:
            with open(os.devnull, "wb") as devnull:
                porcelain.clone(
                    source,
                    target,
                    depth=None if pinned_commit else 1,
                    branch=ref.encode() if ref else None,
                    checkout=True,
                    errstream=devnull,
                )
        except Exception as exc:  # dulwich gooit uiteenlopende fouttypes
            raise SourceError("source_unreachable", f"Git-bron ophalen mislukt ({type(exc).__name__}).") from exc
        repo = Repo(target)
        try:
            head = repo.head().decode()
        finally:
            repo.close()
        if pinned_commit and head != pinned_commit:
            raise SourceError(
                "source_commit_mismatch",
                f"Ref {ref!r} wijst naar commit {head[:12]}, niet naar de vastgepinde commit {pinned_commit[:12]}.",
            )
        root = Path(target) / _safe_rel(subpath) if subpath else Path(target)
        files = [f for f in _read_dir(root) if not f.path.startswith(".git/")]
        return FetchResult(files=files, revision=head)


def fetch_http(
    url: str, etag: str | None, *, allow_insecure: bool, http_factory: Callable[[], httpx.Client]
) -> FetchResult:
    """``url`` wijst naar ``index.json``: ``{"manifests": [{"path": "x.manifest.yaml", "sha256": "<hex>"}]}``."""
    check_remote_url(url, allow_insecure=allow_insecure)
    base = url.rsplit("/", 1)[0] + "/"
    with http_factory() as client:
        headers = {"If-None-Match": etag} if etag else {}
        resp = client.get(url, headers=headers)
        if resp.status_code == 304:
            return FetchResult(revision=etag, not_modified=True)
        if resp.status_code != 200:
            raise SourceError("source_unreachable", f"Index ophalen mislukt (HTTP {resp.status_code}).")
        if len(resp.content) > MAX_INDEX_BYTES:
            raise SourceError("source_too_large", "De index is te groot.")
        try:
            index = json.loads(resp.content)
            entries = index["manifests"]
            if not isinstance(entries, list):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError(
                "source_index_invalid", "De index is geen geldige appstore-index (manifests-lijst)."
            ) from exc
        if len(entries) > MAX_FILES:
            raise SourceError("source_too_large", f"De index noemt meer dan {MAX_FILES} manifesten.")
        files: list[FetchedFile] = []
        for item in entries:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("sha256"), str)
            ):
                raise SourceError("source_index_invalid", "Elke index-regel heeft 'path' en 'sha256' nodig.")
            rel = _safe_rel(item["path"])
            file_url = urljoin(base, rel)
            if not file_url.startswith(base):
                raise SourceError("source_path_forbidden", f"Pad {rel!r} valt buiten de bron.")
            r = client.get(file_url)
            if r.status_code != 200:
                raise SourceError("source_unreachable", f"Manifest {rel!r} ophalen mislukt (HTTP {r.status_code}).")
            content = r.content[: MAX_MANIFEST_BYTES + 1]
            if hashlib.sha256(r.content).hexdigest() != item["sha256"].lower():
                raise SourceError(
                    "source_hash_mismatch", f"Manifest {rel!r} komt niet overeen met de sha256 in de index."
                )
            signature = None
            sig = client.get(file_url + ".sig")
            if sig.status_code == 200 and len(sig.content) < 1024:
                signature = sig.text.strip()
            files.append(FetchedFile(path=rel, content=content, signature=signature))
        return FetchResult(files=files, revision=resp.headers.get("etag"))
