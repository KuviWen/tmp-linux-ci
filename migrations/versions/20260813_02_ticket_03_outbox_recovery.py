"""Add the ticket 03 transactional outbox and projection version state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_02"
down_revision: str | Sequence[str] | None = "20260813_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_outbox_events",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.Column("producer", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_outbox_event_aggregate_version",
        ),
    )
    op.create_table(
        "ops_outbox_dispatch",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.String(length=32), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
    )
    op.create_table(
        "research_projection_status",
        sa.Column("record_id", sa.String(length=36), primary_key=True),
        sa.Column("core_projection_version", sa.Integer(), nullable=False),
        sa.Column("evidence_projection_version", sa.Integer(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
    )
    prior_research_records = sa.table(
        "serving_research_records",
        sa.column("record_id", sa.String(length=36)),
    )
    projection_status = sa.table(
        "research_projection_status",
        sa.column("record_id", sa.String(length=36)),
        sa.column("core_projection_version", sa.Integer()),
        sa.column("evidence_projection_version", sa.Integer()),
        sa.column("stale", sa.Boolean()),
    )
    op.execute(
        projection_status.insert().from_select(
            [
                "record_id",
                "core_projection_version",
                "evidence_projection_version",
                "stale",
            ],
            sa.select(
                prior_research_records.c.record_id,
                sa.literal(0),
                sa.literal(0),
                sa.literal(False),
            ),
        )
    )
    op.create_table(
        "ops_outbox_delivery_attempts",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attempt_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "event_id",
            "attempt_number",
            name="uq_outbox_delivery_event_attempt",
        ),
    )
    op.create_table(
        "ops_processed_outbox_events",
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "consumer_name",
            "event_id",
            name="uq_processed_outbox_consumer_event",
        ),
    )
    op.create_table(
        "ops_projection_cursors",
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "consumer_name",
            "aggregate_id",
            name="uq_projection_cursor_consumer_aggregate",
        ),
    )
    op.create_table(
        "ops_prediction_projection_events",
        sa.Column("event_id", sa.String(length=36), primary_key=True),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "ops_outbox_incidents",
        sa.Column("incident_id", sa.String(length=36), primary_key=True),
        sa.Column("fingerprint", sa.String(length=128), nullable=False, unique=True),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("impact_scope", sa.String(length=160), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ops_outbox_incidents")
    op.drop_table("ops_prediction_projection_events")
    op.drop_table("ops_projection_cursors")
    op.drop_table("ops_processed_outbox_events")
    op.drop_table("ops_outbox_delivery_attempts")
    op.drop_table("research_projection_status")
    op.drop_table("ops_outbox_dispatch")
    op.drop_table("ops_outbox_events")
