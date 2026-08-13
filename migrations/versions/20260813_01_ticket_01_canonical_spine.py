"""Create the ticket 01 canonical engineering-spine schema."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "serving_research_records",
        sa.Column("record_id", sa.String(length=36), primary_key=True),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("information_cutoff", sa.String(length=32), nullable=False),
        sa.Column("execution_purpose", sa.String(length=32), nullable=False),
        sa.Column("fixture_scenario", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "listing_id",
            "information_cutoff",
            "execution_purpose",
            "fixture_scenario",
            name="uq_research_record_listing_cutoff_purpose_scenario",
        ),
    )
    op.create_table(
        "ops_work_attempts",
        sa.Column("work_id", sa.String(length=36), primary_key=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("execution_purpose", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "ops_health_assessments",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("assessment_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
    )
    op.create_table(
        "security_audit_events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
    )
    op.create_table(
        "lineage_canonical_artifacts",
        sa.Column("artifact_id", sa.String(length=72), primary_key=True),
        sa.Column("artifact_kind", sa.String(length=64), nullable=False),
        sa.Column("execution_purpose", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "lineage_trace_artifact_refs",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("artifact_id", sa.String(length=72), nullable=False),
        sa.UniqueConstraint("trace_id", "artifact_id", name="uq_trace_artifact_ref"),
    )
    op.create_table(
        "serving_fixture_prediction_results",
        sa.Column("prediction_id", sa.String(length=36), primary_key=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("horizon_sessions", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "serving_production_prediction_records",
        sa.Column("prediction_id", sa.String(length=36), primary_key=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("listing_id", sa.String(length=36), nullable=False),
        sa.Column("horizon_sessions", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("serving_production_prediction_records")
    op.drop_table("serving_fixture_prediction_results")
    op.drop_table("lineage_trace_artifact_refs")
    op.drop_table("lineage_canonical_artifacts")
    op.drop_table("security_audit_events")
    op.drop_table("ops_health_assessments")
    op.drop_table("ops_work_attempts")
    op.drop_table("serving_research_records")
