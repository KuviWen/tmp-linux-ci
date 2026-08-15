"""Add expiry, validation evidence, and durable secret cleanup."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_07"
down_revision: str | Sequence[str] | None = "20260815_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "security_source_credential_versions",
        sa.Column("expires_at", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "security_source_credential_versions",
        sa.Column("validation_evidence", sa.JSON(), nullable=True),
    )
    op.create_table(
        "security_source_secret_cleanup_queue",
        sa.Column("secret_ref_id", sa.String(length=256), primary_key=True),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("queued_at", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("security_source_secret_cleanup_queue")
    op.drop_column("security_source_credential_versions", "validation_evidence")
    op.drop_column("security_source_credential_versions", "expires_at")
