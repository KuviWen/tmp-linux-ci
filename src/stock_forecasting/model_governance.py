from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import BinaryIO, Literal, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from stock_forecasting.platform.outbox_relay import outbox_dispatch, outbox_events
from stock_forecasting.platform.schema import model_lifecycle_events


class LifecycleConflict(RuntimeError):
    """Raised when an append-only lifecycle precondition is stale or inconsistent."""


@dataclass(frozen=True)
class GateMeasurement:
    name: str
    value: float


@dataclass(frozen=True)
class HardGateEvidence:
    evidence_id: str
    evidence_kind: Literal["formal_evidence", "engineering_example"]
    policy_version_id: str
    evaluation_report_id: str
    evidence_refs: tuple[str, ...]
    measurements: tuple[GateMeasurement, ...]

    @classmethod
    def create(
        cls,
        *,
        evidence_kind: Literal["formal_evidence", "engineering_example"],
        policy_version_id: str,
        evaluation_report_id: str,
        evidence_refs: tuple[str, ...],
        measurements: tuple[GateMeasurement, ...],
    ) -> HardGateEvidence:
        ordered_measurements = tuple(sorted(measurements, key=lambda item: item.name))
        ordered_refs = tuple(sorted(evidence_refs))
        payload = {
            "evidence_kind": evidence_kind,
            "policy_version_id": policy_version_id,
            "evaluation_report_id": evaluation_report_id,
            "evidence_refs": ordered_refs,
            "measurements": [
                {"name": item.name, "value": item.value} for item in ordered_measurements
            ],
        }
        return cls(
            evidence_id=_content_id("hard_gate_evidence", payload),
            evidence_kind=evidence_kind,
            policy_version_id=policy_version_id,
            evaluation_report_id=evaluation_report_id,
            evidence_refs=ordered_refs,
            measurements=ordered_measurements,
        )

    def is_content_addressed(self) -> bool:
        rebuilt = self.create(
            evidence_kind=self.evidence_kind,
            policy_version_id=self.policy_version_id,
            evaluation_report_id=self.evaluation_report_id,
            evidence_refs=self.evidence_refs,
            measurements=self.measurements,
        )
        return rebuilt == self


@dataclass(frozen=True)
class GateThreshold:
    category: str
    comparison: Literal["at_least", "at_most"]
    limit: float

    def passes(self, value: float) -> bool:
        if self.comparison == "at_least":
            return value >= self.limit
        return value <= self.limit


