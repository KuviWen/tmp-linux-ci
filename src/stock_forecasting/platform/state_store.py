from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, cast

from sqlalchemy import (
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.pool import StaticPool

from stock_forecasting.contracts import PublicationDisposition
from stock_forecasting.outbox import (
    EventCompatibility,
    RelayClock,
    RelayFault,
    RelayOutcome,
)
from stock_forecasting.platform.outbox_relay import (
    OutboxRelay,
    outbox_dispatch,
    outbox_events,
    research_projection_status,
)
from stock_forecasting.platform.schema import (
    canonical_artifacts,
    fixture_prediction_results,
    health_assessments,
    metadata,
    production_prediction_records,
    research_records,
    security_audit_events,
    trace_artifact_refs,
    work_attempts,
)


class ImmutableStateConflict(RuntimeError):
    """Raised when a caller tries to replace an immutable published record."""


def _content_digest(payload: object) -> str:
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical_payload).hexdigest()


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
        self._outbox = OutboxRelay(self.engine)
        if create_schema:
            metadata.create_all(self.engine)

    def ping(self) -> bool:
        with self.engine.connect() as connection:
            return bool(connection.execute(text("SELECT 1")).scalar_one() == 1)

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
                        fencing_token=0,
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
        return self._outbox.get_event(event_id)

    def list_prediction_records(self, *, trace_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(fixture_prediction_results.c.payload)
                .where(fixture_prediction_results.c.trace_id == trace_id)
                .order_by(fixture_prediction_results.c.horizon_sessions)
            ).scalars()
            return [deepcopy(payload) for payload in payloads]

    def list_prediction_record_evidence(self, *, trace_id: str) -> list[dict[str, str]]:
        with self.engine.connect() as connection:
            records = connection.execute(
                select(
                    fixture_prediction_results.c.prediction_id,
                    fixture_prediction_results.c.payload,
                )
                .where(fixture_prediction_results.c.trace_id == trace_id)
                .order_by(fixture_prediction_results.c.horizon_sessions)
            ).mappings()
            return [
                {
                    "prediction_id": str(record["prediction_id"]),
                    "content_digest": _content_digest(record["payload"]),
                }
                for record in records
            ]

    def relay_outbox(
        self,
        *,
        event_id: str | None = None,
        fault: RelayFault,
        compatibility: EventCompatibility,
        clock: RelayClock,
        worker_id: str,
    ) -> RelayOutcome:
        return self._outbox.relay(
            event_id=event_id,
            fault=fault,
            compatibility=compatibility,
            clock=clock,
            worker_id=worker_id,
        )

    def get_outbox_recovery(self, event_id: str) -> dict[str, Any]:
        return self._outbox.get_recovery(event_id)

    def list_outbox_incidents(self, *, aggregate_id: str) -> list[dict[str, Any]]:
        return self._outbox.list_incidents(aggregate_id=aggregate_id)

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
                        canonical_artifacts.c.payload,
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
            audit_events = list(
                connection.execute(
                    select(
                        security_audit_events.c.event_id,
                        security_audit_events.c.action,
                        security_audit_events.c.outcome,
                        security_audit_events.c.reason_code,
                    )
                    .where(security_audit_events.c.trace_id == trace_id)
                    .order_by(security_audit_events.c.sequence)
                ).mappings()
            )
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
            "artifact_content_digests": {
                artifact["artifact_id"]: _content_digest(artifact["payload"])
                for artifact in artifacts
            },
            "lineage_ids": {
                lineage_kinds[artifact["artifact_kind"]]: artifact["artifact_id"]
                for artifact in artifacts
                if artifact["artifact_kind"] in lineage_kinds
            },
            "fixture_prediction_result_count": len(fixture_count),
            "production_prediction_record_count": len(production_count),
            "audit_events": [dict(event) for event in audit_events],
        }
