"""replaycache voor Hub-JWT's (contract 1.2 §4.6)

De koppelstatus verhuist naar /appdata/secrets/hub-link.json (contract 1.2 §3.5); de tabel hub_link blijft in deze
versie alleen bestaan voor de eenmalige overzetting bij de start (hublink.ensure_link_file) en wordt daarna geleegd.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jwt_replay",
        sa.Column("iss", sa.String(length=64), nullable=False),
        sa.Column("jti", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("iss", "jti", name=op.f("pk_jwt_replay")),
    )
    op.create_index(op.f("ix_jwt_replay_expires_at"), "jwt_replay", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jwt_replay_expires_at"), table_name="jwt_replay")
    op.drop_table("jwt_replay")
