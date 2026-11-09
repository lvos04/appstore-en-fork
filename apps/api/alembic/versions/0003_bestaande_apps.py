"""bestaande apps (B08-002): register van gemeten apps en beslisregister (APP-01…APP-20)

Alleen nieuwe tabellen; er wordt niets aan bestaande gegevens veranderd. Terug (downgrade) verwijdert alleen deze twee
tabellen (het register en het beslisregister); apps op de NAS en hun data worden door de appstore nooit aangeraakt.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "existing_app",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("runtime_name", sa.String(length=128), nullable=False),
        sa.Column("hub_app_uuid", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("known_app_id", sa.String(length=40), nullable=True),
        sa.Column("match_basis", sa.String(length=24), nullable=True),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("match_reason_nl", sa.Text(), nullable=False),
        sa.Column("hub_recognition", sa.String(length=64), nullable=True),
        sa.Column("install_method", sa.String(length=16), nullable=False),
        sa.Column("measured", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("measured_sha256", sa.String(length=64), nullable=False),
        sa.Column("image_history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gone_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adopted_by", sa.String(length=128), nullable=True),
        sa.Column("adoption_warning_nl", sa.Text(), nullable=True),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_existing_app")),
        sa.UniqueConstraint("runtime_name", name=op.f("uq_existing_app_runtime_name")),
    )
    op.create_index(op.f("ix_existing_app_status"), "existing_app", ["status"], unique=False)
    op.create_index(op.f("ix_existing_app_known_app_id"), "existing_app", ["known_app_id"], unique=False)
    op.create_table(
        "decision_record",
        sa.Column("app_id", sa.String(length=40), nullable=False),
        sa.Column("excel_id", sa.String(length=16), nullable=False),
        sa.Column("register_sha256", sa.String(length=64), nullable=False),
        sa.Column("intended", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("app_id", name=op.f("pk_decision_record")),
    )


def downgrade() -> None:
    op.drop_table("decision_record")
    op.drop_index(op.f("ix_existing_app_known_app_id"), table_name="existing_app")
    op.drop_index(op.f("ix_existing_app_status"), table_name="existing_app")
    op.drop_table("existing_app")
