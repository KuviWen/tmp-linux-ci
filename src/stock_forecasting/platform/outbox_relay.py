from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
    or_,
    select,
)
from sqlalchemy.engine import Engine

from stock_forecasting.outbox import (
    EventCompatibility,
    OutOfOrderEvent,
    RelayClock,
    RelayFault,
    RelayLeaseLost,
    RelayOutcome,
)
from stock_forecasting.platform.schema import (
    metadata,
    security_audit_events,
    work_attempts,
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
    Column("claimed_by", String(64), nullable=True),
    Column("lease_expires_at", String(32), nullable=True),
    Column("fencing_token", Integer, nullable=False),
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
    Column("worker_id", String(64), nullable=False),
    Column("fencing_token", Integer, nullable=False),
    Column("lease_expires_at", String(32), nullable=False),
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
    Column("impact_scope", String(160), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("owner", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason_code", String(96), nullable=False),
    Column("occurrence_count", Integer, nullable=False),
    Column("trace_id", String(128), nullable=False),
)


class OutboxRelay:
    """Owns outbox claiming, compatibility, delivery, recovery, and evidence."""

    _lease_duration = timedelta(seconds=2)

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

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

    def _record_incident(self, connection: Any, *, event: Any, reason_code: str) -> None:
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
                    impact_scope=(f"listing:{aggregate_id}:research_and_operations_projection"),
                    severity="SEV3",
                    owner="operations_control",
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
                occurrence_count=int(existing["occurrence_count"]) + 1,
            )
        )

    @staticmethod
    def _release_claim(
        connection: Any,
        *,
        event_id: str,
        worker_id: str,
        fencing_token: int,
    ) -> None:
        connection.execute(
            outbox_dispatch.update()
            .where(
                outbox_dispatch.c.event_id == event_id,
                outbox_dispatch.c.claimed_by == worker_id,
                outbox_dispatch.c.fencing_token == fencing_token,
            )
            .values(claimed_by=None, lease_expires_at=None)
        )

    @staticmethod
    def _assert_current_claim(
        connection: Any,
        *,
        event_id: str,
        worker_id: str,
        fencing_token: int,
        clock: RelayClock,
    ) -> None:
        claim = (
            connection.execute(
                select(
                    outbox_dispatch.c.claimed_by,
                    outbox_dispatch.c.fencing_token,
                    outbox_dispatch.c.lease_expires_at,
                ).where(outbox_dispatch.c.event_id == event_id)
            )
            .mappings()
            .one()
        )
        if (
            claim["claimed_by"] != worker_id
            or int(claim["fencing_token"]) != fencing_token
            or str(claim["lease_expires_at"]) <= clock.now().isoformat()
        ):
            raise RelayLeaseLost("expired_fencing_token")

    def _recover_abandoned(self, *, event: Any, fencing_token: int) -> None:
        event_id = str(event["event_id"])
        with self._engine.begin() as connection:
            abandoned = list(
                connection.execute(
                    select(
                        outbox_delivery_attempts.c.attempt_id,
                        outbox_delivery_attempts.c.attempt_number,
                        outbox_delivery_attempts.c.work_id,
                    ).where(
                        outbox_delivery_attempts.c.event_id == event_id,
                        outbox_delivery_attempts.c.status == "running",
                        outbox_delivery_attempts.c.fencing_token < fencing_token,
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
                self._record_incident(
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

    def get_event(self, event_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
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

    def _load_event(self, event_id: str | None) -> Any:
        with self._engine.connect() as connection:
            query = (
                select(
                    outbox_events.c.event_id,
                    outbox_events.c.event_type,
                    outbox_events.c.schema_version,
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
                query = query.where(outbox_dispatch.c.status == "pending")
            else:
                query = query.where(outbox_events.c.event_id == event_id)
            return connection.execute(query.limit(1)).mappings().one_or_none()

    def _start_attempt(
        self,
        *,
        event: Any,
        worker_id: str,
        fencing_token: int,
        lease_expires_at: str,
    ) -> tuple[str, str, int]:
        event_id = str(event["event_id"])
        with self._engine.begin() as connection:
            attempt_number = (
                int(
                    connection.execute(
                        select(func.count(outbox_delivery_attempts.c.sequence)).where(
                            outbox_delivery_attempts.c.event_id == event_id
                        )
                    ).scalar_one()
                )
                + 1
            )
            attempt_id = self._relay_id(event_id, "attempt", attempt_number)
            work_id = self._relay_id(event_id, "work", attempt_number)
            connection.execute(
                outbox_delivery_attempts.insert().values(
                    attempt_id=attempt_id,
                    event_id=event_id,
                    attempt_number=attempt_number,
                    work_id=work_id,
                    status="running",
                    reason_code="delivery_started",
                    trace_id=event["trace_id"],
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                    lease_expires_at=lease_expires_at,
                )
            )
            connection.execute(
                work_attempts.insert().values(
                    work_id=work_id,
                    operation="outbox_relay",
                    status="running",
                    execution_purpose="fixture",
                    trace_id=event["trace_id"],
                    idempotency_key=f"outbox:{event_id}:{attempt_number}",
                    attempt_count=attempt_number,
                )
            )
        return attempt_id, work_id, attempt_number

    def _finish_failure(
        self,
        *,
        event: Any,
        attempt_id: str,
        work_id: str,
        attempt_number: int,
        worker_id: str,
        fencing_token: int,
        attempt_status: str,
        work_status: str,
        reason_code: str,
        audit_kind: str | None,
    ) -> None:
        event_id = str(event["event_id"])
        with self._engine.begin() as connection:
            connection.execute(
                outbox_delivery_attempts.update()
                .where(outbox_delivery_attempts.c.attempt_id == attempt_id)
                .values(status=attempt_status, reason_code=reason_code)
            )
            connection.execute(
                work_attempts.update()
                .where(work_attempts.c.work_id == work_id)
                .values(status=work_status)
            )
            self._release_claim(
                connection,
                event_id=event_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
            )
            self._record_incident(connection, event=event, reason_code=reason_code)
            if audit_kind is not None:
                connection.execute(
                    security_audit_events.insert().values(
                        event_id=self._relay_id(event_id, audit_kind, attempt_number),
                        action="outbox_delivery",
                        outcome="denied",
                        reason_code=reason_code,
                        trace_id=event["trace_id"],
                    )
                )

    def relay(
        self,
        *,
        event_id: str | None,
        fault: RelayFault,
        compatibility: EventCompatibility,
        clock: RelayClock,
        worker_id: str,
    ) -> RelayOutcome:
        event = self._load_event(event_id)
        if event is None:
            if event_id is not None:
                raise KeyError(event_id)
            return RelayOutcome(status="empty", event_id=None, aggregate_version=None)
        resolved_event_id = str(event["event_id"])
        aggregate_version = int(event["aggregate_version"])
        if event["delivery_status"] == "delivered":
            return RelayOutcome("already_delivered", resolved_event_id, aggregate_version)
        if event["delivery_status"] == "isolated":
            return RelayOutcome("isolated", resolved_event_id, aggregate_version)

        claim_started_at = clock.now()
        lease_expires_at = claim_started_at + self._lease_duration
        with self._engine.begin() as connection:
            fencing_token = connection.execute(
                outbox_dispatch.update()
                .where(
                    outbox_dispatch.c.event_id == resolved_event_id,
                    outbox_dispatch.c.status == "pending",
                    or_(
                        outbox_dispatch.c.claimed_by.is_(None),
                        outbox_dispatch.c.lease_expires_at <= claim_started_at.isoformat(),
                    ),
                )
                .values(
                    claimed_by=worker_id,
                    lease_expires_at=lease_expires_at.isoformat(),
                    fencing_token=outbox_dispatch.c.fencing_token + 1,
                )
                .returning(outbox_dispatch.c.fencing_token)
            ).scalar_one_or_none()
        if fencing_token is None:
            return RelayOutcome("busy", resolved_event_id, aggregate_version)
        token = int(fencing_token)

        self._recover_abandoned(event=event, fencing_token=token)
        attempt_id, work_id, attempt_number = self._start_attempt(
            event=event,
            worker_id=worker_id,
            fencing_token=token,
            lease_expires_at=lease_expires_at.isoformat(),
        )

        if not compatibility.accepts(
            event_type=str(event["event_type"]),
            schema_version=str(event["schema_version"]),
        ):
            with self._engine.begin() as connection:
                connection.execute(
                    outbox_dispatch.update()
                    .where(
                        outbox_dispatch.c.event_id == resolved_event_id,
                        outbox_dispatch.c.claimed_by == worker_id,
                        outbox_dispatch.c.fencing_token == token,
                    )
                    .values(status="isolated", claimed_by=None, lease_expires_at=None)
                )
                connection.execute(
                    outbox_delivery_attempts.update()
                    .where(outbox_delivery_attempts.c.attempt_id == attempt_id)
                    .values(status="blocked", reason_code="incompatible_event_contract")
                )
                connection.execute(
                    work_attempts.update()
                    .where(work_attempts.c.work_id == work_id)
                    .values(status="blocked")
                )
                self._record_incident(
                    connection,
                    event=event,
                    reason_code="incompatible_event_contract",
                )
                connection.execute(
                    security_audit_events.insert().values(
                        event_id=self._relay_id(
                            resolved_event_id,
                            "audit-isolated",
                            attempt_number,
                        ),
                        action="outbox_delivery",
                        outcome="denied",
                        reason_code="incompatible_event_contract",
                        trace_id=event["trace_id"],
                    )
                )
            return RelayOutcome("isolated", resolved_event_id, aggregate_version)

        try:
            fault.before_consumers(resolved_event_id)
            for consumer_name in ("research_projection", "operations_projection"):
                self._consume(
                    event=event,
                    consumer_name=consumer_name,
                    fault=fault,
                    clock=clock,
                    worker_id=worker_id,
                    fencing_token=token,
                )
            fault.before_ack(resolved_event_id)
        except RelayLeaseLost:
            self._finish_failure(
                event=event,
                attempt_id=attempt_id,
                work_id=work_id,
                attempt_number=attempt_number,
                worker_id=worker_id,
                fencing_token=token,
                attempt_status="failed",
                work_status="failed",
                reason_code="expired_fencing_token",
                audit_kind=None,
            )
            return RelayOutcome("busy", resolved_event_id, aggregate_version)
        except OutOfOrderEvent:
            self._finish_failure(
                event=event,
                attempt_id=attempt_id,
                work_id=work_id,
                attempt_number=attempt_number,
                worker_id=worker_id,
                fencing_token=token,
                attempt_status="deferred",
                work_status="blocked",
                reason_code="out_of_order_aggregate_version",
                audit_kind="audit-deferred",
            )
            return RelayOutcome("deferred", resolved_event_id, aggregate_version)
        except RuntimeError:
            self._finish_failure(
                event=event,
                attempt_id=attempt_id,
                work_id=work_id,
                attempt_number=attempt_number,
                worker_id=worker_id,
                fencing_token=token,
                attempt_status="failed",
                work_status="failed",
                reason_code="consumer_transaction_crash",
                audit_kind="audit-consumer-failed",
            )
            return RelayOutcome("failed", resolved_event_id, aggregate_version)

        with self._engine.begin() as connection:
            acknowledged = connection.execute(
                outbox_dispatch.update()
                .where(
                    outbox_dispatch.c.event_id == resolved_event_id,
                    outbox_dispatch.c.claimed_by == worker_id,
                    outbox_dispatch.c.fencing_token == token,
                    outbox_dispatch.c.lease_expires_at > clock.now().isoformat(),
                )
                .values(status="delivered", claimed_by=None, lease_expires_at=None)
            )
            if acknowledged.rowcount != 1:
                connection.execute(
                    outbox_delivery_attempts.update()
                    .where(
                        outbox_delivery_attempts.c.attempt_id == attempt_id,
                        outbox_delivery_attempts.c.status == "running",
                    )
                    .values(status="failed", reason_code="expired_fencing_token")
                )
                connection.execute(
                    work_attempts.update()
                    .where(work_attempts.c.work_id == work_id)
                    .values(status="failed")
                )
                self._record_incident(
                    connection,
                    event=event,
                    reason_code="expired_fencing_token",
                )
                return RelayOutcome("busy", resolved_event_id, aggregate_version)
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
                    event_id=self._relay_id(
                        resolved_event_id,
                        "audit-delivered",
                        attempt_number,
                    ),
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
        return RelayOutcome("delivered", resolved_event_id, aggregate_version)

    def _consume(
        self,
        *,
        event: Any,
        consumer_name: str,
        fault: RelayFault,
        clock: RelayClock,
        worker_id: str,
        fencing_token: int,
    ) -> None:
        event_id = str(event["event_id"])
        aggregate_id = str(event["aggregate_id"])
        aggregate_version = int(event["aggregate_version"])
        payload = cast(dict[str, Any], event["payload"])
        with self._engine.begin() as connection:
            self._assert_current_claim(
                connection,
                event_id=event_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                clock=clock,
            )
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
                    .values(evidence_projection_version=aggregate_version, stale=False)
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
            self._assert_current_claim(
                connection,
                event_id=event_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                clock=clock,
            )
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

    def get_recovery(self, event_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            attempts = list(
                connection.execute(
                    select(
                        outbox_delivery_attempts.c.attempt_number,
                        outbox_delivery_attempts.c.status,
                        outbox_delivery_attempts.c.reason_code,
                        outbox_delivery_attempts.c.work_id,
                        outbox_delivery_attempts.c.worker_id,
                        outbox_delivery_attempts.c.fencing_token,
                        outbox_delivery_attempts.c.lease_expires_at,
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

    def list_incidents(self, *, aggregate_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    outbox_incidents.c.incident_id,
                    outbox_incidents.c.fingerprint,
                    outbox_incidents.c.aggregate_id,
                    outbox_incidents.c.impact_scope,
                    outbox_incidents.c.severity,
                    outbox_incidents.c.owner,
                    outbox_incidents.c.status,
                    outbox_incidents.c.reason_code,
                    outbox_incidents.c.occurrence_count,
                    outbox_incidents.c.trace_id,
                ).where(outbox_incidents.c.aggregate_id == aggregate_id)
            ).mappings()
            return [dict(row) for row in rows]
