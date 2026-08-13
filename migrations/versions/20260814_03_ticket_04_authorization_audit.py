"""Add structured authorization decision evidence to security audit events."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_03"
down_revision: str | Sequence[str] | None = "20260813_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "security_audit_events",
        sa.Column("authorization", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("security_audit_events", "authorization")
