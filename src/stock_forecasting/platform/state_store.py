from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from sqlalchemy import (
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.pool import StaticPool

from stock_forecasting.content_address import (
    canonical_json_bytes,
    content_id,
    sha256_hex,
)
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
    authorization_policy_sets,
    canonical_artifacts,
    fixture_prediction_results,
    health_assessments,
    metadata,
    price_research_eligibility,
    production_operations_events,
    production_prediction_records,
    research_records,
    security_audit_events,
    source_credential_versions,
    source_secret_cleanup_queue,
    trace_artifact_refs,
    work_attempts,
)
from stock_forecasting.source_retrieval_receipt import SourceRetrievalReceipt


class ImmutableStateConflict(RuntimeError):
    """Raised when a caller tries to replace an immutable published record."""


def _content_digest(payload: object) -> str:
    return sha256_hex(canonical_json_bytes(payload))


def _canonical_artifact_id(artifact_kind: str, payload: object) -> str:
    return content_id(artifact_kind, payload)


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

    def authorization_policy_sets_are_read_only_for_current_role(self) -> bool:
        if self.engine.dialect.name != "postgresql":
            return False
        with self.engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        """
                        SELECT NOT role.rolsuper
                           AND NOT has_table_privilege(
                               current_user,
                               'public.authorization_policy_sets',
                               'INSERT'
                           )
                           AND NOT has_table_privilege(
                               current_user,
                               'public.authorization_policy_sets',
                               'UPDATE'
                           )
                           AND NOT has_table_privilege(
                               current_user,
                               'public.authorization_policy_sets',
                               'DELETE'
                           )
                        FROM pg_roles AS role
                        WHERE role.rolname = current_user
                        """
                    )
                ).scalar_one()
            )

    def model_lifecycle_events_are_append_only_for_current_role(self) -> bool:
        if self.engine.dialect.name != "postgresql":
            return False
        with self.engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        """
                        SELECT NOT role.rolsuper
                           AND has_table_privilege(
                               current_user,
                               'public.model_lifecycle_events',
                               'SELECT, INSERT'
                           )
                           AND NOT has_table_privilege(
                               current_user,
                               'public.model_lifecycle_events',
                               'UPDATE'
                           )
                           AND NOT has_table_privilege(
                               current_user,
                               'public.model_lifecycle_events',
                               'DELETE'
                           )
                        FROM pg_roles AS role
                        WHERE role.rolname = current_user
                        """
                    )
                ).scalar_one()
            )

    def install_authorization_policy_set(
        self,
        *,
        policy_set_id: str,
        principal_id: str,
        payload: dict[str, Any],
    ) -> None:
        content_digest = _content_digest(payload)
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(
                        authorization_policy_sets.c.principal_id,
                        authorization_policy_sets.c.content_digest,
                    ).where(
                        authorization_policy_sets.c.policy_set_id == policy_set_id,
                        authorization_policy_sets.c.principal_id == principal_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                connection.execute(
                    authorization_policy_sets.insert().values(
                        policy_set_id=policy_set_id,
                        principal_id=principal_id,
                        content_digest=content_digest,
                        payload=payload,
                    )
                )
                return
            if existing["content_digest"] != content_digest:
                raise ImmutableStateConflict("immutable_authorization_policy_conflict")

    def get_authorization_policy_set(
        self,
        *,
        policy_set_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(authorization_policy_sets.c.payload).where(
                    authorization_policy_sets.c.policy_set_id == policy_set_id,
                    authorization_policy_sets.c.principal_id == principal_id,
                )
            ).scalar_one_or_none()
        if payload is None:
            raise KeyError(policy_set_id)
        return deepcopy(cast(dict[str, Any], payload))

    def publish_fixture_trace(
        self,
        *,
        record_id: str,
        payload: dict[str, Any],
        work_id: str,
        trace_id: str,
        idempotency_key: str,
        health_assessment_id: str,
        outbox_event_id: str,
        operations: PublicationDisposition,
        artifacts: list[dict[str, Any]],
        fixture_predictions: list[dict[str, Any]],
        authorization_decision: dict[str, object],
    ) -> dict[str, Any]:
        identity = payload["identity"]
        with self.engine.begin() as connection:
            self._insert_authorization_decision(
                connection,
                authorization=authorization_decision,
                outcome="allowed",
                trace_id=trace_id,
            )
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
                "authorization_dataset_id": authorization_decision["dataset_id"],
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

    def get_listing_authorization_dataset(
        self,
        *,
        listing_id: str,
        information_cutoff: str,
        execution_purpose: str = "fixture",
        fixture_scenario: str = "normal",
    ) -> str | None:
        with self.engine.connect() as connection:
            dataset_id = connection.execute(
                select(research_records.c.authorization_dataset_id).where(
                    research_records.c.listing_id == listing_id,
                    research_records.c.information_cutoff == information_cutoff,
                    research_records.c.execution_purpose == execution_purpose,
                    research_records.c.fixture_scenario == fixture_scenario,
                )
            ).scalar_one_or_none()
        return cast(str | None, dataset_id)

    def publish_production_trace(
        self,
        *,
        publication: dict[str, Any],
        research_record_payloads: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        predictions: list[dict[str, Any]],
        trace_id: str,
        idempotency_key: str,
    ) -> int:
        forecast_batch_id = str(publication["forecast_batch_id"])
        outbox_event_id = str(publication["outbox_event_id"])
        work_id = content_id("production_work", {"forecast_batch_id": forecast_batch_id})[-36:]
        health_assessment_id = content_id(
            "production_health",
            {"forecast_batch_id": forecast_batch_id},
        )[-36:]
        audit_event_id = content_id(
            "production_audit",
            {"forecast_batch_id": forecast_batch_id},
        )[-36:]
        publication_digest = _content_digest(
            {
                "publication": publication,
                "research_records": research_record_payloads,
                "artifacts": artifacts,
                "predictions": predictions,
            }
        )
        has_unavailable = any(item["prediction_status"] == "unavailable" for item in predictions)
        slo_breached = bool(publication["slo_breached"])
        with self.engine.begin() as connection:
            existing_work = (
                connection.execute(
                    select(
                        work_attempts.c.trace_id,
                        work_attempts.c.status,
                    ).where(work_attempts.c.idempotency_key == idempotency_key)
                )
                .mappings()
                .one_or_none()
            )
            if existing_work is not None:
                if existing_work["trace_id"] != trace_id or existing_work["status"] != "succeeded":
                    raise ImmutableStateConflict("immutable_production_work_conflict")
                existing_payload = connection.execute(
                    select(outbox_events.c.payload).where(
                        outbox_events.c.trace_id == trace_id,
                        outbox_events.c.event_type == "production_forecast_publication.completed",
                    )
                ).scalar_one_or_none()
                if (
                    not isinstance(existing_payload, dict)
                    or existing_payload.get("publication_digest") != publication_digest
                ):
                    raise ImmutableStateConflict("immutable_production_work_conflict")
                return 1

            connection.execute(
                outbox_events.insert().values(
                    event_id=outbox_event_id,
                    event_type="production_forecast_publication.completed",
                    schema_version="1.0.0",
                    aggregate_id=forecast_batch_id,
                    aggregate_version=1,
                    occurred_at=str(publication["completed_at"]),
                    producer="forecast_execution",
                    trace_id=trace_id,
                    payload={
                        "record_ids": [item["record_id"] for item in research_record_payloads],
                        "forecast_batch_id": forecast_batch_id,
                        "prediction_count": len(predictions),
                        "publication_digest": publication_digest,
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
                            execution_purpose="production",
                            payload=artifact["payload"],
                        )
                    )
                elif (
                    existing_artifact["artifact_kind"] != artifact["artifact_kind"]
                    or existing_artifact["execution_purpose"] != "production"
                    or existing_artifact["payload"] != artifact["payload"]
                ):
                    raise ImmutableStateConflict("immutable_artifact_conflict")
                connection.execute(
                    trace_artifact_refs.insert().values(
                        trace_id=trace_id,
                        artifact_id=artifact["artifact_id"],
                    )
                )
            for prediction in predictions:
                connection.execute(
                    production_prediction_records.insert().values(
                        prediction_id=prediction["prediction_id"],
                        trace_id=trace_id,
                        listing_id=prediction["listing_id"],
                        horizon_sessions=prediction["horizon_sessions"],
                        payload=prediction,
                    )
                )
            for record in research_record_payloads:
                payload = {key: value for key, value in record.items() if key != "record_id"}
                connection.execute(
                    research_records.insert().values(
                        record_id=record["record_id"],
                        listing_id=record["listing_id"],
                        authorization_dataset_id=record["lineage"]["source_policy_manifest_id"],
                        information_cutoff=record["information_cutoff"],
                        execution_purpose="production",
                        fixture_scenario="normal",
                        payload=payload,
                    )
                )
                connection.execute(
                    research_projection_status.insert().values(
                        record_id=record["record_id"],
                        core_projection_version=1,
                        evidence_projection_version=0,
                        stale=True,
                    )
                )
            connection.execute(
                work_attempts.insert().values(
                    work_id=work_id,
                    operation="production_eod",
                    status="succeeded",
                    execution_purpose="production",
                    trace_id=trace_id,
                    idempotency_key=idempotency_key,
                    attempt_count=1,
                )
            )
            connection.execute(
                health_assessments.insert().values(
                    assessment_id=health_assessment_id,
                    scope=f"production_eod:{publication['market']}",
                    status="degraded" if has_unavailable or slo_breached else "healthy",
                    reason_code=(
                        "production_t_plus_120_breached"
                        if slo_breached
                        else "production_partial_unavailable"
                        if has_unavailable
                        else "production_publication_complete"
                    ),
                    trace_id=trace_id,
                )
            )
            connection.execute(
                security_audit_events.insert().values(
                    event_id=audit_event_id,
                    action="production_forecast.publish",
                    outcome="allowed",
                    reason_code="production_publication_complete",
                    trace_id=trace_id,
                    authorization=None,
                )
            )
            for milestone in cast(list[dict[str, Any]], publication["milestones"]):
                event_kind = str(milestone["event_kind"])
                connection.execute(
                    production_operations_events.insert().values(
                        event_id=content_id(
                            "production_operation_event",
                            {
                                "forecast_batch_id": forecast_batch_id,
                                "event_kind": event_kind,
                            },
                        )[-36:],
                        forecast_batch_id=forecast_batch_id,
                        event_kind=event_kind,
                        occurred_at=milestone["observed_at"],
                        payload=milestone,
                    )
                )
            source_health_payload = {
                "event_kind": "source_health",
                "status": "degraded" if has_unavailable else "healthy",
                "reason_code": (
                    "production_partial_unavailable"
                    if has_unavailable
                    else "production_source_inputs_healthy"
                ),
            }
            self._insert_production_operation_event(
                connection,
                forecast_batch_id=forecast_batch_id,
                event_kind="source_health",
                occurred_at=str(publication["completed_at"]),
                payload=source_health_payload,
            )
            if slo_breached:
                incident_payload = {
                    "event_kind": "incident",
                    "status": "open",
                    "severity": "SEV3",
                    "reason_code": "production_t_plus_120_breached",
                }
                self._insert_production_operation_event(
                    connection,
                    forecast_batch_id=forecast_batch_id,
                    event_kind="incident",
                    occurred_at=str(publication["completed_at"]),
                    payload=incident_payload,
                )
                self._insert_production_operation_event(
                    connection,
                    forecast_batch_id=forecast_batch_id,
                    event_kind="notification",
                    occurred_at=str(publication["completed_at"]),
                    payload={
                        "event_kind": "notification",
                        "delivery_status": "pending",
                        "reason_code": "production_t_plus_120_breached",
                    },
                )
        return 1

    @staticmethod
    def _insert_production_operation_event(
        connection: Connection,
        *,
        forecast_batch_id: str,
        event_kind: str,
        occurred_at: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            production_operations_events.insert().values(
                event_id=content_id(
                    "production_operation_event",
                    {
                        "forecast_batch_id": forecast_batch_id,
                        "event_kind": event_kind,
                    },
                )[-36:],
                forecast_batch_id=forecast_batch_id,
                event_kind=event_kind,
                occurred_at=occurred_at,
                payload=payload,
            )
        )

    def list_production_operations(self, forecast_batch_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            rows = list(
                connection.execute(
                    select(
                        production_operations_events.c.event_kind,
                        production_operations_events.c.payload,
                    )
                    .where(production_operations_events.c.forecast_batch_id == forecast_batch_id)
                    .order_by(production_operations_events.c.sequence)
                ).mappings()
            )
        milestones = [
            deepcopy(cast(dict[str, Any], row["payload"]))
            for row in rows
            if str(row["event_kind"]).startswith("t_plus_")
        ]
        source_health = next(
            (
                deepcopy(cast(dict[str, Any], row["payload"]))
                for row in rows
                if row["event_kind"] == "source_health"
            ),
            None,
        )
        return {
            "forecast_batch_id": forecast_batch_id,
            "milestones": milestones,
            "source_health": source_health,
            "incidents": [
                deepcopy(cast(dict[str, Any], row["payload"]))
                for row in rows
                if row["event_kind"] == "incident"
            ],
            "notifications": [
                deepcopy(cast(dict[str, Any], row["payload"]))
                for row in rows
                if str(row["event_kind"]).startswith("notification")
            ],
        }

    def record_production_notification_delivery(
        self,
        *,
        forecast_batch_id: str,
        delivered_at: str,
        payload: dict[str, Any],
    ) -> None:
        event_kind = "notification_delivery"
        event_id = content_id(
            "production_operation_event",
            {"forecast_batch_id": forecast_batch_id, "event_kind": event_kind},
        )[-36:]
        audit_event_id = content_id(
            "production_notification_audit",
            {"forecast_batch_id": forecast_batch_id},
        )[-36:]
        with self.engine.begin() as connection:
            pending = connection.execute(
                select(production_operations_events.c.event_id).where(
                    production_operations_events.c.forecast_batch_id == forecast_batch_id,
                    production_operations_events.c.event_kind == "notification",
                )
            ).scalar_one_or_none()
            if pending is None:
                raise ImmutableStateConflict("production_notification_not_pending")
            existing = connection.execute(
                select(production_operations_events.c.payload).where(
                    production_operations_events.c.event_id == event_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing != payload:
                    raise ImmutableStateConflict("immutable_notification_delivery_conflict")
                return
            self._insert_production_operation_event(
                connection,
                forecast_batch_id=forecast_batch_id,
                event_kind=event_kind,
                occurred_at=delivered_at,
                payload=payload,
            )
            connection.execute(
                security_audit_events.insert().values(
                    event_id=audit_event_id,
                    action="production_notification.deliver",
                    outcome=("allowed" if payload["delivery_status"] == "delivered" else "denied"),
                    reason_code=payload["reason_code"],
                    trace_id=forecast_batch_id,
                    authorization=None,
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
                    authorization=None,
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

    def record_authorization_decision(
        self,
        *,
        authorization: dict[str, object],
        outcome: str,
        trace_id: str,
    ) -> None:
        event_id = authorization["evaluation_id"]
        if event_id is None:
            raise ValueError("authorization_evaluation_id_required")
        with self.engine.begin() as connection:
            self._insert_authorization_decision(
                connection,
                authorization=authorization,
                outcome=outcome,
                trace_id=trace_id,
            )

    def record_security_event(
        self,
        *,
        event_id: str,
        action: str,
        outcome: str,
        reason_code: str,
        trace_id: str,
        authorization: dict[str, object] | None = None,
    ) -> None:
        expected = {
            "event_id": event_id,
            "action": action,
            "outcome": outcome,
            "reason_code": reason_code,
            "trace_id": trace_id,
            "authorization": authorization,
        }
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(security_audit_events).where(
                        security_audit_events.c.event_id == event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                actual = {key: existing[key] for key in expected}
                if actual != expected:
                    raise ImmutableStateConflict("immutable_security_event_conflict")
                return
            connection.execute(security_audit_events.insert().values(**expected))

    def get_security_event(self, *, event_id: str) -> dict[str, object] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(security_audit_events).where(
                        security_audit_events.c.event_id == event_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return deepcopy(dict(row))

    def get_authorization_decision(self, *, evaluation_id: str) -> dict[str, object]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        security_audit_events.c.outcome,
                        security_audit_events.c.trace_id,
                        security_audit_events.c.authorization,
                    ).where(security_audit_events.c.event_id == evaluation_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None or not isinstance(row["authorization"], dict):
            raise KeyError(evaluation_id)
        authorization = deepcopy(cast(dict[str, object], row["authorization"]))
        authorization["outcome"] = row["outcome"]
        authorization["trace_id"] = row["trace_id"]
        return authorization

    def get_source_credential(self, *, provider_id: str) -> dict[str, object] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(source_credential_versions)
                    .where(source_credential_versions.c.provider_id == provider_id)
                    .order_by(source_credential_versions.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        result = dict(row)
        if result.get("expires_at") is None:
            result.pop("expires_at", None)
        if result.get("validation_evidence") is None:
            result.pop("validation_evidence", None)
        if result.get("revoked_at") is None:
            result.pop("revoked_at", None)
        return result

    def publish_source_credential(
        self,
        *,
        provider_id: str,
        secret_ref_id: str,
        readiness: str,
        reason_code: str,
        configured_at: str,
        expires_at: str | None,
        authorization: dict[str, object],
        trace_id: str,
    ) -> dict[str, object]:
        with self.engine.begin() as connection:
            latest_version = int(
                connection.execute(
                    select(func.coalesce(func.max(source_credential_versions.c.version), 0)).where(
                        source_credential_versions.c.provider_id == provider_id
                    )
                ).scalar_one()
            )
            current = (
                connection.execute(
                    select(source_credential_versions)
                    .where(source_credential_versions.c.provider_id == provider_id)
                    .order_by(source_credential_versions.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if current is not None and current["readiness"] != "revoked":
                raise ImmutableStateConflict("source_credential_already_configured")
            version = latest_version + 1
            self._insert_authorization_decision(
                connection,
                authorization=authorization,
                outcome="allowed",
                trace_id=trace_id,
            )
            connection.execute(
                source_credential_versions.insert().values(
                    provider_id=provider_id,
                    version=version,
                    secret_ref_id=secret_ref_id,
                    readiness=readiness,
                    reason_code=reason_code,
                    configured_at=configured_at,
                    expires_at=expires_at,
                    last_validated_at=None,
                    validation_evidence=None,
                    revoked_at=None,
                )
            )
        result: dict[str, object] = {
            "provider_id": provider_id,
            "readiness": readiness,
            "reason_code": reason_code,
            "secret_ref_id": secret_ref_id,
            "version": version,
            "configured_at": configured_at,
            "last_validated_at": None,
        }
        if expires_at is not None:
            result["expires_at"] = expires_at
        return result

    def rotate_source_credential(
        self,
        *,
        provider_id: str,
        secret_ref_id: str,
        readiness: str,
        reason_code: str,
        configured_at: str,
        expires_at: str | None,
        authorization: dict[str, object],
        trace_id: str,
    ) -> tuple[dict[str, object], str]:
        with self.engine.begin() as connection:
            current = (
                connection.execute(
                    select(source_credential_versions)
                    .where(source_credential_versions.c.provider_id == provider_id)
                    .order_by(source_credential_versions.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if current is None or current["readiness"] == "revoked":
                raise ImmutableStateConflict("source_credential_not_configured")
            version = int(current["version"]) + 1
            self._insert_authorization_decision(
                connection,
                authorization=authorization,
                outcome="allowed",
                trace_id=trace_id,
            )
            connection.execute(
                source_credential_versions.insert().values(
                    provider_id=provider_id,
                    version=version,
                    secret_ref_id=secret_ref_id,
                    readiness=readiness,
                    reason_code=reason_code,
                    configured_at=configured_at,
                    expires_at=expires_at,
                    last_validated_at=None,
                    validation_evidence=None,
                    revoked_at=None,
                )
            )
            connection.execute(
                source_secret_cleanup_queue.insert().values(
                    secret_ref_id=current["secret_ref_id"],
                    provider_id=provider_id,
                    queued_at=configured_at,
                    completed_at=None,
                )
            )
        outcome: dict[str, object] = {
            "provider_id": provider_id,
            "readiness": readiness,
            "reason_code": reason_code,
            "secret_ref_id": secret_ref_id,
            "version": version,
            "configured_at": configured_at,
            "last_validated_at": None,
        }
        if expires_at is not None:
            outcome["expires_at"] = expires_at
        return (
            outcome,
            str(current["secret_ref_id"]),
        )

    def revoke_source_credential(
        self,
        *,
        provider_id: str,
        revoked_at: str,
        authorization: dict[str, object],
        trace_id: str,
    ) -> dict[str, object]:
        with self.engine.begin() as connection:
            current = (
                connection.execute(
                    select(source_credential_versions)
                    .where(source_credential_versions.c.provider_id == provider_id)
                    .order_by(source_credential_versions.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if current is None or current["readiness"] == "revoked":
                raise ImmutableStateConflict("source_credential_not_configured")
            version = int(current["version"]) + 1
            self._insert_authorization_decision(
                connection,
                authorization=authorization,
                outcome="allowed",
                trace_id=trace_id,
            )
            connection.execute(
                source_credential_versions.insert().values(
                    provider_id=provider_id,
                    version=version,
                    secret_ref_id=current["secret_ref_id"],
                    readiness="revoked",
                    reason_code="source_credential_revoked",
                    configured_at=current["configured_at"],
                    expires_at=current["expires_at"],
                    last_validated_at=current["last_validated_at"],
                    validation_evidence=current["validation_evidence"],
                    revoked_at=revoked_at,
                )
            )
            connection.execute(
                source_secret_cleanup_queue.insert().values(
                    secret_ref_id=current["secret_ref_id"],
                    provider_id=provider_id,
                    queued_at=revoked_at,
                    completed_at=None,
                )
            )
        result: dict[str, object] = {
            "provider_id": provider_id,
            "readiness": "revoked",
            "reason_code": "source_credential_revoked",
            "secret_ref_id": current["secret_ref_id"],
            "version": version,
            "configured_at": current["configured_at"],
            "last_validated_at": current["last_validated_at"],
            "revoked_at": revoked_at,
        }
        if current["expires_at"] is not None:
            result["expires_at"] = current["expires_at"]
        return result

    def record_source_credential_validation(
        self,
        *,
        provider_id: str,
        readiness: str,
        reason_code: str,
        validated_at: str,
        expected_version: int,
        expected_secret_ref_id: str,
        validation_evidence: dict[str, object],
        source_contract_assessment: dict[str, object] | None,
        authorization: dict[str, object],
        trace_id: str,
    ) -> dict[str, object]:
        with self.engine.begin() as connection:
            current = (
                connection.execute(
                    select(source_credential_versions)
                    .where(source_credential_versions.c.provider_id == provider_id)
                    .order_by(source_credential_versions.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if current is None or current["readiness"] == "revoked":
                raise ImmutableStateConflict("source_credential_not_configured")
            if (
                int(current["version"]) != expected_version
                or current["secret_ref_id"] != expected_secret_ref_id
            ):
                raise ImmutableStateConflict("source_credential_validation_stale")
            version = int(current["version"]) + 1
            self._insert_authorization_decision(
                connection,
                authorization=authorization,
                outcome="allowed",
                trace_id=trace_id,
            )
            connection.execute(
                source_credential_versions.insert().values(
                    provider_id=provider_id,
                    version=version,
                    secret_ref_id=current["secret_ref_id"],
                    readiness=readiness,
                    reason_code=reason_code,
                    configured_at=current["configured_at"],
                    expires_at=current["expires_at"],
                    last_validated_at=validated_at,
                    validation_evidence=validation_evidence,
                    revoked_at=None,
                )
            )
            source_contract_assessment_artifact_id: str | None = None
            if source_contract_assessment is not None:
                assessment_payload: dict[str, object] = {
                    "provider_id": provider_id,
                    "assessed_at": validated_at,
                    "credential_version": version,
                    "assessment": source_contract_assessment,
                }
                source_contract_assessment_artifact_id = _canonical_artifact_id(
                    "source_contract_assessment",
                    assessment_payload,
                )
                connection.execute(
                    canonical_artifacts.insert().values(
                        artifact_id=source_contract_assessment_artifact_id,
                        artifact_kind="source_contract_assessment",
                        execution_purpose="source_administration",
                        payload=assessment_payload,
                    )
                )
                connection.execute(
                    trace_artifact_refs.insert().values(
                        trace_id=trace_id,
                        artifact_id=source_contract_assessment_artifact_id,
                    )
                )
        credential = {
            "provider_id": provider_id,
            "readiness": readiness,
            "reason_code": reason_code,
            "secret_ref_id": current["secret_ref_id"],
            "version": version,
            "configured_at": current["configured_at"],
            "last_validated_at": validated_at,
        }
        if current["expires_at"] is not None:
            credential["expires_at"] = current["expires_at"]
        if validation_evidence:
            credential["validation_evidence"] = validation_evidence
        return {
            "credential": credential,
            "source_contract_assessment": source_contract_assessment,
            "source_contract_assessment_artifact_id": (source_contract_assessment_artifact_id),
        }

    def list_pending_source_secret_cleanup(self, *, provider_id: str) -> list[str]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(source_secret_cleanup_queue.c.secret_ref_id)
                .where(
                    source_secret_cleanup_queue.c.provider_id == provider_id,
                    source_secret_cleanup_queue.c.completed_at.is_(None),
                )
                .order_by(source_secret_cleanup_queue.c.queued_at)
            ).scalars()
            return [str(secret_ref_id) for secret_ref_id in rows]

    def queue_source_secret_cleanup(
        self,
        *,
        secret_ref_id: str,
        provider_id: str,
        queued_at: str,
    ) -> None:
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(source_secret_cleanup_queue.c.secret_ref_id).where(
                    source_secret_cleanup_queue.c.secret_ref_id == secret_ref_id
                )
            ).scalar_one_or_none()
            if existing is None:
                connection.execute(
                    source_secret_cleanup_queue.insert().values(
                        secret_ref_id=secret_ref_id,
                        provider_id=provider_id,
                        queued_at=queued_at,
                        completed_at=None,
                    )
                )

    def complete_source_secret_cleanup(
        self,
        *,
        secret_ref_id: str,
        completed_at: str,
    ) -> None:
        with self.engine.begin() as connection:
            result = connection.execute(
                source_secret_cleanup_queue.update()
                .where(
                    source_secret_cleanup_queue.c.secret_ref_id == secret_ref_id,
                    source_secret_cleanup_queue.c.completed_at.is_(None),
                )
                .values(completed_at=completed_at)
            )
            if result.rowcount != 1:
                raise ImmutableStateConflict("source_secret_cleanup_not_pending")

    def get_price_research_eligibility(self, *, listing_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(price_research_eligibility.c.payload)
                .where(price_research_eligibility.c.listing_id == listing_id)
                .order_by(price_research_eligibility.c.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
        if payload is None:
            raise KeyError(listing_id)
        return deepcopy(cast(dict[str, Any], payload))

    def get_price_source_checkpoint(self, *, source_id: str, source_mode: str) -> str | None:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(price_research_eligibility.c.payload)
                .where(
                    price_research_eligibility.c.source_id == source_id,
                    price_research_eligibility.c.source_mode == source_mode,
                )
                .order_by(price_research_eligibility.c.sequence.desc())
            ).scalars()
            for payload in payloads:
                checkpoint = cast(dict[str, Any], payload).get("checkpoint")
                if isinstance(checkpoint, str):
                    return checkpoint
        return None

    def list_price_research_eligibility(
        self,
        *,
        listing_id: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(
            price_research_eligibility.c.listing_id,
            price_research_eligibility.c.source_id,
            price_research_eligibility.c.source_mode,
            price_research_eligibility.c.payload,
        ).order_by(price_research_eligibility.c.sequence.desc())
        if listing_id is not None:
            statement = statement.where(price_research_eligibility.c.listing_id == listing_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings()
            latest: dict[tuple[str, str, str], dict[str, Any]] = {}
            for row in rows:
                key = (row["listing_id"], row["source_id"], row["source_mode"])
                if key not in latest:
                    latest[key] = deepcopy(cast(dict[str, Any], row["payload"]))
        return sorted(
            latest.values(),
            key=lambda item: (
                str(item["source_mode"]) != "current",
                str(item["source_id"]),
            ),
        )

    def publish_price_research_evaluation(
        self,
        *,
        trace_id: str,
        execution_purpose: str,
        artifacts: list[dict[str, Any]],
        authorization: dict[str, object],
        authorization_outcome: str,
        eligibility_records: list[dict[str, object]],
    ) -> None:
        with self.engine.begin() as connection:
            self._insert_authorization_decision(
                connection,
                authorization=authorization,
                outcome=authorization_outcome,
                trace_id=trace_id,
            )
            for artifact in artifacts:
                existing = (
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
                if existing is None:
                    connection.execute(
                        canonical_artifacts.insert().values(
                            artifact_id=artifact["artifact_id"],
                            artifact_kind=artifact["artifact_kind"],
                            execution_purpose=execution_purpose,
                            payload=artifact["payload"],
                        )
                    )
                elif (
                    existing["artifact_kind"] != artifact["artifact_kind"]
                    or existing["execution_purpose"] != execution_purpose
                    or existing["payload"] != artifact["payload"]
                ):
                    raise ImmutableStateConflict("immutable_artifact_conflict")
                reference_exists = connection.execute(
                    select(trace_artifact_refs.c.sequence).where(
                        trace_artifact_refs.c.trace_id == trace_id,
                        trace_artifact_refs.c.artifact_id == artifact["artifact_id"],
                    )
                ).scalar_one_or_none()
                if reference_exists is None:
                    connection.execute(
                        trace_artifact_refs.insert().values(
                            trace_id=trace_id,
                            artifact_id=artifact["artifact_id"],
                        )
                    )
            for record in eligibility_records:
                existing_payload = connection.execute(
                    select(price_research_eligibility.c.payload).where(
                        price_research_eligibility.c.eligibility_id == record["eligibility_id"],
                        price_research_eligibility.c.listing_id == record["listing_id"],
                    )
                ).scalar_one_or_none()
                if existing_payload is None:
                    connection.execute(price_research_eligibility.insert().values(**record))
                elif existing_payload != record["payload"]:
                    raise ImmutableStateConflict("immutable_price_eligibility_conflict")

    def get_canonical_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        canonical_artifacts.c.artifact_kind,
                        canonical_artifacts.c.execution_purpose,
                        canonical_artifacts.c.payload,
                    ).where(canonical_artifacts.c.artifact_id == artifact_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise KeyError(artifact_id)
        return deepcopy(dict(row))

    def find_latest_price_qualification_gate(
        self,
        *,
        manifest_id: str,
        source_path_id: str,
    ) -> tuple[str, dict[str, object]] | None:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    canonical_artifacts.c.artifact_id,
                    canonical_artifacts.c.payload,
                )
                .select_from(
                    trace_artifact_refs.join(
                        canonical_artifacts,
                        trace_artifact_refs.c.artifact_id == canonical_artifacts.c.artifact_id,
                    )
                )
                .where(
                    canonical_artifacts.c.artifact_kind == "taiwan_price_qualification_gate",
                    canonical_artifacts.c.execution_purpose == "governance",
                )
                .order_by(trace_artifact_refs.c.sequence.desc())
            ).mappings()
            for row in rows:
                payload = row["payload"]
                if (
                    isinstance(payload, dict)
                    and payload.get("manifest_id") == manifest_id
                    and payload.get("source_path_id") == source_path_id
                ):
                    return str(row["artifact_id"]), deepcopy(cast(dict[str, object], payload))
        return None

    def _publish_authorized_governance_artifact(
        self,
        *,
        artifact_kind: str,
        payload: dict[str, object],
        trace_id: str,
        authorizations: list[dict[str, object]],
    ) -> str:
        if artifact_kind not in {
            "open_data_source_basis_evidence",
            "zero_fee_source_basis_evidence",
            "historical_availability_claim",
            "taiwan_price_qualification_gate",
        }:
            raise ValueError("unsupported_qualification_artifact_kind")
        if not authorizations:
            raise ValueError("qualification_authorization_required")
        for authorization in authorizations:
            if (
                authorization.get("action") != "price_qualification.govern"
                or authorization.get("reason_code") != "authorized"
            ):
                raise ValueError("qualification_authorization_invalid")
        return self._publish_trace_artifact(
            artifact_kind=artifact_kind,
            execution_purpose="governance",
            payload=payload,
            trace_id=trace_id,
            authorization_outcomes=[(authorization, "allowed") for authorization in authorizations],
        )

    def _publish_governance_rejection(
        self,
        *,
        payload: dict[str, object],
        trace_id: str,
        authorizations: list[dict[str, object]],
    ) -> str:
        if (
            set(payload) != {"operation", "reason_code"}
            or payload.get("operation")
            not in {
                "register_open_data_source_basis_evidence",
                "register_zero_fee_source_basis_evidence",
                "register_historical_availability_claim",
                "register_formal_qualification_gate",
                "attest_historical_evidence",
                "qualify_historical_evidence",
            }
            or not isinstance(payload.get("reason_code"), str)
            or not payload["reason_code"]
            or not authorizations
            or any(
                authorization.get("action")
                != (
                    "market_data.collect"
                    if payload.get("operation") == "attest_historical_evidence"
                    else "price_qualification.govern"
                )
                for authorization in authorizations
            )
        ):
            raise ValueError("qualification_governance_rejection_invalid")
        return self._publish_trace_artifact(
            artifact_kind="qualification_governance_rejection",
            execution_purpose="governance",
            payload=payload,
            trace_id=trace_id,
            authorization_outcomes=[
                (
                    authorization,
                    "allowed" if authorization.get("reason_code") == "authorized" else "denied",
                )
                for authorization in authorizations
            ],
        )

    def publish_current_source_rights_resolution(
        self,
        *,
        payload: dict[str, object],
        trace_id: str,
    ) -> str:
        required = {
            "evaluation_id",
            "decision_id",
            "outcome",
            "reason_code",
            "subject_principal_id",
            "runtime_environment",
            "subject_attributes_evidence_id",
            "subject_attributes_valid_until",
            "subject_data_protection_classes",
            "subject_principal_classification",
            "dataset_id",
            "prior_evaluation_id",
            "prior_decision_id",
            "prior_trace_id",
            "prior_correlation_id",
            "evaluated_at",
            "valid_until",
            "grant_version_id",
            "source_policy_version_id",
            "source_entitlement_version_id",
        }
        if set(payload) != required or payload.get("outcome") not in {"allowed", "denied"}:
            raise ValueError("current_source_rights_resolution_invalid")
        return self._publish_trace_artifact(
            artifact_kind="current_source_rights_resolution",
            execution_purpose="price_research",
            payload=payload,
            trace_id=trace_id,
            authorization_outcomes=[],
        )

    def _publish_trace_artifact(
        self,
        *,
        artifact_kind: str,
        execution_purpose: str,
        payload: dict[str, object],
        trace_id: str,
        authorization_outcomes: list[tuple[dict[str, object], str]],
    ) -> str:
        artifact_id = _canonical_artifact_id(artifact_kind, payload)
        with self.engine.begin() as connection:
            for authorization, outcome in authorization_outcomes:
                self._insert_authorization_decision(
                    connection,
                    authorization=authorization,
                    outcome=outcome,
                    trace_id=trace_id,
                )
            existing = (
                connection.execute(
                    select(
                        canonical_artifacts.c.artifact_kind,
                        canonical_artifacts.c.execution_purpose,
                        canonical_artifacts.c.payload,
                    ).where(canonical_artifacts.c.artifact_id == artifact_id)
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                connection.execute(
                    canonical_artifacts.insert().values(
                        artifact_id=artifact_id,
                        artifact_kind=artifact_kind,
                        execution_purpose=execution_purpose,
                        payload=payload,
                    )
                )
            elif (
                existing["artifact_kind"] != artifact_kind
                or existing["execution_purpose"] != execution_purpose
                or existing["payload"] != payload
            ):
                raise ImmutableStateConflict("immutable_artifact_conflict")
            reference_exists = connection.execute(
                select(trace_artifact_refs.c.sequence).where(
                    trace_artifact_refs.c.trace_id == trace_id,
                    trace_artifact_refs.c.artifact_id == artifact_id,
                )
            ).scalar_one_or_none()
            if reference_exists is None:
                connection.execute(
                    trace_artifact_refs.insert().values(
                        trace_id=trace_id,
                        artifact_id=artifact_id,
                    )
                )
        return artifact_id

    def get_verified_governance_artifact(
        self,
        *,
        artifact_id: str,
        artifact_kind: str,
    ) -> dict[str, object]:
        artifact = self.get_canonical_artifact(artifact_id)
        payload = artifact["payload"]
        if (
            artifact["artifact_kind"] != artifact_kind
            or artifact["execution_purpose"] != "governance"
            or not isinstance(payload, dict)
            or _canonical_artifact_id(artifact_kind, payload) != artifact_id
        ):
            raise KeyError(artifact_id)
        return deepcopy(cast(dict[str, object], payload))

    def _publish_historical_evidence_artifact(
        self,
        *,
        artifact_kind: str,
        payload: dict[str, object],
        trace_id: str,
        authorizations: list[dict[str, object]],
    ) -> str:
        if artifact_kind not in {
            "historical_availability_claim",
            "historical_evidence_verification",
            "historical_qualification_report",
            "historical_claim_impact",
            "historical_reconstruction_dataset",
            "historical_adjustment_version",
            "historical_mature_labels",
            "historical_feature_snapshot",
            "historical_fold_manifest",
        }:
            raise ValueError("unsupported_historical_evidence_artifact_kind")
        if not authorizations or any(
            authorization.get("action") != "price_qualification.govern"
            or authorization.get("reason_code") != "authorized"
            or authorization.get("dataset_id") != payload.get("source_id")
            for authorization in authorizations
        ):
            raise ValueError("historical_evidence_authorization_invalid")
        execution_purpose = (
            "governance"
            if artifact_kind
            in {
                "historical_availability_claim",
                "historical_evidence_verification",
                "historical_qualification_report",
                "historical_claim_impact",
            }
            else "historical_reconstruction"
        )
        return self._publish_trace_artifact(
            artifact_kind=artifact_kind,
            execution_purpose=execution_purpose,
            payload=payload,
            trace_id=trace_id,
            authorization_outcomes=[(authorization, "allowed") for authorization in authorizations],
        )

    def _publish_historical_evidence_attestation(
        self,
        *,
        payload: dict[str, object],
        trace_id: str,
        authorizations: list[dict[str, object]],
    ) -> str:
        required_fields = {
            "attestation_schema_version",
            "listing_id",
            "market",
            "source_id",
            "evidence_level",
            "evidence_object_id",
            "evidence_checksum",
            "calendar_object_id",
            "calendar_checksum",
            "reference_object_id",
            "reference_checksum",
            "source_policy_version_id",
            "source_basis_id",
            "source_access_basis",
            "collection_authorization_decision_ids",
            "collector_principal_ids",
            "observation_receipt_id",
            "distribution_bindings",
            "first_observed_at",
            "attested_at",
        }
        if (
            set(payload) != required_fields
            or payload.get("attestation_schema_version") != "historical-evidence-attestation/v1"
            or not authorizations
            or any(
                authorization.get("action") != "market_data.collect"
                or authorization.get("reason_code") != "authorized"
                or authorization.get("dataset_id") != payload.get("source_id")
                or payload.get("source_policy_version_id")
                != authorization.get("source_policy_version_id")
                or not isinstance(authorization.get("decision_id"), str)
                or not isinstance(authorization.get("principal_id"), str)
                for authorization in authorizations
            )
            or payload.get("collection_authorization_decision_ids")
            != sorted(str(authorization.get("decision_id")) for authorization in authorizations)
            or payload.get("collector_principal_ids")
            != sorted({str(authorization.get("principal_id")) for authorization in authorizations})
            or not isinstance(payload.get("observation_receipt_id"), str)
            or payload.get("distribution_bindings")
            != sorted(
                [
                    {
                        "distribution_id": str(authorization["distribution_id"]),
                        "distribution_url": str(authorization["distribution_url"]),
                    }
                    for authorization in authorizations
                    if isinstance(authorization.get("distribution_id"), str)
                    and isinstance(authorization.get("distribution_url"), str)
                ],
                key=lambda binding: (
                    binding["distribution_id"],
                    binding["distribution_url"],
                ),
            )
        ):
            raise ValueError("historical_evidence_attestation_invalid")
        return self._publish_trace_artifact(
            artifact_kind="historical_evidence_attestation",
            execution_purpose="governance",
            payload=payload,
            trace_id=trace_id,
            authorization_outcomes=[(authorization, "allowed") for authorization in authorizations],
        )

    def _publish_historical_observation_receipt(
        self,
        *,
        receipt: SourceRetrievalReceipt,
        trace_id: str,
        authorization: dict[str, object],
    ) -> str:
        if (
            authorization.get("action") != "market_data.collect"
            or authorization.get("reason_code") != "authorized"
            or authorization.get("dataset_id") != receipt.source_id
            or authorization.get("distribution_id") != receipt.distribution_id
            or authorization.get("distribution_url") != receipt.distribution_url
            or authorization.get("evaluated_at") != receipt.acquired_at_text
        ):
            raise ValueError("historical_observation_receipt_invalid")
        return self._publish_trace_artifact(
            artifact_kind="source_retrieval_receipt",
            execution_purpose="price_research",
            payload=receipt.to_payload(),
            trace_id=trace_id,
            authorization_outcomes=[(authorization, "allowed")],
        )

    def find_first_source_retrieval_receipt(
        self,
        *,
        object_id: str,
        source_id: str,
        distribution_id: str,
        distribution_url: str,
    ) -> tuple[str, SourceRetrievalReceipt] | None:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    canonical_artifacts.c.artifact_id,
                    canonical_artifacts.c.payload,
                )
                .select_from(
                    trace_artifact_refs.join(
                        canonical_artifacts,
                        trace_artifact_refs.c.artifact_id == canonical_artifacts.c.artifact_id,
                    )
                )
                .where(
                    canonical_artifacts.c.artifact_kind == "source_retrieval_receipt",
                    canonical_artifacts.c.execution_purpose == "price_research",
                )
                .order_by(trace_artifact_refs.c.sequence.asc())
            ).mappings()
            for row in rows:
                payload = row["payload"]
                if not isinstance(payload, dict):
                    raise ValueError("source_retrieval_receipt_invalid")
                receipt = SourceRetrievalReceipt.from_payload(payload)
                if (
                    receipt.object_id == object_id
                    and receipt.source_id == source_id
                    and receipt.distribution_id == distribution_id
                    and receipt.distribution_url == distribution_url
                ):
                    return str(row["artifact_id"]), receipt
        return None

    def _publish_historical_policy_blocked(
        self,
        *,
        payload: dict[str, object],
        trace_id: str,
        authorizations: list[dict[str, object]],
    ) -> str:
        if (
            payload.get("qualification_report_schema_version")
            != "historical-qualification-report/v1"
            or payload.get("status") != "policy_blocked"
            or not isinstance(payload.get("listing_id"), str)
            or not isinstance(payload.get("market"), str)
            or not isinstance(payload.get("source_id"), str)
            or not isinstance(payload.get("reason_code"), str)
            or not authorizations
            or not any(
                authorization.get("reason_code") != "authorized" for authorization in authorizations
            )
            or any(
                authorization.get("action") != "price_qualification.govern"
                or authorization.get("dataset_id") != payload.get("source_id")
                for authorization in authorizations
            )
        ):
            raise ValueError("historical_policy_blocked_report_invalid")
        return self._publish_trace_artifact(
            artifact_kind="historical_qualification_report",
            execution_purpose="governance",
            payload=payload,
            trace_id=trace_id,
            authorization_outcomes=[
                (
                    authorization,
                    "allowed" if authorization.get("reason_code") == "authorized" else "denied",
                )
                for authorization in authorizations
            ],
        )

    def list_historical_claim_impacts(self, *, claim_id: str) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(canonical_artifacts.c.payload)
                .select_from(
                    trace_artifact_refs.join(
                        canonical_artifacts,
                        trace_artifact_refs.c.artifact_id == canonical_artifacts.c.artifact_id,
                    )
                )
                .where(canonical_artifacts.c.artifact_kind == "historical_claim_impact")
                .order_by(trace_artifact_refs.c.sequence)
            ).scalars()
            return [
                deepcopy(cast(dict[str, object], payload))
                for payload in payloads
                if isinstance(payload, dict) and payload.get("prior_claim_id") == claim_id
            ]

    def list_historical_qualification_reports(
        self,
        *,
        listing_id: str | None = None,
    ) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    canonical_artifacts.c.artifact_id,
                    canonical_artifacts.c.payload,
                )
                .select_from(
                    trace_artifact_refs.join(
                        canonical_artifacts,
                        trace_artifact_refs.c.artifact_id == canonical_artifacts.c.artifact_id,
                    )
                )
                .where(canonical_artifacts.c.artifact_kind == "historical_qualification_report")
                .order_by(trace_artifact_refs.c.sequence.desc())
            ).mappings()
            reports: list[dict[str, object]] = []
            for row in rows:
                payload = row["payload"]
                if not isinstance(payload, dict):
                    continue
                if listing_id is not None and payload.get("listing_id") != listing_id:
                    continue
                reports.append(
                    {
                        "qualification_report_id": str(row["artifact_id"]),
                        **deepcopy(cast(dict[str, object], payload)),
                    }
                )
            return reports

    def has_canonical_artifact(self, artifact_id: str) -> bool:
        with self.engine.connect() as connection:
            return (
                connection.execute(
                    select(canonical_artifacts.c.artifact_id).where(
                        canonical_artifacts.c.artifact_id == artifact_id
                    )
                ).scalar_one_or_none()
                is not None
            )

    def _insert_authorization_decision(
        self,
        connection: Connection,
        *,
        authorization: dict[str, object],
        outcome: str,
        trace_id: str,
    ) -> None:
        event_id = authorization["evaluation_id"]
        if not isinstance(event_id, str):
            raise ValueError("authorization_evaluation_id_required")
        expected = {
            "event_id": event_id,
            "action": authorization["action"],
            "outcome": outcome,
            "reason_code": authorization["reason_code"],
            "trace_id": trace_id,
            "authorization": authorization,
        }
        existing = (
            connection.execute(
                select(security_audit_events).where(security_audit_events.c.event_id == event_id)
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            if any(existing[key] != value for key, value in expected.items()):
                raise ImmutableStateConflict("immutable_authorization_evaluation_conflict")
            return
        connection.execute(security_audit_events.insert().values(**expected))

    def get_listing_research(
        self,
        *,
        listing_id: str,
        information_cutoff: str,
        execution_purpose: str = "fixture",
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
                        research_records.c.execution_purpose == execution_purpose,
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

    def list_listing_research_history(
        self,
        *,
        listing_id: str,
        execution_purpose: str,
    ) -> list[dict[str, Any]]:
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
                .where(
                    research_records.c.listing_id == listing_id,
                    research_records.c.execution_purpose == execution_purpose,
                    research_records.c.fixture_scenario == "normal",
                )
                .order_by(research_records.c.information_cutoff.desc())
            ).mappings()
            history: list[dict[str, Any]] = []
            for row in rows:
                record = cast(dict[str, Any], deepcopy(row["payload"]))
                record["projection"] = {
                    "core_projection_version": row["core_projection_version"],
                    "evidence_projection_version": row["evidence_projection_version"],
                    "stale": row["stale"],
                }
                history.append(record)
            return history

    def get_outbox_event(self, event_id: str) -> dict[str, Any]:
        return self._outbox.get_event(event_id)

    def list_prediction_records(self, *, trace_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            fixture_payloads = connection.execute(
                select(fixture_prediction_results.c.payload)
                .where(fixture_prediction_results.c.trace_id == trace_id)
                .order_by(fixture_prediction_results.c.horizon_sessions)
            ).scalars()
            production_payloads = connection.execute(
                select(production_prediction_records.c.payload)
                .where(production_prediction_records.c.trace_id == trace_id)
                .order_by(
                    production_prediction_records.c.listing_id,
                    production_prediction_records.c.horizon_sessions,
                )
            ).scalars()
            return [
                deepcopy(payload)
                for payload in (*tuple(fixture_payloads), *tuple(production_payloads))
            ]

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

    def list_audit_events(self, *, trace_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    security_audit_events.c.action,
                    security_audit_events.c.outcome,
                    security_audit_events.c.reason_code,
                    security_audit_events.c.trace_id,
                    security_audit_events.c.authorization,
                )
                .where(security_audit_events.c.trace_id == trace_id)
                .order_by(security_audit_events.c.sequence)
            ).mappings()
            events: list[dict[str, Any]] = []
            for row in rows:
                event = {
                    "action": row["action"],
                    "outcome": row["outcome"],
                    "reason_code": row["reason_code"],
                    "trace_id": row["trace_id"],
                }
                if row["authorization"] is not None:
                    event.update(row["authorization"])
                events.append(event)
            return events

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
            work = (
                connection.execute(
                    select(
                        work_attempts.c.work_id,
                        work_attempts.c.operation,
                        work_attempts.c.status,
                        work_attempts.c.execution_purpose,
                    )
                    .where(work_attempts.c.trace_id == trace_id)
                    .order_by(work_attempts.c.work_id)
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            health = (
                connection.execute(
                    select(
                        health_assessments.c.scope,
                        health_assessments.c.status,
                        health_assessments.c.reason_code,
                    )
                    .where(health_assessments.c.trace_id == trace_id)
                    .order_by(health_assessments.c.sequence.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
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
            "artifact_payloads": {
                artifact["artifact_id"]: deepcopy(artifact["payload"]) for artifact in artifacts
            },
            "lineage_ids": {
                lineage_kinds[artifact["artifact_kind"]]: artifact["artifact_id"]
                for artifact in artifacts
                if artifact["artifact_kind"] in lineage_kinds
            },
            "fixture_prediction_result_count": len(fixture_count),
            "production_prediction_record_count": len(production_count),
            "audit_events": [dict(event) for event in audit_events],
            "work": dict(work) if work is not None else None,
            "health": dict(health) if health is not None else None,
        }
