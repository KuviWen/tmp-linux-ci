from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from stock_forecasting.platform.schema import model_lifecycle_events


class LifecycleConflict(RuntimeError):
    """Raised when an append-only lifecycle precondition is stale or inconsistent."""


@dataclass(frozen=True)
class HardGateEvidence:
    qualification: bool
    point_in_time: bool
    leakage: bool
    calibration: bool
    economics: bool
    stability: bool
    coverage: bool
    operational: bool
    security: bool
    reproducibility: bool


@dataclass(frozen=True)
class RecordCandidate:
    command_id: str
    model_family_id: str
    candidate_id: str
    model_family: str
    artifact_id: str
    evaluation_report_id: str
    training_intent_id: str
    intent_initiator: str
    training_executor: str
    improvement_percentage_points: float
    calibrator_statuses: tuple[str, ...]
    expected_version: int
    occurred_at: datetime
    class_prior_equal_cell_macro_f1: float = 0.0
    logistic_equal_cell_macro_f1: float = 0.0
    fold_count: int = 0
    formal_qualification: bool = False


@dataclass(frozen=True)
class EvaluateBootstrapCandidate:
    command_id: str
    model_family_id: str
    candidate_id: str
    policy_version_id: str
    hard_gates: HardGateEvidence
    expected_version: int
    occurred_at: datetime


@dataclass(frozen=True)
class DecideApproval:
    command_id: str
    model_family_id: str
    candidate_id: str
    artifact_id: str
    evaluation_report_id: str
    policy_version_id: str
    approver_id: str
    decision: Literal["approved", "rejected"]
    reason: str
    expected_assignment: str
    expected_version: int
    occurred_at: datetime


@dataclass(frozen=True)
class RecordShadowEod:
    command_id: str
    model_family_id: str
    candidate_id: str
    shadow_run_id: str
    market_eligibility: tuple[str, ...]
    expected_version: int
    occurred_at: datetime


@dataclass(frozen=True)
class RecordDevelopmentGateFailure:
    command_id: str
    model_family_id: str
    candidate_id: str
    policy_version_id: str
    failed_gates: tuple[str, ...]
    expected_version: int
    occurred_at: datetime


LifecycleCommand = (
    RecordCandidate
    | EvaluateBootstrapCandidate
    | DecideApproval
    | RecordShadowEod
    | RecordDevelopmentGateFailure
)


@dataclass(frozen=True)
class GateDecision:
    gate_decision_id: str
    candidate_id: str
    artifact_id: str
    evaluation_report_id: str
    policy_version_id: str
    status: Literal["passed", "failed"]
    failed_gates: tuple[str, ...]
    serving_status: Literal["blocked"] = "blocked"


@dataclass(frozen=True)
class ApprovalDecision:
    approval_decision_id: str
    candidate_id: str
    artifact_id: str
    evaluation_report_id: str
    policy_version_id: str
    approver_id: str
    decision: Literal["approved", "rejected"]
    reason: str
    expected_assignment: str
    expires_at: datetime
    invalidated_reason: str | None


@dataclass(frozen=True)
class ShadowEvidence:
    shadow_run_id: str
    candidate_id: str
    eligible_cycle_count: int
    blocked_reason: str | None
    production_history_written: bool = False


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    command_id: str
    model_family_id: str
    version: int
    event_kind: str
    payload_json: str
    occurred_at: datetime


@dataclass(frozen=True)
class LifecycleResult:
    status: str
    version: int
    gate_decision: GateDecision | None = None
    approval_decision: ApprovalDecision | None = None
    shadow_evidence: ShadowEvidence | None = None


