from __future__ import annotations

from copy import deepcopy
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.pool import StaticPool

from stock_forecasting.contracts import PublicationDisposition
from stock_forecasting.outbox import OutOfOrderEvent, RelayFault, RelayOutcome

metadata = MetaData()

research_records = Table(
    "serving_research_records",
    metadata,
    Column("record_id", String(36), primary_key=True),
    Column("listing_id", String(36), nullable=False),
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

outbox_events = Table(
    "ops_outbox_events",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("event_type", String(96), nullable=False),
    Column("schema_version", String(16), nullable=False),
    Column("aggregate_id", String(36), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    Column("occurred_at", String(32), nullable=False),
    Column("producer", String(64), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "aggregate_id",
        "aggregate_version",
        name="uq_outbox_event_aggregate_version",
    ),
)

outbox_dispatch = Table(
    "ops_outbox_dispatch",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("status", String(32), nullable=False),
)

research_projection_status = Table(
    "research_projection_status",
    metadata,
    Column("record_id", String(36), primary_key=True),
    Column("core_projection_version", Integer, nullable=False),
    Column("evidence_projection_version", Integer, nullable=False),
    Column("stale", Boolean, nullable=False),
)

outbox_delivery_attempts = Table(
    "ops_outbox_delivery_attempts",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("attempt_id", String(36), nullable=False, unique=True),
    Column("event_id", String(36), nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("work_id", String(36), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason_code", String(96), nullable=False),
    Column("trace_id", String(128), nullable=False),
    UniqueConstraint(
        "event_id",
        "attempt_number",
        name="uq_outbox_delivery_event_attempt",
    ),
)

processed_outbox_events = Table(
    "ops_processed_outbox_events",
    metadata,
    Column("consumer_name", String(64), nullable=False),
    Column("event_id", String(36), nullable=False),
    Column("aggregate_id", String(36), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    UniqueConstraint(
        "consumer_name",
        "event_id",
        name="uq_processed_outbox_consumer_event",
    ),
)

projection_cursors = Table(
    "ops_projection_cursors",
    metadata,
    Column("consumer_name", String(64), nullable=False),
    Column("aggregate_id", String(36), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    UniqueConstraint(
        "consumer_name",
        "aggregate_id",
        name="uq_projection_cursor_consumer_aggregate",
    ),
)

operations_prediction_projections = Table(
    "ops_prediction_projection_events",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("aggregate_id", String(36), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
)

outbox_incidents = Table(
    "ops_outbox_incidents",
    metadata,
    Column("incident_id", String(36), primary_key=True),
    Column("fingerprint", String(128), nullable=False, unique=True),
    Column("aggregate_id", String(36), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason_code", String(96), nullable=False),
    Column("occurrence_count", Integer, nullable=False),
    Column("trace_id", String(128), nullable=False),
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

    @staticmethod
    def _relay_id(event_id: str, kind: str, attempt_number: int) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"stock-forecasting/outbox/{event_id}/{kind}/{attempt_number}",
            )
        )

    @staticmethod
    def _incident_id(aggregate_id: str, reason_code: str) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"stock-forecasting/incident/outbox-projection-delay/{reason_code}/{aggregate_id}",
            )
        )

    def _record_outbox_incident(
        self,
        connection: Any,
        *,
        event: Any,
        reason_code: str,
    ) -> None:
        aggregate_id = str(event["aggregate_id"])
        fingerprint = f"outbox_projection_delay:{reason_code}:{aggregate_id}"
        existing = (
            connection.execute(
                select(
                    outbox_incidents.c.incident_id,
                    outbox_incidents.c.occurrence_count,
                ).where(outbox_incidents.c.fingerprint == fingerprint)
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            connection.execute(
                outbox_incidents.insert().values(
                    incident_id=self._incident_id(aggregate_id, reason_code),
                    fingerprint=fingerprint,
                    aggregate_id=aggregate_id,
                    status="open",
                    reason_code=reason_code,
                    occurrence_count=1,
                    trace_id=event["trace_id"],
                )
            )
            return
        connection.execute(
            outbox_incidents.update()
            .where(outbox_incidents.c.incident_id == existing["incident_id"])
            .values(
                status="open",
                reason_code=reason_code,
                occurrence_count=int(existing["occurrence_count"]) + 1,
            )
        )

    def _recover_abandoned_deliveries(self, *, event: Any) -> None:
        event_id = str(event["event_id"])
        with self.engine.begin() as connection:
            abandoned = list(
                connection.execute(
                    select(
                        outbox_delivery_attempts.c.attempt_id,
                        outbox_delivery_attempts.c.attempt_number,
                        outbox_delivery_attempts.c.work_id,
                    ).where(
                        outbox_delivery_attempts.c.event_id == event_id,
                        outbox_delivery_attempts.c.status == "running",
                    )
                ).mappings()
            )
            for attempt in abandoned:
                connection.execute(
                    outbox_delivery_attempts.update()
                    .where(outbox_delivery_attempts.c.attempt_id == attempt["attempt_id"])
                    .values(status="crashed", reason_code="relay_process_terminated")
                )
                connection.execute(
                    work_attempts.update()
                    .where(work_attempts.c.work_id == attempt["work_id"])
                    .values(status="failed")
                )
                self._record_outbox_incident(
                    connection,
                    event=event,
                    reason_code="relay_process_terminated",
                )
                connection.execute(
                    security_audit_events.insert().values(
                        event_id=self._relay_id(
                            event_id,
                            "audit-recovery",
                            int(attempt["attempt_number"]),
                        ),
                        action="outbox_recovery",
                        outcome="allowed",
                        reason_code="relay_process_terminated",
                        trace_id=event["trace_id"],
                    )
                )

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
        outbox_event_id: str,
        operations: PublicationDisposition,
        artifacts: list[dict[str, Any]],
        fixture_predictions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        identity = payload["identity"]
        with self.engine.begin() as connection:
            existing_event = (
                connection.execute(
                    select(
                        outbox_events.c.aggregate_version,
                        outbox_events.c.aggregate_id,
                        outbox_events.c.trace_id,
                    ).where(outbox_events.c.event_id == outbox_event_id)
                )
                .mappings()
                .one_or_none()
            )
            if existing_event is None:
                aggregate_version = (
                    int(
                        connection.execute(
                            select(
                                func.coalesce(func.max(outbox_events.c.aggregate_version), 0)
                            ).where(outbox_events.c.aggregate_id == identity["listing_id"])
                        ).scalar_one()
                    )
                    + 1
                )
                connection.execute(
                    outbox_events.insert().values(
                        event_id=outbox_event_id,
                        event_type="forecast_publication.completed",
                        schema_version="1.0.0",
                        aggregate_id=identity["listing_id"],
                        aggregate_version=aggregate_version,
                        occurred_at=payload["observed_at"],
                        producer="forecast_execution",
                        trace_id=trace_id,
                        payload={
                            "record_id": record_id,
                            "listing_id": identity["listing_id"],
                            "prediction_count": len(fixture_predictions),
                        },
                    )
                )
                connection.execute(
                    outbox_dispatch.insert().values(
                        event_id=outbox_event_id,
                        status="pending",
                    )
                )
            else:
                if (
                    existing_event["aggregate_id"] != identity["listing_id"]
                    or existing_event["trace_id"] != trace_id
                ):
                    raise ImmutableStateConflict("immutable_outbox_event_conflict")
                aggregate_version = int(existing_event["aggregate_version"])

            published_payload = deepcopy(payload)
            published_payload["projection"] = {
                "core_projection_version": aggregate_version,
                "evidence_projection_version": 0,
                "stale": True,
            }
            record_values = {
                "record_id": record_id,
                "listing_id": identity["listing_id"],
                "information_cutoff": payload["information_cutoff"],
                "execution_purpose": payload["execution_purpose"],
                "fixture_scenario": payload["source_evidence"]["fixture_scenario"],
                "payload": published_payload,
            }
            existing_record = connection.execute(
                select(research_records.c.payload).where(
                    research_records.c.listing_id == record_values["listing_id"],
                    research_records.c.information_cutoff == record_values["information_cutoff"],
                    research_records.c.execution_purpose == record_values["execution_purpose"],
                    research_records.c.fixture_scenario == record_values["fixture_scenario"],
                )
            ).scalar_one_or_none()
            if existing_record is None:
                connection.execute(research_records.insert().values(**record_values))
                connection.execute(
                    research_projection_status.insert().values(
                        record_id=record_id,
                        core_projection_version=aggregate_version,
                        evidence_projection_version=0,
                        stale=True,
                    )
                )
            elif existing_record != published_payload:
                raise ImmutableStateConflict("immutable_research_record_conflict")

            existing_work = connection.execute(
                select(work_attempts.c.work_id).where(work_attempts.c.work_id == work_id)
            ).scalar_one_or_none()
            if existing_work is None:
                connection.execute(
                    work_attempts.insert().values(
                        work_id=work_id,
                        operation="fixture_eod",
                        status=operations.work_status,
                        execution_purpose="fixture",
                        trace_id=trace_id,
                        idempotency_key=idempotency_key,
                        attempt_count=1,
                    )
                )
                connection.execute(
                    health_assessments.insert().values(
                        assessment_id=health_assessment_id,
                        scope=operations.health_scope,
                        status=operations.health_status,
                        reason_code=operations.health_reason_code,
                        trace_id=trace_id,
                    )
                )
                connection.execute(
                    security_audit_events.insert().values(
                        event_id=audit_event_id,
                        action="fixture_eod_publication",
                        outcome="allowed",
                        reason_code=operations.audit_reason_code,
                        trace_id=trace_id,
                    )
                )
                for artifact in artifacts:
                    existing_artifact = (
                        connection.execute(
                            select(
                                canonical_artifacts.c.artifact_kind,
                                canonical_artifacts.c.execution_purpose,
                                canonical_artifacts.c.payload,
                            ).where(canonical_artifacts.c.artifact_id == artifact["artifact_id"])
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing_artifact is None:
                        connection.execute(
                            canonical_artifacts.insert().values(
                                artifact_id=artifact["artifact_id"],
                                artifact_kind=artifact["artifact_kind"],
                                execution_purpose="fixture",
                                payload=artifact["payload"],
                            )
                        )
                    elif (
                        existing_artifact["artifact_kind"] != artifact["artifact_kind"]
                        or existing_artifact["execution_purpose"] != "fixture"
                        or existing_artifact["payload"] != artifact["payload"]
                    ):
                        raise ImmutableStateConflict("immutable_artifact_conflict")
                    existing_reference = connection.execute(
                        select(trace_artifact_refs.c.sequence).where(
                            trace_artifact_refs.c.trace_id == trace_id,
                            trace_artifact_refs.c.artifact_id == artifact["artifact_id"],
                        )
                    ).scalar_one_or_none()
                    if existing_reference is None:
                        connection.execute(
                            trace_artifact_refs.insert().values(
                                trace_id=trace_id,
                                artifact_id=artifact["artifact_id"],
                            )
                        )
                for prediction in fixture_predictions:
                    existing_prediction = connection.execute(
                        select(fixture_prediction_results.c.payload).where(
                            fixture_prediction_results.c.prediction_id
                            == prediction["prediction_id"]
                        )
                    ).scalar_one_or_none()
                    if existing_prediction is None:
                        connection.execute(
                            fixture_prediction_results.insert().values(
                                prediction_id=prediction["prediction_id"],
                                trace_id=trace_id,
                                listing_id=identity["listing_id"],
                                horizon_sessions=prediction["horizon_sessions"],
                                payload=prediction["payload"],
                            )
                        )
                    elif existing_prediction != prediction["payload"]:
                        raise ImmutableStateConflict("immutable_fixture_prediction_conflict")

        return {
            "event_id": outbox_event_id,
            "aggregate_version": aggregate_version,
        }

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
        fixture_scenario: str = "normal",
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        research_records.c.payload,
                        research_projection_status.c.core_projection_version,
                        research_projection_status.c.evidence_projection_version,
                        research_projection_status.c.stale,
                    )
                    .select_from(
                        research_records.join(
                            research_projection_status,
                            research_records.c.record_id == research_projection_status.c.record_id,
                        )
                    )
                    .where(
                        research_records.c.listing_id == listing_id,
                        research_records.c.information_cutoff == information_cutoff,
                        research_records.c.fixture_scenario == fixture_scenario,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        result = cast(dict[str, Any], deepcopy(row["payload"]))
        result["projection"] = {
            "core_projection_version": row["core_projection_version"],
            "evidence_projection_version": row["evidence_projection_version"],
            "stale": row["stale"],
        }
        return result

    def list_research_records(self, *, execution_purpose: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    research_records.c.payload,
                    research_projection_status.c.core_projection_version,
                    research_projection_status.c.evidence_projection_version,
                    research_projection_status.c.stale,
                )
                .select_from(
                    research_records.join(
                        research_projection_status,
                        research_records.c.record_id == research_projection_status.c.record_id,
                    )
                )
                .where(research_records.c.execution_purpose == execution_purpose)
                .where(research_records.c.fixture_scenario == "normal")
                .order_by(research_records.c.record_id)
            ).mappings()
            records: list[dict[str, Any]] = []
            for row in rows:
                record = cast(dict[str, Any], deepcopy(row["payload"]))
                record["projection"] = {
                    "core_projection_version": row["core_projection_version"],
                    "evidence_projection_version": row["evidence_projection_version"],
                    "stale": row["stale"],
                }
                records.append(record)
            return records

    def get_outbox_event(self, event_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        outbox_events.c.event_id,
                        outbox_events.c.event_type,
                        outbox_events.c.schema_version,
                        outbox_events.c.aggregate_id,
                        outbox_events.c.aggregate_version,
                        outbox_events.c.producer,
                        outbox_events.c.trace_id,
                        outbox_dispatch.c.status.label("delivery_status"),
                    )
                    .select_from(
                        outbox_events.join(
                            outbox_dispatch,
                            outbox_events.c.event_id == outbox_dispatch.c.event_id,
                        )
                    )
                    .where(outbox_events.c.event_id == event_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise KeyError(event_id)
        return dict(row)

    def list_prediction_records(self, *, trace_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(fixture_prediction_results.c.payload)
                .where(fixture_prediction_results.c.trace_id == trace_id)
                .order_by(fixture_prediction_results.c.horizon_sessions)
            ).scalars()
            return [deepcopy(payload) for payload in payloads]

    def relay_outbox(
        self,
        *,
        event_id: str | None = None,
        fault: RelayFault,
    ) -> RelayOutcome:
        with self.engine.connect() as connection:
            event_query = (
                select(
                    outbox_events.c.event_id,
                    outbox_events.c.aggregate_id,
                    outbox_events.c.aggregate_version,
                    outbox_events.c.trace_id,
                    outbox_events.c.payload,
                    outbox_dispatch.c.status.label("delivery_status"),
                )
                .select_from(
                    outbox_events.join(
                        outbox_dispatch,
                        outbox_events.c.event_id == outbox_dispatch.c.event_id,
                    )
                )
                .order_by(outbox_events.c.aggregate_id, outbox_events.c.aggregate_version)
            )
            if event_id is None:
                event_query = event_query.where(outbox_dispatch.c.status == "pending")
            else:
                event_query = event_query.where(outbox_events.c.event_id == event_id)
            event = connection.execute(event_query.limit(1)).mappings().one_or_none()

        if event is None:
            if event_id is not None:
                raise KeyError(event_id)
            return RelayOutcome(status="empty", event_id=None, aggregate_version=None)
        resolved_event_id = str(event["event_id"])
        aggregate_version = int(event["aggregate_version"])
        if event["delivery_status"] == "delivered":
            return RelayOutcome(
                status="already_delivered",
                event_id=resolved_event_id,
                aggregate_version=aggregate_version,
            )

        self._recover_abandoned_deliveries(event=event)

        with self.engine.begin() as connection:
            attempt_number = (
                int(
                    connection.execute(
                        select(func.count(outbox_delivery_attempts.c.sequence)).where(
                            outbox_delivery_attempts.c.event_id == resolved_event_id
                        )
                    ).scalar_one()
                )
                + 1
            )
            attempt_id = self._relay_id(resolved_event_id, "attempt", attempt_number)
            work_id = self._relay_id(resolved_event_id, "work", attempt_number)
            connection.execute(
                outbox_delivery_attempts.insert().values(
                    attempt_id=attempt_id,
                    event_id=resolved_event_id,
                    attempt_number=attempt_number,
                    work_id=work_id,
                    status="running",
                    reason_code="delivery_started",
                    trace_id=event["trace_id"],
                )
            )
            connection.execute(
                work_attempts.insert().values(
                    work_id=work_id,
                    operation="outbox_relay",
                    status="running",
                    execution_purpose="fixture",
                    trace_id=event["trace_id"],
                    idempotency_key=f"outbox:{resolved_event_id}:{attempt_number}",
                    attempt_count=attempt_number,
                )
            )

        try:
            fault.before_consumers(resolved_event_id)
            for consumer_name in ("research_projection", "operations_projection"):
                self._consume_outbox_event(
                    event=event,
                    consumer_name=consumer_name,
                    fault=fault,
                )
            fault.before_ack(resolved_event_id)
        except OutOfOrderEvent:
            with self.engine.begin() as connection:
                connection.execute(
                    outbox_delivery_attempts.update()
                    .where(outbox_delivery_attempts.c.attempt_id == attempt_id)
                    .values(status="deferred", reason_code="out_of_order_aggregate_version")
                )
                connection.execute(
                    work_attempts.update()
                    .where(work_attempts.c.work_id == work_id)
                    .values(status="blocked")
                )
                self._record_outbox_incident(
                    connection,
                    event=event,
                    reason_code="out_of_order_aggregate_version",
                )
                connection.execute(
                    security_audit_events.insert().values(
                        event_id=self._relay_id(
                            resolved_event_id,
                            "audit-deferred",
                            attempt_number,
                        ),
                        action="outbox_delivery",
                        outcome="denied",
                        reason_code="out_of_order_aggregate_version",
                        trace_id=event["trace_id"],
                    )
                )
            return RelayOutcome(
                status="deferred",
                event_id=resolved_event_id,
                aggregate_version=aggregate_version,
            )
        except RuntimeError:
            with self.engine.begin() as connection:
                connection.execute(
                    outbox_delivery_attempts.update()
                    .where(outbox_delivery_attempts.c.attempt_id == attempt_id)
                    .values(status="failed", reason_code="consumer_transaction_crash")
                )
                connection.execute(
                    work_attempts.update()
                    .where(work_attempts.c.work_id == work_id)
                    .values(status="failed")
                )
                self._record_outbox_incident(
                    connection,
                    event=event,
                    reason_code="consumer_transaction_crash",
                )
                connection.execute(
                    security_audit_events.insert().values(
                        event_id=self._relay_id(
                            resolved_event_id,
                            "audit-consumer-failed",
                            attempt_number,
                        ),
                        action="outbox_delivery",
                        outcome="denied",
                        reason_code="consumer_transaction_crash",
                        trace_id=event["trace_id"],
                    )
                )
            return RelayOutcome(
                status="failed",
                event_id=resolved_event_id,
                aggregate_version=aggregate_version,
            )

        with self.engine.begin() as connection:
            connection.execute(
                outbox_dispatch.update()
                .where(outbox_dispatch.c.event_id == resolved_event_id)
                .values(status="delivered")
            )
            connection.execute(
                outbox_delivery_attempts.update()
                .where(outbox_delivery_attempts.c.attempt_id == attempt_id)
                .values(status="delivered", reason_code="consumer_effects_committed")
            )
            connection.execute(
                work_attempts.update()
                .where(work_attempts.c.work_id == work_id)
                .values(status="succeeded")
            )
            connection.execute(
                security_audit_events.insert().values(
                    event_id=self._relay_id(resolved_event_id, "audit-delivered", attempt_number),
                    action="outbox_delivery",
                    outcome="allowed",
                    reason_code="consumer_effects_committed",
                    trace_id=event["trace_id"],
                )
            )
            connection.execute(
                outbox_incidents.update()
                .where(outbox_incidents.c.aggregate_id == event["aggregate_id"])
                .values(status="monitoring")
            )
        return RelayOutcome(
            status="delivered",
            event_id=resolved_event_id,
            aggregate_version=aggregate_version,
        )

    def _consume_outbox_event(
        self,
        *,
        event: Any,
        consumer_name: str,
        fault: RelayFault,
    ) -> None:
        event_id = str(event["event_id"])
        aggregate_id = str(event["aggregate_id"])
        aggregate_version = int(event["aggregate_version"])
        payload = cast(dict[str, Any], event["payload"])
        with self.engine.begin() as connection:
            processed = connection.execute(
                select(processed_outbox_events.c.event_id).where(
                    processed_outbox_events.c.consumer_name == consumer_name,
                    processed_outbox_events.c.event_id == event_id,
                )
            ).scalar_one_or_none()
            if processed is not None:
                return

            current_version = connection.execute(
                select(projection_cursors.c.aggregate_version).where(
                    projection_cursors.c.consumer_name == consumer_name,
                    projection_cursors.c.aggregate_id == aggregate_id,
                )
            ).scalar_one_or_none()
            expected_version = 1 if current_version is None else int(current_version) + 1
            if aggregate_version != expected_version:
                raise OutOfOrderEvent("out_of_order_aggregate_version")

            if consumer_name == "research_projection":
                connection.execute(
                    research_projection_status.update()
                    .where(research_projection_status.c.record_id == payload["record_id"])
                    .values(
                        evidence_projection_version=aggregate_version,
                        stale=False,
                    )
                )
            else:
                connection.execute(
                    operations_prediction_projections.insert().values(
                        event_id=event_id,
                        aggregate_id=aggregate_id,
                        aggregate_version=aggregate_version,
                        trace_id=event["trace_id"],
                        payload=payload,
                    )
                )
            fault.before_consumer_commit(consumer_name, event_id)
            connection.execute(
                processed_outbox_events.insert().values(
                    consumer_name=consumer_name,
                    event_id=event_id,
                    aggregate_id=aggregate_id,
                    aggregate_version=aggregate_version,
                )
            )
            cursor = connection.execute(
                select(projection_cursors.c.aggregate_version).where(
                    projection_cursors.c.consumer_name == consumer_name,
                    projection_cursors.c.aggregate_id == aggregate_id,
                )
            ).scalar_one_or_none()
            if cursor is None:
                connection.execute(
                    projection_cursors.insert().values(
                        consumer_name=consumer_name,
                        aggregate_id=aggregate_id,
                        aggregate_version=aggregate_version,
                    )
                )
            else:
                connection.execute(
                    projection_cursors.update()
                    .where(
                        projection_cursors.c.consumer_name == consumer_name,
                        projection_cursors.c.aggregate_id == aggregate_id,
                    )
                    .values(aggregate_version=aggregate_version)
                )

    def get_outbox_recovery(self, event_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            attempts = list(
                connection.execute(
                    select(
                        outbox_delivery_attempts.c.attempt_number,
                        outbox_delivery_attempts.c.status,
                        outbox_delivery_attempts.c.reason_code,
                        outbox_delivery_attempts.c.work_id,
                        work_attempts.c.status.label("work_status"),
                    )
                    .select_from(
                        outbox_delivery_attempts.join(
                            work_attempts,
                            outbox_delivery_attempts.c.work_id == work_attempts.c.work_id,
                        )
                    )
                    .where(outbox_delivery_attempts.c.event_id == event_id)
                    .order_by(outbox_delivery_attempts.c.attempt_number)
                ).mappings()
            )
            counts = {
                consumer_name: int(
                    connection.execute(
                        select(func.count())
                        .select_from(processed_outbox_events)
                        .where(
                            processed_outbox_events.c.consumer_name == consumer_name,
                            processed_outbox_events.c.event_id == event_id,
                        )
                    ).scalar_one()
                )
                for consumer_name in ("research_projection", "operations_projection")
            }
            return {
                "delivery_attempts": [dict(attempt) for attempt in attempts],
                "consumer_effect_counts": counts,
            }

    def list_outbox_incidents(self, *, aggregate_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    outbox_incidents.c.incident_id,
                    outbox_incidents.c.fingerprint,
                    outbox_incidents.c.aggregate_id,
                    outbox_incidents.c.status,
                    outbox_incidents.c.reason_code,
                    outbox_incidents.c.occurrence_count,
                    outbox_incidents.c.trace_id,
                ).where(outbox_incidents.c.aggregate_id == aggregate_id)
            ).mappings()
            return [dict(row) for row in rows]

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
            "artifact_ids": [artifact["artifact_id"] for artifact in artifacts],
            "lineage_ids": {
                lineage_kinds[artifact["artifact_kind"]]: artifact["artifact_id"]
                for artifact in artifacts
                if artifact["artifact_kind"] in lineage_kinds
            },
            "fixture_prediction_result_count": len(fixture_count),
            "production_prediction_record_count": len(production_count),
        }
