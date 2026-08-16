"""Add the append-only model lifecycle authority ledger."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_08"
down_revision: str | Sequence[str] | None = "20260815_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_lifecycle_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("event_id", sa.String(length=72), nullable=False, unique=True),
        sa.Column("command_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("model_family_id", sa.String(length=128), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("event_kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "model_family_id",
            "aggregate_version",
            name="uq_model_lifecycle_family_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("model_lifecycle_events")
