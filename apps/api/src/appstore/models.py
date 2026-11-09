"""Databasemodel (SQLAlchemy 2). Wijzigingen alleen via Alembic-migraties (``apps/api/alembic``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .timeutil import utcnow

TS = DateTime(timezone=True)
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


# ------------------------------------------------------------------ catalogus
class CatalogSource(Base):
    __tablename__ = "catalog_source"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # bundled|git|http
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pinned_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subpath: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    require_signature: Mapped[bool] = mapped_column(Boolean, default=False)
    trusted_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    trusted_key_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    last_sync_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(16), default="never")  # never|ok|failed
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)  # commit of ETag


class CatalogEntry(Base):
    __tablename__ = "catalog_entry"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    app_id: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(32))
    owner_repo: Mapped[str] = mapped_column(String(160))
    install_method: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_source.id", ondelete="RESTRICT"), index=True)
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manifest_version.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="valid")  # valid|withdrawn
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class ManifestVersion(Base):
    __tablename__ = "manifest_version"
    __table_args__ = (
        UniqueConstraint("source_id", "file_path", "manifest_sha256", name="uq_manifest_version_source_file_sha"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    app_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_entry.id", ondelete="SET NULL"), nullable=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_source.id", ondelete="RESTRICT"), index=True)
    file_path: Mapped[str] = mapped_column(String(512))
    manifest_sha256: Mapped[str] = mapped_column(String(71))
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    valid: Mapped[bool] = mapped_column(Boolean)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    publisher_signature: Mapped[str] = mapped_column(String(16))  # verified|not_required|absent|invalid
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    superseded_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class SyncJob(Base):
    __tablename__ = "sync_job"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_source.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|succeeded|failed
    requested_by: Mapped[str] = mapped_column(String(128))
    requested_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ------------------------------------------------------------------ bewijs, idempotency, audit, outbox
class EvidenceRecord(Base):
    __tablename__ = "evidence_record"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))  # manifest_validation|manifest_preview|source_sync
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128))
    trace_id: Mapped[str] = mapped_column(String(36))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_record_scope_key"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    scope: Mapped[str] = mapped_column(String(256))
    key: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user|service|system
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str] = mapped_column(String(36))


class OutboxEvent(Base):
    __tablename__ = "outbox_event"
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(128))
    classification: Mapped[str] = mapped_column(String(16), default="internal")
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[str] = mapped_column(String(36))
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dead_letter: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------------------------ replaycache voor Hub-JWT's (contract 1.2 §4.6)
class JwtReplay(Base):
    """``(iss, jti)`` van elke **mutatie** met een Hub-JWT, tot ``exp`` + 30 s; gedeeld door alle processen."""

    __tablename__ = "jwt_replay"
    iss: Mapped[str] = mapped_column(String(64), primary_key=True)
    jti: Mapped[str] = mapped_column(String(128), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(TS, index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(512))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ------------------------------------------------------------------ bestaande apps (B08-002, APP-01…APP-20)
class ExistingApp(Base):
    """Eén op de NAS geïnstalleerde app zoals de Hub hem meet (``GET /api/v1/apps``). Alleen een register: de appstore
    wijzigt nooit iets aan de app, zijn data of zijn rechten. Wordt nooit verwijderd; verdwenen = ``present = false``.

    ``status``: ``onbekend`` (niet of niet eenduidig herkend; blijft ongemoeid), ``kandidaat`` (herkend, wacht op
    bevestiging van de eigenaar) of ``adopted`` (eigenaar bevestigd door een beheerder; in de Hub-UI "Geïmporteerd").
    """

    __tablename__ = "existing_app"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    runtime_name: Mapped[str] = mapped_column(String(128), unique=True)  # TrueNAS-appnaam (runtime_id bij de Hub)
    hub_app_uuid: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # onbekend|kandidaat|adopted
    known_app_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    match_basis: Mapped[str | None] = mapped_column(String(24), nullable=True)
    candidates: Mapped[list[str]] = mapped_column(JSONB, default=list)
    match_reason_nl: Mapped[str] = mapped_column(Text)
    hub_recognition: Mapped[str | None] = mapped_column(String(64), nullable=True)  # alleen een hint, nooit de basis
    install_method: Mapped[str] = mapped_column(String(16))  # custom_app|catalog_app|custom_compose|onbekend
    measured: Mapped[dict[str, Any]] = mapped_column(JSONB)  # image, images, catalogus, poorten, opslag, datasets, …
    measured_sha256: Mapped[str] = mapped_column(String(64))
    image_history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(TS)
    last_seen: Mapped[datetime] = mapped_column(TS)
    gone_since: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    owner: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    adopted_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    adopted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    adoption_warning_nl: Mapped[str | None] = mapped_column(Text, nullable=True)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # adopteren/loslaten (audit in de rij)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class DecisionRecord(Base):
    """Beslisregister per bekende app (APP_REGISTER): voorgenomen besluit, afvinkbare controlelijst met bewijs en het
    vastgelegde besluit. Uitvoeren gebeurt nooit hier (R2/R3 via lifecycle/executor)."""

    __tablename__ = "decision_record"
    app_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    excel_id: Mapped[str] = mapped_column(String(16))
    register_sha256: Mapped[str] = mapped_column(String(64))
    intended: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


# ------------------------------------------------------------------ koppeling met de Hub: OUD (≤ contract 1.1)
class HubLink(Base):
    """Alleen nog gelezen door de eenmalige overzetting naar ``/appdata/secrets/hub-link.json`` (contract 1.2 §3.5,
    ``hublink.ensure_link_file``); daarna is de tabel leeg. Een latere migratie verwijdert hem."""

    __tablename__ = "hub_link"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # altijd 1
    status: Mapped[str] = mapped_column(String(16), default="not_linked")  # not_linked|linked|failed|revoked
    hub_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    service_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_via: Mapped[str | None] = mapped_column(String(16), nullable=True)  # token|koppelcode
    linked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    jwks: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    jwks_fetched_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    policy_public_jwk: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    used_enrollment_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_attempt_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