@dataclass(frozen=True)
class BootstrapGatePolicyVersion:
    policy_version_id: str
    policy_name: str
    thresholds: tuple[tuple[str, GateThreshold], ...]
    serialized: bytes

    @classmethod
    def create(
        cls,
        *,
        policy_name: str,
        thresholds: Mapping[str, GateThreshold],
    ) -> BootstrapGatePolicyVersion:
        ordered = tuple(sorted(thresholds.items()))
        payload = {
            "policy_name": policy_name,
            "thresholds": [
                {
                    "name": name,
                    "category": threshold.category,
                    "comparison": threshold.comparison,
                    "limit": threshold.limit,
                }
                for name, threshold in ordered
            ],
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return cls(
            policy_version_id=f"sha256:{hashlib.sha256(serialized).hexdigest()}",
            policy_name=policy_name,
            thresholds=ordered,
            serialized=serialized,
        )

    @classmethod
    def from_serialized(
        cls, policy_version_id: str, serialized: bytes
    ) -> BootstrapGatePolicyVersion:
        payload = cast(dict[str, object], json.loads(serialized))
        raw_thresholds = cast(list[dict[str, object]], payload["thresholds"])
        policy = cls.create(
            policy_name=str(payload["policy_name"]),
            thresholds={
                str(item["name"]): GateThreshold(
                    category=str(item["category"]),
                    comparison=cast(Literal["at_least", "at_most"], item["comparison"]),
                    limit=float(cast(float, item["limit"])),
                )
                for item in raw_thresholds
            },
        )
        if policy.policy_version_id != policy_version_id or policy.serialized != serialized:
            raise ValueError("gate_policy_checksum_mismatch")
        return policy


_BOOTSTRAP_THRESHOLDS_V1: Mapping[str, GateThreshold] = MappingProxyType(
    {
        "qualification.manifest_fraction": GateThreshold("qualification", "at_least", 1.0),
        "point_in_time.contract_fraction": GateThreshold("point_in_time", "at_least", 1.0),
        "leakage.contract_fraction": GateThreshold("leakage", "at_least", 1.0),
        "calibration.equal_cell_ece": GateThreshold("calibration", "at_most", 0.05),
        "calibration.max_full_support_cell_ece": GateThreshold("calibration", "at_most", 0.08),
        "calibration.max_degraded_support_cell_ece": GateThreshold("calibration", "at_most", 0.10),
        "calibration.sufficient_calibrator_count": GateThreshold("calibration", "at_least", 6.0),
        "calibration.identity_fallback_count": GateThreshold("calibration", "at_most", 0.0),
        "calibration.nll_degradation_fraction": GateThreshold("calibration", "at_most", 0.0),
        "calibration.brier_degradation_fraction": GateThreshold("calibration", "at_most", 0.0),
        "economics.positive_market_rank_ic_count": GateThreshold("economics", "at_least", 2.0),
        "economics.positive_cell_rank_ic_count": GateThreshold("economics", "at_least", 4.0),
        "economics.ic_information_ratio": GateThreshold("economics", "at_least", 0.30),
        "economics.nonnegative_market_excess_count": GateThreshold("economics", "at_least", 2.0),
        "economics.nonnegative_cell_excess_count": GateThreshold("economics", "at_least", 4.0),
        "economics.drawdown_worsening_points": GateThreshold("economics", "at_most", 2.0),
        "stability.noninferior_quarter_count": GateThreshold("stability", "at_least", 6.0),
        "stability.max_consecutive_lagging_quarters": GateThreshold("stability", "at_most", 2.0),
        "stability.seed_macro_f1_std_points": GateThreshold("stability", "at_most", 1.0),
        "stability.worst_seed_delta_points": GateThreshold("stability", "at_least", 0.0),
        "coverage.large_slice_max_decline_points": GateThreshold("coverage", "at_most", 2.0),
        "coverage.degraded_coverage_decline_points": GateThreshold("coverage", "at_most", 5.0),
        "coverage.degraded_macro_f1_decline_points": GateThreshold("coverage", "at_most", 2.0),
        "operational.trainable_parameter_count": GateThreshold(
            "operational", "at_most", 15_000_000.0
        ),
        "operational.cpu_prediction_minutes": GateThreshold("operational", "at_most", 10.0),
        "operational.daily_pipeline_minutes": GateThreshold("operational", "at_most", 120.0),
        "security.safe_artifact_fraction": GateThreshold("security", "at_least", 1.0),
        "security.critical_finding_count": GateThreshold("security", "at_most", 0.0),
        "security.artifact_corruption_count": GateThreshold("security", "at_most", 0.0),
        "reproducibility.sample_replay_fraction": GateThreshold("reproducibility", "at_least", 1.0),
        "reproducibility.cpu_probability_max_delta": GateThreshold(
            "reproducibility", "at_most", 0.000001
        ),
    }
)

BOOTSTRAP_GATE_POLICY_V1 = BootstrapGatePolicyVersion.create(
    policy_name="bootstrap-gate-policy-v1",
    thresholds=_BOOTSTRAP_THRESHOLDS_V1,
)


class GatePolicyRepository(Protocol):
    def get(self, policy_version_id: str) -> BootstrapGatePolicyVersion: ...


class GateEvidenceRepository(Protocol):
    def is_verified(self, artifact_id: str) -> bool: ...


class ContentAddressedObjectRepository(Protocol):
    def open_by_id(self, object_id: str) -> BinaryIO: ...


class InMemoryGatePolicyRepository:
    def __init__(self, policies: tuple[BootstrapGatePolicyVersion, ...]) -> None:
        self._policies = {policy.policy_version_id: policy for policy in policies}

    def get(self, policy_version_id: str) -> BootstrapGatePolicyVersion:
        try:
            return self._policies[policy_version_id]
        except KeyError as error:
            raise KeyError(policy_version_id) from error


class ObjectGatePolicyRepository:
    def __init__(
        self,
        objects: ContentAddressedObjectRepository | Callable[[], ContentAddressedObjectRepository],
    ) -> None:
        self._objects = objects

    def get(self, policy_version_id: str) -> BootstrapGatePolicyVersion:
        try:
            objects = self._objects() if callable(self._objects) else self._objects
            serialized = objects.open_by_id(policy_version_id).read()
            return BootstrapGatePolicyVersion.from_serialized(policy_version_id, serialized)
        except (FileNotFoundError, OSError, ValueError) as error:
            raise KeyError(policy_version_id) from error


class ObjectGateEvidenceRepository:
    def __init__(
        self,
        objects: ContentAddressedObjectRepository | Callable[[], ContentAddressedObjectRepository],
    ) -> None:
        self._objects = objects

    def is_verified(self, artifact_id: str) -> bool:
        try:
            objects = self._objects() if callable(self._objects) else self._objects
            objects.open_by_id(artifact_id).read()
        except (FileNotFoundError, OSError, ValueError):
            return False
        return True


class UnavailableGateEvidenceRepository:
    def is_verified(self, artifact_id: str) -> bool:
        return False


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
class ShadowRunEvidence:
    evidence_id: str
    shadow_run_id: str
    eligible_eod_date: date
    previous_shadow_run_id: str | None
    markets: tuple[str, ...]
    cold_load_checksum_verified: bool
    schema_compatible: bool
    probability_invariants_verified: bool
    comparison_completed: bool
    source_policy_verified: bool
    cpu_prediction_seconds: float

    @classmethod
    def create(
        cls,
        *,
        shadow_run_id: str,
        eligible_eod_date: date,
        previous_shadow_run_id: str | None,
        markets: tuple[str, ...],
        cold_load_checksum_verified: bool,
        schema_compatible: bool,
        probability_invariants_verified: bool,
        comparison_completed: bool,
        source_policy_verified: bool,
        cpu_prediction_seconds: float,
    ) -> ShadowRunEvidence:
        ordered_markets = tuple(sorted(markets))
        payload = {
            "shadow_run_id": shadow_run_id,
            "eligible_eod_date": eligible_eod_date.isoformat(),
            "previous_shadow_run_id": previous_shadow_run_id,
            "markets": ordered_markets,
            "cold_load_checksum_verified": cold_load_checksum_verified,
            "schema_compatible": schema_compatible,
            "probability_invariants_verified": probability_invariants_verified,
            "comparison_completed": comparison_completed,
            "source_policy_verified": source_policy_verified,
            "cpu_prediction_seconds": cpu_prediction_seconds,
        }
        return cls(
            evidence_id=_content_id("shadow_run_evidence", payload),
            shadow_run_id=shadow_run_id,
            eligible_eod_date=eligible_eod_date,
            previous_shadow_run_id=previous_shadow_run_id,
            markets=ordered_markets,
            cold_load_checksum_verified=cold_load_checksum_verified,
            schema_compatible=schema_compatible,
            probability_invariants_verified=probability_invariants_verified,
            comparison_completed=comparison_completed,
            source_policy_verified=source_policy_verified,
            cpu_prediction_seconds=cpu_prediction_seconds,
        )

    def is_content_addressed(self) -> bool:
        rebuilt = self.create(
            shadow_run_id=self.shadow_run_id,
            eligible_eod_date=self.eligible_eod_date,
            previous_shadow_run_id=self.previous_shadow_run_id,
            markets=self.markets,
            cold_load_checksum_verified=self.cold_load_checksum_verified,
            schema_compatible=self.schema_compatible,
            probability_invariants_verified=self.probability_invariants_verified,
            comparison_completed=self.comparison_completed,
            source_policy_verified=self.source_policy_verified,
            cpu_prediction_seconds=self.cpu_prediction_seconds,
        )
        return rebuilt == self


@dataclass(frozen=True)
class RecordShadowEod:
    command_id: str
    model_family_id: str
    candidate_id: str
    evidence: ShadowRunEvidence
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
    hard_gate_evidence_id: str | None = None
    hard_gate_evidence_refs: tuple[str, ...] = ()
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
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = next(
            (
                event
                for events in self._events.values()
                for event in events
                if event.command_id == command_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.model_family_id != model_family_id
                or existing.event_kind != event_kind
                or existing.payload_json != payload_json
            ):
                raise LifecycleConflict("command_id_payload_conflict")
            return existing
        current_version = len(family_events)
        if expected_version != current_version:
            raise LifecycleConflict("stale_lifecycle_version")
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
                outbox_event_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"stock-forecasting/model-lifecycle-outbox/{event_id}",
                    )
                )
                connection.execute(
                    outbox_events.insert().values(
                        event_id=outbox_event_id,
                        event_type="model_lifecycle.event_recorded",
                        schema_version="1.0.0",
                        aggregate_id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"stock-forecasting/model-family/{model_family_id}",
                            )
                        ),
                        aggregate_version=current_version + 1,
                        occurred_at=occurred_at.isoformat(),
                        producer="model_governance",
                        trace_id=command_id,
                        payload={
                            "lifecycle_event_id": event_id,
                            "event_kind": event_kind,
                            "model_family_id": model_family_id,
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

    def __init__(
        self,
        store: LifecycleStore,
        *,
        policy_repository: GatePolicyRepository | None = None,
        evidence_repository: GateEvidenceRepository | None = None,
    ) -> None:
        self._store = store
        self._policy_repository = policy_repository or InMemoryGatePolicyRepository(
            (BOOTSTRAP_GATE_POLICY_V1,)
        )
        self._evidence_repository = evidence_repository or UnavailableGateEvidenceRepository()

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
        if candidate["formal_qualification"] is not True:
            failed_gates.append("qualification")
        if cast(float, candidate["improvement_percentage_points"]) < 1.0:
            failed_gates.append("minimum_improvement")
        calibrator_statuses = tuple(cast(list[str], candidate["calibrator_statuses"]))
        if len(calibrator_statuses) != 6 or any(
            status != "sufficient_data" for status in calibrator_statuses
        ):
            failed_gates.append("calibration_support")
        evidence = command.hard_gates
        try:
            policy = self._policy_repository.get(command.policy_version_id)
        except KeyError:
            policy = None
        thresholds = dict(policy.thresholds) if policy is not None else {}
        evidence_is_valid = (
            policy is not None
            and evidence.evidence_kind == "formal_evidence"
            and evidence.policy_version_id == command.policy_version_id
            and evidence.evaluation_report_id == candidate["evaluation_report_id"]
            and evidence.is_content_addressed()
            and bool(evidence.evidence_refs)
            and all(
                self._evidence_repository.is_verified(reference)
                for reference in evidence.evidence_refs
            )
            and {item.name for item in evidence.measurements} == set(thresholds)
        )
        if not evidence_is_valid:
            failed_gates.append("hard_gate_evidence")
        else:
            failed_categories = {
                threshold.category
                for measurement in evidence.measurements
                if not (threshold := thresholds[measurement.name]).passes(measurement.value)
            }
            failed_gates.extend(
                category for category in self._gate_names if category in failed_categories
            )
        decision_payload = {
            "candidate_id": command.candidate_id,
            "artifact_id": candidate["artifact_id"],
            "evaluation_report_id": candidate["evaluation_report_id"],
            "policy_version_id": command.policy_version_id,
            "status": "failed" if failed_gates else "passed",
            "failed_gates": failed_gates,
            "hard_gate_evidence_id": evidence.evidence_id,
            "hard_gate_evidence_refs": evidence.evidence_refs,
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
            hard_gate_evidence_id=evidence.evidence_id,
            hard_gate_evidence_refs=evidence.evidence_refs,
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
        _, gate = self._passed_gate(command.model_family_id, command.candidate_id)
        expires_at = command.occurred_at + timedelta(days=7)
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
        elif command.expected_assignment != self._current_assignment(command.model_family_id):
            invalidated_reason = "expected_assignment_changed"
        elif self._has_irreversible_rejection(command):
            invalidated_reason = "prior_rejection_irreversible"
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
            "requested_decision": command.decision,
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

    def _has_irreversible_rejection(self, command: DecideApproval) -> bool:
        for event in self._store.events(command.model_family_id):
            if event.event_kind != "ApprovalDecisionRecorded":
                continue
            payload = json.loads(event.payload_json)
            if (
                payload.get("candidate_id") == command.candidate_id
                and payload.get("artifact_id") == command.artifact_id
                and payload.get("evaluation_report_id") == command.evaluation_report_id
                and payload.get("policy_version_id") == command.policy_version_id
                and payload.get("requested_decision") == "rejected"
                and payload.get("invalidated_reason") == "approver_rejected"
            ):
                return True
        return False

    def _current_assignment(self, model_family_id: str) -> str:
        assignments = self._store.production_assignments(model_family_id)
        return assignments[-1] if assignments else "unassigned"

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
        evidence = command.evidence
        prior_shadow_events = tuple(
            event
            for event in self._store.events(command.model_family_id)
            if event.event_kind == "ShadowEodRecorded"
            and json.loads(event.payload_json)["candidate_id"] == command.candidate_id
        )
        prior_payloads = tuple(json.loads(event.payload_json) for event in prior_shadow_events)
        previous = prior_payloads[-1] if prior_payloads else None
        blocked_reason: str | None = None
        if approval["decision"] != "approved" or approval["invalidated_reason"] is not None:
            blocked_reason = "approval_not_valid"
        elif command.occurred_at >= expires_at:
            blocked_reason = "approval_expired"
        elif approval["expected_assignment"] != self._current_assignment(command.model_family_id):
            blocked_reason = "expected_assignment_changed"
        elif not evidence.is_content_addressed():
            blocked_reason = "shadow_evidence_checksum_mismatch"
        elif set(evidence.markets) != {"XTAI", "XNAS"}:
            blocked_reason = "incomplete_market_shadow"
        elif (
            not all(
                (
                    evidence.cold_load_checksum_verified,
                    evidence.schema_compatible,
                    evidence.probability_invariants_verified,
                    evidence.comparison_completed,
                    evidence.source_policy_verified,
                )
            )
            or evidence.cpu_prediction_seconds > 600
        ):
            blocked_reason = "shadow_checks_failed"
        elif any(
            payload["shadow_run_id"] == evidence.shadow_run_id
            or payload["eligible_eod_date"] == evidence.eligible_eod_date.isoformat()
            for payload in prior_payloads
        ):
            blocked_reason = "duplicate_shadow_run"
        elif evidence.previous_shadow_run_id != (
            str(previous["shadow_run_id"]) if previous is not None else None
        ):
            blocked_reason = "shadow_sequence_broken"
        elif previous is not None and evidence.eligible_eod_date <= date.fromisoformat(
            str(previous["eligible_eod_date"])
        ):
            blocked_reason = "shadow_date_not_increasing"
        completed_cycles = len(prior_shadow_events)
        eligible_cycle_count = completed_cycles + (0 if blocked_reason else 1)
        outcome_evidence = ShadowEvidence(
            shadow_run_id=evidence.shadow_run_id,
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
                "shadow_run_id": evidence.shadow_run_id,
                "shadow_evidence_id": evidence.evidence_id,
                "eligible_eod_date": evidence.eligible_eod_date.isoformat(),
                "previous_shadow_run_id": evidence.previous_shadow_run_id,
                "candidate_id": command.candidate_id,
                "market_eligibility": evidence.markets,
                "cold_load_checksum_verified": evidence.cold_load_checksum_verified,
                "schema_compatible": evidence.schema_compatible,
                "probability_invariants_verified": (evidence.probability_invariants_verified),
                "comparison_completed": evidence.comparison_completed,
                "source_policy_verified": evidence.source_policy_verified,
                "cpu_prediction_seconds": evidence.cpu_prediction_seconds,
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
            shadow_evidence=outcome_evidence,
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
                    "hard_gate_evidence_id": gate.get("hard_gate_evidence_id"),
                    "hard_gate_evidence_refs": gate.get("hard_gate_evidence_refs", []),
                }
                if gate is not None
                else {"status": "not_evaluated"}
            ),
            "approval": (
                {"status": ("approved" if approval["decision"] == "approved" else "rejected")}
                if approval is not None
                else {
                    "status": (
                        "blocked_by_gate"
                        if gate is not None and gate["status"] == "failed"
                        else "awaiting_approval"
                    )
                }
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
