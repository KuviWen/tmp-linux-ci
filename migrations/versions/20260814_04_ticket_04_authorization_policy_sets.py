"""Add immutable application-owned authorization policy sets."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_04"
down_revision: str | Sequence[str] | None = "20260814_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authorization_policy_sets",
        sa.Column("policy_set_id", sa.String(length=128), nullable=False),
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("policy_set_id", "principal_id"),
    )


def downgrade() -> None:
    op.drop_table("authorization_policy_sets")
