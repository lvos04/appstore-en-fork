"""initieel schema: catalogus, versies, bronnen, outbox, audit, koppeling

Revision ID: 0001
Revises: -
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
    )
    op.create_index(op.f("ix_audit_event_at"), "audit_event", ["at"], unique=False)
    op.create_table(
        "catalog_source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("ref", sa.String(length=200), nullable=True),
        sa.Column("pinned_commit", sa.String(length=40), nullable=True),
        sa.Column("subpath", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("require_signature", sa.Boolean(), nullable=False),
        sa.Column("trusted_public_key", sa.Text(), nullable=True),
        sa.Column("trusted_key_id", sa.String(length=16), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=16), nullable=False),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("last_sync_revision", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_source")),
        sa.UniqueConstraint("name", name=op.f("uq_catalog_source_name")),
    )
    op.create_table(
        "evidence_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_record")),
    )
    op.create_index(op.f("ix_evidence_record_created_at"), "evidence_record", ["created_at"], unique=False)
    op.create_table(
        "hub_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("hub_url", sa.String(length=512), nullable=True),
        sa.Column("service_id", sa.String(length=64), nullable=True),
        sa.Column("linked_via", sa.String(length=16), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("token_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("token_last4", sa.String(length=8), nullable=True),
        sa.Column("jwks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("jwks_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_public_jwk", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("used_enrollment_token_hash", sa.String(length=64), nullable=True),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hub_link")),
    )
    op.create_table(
        "idempotency_record",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("scope", sa.String(length=256), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_record")),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_record_scope_key"),
    )
    op.create_index(op.f("ix_idempotency_record_created_at"), "idempotency_record", ["created_at"], unique=False)
    op.create_table(
        "outbox_event",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("actor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dead_letter", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_outbox_event")),
        sa.UniqueConstraint("event_id", name=op.f("uq_outbox_event_event_id")),
    )
    op.create_index(op.f("ix_outbox_event_next_attempt_at"), "outbox_event", ["next_attempt_at"], unique=False)
    op.create_table(
        "catalog_entry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("owner_repo", sa.String(length=160), nullable=False),
        sa.Column("install_method", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("latest_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["catalog_source.id"],
            name=op.f("fk_catalog_entry_source_id_catalog_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_entry")),
        sa.UniqueConstraint("app_id", name=op.f("uq_catalog_entry_app_id")),
    )
    op.create_index(op.f("ix_catalog_entry_source_id"), "catalog_entry", ["source_id"], unique=False)
    op.create_table(
        "sync_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"], ["catalog_source.id"], name=op.f("fk_sync_job_source_id_catalog_source"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_job")),
    )
    op.create_index(op.f("ix_sync_job_source_id"), "sync_job", ["source_id"], unique=False)
    op.create_index(op.f("ix_sync_job_status"), "sync_job", ["status"], unique=False)
    op.create_table(
        "manifest_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("app_id", sa.String(length=40), nullable=True),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=71), nullable=False),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("publisher_signature", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalog_entry.id"],
            name=op.f("fk_manifest_version_entry_id_catalog_entry"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["catalog_source.id"],
            name=op.f("fk_manifest_version_source_id_catalog_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manifest_version")),
        sa.UniqueConstraint("source_id", "file_path", "manifest_sha256", name="uq_manifest_version_source_file_sha"),
    )
    op.create_index(op.f("ix_manifest_version_app_id"), "manifest_version", ["app_id"], unique=False)
    op.create_index(op.f("ix_manifest_version_created_at"), "manifest_version", ["created_at"], unique=False)
    op.create_index(op.f("ix_manifest_version_source_id"), "manifest_version", ["source_id"], unique=False)
    # kringverwijzing catalog_entry ↔ manifest_version: FK pas na beide tabellen
    op.create_foreign_key(
        op.f("fk_catalog_entry_latest_version_id_manifest_version"),
        "catalog_entry",
        "manifest_version",
        ["latest_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_catalog_entry_latest_version_id_manifest_version"), "catalog_entry", type_="foreignkey")
    op.drop_index(op.f("ix_manifest_version_source_id"), table_name="manifest_version")
    op.drop_index(op.f("ix_manifest_version_created_at"), table_name="manifest_version")
    op.drop_index(op.f("ix_manifest_version_app_id"), table_name="manifest_version")
    op.drop_table("manifest_version")
    op.drop_index(op.f("ix_sync_job_status"), table_name="sync_job")
    op.drop_index(op.f("ix_sync_job_source_id"), table_name="sync_job")
    op.drop_table("sync_job")
    op.drop_index(op.f("ix_catalog_entry_source_id"), table_name="catalog_entry")
    op.drop_table("catalog_entry")
    op.drop_index(op.f("ix_outbox_event_next_attempt_at"), table_name="outbox_event")
    op.drop_table("outbox_event")
    op.drop_index(op.f("ix_idempotency_record_created_at"), table_name="idempotency_record")
    op.drop_table("idempotency_record")
    op.drop_table("hub_link")
    op.drop_index(op.f("ix_evidence_record_created_at"), table_name="evidence_record")
    op.drop_table("evidence_record")
    op.drop_table("catalog_source")
    op.drop_index(op.f("ix_audit_event_at"), table_name="audit_event")
    op.drop_table("audit_event")
