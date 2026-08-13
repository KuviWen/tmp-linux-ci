from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.pool import StaticPool

metadata = MetaData()

research_records = Table(
    "serving_research_records",
    metadata,
    Column("record_id", String(36), primary_key=True),
    Column("listing_id", String(36), nullable=False),
    Column("information_cutoff", String(32), nullable=False),
    Column("execution_purpose", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "listing_id",
        "information_cutoff",
        "execution_purpose",
        name="uq_research_record_listing_cutoff_purpose",
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


class ImmutableStateConflict(RuntimeError):
    """Raised when a caller tries to replace an immutable published record."""


class StateStore:
    def __init__(self, database_url: str, *, create_schema: bool) -> None:
        if database_url == "sqlite+pysqlite:///:memory:":
            self.engine = create_engine(
                database_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            self.engine = create_engine(database_url)
        if create_schema:
            metadata.create_all(self.engine)

    def ping(self) -> bool:
        with self.engine.connect() as connection:
            return bool(connection.execute(text("SELECT 1")).scalar_one() == 1)

    def publish_research_record(self, record_id: str, payload: dict[str, Any]) -> None:
        identity = payload["identity"]
        values = {
            "record_id": record_id,
            "listing_id": identity["listing_id"],
            "information_cutoff": payload["information_cutoff"],
            "execution_purpose": payload["execution_purpose"],
            "payload": payload,
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(research_records.c.payload).where(
                    research_records.c.listing_id == values["listing_id"],
                    research_records.c.information_cutoff == values["information_cutoff"],
                    research_records.c.execution_purpose == values["execution_purpose"],
                )
            ).scalar_one_or_none()
            if existing is None:
                connection.execute(research_records.insert().values(**values))
            elif existing != payload:
                raise ImmutableStateConflict("immutable_research_record_conflict")

    def publish_fixture_trace(
        self,
        *,
        record_id: str,
        payload: dict[str, Any],
        work_id: str,
        trace_id: str,
        idempotency_key: str,
        health_assessment_id: str,
        audit_event_id: str,
        artifacts: list[dict[str, Any]],
        fixture_predictions: list[dict[str, Any]],
    ) -> None:
        identity = payload["identity"]
        record_values = {
            "record_id": record_id,
            "listing_id": identity["listing_id"],
            "information_cutoff": payload["information_cutoff"],
            "execution_purpose": payload["execution_purpose"],
            "payload": payload,
        }
        with self.engine.begin() as connection:
            existing_record = connection.execute(
                select(research_records.c.payload).where(
                    research_records.c.listing_id == record_values["listing_id"],
                    research_records.c.information_cutoff == record_values["information_cutoff"],
                    research_records.c.execution_purpose == record_values["execution_purpose"],
                )
            ).scalar_one_or_none()
            if existing_record is None:
                connection.execute(research_records.insert().values(**record_values))
            elif existing_record != payload:
                raise ImmutableStateConflict("immutable_research_record_conflict")

            existing_work = connection.execute(
                select(work_attempts.c.work_id).where(work_attempts.c.work_id == work_id)
            ).scalar_one_or_none()
            if existing_work is None:
                connection.execute(
                    work_attempts.insert().values(
                        work_id=work_id,
                        operation="fixture_eod",
                        status="succeeded",
                        execution_purpose="fixture",
                        trace_id=trace_id,
                        idempotency_key=idempotency_key,
                        attempt_count=1,
                    )
                )
                connection.execute(
                    health_assessments.insert().values(
                        assessment_id=health_assessment_id,
                        scope="xtai_fixture_source",
                        status="ready",
                        reason_code="coverage_complete",
                        trace_id=trace_id,
                    )
                )
                connection.execute(
                    security_audit_events.insert().values(
                        event_id=audit_event_id,
                        action="fixture_eod_publication",
                        outcome="allowed",
                        reason_code="fixture_policy_active",
                        trace_id=trace_id,
                    )
                )
                for artifact in artifacts:
                    existing_artifact = connection.execute(
                        select(canonical_artifacts.c.payload).where(
                            canonical_artifacts.c.artifact_id == artifact["artifact_id"]
                        )
                    ).scalar_one_or_none()
                    if existing_artifact is None:
                        connection.execute(
                            canonical_artifacts.insert().values(
                                artifact_id=artifact["artifact_id"],
                                artifact_kind=artifact["artifact_kind"],
                                execution_purpose="fixture",
                                payload=artifact["payload"],
                            )
                        )
                    elif existing_artifact != artifact["payload"]:
                        raise ImmutableStateConflict("immutable_artifact_conflict")
                    connection.execute(
                        trace_artifact_refs.insert().values(
                            trace_id=trace_id,
                            artifact_id=artifact["artifact_id"],
                        )
                    )
                for prediction in fixture_predictions:
                    connection.execute(
                        fixture_prediction_results.insert().values(
                            prediction_id=prediction["prediction_id"],
                            trace_id=trace_id,
                            listing_id=identity["listing_id"],
                            horizon_sessions=prediction["horizon_sessions"],
                            payload=prediction["payload"],
                        )
                    )

    def record_fixture_use_denial(
        self,
        *,
        event_id: str,
        assessment_id: str,
        action: str,
        trace_id: str,
    ) -> None:
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(security_audit_events.c.event_id).where(
                    security_audit_events.c.event_id == event_id
                )
            ).scalar_one_or_none()
            if exists is not None:
                return
            connection.execute(
                security_audit_events.insert().values(
                    event_id=event_id,
                    action=action,
                    outcome="denied",
                    reason_code="fixture_use_forbidden",
                    trace_id=trace_id,
                )
            )
            connection.execute(
                health_assessments.insert().values(
                    assessment_id=assessment_id,
                    scope="fixture_isolation",
                    status="blocked",
                    reason_code="fixture_use_forbidden",
                    trace_id=trace_id,
                )
            )

    def get_listing_research(
        self,
        *,
        listing_id: str,
        information_cutoff: str,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(research_records.c.payload).where(
                    research_records.c.listing_id == listing_id,
                    research_records.c.information_cutoff == information_cutoff,
                )
            ).scalar_one_or_none()
        return deepcopy(payload) if payload is not None else None

    def list_research_records(self, *, execution_purpose: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(research_records.c.payload)
                .where(research_records.c.execution_purpose == execution_purpose)
                .order_by(research_records.c.record_id)
            ).scalars()
            return [deepcopy(payload) for payload in payloads]

    def get_work(self, work_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        work_attempts.c.work_id,
                        work_attempts.c.operation,
                        work_attempts.c.status,
                        work_attempts.c.execution_purpose,
                        work_attempts.c.trace_id,
                        work_attempts.c.attempt_count,
                    ).where(work_attempts.c.work_id == work_id)
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    def list_health(self, *, scope: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = list(
                connection.execute(
                    select(
                        health_assessments.c.status,
                        health_assessments.c.reason_code,
                    )
                    .where(health_assessments.c.scope == scope)
                    .order_by(health_assessments.c.sequence)
                ).mappings()
            )
        if not rows:
            return []
        return [
            {
                "scope": scope,
                "status": rows[-1]["status"],
                "reason_code": rows[-1]["reason_code"],
                "affected_attempts": len(rows),
            }
        ]

    def list_audit_events(self, *, trace_id: str) -> list[dict[str, str]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    security_audit_events.c.action,
                    security_audit_events.c.outcome,
                    security_audit_events.c.reason_code,
                    security_audit_events.c.trace_id,
                )
                .where(security_audit_events.c.trace_id == trace_id)
                .order_by(security_audit_events.c.sequence)
            ).mappings()
            return [dict(row) for row in rows]

    def get_trace_evidence(self, trace_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            artifacts = list(
                connection.execute(
                    select(
                        canonical_artifacts.c.artifact_kind,
                        canonical_artifacts.c.artifact_id,
                        canonical_artifacts.c.execution_purpose,
                    )
                    .select_from(
                        trace_artifact_refs.join(
                            canonical_artifacts,
                            trace_artifact_refs.c.artifact_id == canonical_artifacts.c.artifact_id,
                        )
                    )
                    .where(trace_artifact_refs.c.trace_id == trace_id)
                    .order_by(trace_artifact_refs.c.sequence)
                ).mappings()
            )
            fixture_count = connection.execute(
                select(fixture_prediction_results.c.prediction_id).where(
                    fixture_prediction_results.c.trace_id == trace_id
                )
            ).all()
            production_count = connection.execute(
                select(production_prediction_records.c.prediction_id).where(
                    production_prediction_records.c.trace_id == trace_id
                )
            ).all()
        if not artifacts:
            raise KeyError(trace_id)
        lineage_kinds = {
            "dataset_version": "dataset_version_id",
            "data_selection": "data_selection_id",
            "feature_snapshot": "feature_snapshot_id",
            "model_artifact": "model_artifact_id",
            "serving_assignment": "serving_assignment_id",
        }
        return {
            "execution_purpose": artifacts[0]["execution_purpose"],
            "artifact_kinds": [artifact["artifact_kind"] for artifact in artifacts],
            "lineage_ids": {
                lineage_kinds[artifact["artifact_kind"]]: artifact["artifact_id"]
                for artifact in artifacts
                if artifact["artifact_kind"] in lineage_kinds
            },
            "fixture_prediction_result_count": len(fixture_count),
            "production_prediction_record_count": len(production_count),
        }
