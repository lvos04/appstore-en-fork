"""Catalogusbeheer: bronnen synchroniseren, manifestversies vastleggen (met ondertekende provenance), entries bijhouden."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import __version__
from ..config import Settings
from ..events import emit, record_evidence
from ..manifest.findings import Finding, Severity, error
from ..manifest.model import Manifest, ParamRef
from ..manifest.provenance import ProvenanceRecord, SourceRef, key_id, load_public_key, sign_provenance, verify_data
from ..manifest.validator import ValidationReport, build_report, validate_text
from ..models import CatalogEntry, CatalogSource, ManifestVersion
from ..secrets_store import Secrets
from ..timeutil import utcnow
from .sources import FetchedFile, FetchResult, SourceError, fetch_bundled, fetch_git, fetch_http

log = logging.getLogger("appstore.catalog")

BUNDLED_SOURCE_NAME = "ingebouwd"


@dataclass
class SyncOutcome:
    ok: bool
    revision: str | None = None
    not_modified: bool = False
    added: list[str] = field(default_factory=list)
    unchanged: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_nl: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "revision": self.revision,
            "not_modified": self.not_modified,
            "added": self.added,
            "unchanged": self.unchanged,
            "rejected": self.rejected,
            "error_code": self.error_code,
            "error_nl": self.error_nl,
        }


def ensure_bundled_source(db: Session) -> CatalogSource:
    src = db.scalar(select(CatalogSource).where(CatalogSource.kind == "bundled"))
    if src is None:
        src = CatalogSource(
            id=uuid.uuid4(),
            name=BUNDLED_SOURCE_NAME,
            kind="bundled",
            enabled=True,
            require_signature=False,
            created_by="system",
            row_version=1,
            last_sync_status="never",
        )
        db.add(src)
        db.flush()
    return src


def fetch(source: CatalogSource, settings: Settings, http_factory: Callable[[], httpx.Client]) -> FetchResult:
    if source.kind == "bundled":
        return fetch_bundled(settings.bundled_catalog_dir, source.subpath)
    if source.kind == "git":
        assert source.url is not None
        return fetch_git(
            source.url, source.ref, source.pinned_commit, source.subpath, allow_insecure=settings.allow_insecure_sources
        )
    if source.kind == "http":
        assert source.url is not None
        return fetch_http(
            source.url,
            source.last_sync_revision,
            allow_insecure=settings.allow_insecure_sources,
            http_factory=http_factory,
        )
    raise SourceError("source_kind_unknown", f"Onbekende bronsoort {source.kind!r}.")


def _publisher_check(
    source: CatalogSource, data: dict[str, Any] | None, file: FetchedFile
) -> tuple[str, Finding | None]:
    if not source.trusted_public_key:
        if source.require_signature:
            return "absent", error(
                "signature_missing", "", "De bron vereist een handtekening maar heeft geen vertrouwde sleutel."
            )
        return "not_required", None
    if file.signature is None:
        if source.require_signature:
            return "absent", error(
                "signature_missing", "", "Handtekening (.sig) ontbreekt terwijl de bron die vereist."
            )
        return "absent", None
    key = load_public_key(source.trusted_public_key)
    if data is None or not verify_data(key, data, file.signature):
        return "invalid", error("signature_invalid", "", "De handtekening van de uitgever klopt niet.")
    return "verified", None


def record_manifest(
    db: Session,
    source: CatalogSource,
    file: FetchedFile,
    revision: str | None,
    secrets: Secrets,
    trace_id: str,
) -> tuple[str, ManifestVersion | None, ValidationReport]:
    """Valideer en leg één manifest vast. Geeft (``added``/``unchanged``/``rejected``, versie, rapport)."""
    report, manifest, data = validate_text(file.content)
    signature_status, sig_finding = _publisher_check(source, data, file)
    extra: list[Finding] = [sig_finding] if sig_finding else []
    entry = db.scalar(select(CatalogEntry).where(CatalogEntry.app_id == report.app_id)) if report.app_id else None
    if entry is not None and entry.source_id != source.id:
        extra.append(
            error(
                "app_id_claimed_by_other_source",
                "app_id",
                f"App {report.app_id!r} hoort bij een andere catalogusbron; deze bron mag hem niet overschrijven.",
            )
        )
    if extra:
        report = build_report(data, [*report.findings, *extra], report.manifest_sha256)
        manifest = None
    sha = report.manifest_sha256 or "sha256:onleesbaar"
    existing = db.scalar(
        select(ManifestVersion).where(
            ManifestVersion.source_id == source.id,
            ManifestVersion.file_path == file.path,
            ManifestVersion.manifest_sha256 == sha,
        )
    )
    if existing is not None:
        return "unchanged", existing, report
    now = utcnow()
    record = ProvenanceRecord(
        app_id=report.app_id,
        version=report.version,
        manifest_sha256=sha,
        schema_sha256=report.schema_sha256,
        validator_version=__version__,
        source=SourceRef(
            source_id=str(source.id),
            kind=source.kind,
            url=source.url,
            ref=source.ref,
            commit=revision,
            path=file.path,
        ),
        fetched_at=now,
        publisher_signature="absent" if signature_status == "invalid" else signature_status,
        publisher_key_id=source.trusted_key_id if signature_status == "verified" else None,
        valid=report.valid,
        errors=report.errors,
        warnings=report.warnings,
    )
    signed = sign_provenance(secrets.identity, record)
    version = ManifestVersion(
        id=uuid.uuid4(),
        app_id=report.app_id,
        source_id=source.id,
        file_path=file.path,
        manifest_sha256=sha,
        app_version=report.version,
        manifest=data if isinstance(data, dict) else None,
        raw_text=file.content.decode("utf-8", errors="replace")[:300_000],
        valid=report.valid,
        report=report.model_dump(mode="json"),
        provenance=signed.model_dump(mode="json"),
        publisher_signature=signature_status,
        created_at=now,
    )
    db.add(version)
    db.flush()
    if manifest is None or not report.valid:
        emit(
            db,
            "catalog.manifest.rejected",
            "manifest_version",
            str(version.id),
            {
                "app_id": report.app_id,
                "file": file.path,
                "errors": report.errors,
                "codes": sorted({f.code for f in report.findings if f.severity is Severity.ERROR}),
            },
            trace_id,
        )
        return "rejected", version, report
    if entry is None:
        entry = CatalogEntry(
            id=uuid.uuid4(),
            app_id=manifest.app_id,
            name=manifest.name,
            category=manifest.category,
            owner_repo=manifest.owner_repo,
            install_method=manifest.install.method,
            source_id=source.id,
            status="valid",
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        db.add(entry)
        db.flush()
    else:
        entry.name, entry.category, entry.owner_repo = manifest.name, manifest.category, manifest.owner_repo
        entry.install_method, entry.status = manifest.install.method, "valid"
        entry.updated_at = now
        entry.row_version += 1
        db.execute(
            update(ManifestVersion)
            .where(ManifestVersion.entry_id == entry.id, ManifestVersion.superseded_at.is_(None))
            .values(superseded_at=now)
        )
    version.entry_id = entry.id
    entry.latest_version_id = version.id
    emit(
        db,
        "catalog.manifest.added",
        "catalog_entry",
        manifest.app_id,
        {"version": manifest.version, "manifest_sha256": sha, "version_id": str(version.id), "source": source.name},
        trace_id,
    )
    return "added", version, report


def sync_source(
    db: Session,
    source: CatalogSource,
    settings: Settings,
    secrets: Secrets,
    http_factory: Callable[[], httpx.Client],
    trace_id: str,
) -> SyncOutcome:
    """Synchroniseer één bron. Commit niet zelf; bij een ophaalfout wordt niets aan de catalogus gewijzigd."""
    now = utcnow()
    try:
        fetched = fetch(source, settings, http_factory)
    except SourceError as exc:
        source.last_sync_at, source.last_sync_status, source.last_sync_error = now, "failed", exc.message
        emit(
            db,
            "catalog.source_sync.failed",
            "catalog_source",
            str(source.id),
            {"code": exc.code, "message": exc.message, "ernst": "middel"},
            trace_id,
        )
        return SyncOutcome(ok=False, error_code=exc.code, error_nl=exc.message)
    except httpx.HTTPError as exc:
        msg = f"Bron niet bereikbaar ({type(exc).__name__})."
        source.last_sync_at, source.last_sync_status, source.last_sync_error = now, "failed", msg
        emit(
            db,
            "catalog.source_sync.failed",
            "catalog_source",
            str(source.id),
            {"code": "source_unreachable", "message": msg, "ernst": "middel"},
            trace_id,
        )
        return SyncOutcome(ok=False, error_code="source_unreachable", error_nl=msg)
    outcome = SyncOutcome(ok=True, revision=fetched.revision, not_modified=fetched.not_modified)
    for file in fetched.files:
        try:
            # ADR-0018: fail-closed per manifest. Een onverwachte fout bij één manifest weigert alleen dat manifest
            # (de savepoint draait zijn wijzigingen terug); de andere manifesten van de bron worden gewoon verwerkt.
            with db.begin_nested():
                status, version, report = record_manifest(db, source, file, fetched.revision, secrets, trace_id)
        except Exception as exc:
            log.error("manifest %s van bron %s niet verwerkt (%s)", file.path, source.name, type(exc).__name__)
            outcome.rejected.append(
                {"file": file.path, "app_id": None, "version_id": None, "codes": ["manifest_niet_verwerkt"]}
            )
            continue
        if status == "added":
            outcome.added.append(f"{report.app_id} {report.version}")
        elif status == "unchanged":
            outcome.unchanged += 1
        else:
            outcome.rejected.append(
                {
                    "file": file.path,
                    "app_id": report.app_id,
                    "version_id": str(version.id) if version else None,
                    "codes": sorted({f.code for f in report.findings if f.severity is Severity.ERROR}),
                }
            )
    source.last_sync_at, source.last_sync_status, source.last_sync_error = now, "ok", None
    source.last_sync_revision = fetched.revision
    emit(
        db,
        "catalog.source_sync.succeeded",
        "catalog_source",
        str(source.id),
        {
            "added": len(outcome.added),
            "rejected": len(outcome.rejected),
            "unchanged": outcome.unchanged,
            "revision": fetched.revision,
        },
        trace_id,
    )
    record_evidence(
        db, "source_sync", None, trace_id, {"source": source.name, "kind": source.kind, **outcome.as_dict()}
    )
    return outcome


# ---------------------------------------------------------------------------------------------- weergave
def requirements(m: Manifest) -> dict[str, Any]:
    """ "Transparante eisen" (B08-001 schermen): wat de app nodig heeft en mag, vóór installatie zichtbaar."""

    def port_repr(p: Any) -> dict[str, Any]:
        host = {"param": p.host_port.param} if isinstance(p.host_port, ParamRef) else p.host_port
        return {"name": p.name, "container_port": p.container_port, "host_port": host, "protocol": p.protocol}

    return {
        "install_method": m.install.method,
        "image": m.image.model_dump() if m.image else None,
        "catalog": m.catalog.model_dump() if m.catalog else None,
        "ports": [port_repr(p) for p in m.network.ports],
        "host_network": m.network.host_network,
        "storage": [s.model_dump() for s in m.storage],
        "privileges": m.privileges.model_dump(),
        "privilege_reason": m.privilege_reason,
        "gpu": m.privileges.gpu,  # het maximum (contract 1.3 §9)
        "gpu_request": (
            None
            if m.custom_app is None
            else ({"param": m.custom_app.gpu.param} if isinstance(m.custom_app.gpu, ParamRef) else m.custom_app.gpu)
        ),
        "resources": m.custom_app.resources.model_dump() if m.custom_app else None,
        "run_as": m.custom_app.run_as.model_dump() if m.custom_app else None,
        "backup": m.backup.model_dump(mode="json"),
        "risk": m.risk.model_dump(),
        "upgrade": m.upgrade.model_dump(),
        "dependencies": [d.model_dump() for d in m.dependencies],
        "decisions_required": m.decisions_required,
        "parameters": [{k: v for k, v in p.model_dump().items() if k != "example"} for p in m.parameters],
        "sso": m.sso,
        "compatibility": m.compatibility.model_dump() if m.compatibility else None,
        # contract 1.3 §9: reden van de installatiemethode, taken na installatie (gate) en het monitorplan
        "install_reason": m.install.reason,
        "post_install_checks": [c.model_dump() for c in m.post_install_checks],
        "monitor": m.healthcheck.monitor.model_dump() if m.healthcheck.monitor else None,
        "accepted_findings": [a.model_dump() for a in m.accepted_findings],
    }


def verify_stored_provenance(version: ManifestVersion, secrets: Secrets) -> bool:
    from ..manifest.provenance import SignedProvenance, verify_provenance

    try:
        signed = SignedProvenance.model_validate(version.provenance)
    except ValueError:
        return False
    return signed.record.manifest_sha256 == version.manifest_sha256 and verify_provenance(
        secrets.identity.public_key(), signed
    )


def trusted_key_id(text: str) -> str:
    return key_id(load_public_key(text))