class LifecycleStore(Protocol):
    def append(
        self,
        *,
        command_id: str,
        model_family_id: str,
        expected_version: int,
        event_kind: str,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> LifecycleEvent: ...

    def events(self, model_family_id: str) -> tuple[LifecycleEvent, ...]: ...

    def production_assignments(self, model_family_id: str) -> tuple[str, ...]: ...


class InMemoryLifecycleStore:
    def __init__(
        self,
        *,
        historical_production_assignments: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._events: dict[str, list[LifecycleEvent]] = {}
        self._historical_production_assignments = historical_production_assignments or {}

    def append(
        self,
        *,
        command_id: str,
        model_family_id: str,
        expected_version: int,
        event_kind: str,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> LifecycleEvent:
        family_events = self._events.setdefault(model_family_id, [])
        current_version = len(family_events)
        if expected_version != current_version:
            raise LifecycleConflict("stale_lifecycle_version")
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        event_id = _content_id(
            "lifecycle_event",
            {
                "command_id": command_id,
                "model_family_id": model_family_id,
                "version": current_version + 1,
                "event_kind": event_kind,
                "payload": payload,
                "occurred_at": occurred_at.isoformat(),
            },
        )
        event = LifecycleEvent(
            event_id=event_id,
            command_id=command_id,
            model_family_id=model_family_id,
            version=current_version + 1,
            event_kind=event_kind,
            payload_json=payload_json,
            occurred_at=occurred_at,
        )
        family_events.append(event)
        return event

    def events(self, model_family_id: str) -> tuple[LifecycleEvent, ...]:
        return tuple(self._events.get(model_family_id, ()))

    def production_assignments(self, model_family_id: str) -> tuple[str, ...]:
        lifecycle_assignments = tuple(
            event.event_id
            for event in self.events(model_family_id)
            if event.event_kind == "ProductionAssignmentCreated"
        )
        return (
            self._historical_production_assignments.get(model_family_id, ()) + lifecycle_assignments
        )


class SqlAlchemyLifecycleStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(
        self,
        *,
        command_id: str,
        model_family_id: str,
        expected_version: int,
        event_kind: str,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> LifecycleEvent:
        try:
            with self._engine.begin() as connection:
                existing = (
                    connection.execute(
                        select(model_lifecycle_events).where(
                            model_lifecycle_events.c.command_id == command_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    if (
                        existing["model_family_id"] != model_family_id
                        or existing["event_kind"] != event_kind
                        or existing["payload"] != payload
                    ):
                        raise LifecycleConflict("command_id_payload_conflict")
                    return self._event_from_row(dict(existing))
                current_version = int(
                    connection.execute(
                        select(
                            func.coalesce(func.max(model_lifecycle_events.c.aggregate_version), 0)
                        ).where(model_lifecycle_events.c.model_family_id == model_family_id)
                    ).scalar_one()
                )
                if expected_version != current_version:
                    raise LifecycleConflict("stale_lifecycle_version")
                event_payload = {
                    "command_id": command_id,
                    "model_family_id": model_family_id,
                    "version": current_version + 1,
                    "event_kind": event_kind,
                    "payload": payload,
                    "occurred_at": occurred_at.isoformat(),
                }
                event_id = _content_id("lifecycle_event", event_payload)
                connection.execute(
                    model_lifecycle_events.insert().values(
                        event_id=event_id,
                        command_id=command_id,
                        model_family_id=model_family_id,
                        aggregate_version=current_version + 1,
                        event_kind=event_kind,
                        payload=payload,
                        occurred_at=occurred_at.isoformat(),
                    )
                )
                return LifecycleEvent(
                    event_id=event_id,
                    command_id=command_id,
                    model_family_id=model_family_id,
                    version=current_version + 1,
                    event_kind=event_kind,
                    payload_json=json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    occurred_at=occurred_at,
                )
        except IntegrityError as error:
            raise LifecycleConflict("concurrent_lifecycle_append") from error

    def events(self, model_family_id: str) -> tuple[LifecycleEvent, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(model_lifecycle_events)
                    .where(model_lifecycle_events.c.model_family_id == model_family_id)
                    .order_by(model_lifecycle_events.c.aggregate_version)
                )
                .mappings()
                .all()
            )
        return tuple(self._event_from_row(dict(row)) for row in rows)

    def production_assignments(self, model_family_id: str) -> tuple[str, ...]:
        return tuple(
            event.event_id
            for event in self.events(model_family_id)
            if event.event_kind == "ProductionAssignmentCreated"
        )

    @staticmethod
    def _event_from_row(row: dict[str, object]) -> LifecycleEvent:
        payload = cast(dict[str, object], row["payload"])
        return LifecycleEvent(
            event_id=str(row["event_id"]),
            command_id=str(row["command_id"]),
            model_family_id=str(row["model_family_id"]),
            version=int(cast(int, row["aggregate_version"])),
            event_kind=str(row["event_kind"]),
            payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
        )


class ModelLifecycle:
    _gate_names: tuple[str, ...] = (
        "qualification",
        "point_in_time",
        "leakage",
        "calibration",
        "economics",
        "stability",
        "coverage",
        "operational",
        "security",
        "reproducibility",
    )

    def __init__(self, store: LifecycleStore) -> None:
        self._store = store

    def execute(self, command: LifecycleCommand) -> LifecycleResult:
        if isinstance(command, RecordCandidate):
            return self._record_candidate(command)
        if isinstance(command, EvaluateBootstrapCandidate):
            return self._evaluate_bootstrap(command)
        if isinstance(command, DecideApproval):
            return self._decide_approval(command)
        if isinstance(command, RecordShadowEod):
            return self._record_shadow(command)
        return self._record_development_failure(command)

    def _record_candidate(self, command: RecordCandidate) -> LifecycleResult:
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            event_kind="CandidateRecorded",
            payload={
                "candidate_id": command.candidate_id,
                "model_family": command.model_family,
                "artifact_id": command.artifact_id,
                "evaluation_report_id": command.evaluation_report_id,
                "training_intent_id": command.training_intent_id,
                "intent_initiator": command.intent_initiator,
                "training_executor": command.training_executor,
                "improvement_percentage_points": (command.improvement_percentage_points),
                "calibrator_statuses": command.calibrator_statuses,
                "class_prior_equal_cell_macro_f1": (command.class_prior_equal_cell_macro_f1),
                "logistic_equal_cell_macro_f1": (command.logistic_equal_cell_macro_f1),
                "fold_count": command.fold_count,
                "formal_qualification": command.formal_qualification,
            },
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(status="candidate_recorded", version=event.version)

    def _evaluate_bootstrap(self, command: EvaluateBootstrapCandidate) -> LifecycleResult:
        candidate = self._candidate(command.model_family_id, command.candidate_id)
        failed_gates: list[str] = []
        if self._store.production_assignments(command.model_family_id):
            failed_gates.append("bootstrap_disabled_after_first_production_assignment")
        if candidate["model_family"] != "regularized_multinomial_logistic":
            failed_gates.append("model_family")
        if cast(float, candidate["improvement_percentage_points"]) < 1.0:
            failed_gates.append("minimum_improvement")
        calibrator_statuses = tuple(cast(list[str], candidate["calibrator_statuses"]))
        if len(calibrator_statuses) != 6 or any(
            status != "sufficient_data" for status in calibrator_statuses
        ):
            failed_gates.append("calibration_support")
        hard_gate_values = asdict(command.hard_gates)
        failed_gates.extend(name for name in self._gate_names if not bool(hard_gate_values[name]))
        decision_payload = {
            "candidate_id": command.candidate_id,
            "artifact_id": candidate["artifact_id"],
            "evaluation_report_id": candidate["evaluation_report_id"],
            "policy_version_id": command.policy_version_id,
            "status": "failed" if failed_gates else "passed",
            "failed_gates": failed_gates,
            "serving_status": "blocked",
        }
        decision = GateDecision(
            gate_decision_id=_content_id("gate_decision", decision_payload),
            candidate_id=command.candidate_id,
            artifact_id=str(candidate["artifact_id"]),
            evaluation_report_id=str(candidate["evaluation_report_id"]),
            policy_version_id=command.policy_version_id,
            status="failed" if failed_gates else "passed",
            failed_gates=tuple(failed_gates),
        )
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            event_kind="GateDecisionRecorded",
            payload={**decision_payload, "gate_decision_id": decision.gate_decision_id},
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(
            status="gate_failed" if failed_gates else "gate_passed",
            version=event.version,
            gate_decision=decision,
        )

    def _candidate(self, model_family_id: str, candidate_id: str) -> dict[str, object]:
        for event in reversed(self._store.events(model_family_id)):
            if event.event_kind != "CandidateRecorded":
                continue
            payload = json.loads(event.payload_json)
            if payload["candidate_id"] == candidate_id:
                return dict(payload)
        raise KeyError(candidate_id)

    def _decide_approval(self, command: DecideApproval) -> LifecycleResult:
        candidate = self._candidate(command.model_family_id, command.candidate_id)
        gate_event, gate = self._passed_gate(command.model_family_id, command.candidate_id)
        expires_at = gate_event.occurred_at + timedelta(days=7)
        invalidated_reason: str | None = None
        if command.approver_id in {
            candidate["intent_initiator"],
            candidate["training_executor"],
        }:
            invalidated_reason = "duty_separation_violation"
        elif (
            command.artifact_id != gate["artifact_id"]
            or command.evaluation_report_id != gate["evaluation_report_id"]
            or command.policy_version_id != gate["policy_version_id"]
        ):
            invalidated_reason = "evidence_reference_mismatch"
        elif command.occurred_at > expires_at:
            invalidated_reason = "approval_expired"
        elif not command.reason.strip():
            invalidated_reason = "reason_required"
        elif command.decision == "rejected":
            invalidated_reason = "approver_rejected"
        effective_decision: Literal["approved", "rejected"] = (
            "rejected" if invalidated_reason is not None else "approved"
        )
        decision_payload = {
            "candidate_id": command.candidate_id,
            "artifact_id": command.artifact_id,
            "evaluation_report_id": command.evaluation_report_id,
            "policy_version_id": command.policy_version_id,
            "approver_id": command.approver_id,
            "decision": effective_decision,
            "reason": command.reason,
            "expected_assignment": command.expected_assignment,
            "expires_at": expires_at.isoformat(),
            "invalidated_reason": invalidated_reason,
        }
        decision = ApprovalDecision(
            approval_decision_id=_content_id("approval_decision", decision_payload),
            candidate_id=command.candidate_id,
            artifact_id=command.artifact_id,
            evaluation_report_id=command.evaluation_report_id,
            policy_version_id=command.policy_version_id,
            approver_id=command.approver_id,
            decision=effective_decision,
            reason=command.reason,
            expected_assignment=command.expected_assignment,
            expires_at=expires_at,
            invalidated_reason=invalidated_reason,
        )
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            event_kind="ApprovalDecisionRecorded",
            payload={
                **decision_payload,
                "approval_decision_id": decision.approval_decision_id,
            },
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(
            status="approved" if effective_decision == "approved" else "approval_rejected",
            version=event.version,
            approval_decision=decision,
        )

    def _passed_gate(
        self, model_family_id: str, candidate_id: str
    ) -> tuple[LifecycleEvent, dict[str, object]]:
        for event in reversed(self._store.events(model_family_id)):
            if event.event_kind != "GateDecisionRecorded":
                continue
            payload = json.loads(event.payload_json)
            if payload["candidate_id"] != candidate_id:
                continue
            if payload["status"] != "passed":
                raise LifecycleConflict("candidate_gate_not_passed")
            return event, dict(payload)
        raise LifecycleConflict("candidate_gate_not_evaluated")

    def _record_shadow(self, command: RecordShadowEod) -> LifecycleResult:
        approval = self._current_approval(command.model_family_id, command.candidate_id)
        expires_at = datetime.fromisoformat(str(approval["expires_at"]))
        blocked_reason: str | None = None
        if approval["decision"] != "approved" or approval["invalidated_reason"] is not None:
            blocked_reason = "approval_not_valid"
        elif command.occurred_at > expires_at:
            blocked_reason = "approval_expired"
        elif set(command.market_eligibility) != {"XTAI", "XNAS"}:
            blocked_reason = "incomplete_market_shadow"
        completed_cycles = sum(
            event.event_kind == "ShadowEodRecorded"
            and json.loads(event.payload_json)["candidate_id"] == command.candidate_id
            for event in self._store.events(command.model_family_id)
        )
        eligible_cycle_count = completed_cycles + (0 if blocked_reason else 1)
        evidence = ShadowEvidence(
            shadow_run_id=command.shadow_run_id,
            candidate_id=command.candidate_id,
            eligible_cycle_count=eligible_cycle_count,
            blocked_reason=blocked_reason,
        )
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            event_kind=("ShadowEodBlocked" if blocked_reason else "ShadowEodRecorded"),
            payload={
                "shadow_run_id": command.shadow_run_id,
                "candidate_id": command.candidate_id,
                "market_eligibility": command.market_eligibility,
                "eligible_cycle_count": eligible_cycle_count,
                "blocked_reason": blocked_reason,
                "production_history_written": False,
            },
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(
            status=(
                "shadow_blocked"
                if blocked_reason
                else "shadow_complete"
                if eligible_cycle_count == 5
                else "shadow_recorded"
            ),
            version=event.version,
            shadow_evidence=evidence,
        )

    def _current_approval(self, model_family_id: str, candidate_id: str) -> dict[str, object]:
        for event in reversed(self._store.events(model_family_id)):
            if event.event_kind != "ApprovalDecisionRecorded":
                continue
            payload = json.loads(event.payload_json)
            if payload["candidate_id"] == candidate_id:
                return dict(payload)
        raise LifecycleConflict("candidate_not_approved")

    def _record_development_failure(self, command: RecordDevelopmentGateFailure) -> LifecycleResult:
        decision_payload = {
            "candidate_id": command.candidate_id,
            "artifact_id": "unavailable:not-produced",
            "evaluation_report_id": "unavailable:not-produced",
            "policy_version_id": command.policy_version_id,
            "status": "failed",
            "failed_gates": command.failed_gates,
            "serving_status": "blocked",
        }
        decision = GateDecision(
            gate_decision_id=_content_id("gate_decision", decision_payload),
            candidate_id=command.candidate_id,
            artifact_id="unavailable:not-produced",
            evaluation_report_id="unavailable:not-produced",
            policy_version_id=command.policy_version_id,
            status="failed",
            failed_gates=command.failed_gates,
        )
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            event_kind="GateDecisionRecorded",
            payload={**decision_payload, "gate_decision_id": decision.gate_decision_id},
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(
            status="gate_failed",
            version=event.version,
            gate_decision=decision,
        )


class ModelGovernanceQuery:
    def __init__(self, store: LifecycleStore) -> None:
        self._store = store

    def get_backtest(self, model_family_id: str) -> dict[str, object]:
        events = self._store.events(model_family_id)
        candidate_event = next(
            (event for event in reversed(events) if event.event_kind == "CandidateRecorded"),
            None,
        )
        if candidate_event is None:
            raise KeyError(model_family_id)
        candidate = cast(dict[str, object], json.loads(candidate_event.payload_json))
        candidate_id = str(candidate["candidate_id"])
        gate = self._latest_payload(events, "GateDecisionRecorded", candidate_id)
        approval = self._latest_payload(events, "ApprovalDecisionRecorded", candidate_id)
        shadow_count = sum(
            event.event_kind == "ShadowEodRecorded"
            and json.loads(event.payload_json)["candidate_id"] == candidate_id
            for event in events
        )
        assignments = self._store.production_assignments(model_family_id)
        calibrator_statuses = cast(list[str], candidate["calibrator_statuses"])
        return {
            "model_family_id": model_family_id,
            "candidate": {
                "candidate_id": candidate_id,
                "model_family": candidate["model_family"],
                "artifact_id": candidate["artifact_id"],
                "formal_qualification": candidate["formal_qualification"],
            },
            "baseline": {
                "model_family": "class_prior",
                "equal_cell_macro_f1": candidate["class_prior_equal_cell_macro_f1"],
            },
            "evaluation": {
                "evaluation_report_id": candidate["evaluation_report_id"],
                "logistic_equal_cell_macro_f1": candidate["logistic_equal_cell_macro_f1"],
                "improvement_percentage_points": candidate["improvement_percentage_points"],
            },
            "calibration": {
                "sufficient": sum(status == "sufficient_data" for status in calibrator_statuses),
                "required": 6,
            },
            "support": {"fold_count": candidate["fold_count"]},
            "gate": (
                {
                    "status": gate["status"],
                    "policy_version_id": gate["policy_version_id"],
                    "failed_gates": gate["failed_gates"],
                }
                if gate is not None
                else {"status": "not_evaluated"}
            ),
            "approval": (
                {"status": ("approved" if approval["decision"] == "approved" else "rejected")}
                if approval is not None
                else {"status": "awaiting_approval"}
            ),
            "shadow": {"eligible_cycle_count": shadow_count, "required": 5},
            "serving": {
                "status": "assigned" if assignments else "blocked",
                "production_assignment_id": assignments[-1] if assignments else None,
            },
        }

    @staticmethod
    def _latest_payload(
        events: tuple[LifecycleEvent, ...],
        event_kind: str,
        candidate_id: str,
    ) -> dict[str, object] | None:
        for event in reversed(events):
            if event.event_kind != event_kind:
                continue
            payload = cast(dict[str, object], json.loads(event.payload_json))
            if payload["candidate_id"] == candidate_id:
                return payload
        return None


def _content_id(kind: str, payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(kind.encode() + serialized).hexdigest()}"
