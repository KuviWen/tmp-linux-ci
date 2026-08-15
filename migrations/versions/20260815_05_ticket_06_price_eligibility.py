"""Add append-only Taiwan price research eligibility evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_05"
down_revision: str | Sequence[str] | None = "20260814_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "serving_price_research_eligibility",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("eligibility_id", sa.String(length=36), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("evaluated_at", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "eligibility_id",
            "listing_id",
            name="uq_price_research_eligibility",
        ),
    )


def downgrade() -> None:
    op.drop_table("serving_price_research_eligibility")
