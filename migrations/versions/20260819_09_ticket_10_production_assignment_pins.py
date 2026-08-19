"""Persist one immutable production assignment pin per market batch."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_09"
down_revision: str | Sequence[str] | None = "20260817_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "serving_production_assignment_pins",
        sa.Column("pin_id", sa.String(length=72), primary_key=True),
        sa.Column("model_family_id", sa.String(length=128), nullable=False),
        sa.Column("forecast_batch_id", sa.String(length=36), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "model_family_id",
            "forecast_batch_id",
            "market",
            name="uq_production_assignment_pin_batch_market",
        ),
    )
    op.create_table(
        "ops_production_forecast_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("event_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("forecast_batch_id", sa.String(length=36), nullable=False),
        sa.Column("event_kind", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "forecast_batch_id",
            "event_kind",
            name="uq_production_forecast_event_kind",
        ),
    )


def downgrade() -> None:
    op.drop_table("ops_production_forecast_events")
    op.drop_table("serving_production_assignment_pins")
