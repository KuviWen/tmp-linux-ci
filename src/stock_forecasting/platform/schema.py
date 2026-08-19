from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, UniqueConstraint

metadata = MetaData()

research_records = Table(
    "serving_research_records",
    metadata,
    Column("record_id", String(36), primary_key=True),
    Column("listing_id", String(36), nullable=False),
    Column("authorization_dataset_id", String(128), nullable=True),
    Column("information_cutoff", String(32), nullable=False),
    Column("execution_purpose", String(32), nullable=False),
    Column("fixture_scenario", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "listing_id",
        "information_cutoff",
        "execution_purpose",
        "fixture_scenario",
        name="uq_research_record_listing_cutoff_purpose_scenario",
    ),
)

work_attempts = Table(
    "ops_work_attempts",
    metadata,
    Column("work_id", String(36), primary_key=True),
    Column("operation", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("execution_purpose", String(32), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("attempt_count", Integer, nullable=False),
)

health_assessments = Table(
    "ops_health_assessments",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("assessment_id", String(36), nullable=False, unique=True),
    Column("scope", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("trace_id", String(128), nullable=False),
)

security_audit_events = Table(
    "security_audit_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("action", String(128), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("authorization", JSON, nullable=True),
)

authorization_policy_sets = Table(
    "authorization_policy_sets",
    metadata,
    Column("policy_set_id", String(128), primary_key=True),
    Column("principal_id", String(36), primary_key=True),
    Column("content_digest", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
)

canonical_artifacts = Table(
    "lineage_canonical_artifacts",
    metadata,
    Column("artifact_id", String(72), primary_key=True),
    Column("artifact_kind", String(64), nullable=False),
    Column("execution_purpose", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
)

trace_artifact_refs = Table(
    "lineage_trace_artifact_refs",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("trace_id", String(128), nullable=False),
    Column("artifact_id", String(72), nullable=False),
    UniqueConstraint("trace_id", "artifact_id", name="uq_trace_artifact_ref"),
)

price_research_eligibility = Table(
    "serving_price_research_eligibility",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("eligibility_id", String(36), nullable=False),
    Column("listing_id", String(36), nullable=False),
    Column("source_id", String(128), nullable=False),
    Column("source_mode", String(32), nullable=False),
    Column("evaluated_at", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("eligibility_id", "listing_id", name="uq_price_research_eligibility"),
)

source_credential_versions = Table(
    "security_source_credential_versions",
    metadata,
    Column("provider_id", String(128), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("secret_ref_id", String(256), nullable=False),
    Column("readiness", String(32), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("configured_at", String(32), nullable=False),
    Column("expires_at", String(32), nullable=True),
    Column("last_validated_at", String(32), nullable=True),
    Column("validation_evidence", JSON, nullable=True),
    Column("revoked_at", String(32), nullable=True),
)

source_secret_cleanup_queue = Table(
    "security_source_secret_cleanup_queue",
    metadata,
    Column("secret_ref_id", String(256), primary_key=True),
    Column("provider_id", String(128), nullable=False),
    Column("queued_at", String(32), nullable=False),
    Column("completed_at", String(32), nullable=True),
)

fixture_prediction_results = Table(
    "serving_fixture_prediction_results",
    metadata,
    Column("prediction_id", String(36), primary_key=True),
    Column("trace_id", String(128), nullable=False),
    Column("listing_id", String(36), nullable=False),
    Column("horizon_sessions", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)

production_prediction_records = Table(
    "serving_production_prediction_records",
    metadata,
    Column("prediction_id", String(36), primary_key=True),
    Column("trace_id", String(128), nullable=False),
    Column("listing_id", String(36), nullable=False),
    Column("horizon_sessions", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)

model_lifecycle_events = Table(
    "model_lifecycle_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(72), nullable=False, unique=True),
    Column("command_id", String(128), nullable=False, unique=True),
    Column("model_family_id", String(128), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    Column("event_kind", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("occurred_at", String(32), nullable=False),
    UniqueConstraint(
        "model_family_id",
        "aggregate_version",
        name="uq_model_lifecycle_family_version",
    ),
)

production_serving_assignment_pins = Table(
    "serving_production_assignment_pins",
    metadata,
    Column("pin_id", String(72), primary_key=True),
    Column("model_family_id", String(128), nullable=False),
    Column("forecast_batch_id", String(36), nullable=False),
    Column("market", String(8), nullable=False),
    Column("assignment_id", String(72), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "model_family_id",
        "forecast_batch_id",
        "market",
        name="uq_production_assignment_pin_batch_market",
    ),
)

production_operations_events = Table(
    "ops_production_forecast_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("forecast_batch_id", String(36), nullable=False),
    Column("event_kind", String(64), nullable=False),
    Column("occurred_at", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "forecast_batch_id",
        "event_kind",
        name="uq_production_forecast_event_kind",
    ),
)
