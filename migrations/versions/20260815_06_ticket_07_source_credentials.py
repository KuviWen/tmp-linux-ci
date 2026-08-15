"""Add source credential readiness metadata without secret values."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_06"
down_revision: str | Sequence[str] | None = "20260815_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_source_credential_versions",
        sa.Column("provider_id", sa.String(length=128), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("secret_ref_id", sa.String(length=256), nullable=False),
        sa.Column("readiness", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("configured_at", sa.String(length=32), nullable=False),
        sa.Column("last_validated_at", sa.String(length=32), nullable=True),
        sa.Column("revoked_at", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("security_source_credential_versions")
