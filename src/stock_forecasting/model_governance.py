from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from stock_forecasting.content_address import canonical_json, canonical_json_bytes, sha256_id
from stock_forecasting.content_address import content_id as _content_id
from stock_forecasting.evaluation_report import EvaluationReport
from stock_forecasting.forecasting import (
    ClassPriorTrendForecaster,
    ModelArtifact,
    RegularizedMultinomialLogisticTrendForecaster,
    training_selection_id_for,
)
from stock_forecasting.platform.outbox_relay import outbox_dispatch, outbox_events
from stock_forecasting.platform.schema import (
    model_lifecycle_events,
    production_serving_assignment_pins,
)

if TYPE_CHECKING:
    from stock_forecasting.forecast_lab import (
        CandidateEvidenceBundle,
        FoldManifest,
        FormalQualificationEvidence,
        TrainingIntentRef,
    )


class LifecycleConflict(RuntimeError):
    """Raised when an append-only lifecycle precondition is stale or inconsistent."""


class FormalQualificationVerifier(Protocol):
    def verify(
        self,
        evidence: FormalQualificationEvidence,
        intent: TrainingIntentRef,
        fold_manifest: FoldManifest,
    ) -> bool: ...


class UnavailableFormalQualificationVerifier:
    def verify(
        self,
        evidence: FormalQualificationEvidence,
        intent: TrainingIntentRef,
        fold_manifest: FoldManifest,
    ) -> bool:
        return False


class PromotionReadinessVerifier(Protocol):
    def verify(self, readiness: PromotionReadiness) -> bool: ...

    def verify_current_source_policy(self, source_policy_manifest_id: str) -> bool: ...


class UnavailablePromotionReadinessVerifier:
    def verify(self, readiness: PromotionReadiness) -> bool:
        return False

    def verify_current_source_policy(self, source_policy_manifest_id: str) -> bool:
        return False


def _cold_load_model_artifact(artifact: ModelArtifact) -> ModelArtifact:
    parsed = ModelArtifact.from_serialized(artifact.artifact_id, artifact.serialized)
    if artifact.model_family == "regularized_multinomial_logistic":
        RegularizedMultinomialLogisticTrendForecaster.load(artifact.serialized)
    elif artifact.model_family == "class_prior":
        ClassPriorTrendForecaster.load(artifact.serialized)
    else:
        raise ValueError("candidate_model_family_invalid")
    return parsed


@dataclass(frozen=True)
class GateMeasurement:
    name: str
    value: float


@dataclass(frozen=True)
class HardGateReportArtifact:
    artifact_id: str
    policy_version_id: str
    evaluation_report_id: str
    measurements: tuple[GateMeasurement, ...]
    serialized: bytes

    @classmethod
    def create(
        cls,
        *,
        policy_version_id: str,
        evaluation_report_id: str,
        measurements: tuple[GateMeasurement, ...],
    ) -> HardGateReportArtifact:
        ordered = tuple(sorted(measurements, key=lambda item: item.name))
        if len({item.name for item in ordered}) != len(ordered):
            raise ValueError("hard_gate_report_duplicate_measurement")
        if any(not item.name or not isfinite(item.value) for item in ordered):
            raise ValueError("hard_gate_report_invalid_measurement")
        payload = {
            "artifact_kind": "bootstrap_hard_gate_report",
            "schema_version": "bootstrap-hard-gate-report/v1",
            "policy_version_id": policy_version_id,
            "evaluation_report_id": evaluation_report_id,
            "measurements": [{"name": item.name, "value": item.value} for item in ordered],
        }
        serialized = canonical_json_bytes(payload)
        return cls(
            artifact_id=sha256_id(serialized),
            policy_version_id=policy_version_id,
            evaluation_report_id=evaluation_report_id,
            measurements=ordered,
            serialized=serialized,
        )

    @classmethod
    def from_serialized(
        cls,
        artifact_id: str,
        serialized: bytes,
    ) -> HardGateReportArtifact:
        payload = json.loads(serialized)
        if not isinstance(payload, dict) or set(payload) != {
            "artifact_kind",
            "schema_version",
            "policy_version_id",
            "evaluation_report_id",
            "measurements",
        }:
            raise ValueError("hard_gate_report_schema_invalid")
        if (
            payload["artifact_kind"] != "bootstrap_hard_gate_report"
            or payload["schema_version"] != "bootstrap-hard-gate-report/v1"
            or not isinstance(payload["policy_version_id"], str)
            or not isinstance(payload["evaluation_report_id"], str)
            or not isinstance(payload["measurements"], list)
        ):
            raise ValueError("hard_gate_report_schema_invalid")
        measurements: list[GateMeasurement] = []
        for item in payload["measurements"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"name", "value"}
                or not isinstance(item["name"], str)
                or isinstance(item["value"], bool)
                or not isinstance(item["value"], (int, float))
            ):
                raise ValueError("hard_gate_report_schema_invalid")
            measurements.append(GateMeasurement(item["name"], float(item["value"])))
        report = cls.create(
            policy_version_id=payload["policy_version_id"],
            evaluation_report_id=payload["evaluation_report_id"],
            measurements=tuple(measurements),
        )
        if report.artifact_id != artifact_id or report.serialized != serialized:
            raise ValueError("hard_gate_report_checksum_mismatch")
        return report


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


GateCategory = Literal[
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
]
_GATE_NAMES: tuple[GateCategory, ...] = (
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


@dataclass(frozen=True)
class GateThreshold:
    category: GateCategory
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
        if (
            not policy_name
            or not ordered
            or len({name for name, _ in ordered}) != len(ordered)
            or {threshold.category for _, threshold in ordered} != set(_GATE_NAMES)
            or any(
                not name.startswith(f"{threshold.category}.")
                or threshold.comparison not in {"at_least", "at_most"}
                or isinstance(threshold.limit, bool)
                or not isfinite(threshold.limit)
                for name, threshold in ordered
            )
            or policy_name != "bootstrap-gate-policy-v1"
            or dict(ordered) != dict(_BOOTSTRAP_THRESHOLDS_V1)
        ):
            raise ValueError("gate_policy_schema_invalid")
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
        serialized = canonical_json_bytes(payload)
        return cls(
            policy_version_id=sha256_id(serialized),
            policy_name=policy_name,
            thresholds=ordered,
            serialized=serialized,
        )

    @classmethod
    def from_serialized(
        cls, policy_version_id: str, serialized: bytes
    ) -> BootstrapGatePolicyVersion:
        payload = json.loads(serialized)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"policy_name", "thresholds"}
            or not isinstance(payload["policy_name"], str)
            or not isinstance(payload["thresholds"], list)
        ):
            raise ValueError("gate_policy_schema_invalid")
        thresholds: dict[str, GateThreshold] = {}
        for item in payload["thresholds"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"name", "category", "comparison", "limit"}
                or not isinstance(item["name"], str)
                or item["category"] not in _GATE_NAMES
                or item["comparison"] not in {"at_least", "at_most"}
                or isinstance(item["limit"], bool)
                or not isinstance(item["limit"], (int, float))
                or not isfinite(item["limit"])
                or item["name"] in thresholds
            ):
                raise ValueError("gate_policy_schema_invalid")
            thresholds[item["name"]] = GateThreshold(
                category=cast(GateCategory, item["category"]),
                comparison=cast(Literal["at_least", "at_most"], item["comparison"]),
                limit=float(item["limit"]),
            )
        policy = cls.create(
            policy_name=payload["policy_name"],
            thresholds=thresholds,
        )
        if policy.policy_version_id != policy_version_id or policy.serialized != serialized:
            raise ValueError("gate_policy_checksum_mismatch")
        return policy


BOOTSTRAP_MINIMUM_NONINFERIOR_QUARTERS = 6


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
        "economics.turnover_post_cost_condition_passed": GateThreshold(
            "economics", "at_least", 1.0
        ),
        "stability.noninferior_quarter_count": GateThreshold(
            "stability", "at_least", float(BOOTSTRAP_MINIMUM_NONINFERIOR_QUARTERS)
        ),
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


ApprovalMode = Literal["separated_duties", "owner_operated"]


@dataclass(frozen=True)
class ModelApprovalPolicyVersion:
    policy_version_id: str
    policy_name: str
    approval_mode: ApprovalMode
    owner_principal_id: str | None
    serialized: bytes

    @classmethod
    def create(
        cls,
        *,
        policy_name: str,
        approval_mode: ApprovalMode,
        owner_principal_id: str | None,
    ) -> ModelApprovalPolicyVersion:
        if (
            not policy_name
            or approval_mode not in {"separated_duties", "owner_operated"}
            or (approval_mode == "owner_operated" and not owner_principal_id)
            or (approval_mode == "separated_duties" and owner_principal_id is not None)
        ):
            raise ValueError("model_approval_policy_schema_invalid")
        payload = {
            "policy_name": policy_name,
            "approval_mode": approval_mode,
            "owner_principal_id": owner_principal_id,
        }
        serialized = canonical_json_bytes(payload)
        return cls(
            policy_version_id=sha256_id(serialized),
            policy_name=policy_name,
            approval_mode=approval_mode,
            owner_principal_id=owner_principal_id,
            serialized=serialized,
        )

    @classmethod
    def from_serialized(
        cls,
        policy_version_id: str,
        serialized: bytes,
    ) -> ModelApprovalPolicyVersion:
        payload = json.loads(serialized)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"policy_name", "approval_mode", "owner_principal_id"}
            or not isinstance(payload["policy_name"], str)
            or payload["approval_mode"] not in {"separated_duties", "owner_operated"}
            or not (
                payload["owner_principal_id"] is None
                or isinstance(payload["owner_principal_id"], str)
            )
        ):
            raise ValueError("model_approval_policy_schema_invalid")
        policy = cls.create(
            policy_name=payload["policy_name"],
            approval_mode=cast(ApprovalMode, payload["approval_mode"]),
            owner_principal_id=payload["owner_principal_id"],
        )
        if policy.policy_version_id != policy_version_id or policy.serialized != serialized:
            raise ValueError("model_approval_policy_checksum_mismatch")
        return policy


SEPARATED_DUTIES_APPROVAL_POLICY_V1 = ModelApprovalPolicyVersion.create(
    policy_name="separated-duties-model-approval-v1",
    approval_mode="separated_duties",
    owner_principal_id=None,
)


class GatePolicyRepository(Protocol):
    def get(self, policy_version_id: str) -> BootstrapGatePolicyVersion: ...


class GateEvidenceRepository(Protocol):
    def resolve(self, evidence: HardGateEvidence) -> HardGateReportArtifact | None: ...


class ContentAddressedObjectRepository(Protocol):
    def open_by_id(self, object_id: str) -> BinaryIO: ...


class EvaluationReportObjectRepository(ContentAddressedObjectRepository, Protocol):
    def put_verified(
        self,
        stream: BinaryIO,
        *,
        expected_checksum: str,
        metadata: Mapping[str, str],
    ) -> object: ...


class EvaluationReportRepository(Protocol):
    def put(self, report: EvaluationReport) -> None: ...

    def resolve(self, evaluation_report_id: str) -> EvaluationReport | None: ...


class CandidateArtifactRepository(Protocol):
    def put(self, artifact_id: str, serialized: bytes, *, object_kind: str) -> None: ...

    def resolve(self, artifact_id: str) -> bytes | None: ...


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
        except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as error:
            raise KeyError(policy_version_id) from error


class ObjectGateEvidenceRepository:
    def __init__(
        self,
        objects: ContentAddressedObjectRepository | Callable[[], ContentAddressedObjectRepository],
    ) -> None:
        self._objects = objects

    def resolve(self, evidence: HardGateEvidence) -> HardGateReportArtifact | None:
        if len(evidence.evidence_refs) != 1:
            return None
        artifact_id = evidence.evidence_refs[0]
        try:
            objects = self._objects() if callable(self._objects) else self._objects
            serialized = objects.open_by_id(artifact_id).read()
            return HardGateReportArtifact.from_serialized(artifact_id, serialized)
        except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
            return None


class UnavailableGateEvidenceRepository:
    def resolve(self, evidence: HardGateEvidence) -> HardGateReportArtifact | None:
        return None


class InMemoryEvaluationReportRepository:
    def __init__(self) -> None:
        self._reports: dict[str, EvaluationReport] = {}

    def put(self, report: EvaluationReport) -> None:
        existing = self._reports.get(report.evaluation_report_id)
        if existing is not None and existing != report:
            raise ValueError("evaluation_report_id_conflict")
        self._reports[report.evaluation_report_id] = report

    def resolve(self, evaluation_report_id: str) -> EvaluationReport | None:
        return self._reports.get(evaluation_report_id)


class InMemoryCandidateArtifactRepository:
    def __init__(self) -> None:
        self._artifacts: dict[str, bytes] = {}

    def put(self, artifact_id: str, serialized: bytes, *, object_kind: str) -> None:
        if sha256_id(serialized) != artifact_id or not object_kind:
            raise ValueError("candidate_artifact_checksum_mismatch")
        existing = self._artifacts.get(artifact_id)
        if existing is not None and existing != serialized:
            raise ValueError("candidate_artifact_id_conflict")
        self._artifacts[artifact_id] = serialized

    def resolve(self, artifact_id: str) -> bytes | None:
        return self._artifacts.get(artifact_id)


class ObjectCandidateArtifactRepository:
    def __init__(
        self,
        objects: EvaluationReportObjectRepository | Callable[[], EvaluationReportObjectRepository],
    ) -> None:
        self._objects = objects

    def put(self, artifact_id: str, serialized: bytes, *, object_kind: str) -> None:
        objects = self._objects() if callable(self._objects) else self._objects
        objects.put_verified(
            BytesIO(serialized),
            expected_checksum=artifact_id.removeprefix("sha256:"),
            metadata={"content_type": "application/json", "object_kind": object_kind},
        )

    def resolve(self, artifact_id: str) -> bytes | None:
        try:
            objects = self._objects() if callable(self._objects) else self._objects
            return objects.open_by_id(artifact_id).read()
        except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
            return None


class ObjectEvaluationReportRepository:
    def __init__(
        self,
        objects: EvaluationReportObjectRepository | Callable[[], EvaluationReportObjectRepository],
    ) -> None:
        self._objects = objects

    def put(self, report: EvaluationReport) -> None:
        objects = self._objects() if callable(self._objects) else self._objects
        objects.put_verified(
            BytesIO(report.serialized),
            expected_checksum=report.evaluation_report_id.removeprefix("sha256:"),
            metadata={
                "content_type": "application/json",
                "object_kind": "bootstrap_evaluation_report",
            },
        )

    def resolve(self, evaluation_report_id: str) -> EvaluationReport | None:
        try:
            objects = self._objects() if callable(self._objects) else self._objects
            serialized = objects.open_by_id(evaluation_report_id).read()
            return EvaluationReport.from_serialized(evaluation_report_id, serialized)
        except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class RecordCandidate:
    command_id: str
    candidate_bundle: CandidateEvidenceBundle
    expected_version: int
    occurred_at: datetime


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
    candidate_id: str
    artifact_id: str
    evaluation_report_id: str
    gate_decision_id: str
    approval_decision_id: str
    approval_policy_version_id: str
    expected_assignment: str
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
        candidate_id: str,
        artifact_id: str,
        evaluation_report_id: str,
        gate_decision_id: str,
        approval_decision_id: str,
        approval_policy_version_id: str,
        expected_assignment: str,
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
        identifiers = (
            shadow_run_id,
            candidate_id,
            artifact_id,
            evaluation_report_id,
            gate_decision_id,
            approval_decision_id,
            approval_policy_version_id,
            expected_assignment,
        )
        verification_flags = (
            cold_load_checksum_verified,
            schema_compatible,
            probability_invariants_verified,
            comparison_completed,
            source_policy_verified,
        )
        if (
            any(not isinstance(value, str) or not value for value in identifiers)
            or (
                previous_shadow_run_id is not None
                and (not isinstance(previous_shadow_run_id, str) or not previous_shadow_run_id)
            )
            or type(eligible_eod_date) is not date
            or not isinstance(markets, tuple)
            or len(markets) != 2
            or set(markets) != {"XTAI", "XNAS"}
            or any(not isinstance(value, bool) for value in verification_flags)
            or isinstance(cpu_prediction_seconds, bool)
            or not isinstance(cpu_prediction_seconds, (int, float))
        ):
            raise ValueError("shadow_evidence_schema_invalid")
        if not isfinite(cpu_prediction_seconds) or cpu_prediction_seconds < 0:
            raise ValueError("shadow_cpu_prediction_seconds_invalid")
        ordered_markets = tuple(sorted(markets))
        normalized_cpu_seconds = float(cpu_prediction_seconds)
        payload = {
            "shadow_run_id": shadow_run_id,
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "evaluation_report_id": evaluation_report_id,
            "gate_decision_id": gate_decision_id,
            "approval_decision_id": approval_decision_id,
            "approval_policy_version_id": approval_policy_version_id,
            "expected_assignment": expected_assignment,
            "eligible_eod_date": eligible_eod_date.isoformat(),
            "previous_shadow_run_id": previous_shadow_run_id,
            "markets": ordered_markets,
            "cold_load_checksum_verified": cold_load_checksum_verified,
            "schema_compatible": schema_compatible,
            "probability_invariants_verified": probability_invariants_verified,
            "comparison_completed": comparison_completed,
            "source_policy_verified": source_policy_verified,
            "cpu_prediction_seconds": normalized_cpu_seconds,
        }
        return cls(
            evidence_id=_content_id("shadow_run_evidence", payload),
            shadow_run_id=shadow_run_id,
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            evaluation_report_id=evaluation_report_id,
            gate_decision_id=gate_decision_id,
            approval_decision_id=approval_decision_id,
            approval_policy_version_id=approval_policy_version_id,
            expected_assignment=expected_assignment,
            eligible_eod_date=eligible_eod_date,
            previous_shadow_run_id=previous_shadow_run_id,
            markets=ordered_markets,
            cold_load_checksum_verified=cold_load_checksum_verified,
            schema_compatible=schema_compatible,
            probability_invariants_verified=probability_invariants_verified,
            comparison_completed=comparison_completed,
            source_policy_verified=source_policy_verified,
            cpu_prediction_seconds=normalized_cpu_seconds,
        )

    def is_content_addressed(self) -> bool:
        try:
            rebuilt = self.create(
                shadow_run_id=self.shadow_run_id,
                candidate_id=self.candidate_id,
                artifact_id=self.artifact_id,
                evaluation_report_id=self.evaluation_report_id,
                gate_decision_id=self.gate_decision_id,
                approval_decision_id=self.approval_decision_id,
                approval_policy_version_id=self.approval_policy_version_id,
                expected_assignment=self.expected_assignment,
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
        except (TypeError, ValueError):
            return False
        return rebuilt == self


def _shadow_record_payload(
    evidence: ShadowRunEvidence,
    *,
    eligible_cycle_count: int,
    blocked_reason: str | None,
) -> dict[str, object]:
    return {
        "shadow_run_id": evidence.shadow_run_id,
        "shadow_evidence_id": evidence.evidence_id,
        "eligible_eod_date": evidence.eligible_eod_date.isoformat(),
        "previous_shadow_run_id": evidence.previous_shadow_run_id,
        "candidate_id": evidence.candidate_id,
        "artifact_id": evidence.artifact_id,
        "evaluation_report_id": evidence.evaluation_report_id,
        "gate_decision_id": evidence.gate_decision_id,
        "approval_decision_id": evidence.approval_decision_id,
        "approval_policy_version_id": evidence.approval_policy_version_id,
        "expected_assignment": evidence.expected_assignment,
        "market_eligibility": list(evidence.markets),
        "cold_load_checksum_verified": evidence.cold_load_checksum_verified,
        "schema_compatible": evidence.schema_compatible,
        "probability_invariants_verified": evidence.probability_invariants_verified,
        "comparison_completed": evidence.comparison_completed,
        "source_policy_verified": evidence.source_policy_verified,
        "cpu_prediction_seconds": evidence.cpu_prediction_seconds,
        "eligible_cycle_count": eligible_cycle_count,
        "blocked_reason": blocked_reason,
        "production_history_written": False,
    }


@dataclass(frozen=True)
class ShadowRunRecord:
    shadow_record_id: str
    evidence: ShadowRunEvidence
    eligible_cycle_count: int
    blocked_reason: str | None
    production_history_written: Literal[False] = False

    @classmethod
    def create(
        cls,
        evidence: ShadowRunEvidence,
        *,
        eligible_cycle_count: int,
        blocked_reason: str | None,
    ) -> ShadowRunRecord:
        if (
            not evidence.is_content_addressed()
            or isinstance(eligible_cycle_count, bool)
            or not isinstance(eligible_cycle_count, int)
            or eligible_cycle_count < 0
            or eligible_cycle_count > 5
            or (
                blocked_reason is not None
                and (not isinstance(blocked_reason, str) or not blocked_reason)
            )
        ):
            raise ValueError("shadow_run_record_schema_invalid")
        payload = _shadow_record_payload(
            evidence,
            eligible_cycle_count=eligible_cycle_count,
            blocked_reason=blocked_reason,
        )
        return cls(
            shadow_record_id=_content_id("shadow_run_record", payload),
            evidence=evidence,
            eligible_cycle_count=eligible_cycle_count,
            blocked_reason=blocked_reason,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "shadow_record_id": self.shadow_record_id,
            **_shadow_record_payload(
                self.evidence,
                eligible_cycle_count=self.eligible_cycle_count,
                blocked_reason=self.blocked_reason,
            ),
        }

    @classmethod
    def from_payload(cls, payload: object) -> ShadowRunRecord:
        expected = {
            "shadow_record_id",
            "shadow_run_id",
            "shadow_evidence_id",
            "eligible_eod_date",
            "previous_shadow_run_id",
            "candidate_id",
            "artifact_id",
            "evaluation_report_id",
            "gate_decision_id",
            "approval_decision_id",
            "approval_policy_version_id",
            "expected_assignment",
            "market_eligibility",
            "cold_load_checksum_verified",
            "schema_compatible",
            "probability_invariants_verified",
            "comparison_completed",
            "source_policy_verified",
            "cpu_prediction_seconds",
            "eligible_cycle_count",
            "blocked_reason",
            "production_history_written",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or not isinstance(payload["shadow_record_id"], str)
            or not isinstance(payload["shadow_evidence_id"], str)
            or not isinstance(payload["eligible_eod_date"], str)
            or not isinstance(payload["market_eligibility"], list)
            or payload["production_history_written"] is not False
        ):
            raise ValueError("shadow_run_record_schema_invalid")
        try:
            evidence = ShadowRunEvidence.create(
                shadow_run_id=payload["shadow_run_id"],
                candidate_id=payload["candidate_id"],
                artifact_id=payload["artifact_id"],
                evaluation_report_id=payload["evaluation_report_id"],
                gate_decision_id=payload["gate_decision_id"],
                approval_decision_id=payload["approval_decision_id"],
                approval_policy_version_id=payload["approval_policy_version_id"],
                expected_assignment=payload["expected_assignment"],
                eligible_eod_date=date.fromisoformat(payload["eligible_eod_date"]),
                previous_shadow_run_id=payload["previous_shadow_run_id"],
                markets=tuple(payload["market_eligibility"]),
                cold_load_checksum_verified=payload["cold_load_checksum_verified"],
                schema_compatible=payload["schema_compatible"],
                probability_invariants_verified=payload["probability_invariants_verified"],
                comparison_completed=payload["comparison_completed"],
                source_policy_verified=payload["source_policy_verified"],
                cpu_prediction_seconds=payload["cpu_prediction_seconds"],
            )
            record = cls.create(
                evidence,
                eligible_cycle_count=payload["eligible_cycle_count"],
                blocked_reason=payload["blocked_reason"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("shadow_run_record_schema_invalid") from error
        if evidence.evidence_id != payload["shadow_evidence_id"]:
            raise ValueError("shadow_run_record_checksum_mismatch")
        if record.shadow_record_id != payload["shadow_record_id"]:
            raise ValueError("shadow_run_record_checksum_mismatch")
        return record


class ShadowEligibilityVerifier(Protocol):
    def verify_eligible_eod(self, evidence: ShadowRunEvidence) -> bool: ...


class UnavailableShadowEligibilityVerifier:
    def verify_eligible_eod(self, evidence: ShadowRunEvidence) -> bool:
        return False


class ShadowRunVerifier(Protocol):
    def verify_shadow_run(self, evidence: ShadowRunEvidence) -> bool: ...


class UnavailableShadowRunVerifier:
    def verify_shadow_run(self, evidence: ShadowRunEvidence) -> bool:
        return False


@dataclass(frozen=True)
class RecordShadowEod:
    command_id: str
    model_family_id: str
    candidate_id: str
    evidence: ShadowRunEvidence
    expected_version: int
    occurred_at: datetime


@dataclass(frozen=True)
class PromotionReadiness:
    evidence_id: str
    candidate_id: str
    artifact_id: str
    evaluation_report_id: str
    feature_schema_id: str
    runtime_id: str
    source_policy_manifest_id: str
    rollback_assignment_id: str | None
    effective_from_batch_id: str

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        artifact_id: str,
        evaluation_report_id: str,
        feature_schema_id: str,
        runtime_id: str,
        source_policy_manifest_id: str,
        rollback_assignment_id: str | None,
        effective_from_batch_id: str,
    ) -> PromotionReadiness:
        required = (
            candidate_id,
            artifact_id,
            evaluation_report_id,
            feature_schema_id,
            runtime_id,
            source_policy_manifest_id,
            effective_from_batch_id,
        )
        if any(not isinstance(value, str) or not value for value in required) or (
            rollback_assignment_id is not None
            and (not isinstance(rollback_assignment_id, str) or not rollback_assignment_id)
        ):
            raise ValueError("promotion_readiness_schema_invalid")
        payload = {
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "evaluation_report_id": evaluation_report_id,
            "feature_schema_id": feature_schema_id,
            "runtime_id": runtime_id,
            "source_policy_manifest_id": source_policy_manifest_id,
            "rollback_assignment_id": rollback_assignment_id,
            "effective_from_batch_id": effective_from_batch_id,
        }
        return cls(
            evidence_id=_content_id("promotion_readiness", payload),
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            evaluation_report_id=evaluation_report_id,
            feature_schema_id=feature_schema_id,
            runtime_id=runtime_id,
            source_policy_manifest_id=source_policy_manifest_id,
            rollback_assignment_id=rollback_assignment_id,
            effective_from_batch_id=effective_from_batch_id,
        )

    def is_content_addressed(self) -> bool:
        rebuilt = type(self).create(
            candidate_id=self.candidate_id,
            artifact_id=self.artifact_id,
            evaluation_report_id=self.evaluation_report_id,
            feature_schema_id=self.feature_schema_id,
            runtime_id=self.runtime_id,
            source_policy_manifest_id=self.source_policy_manifest_id,
            rollback_assignment_id=self.rollback_assignment_id,
            effective_from_batch_id=self.effective_from_batch_id,
        )
        return rebuilt.evidence_id == self.evidence_id


@dataclass(frozen=True)
class PromoteProductionAssignment:
    command_id: str
    model_family_id: str
    candidate_id: str
    expected_assignment: str
    readiness: PromotionReadiness
    expected_version: int
    occurred_at: datetime


@dataclass(frozen=True)
class RollbackProductionAssignment:
    command_id: str
    model_family_id: str
    expected_assignment: str
    rollback_target_assignment_id: str
    effective_from_batch_id: str
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
    | PromoteProductionAssignment
    | RollbackProductionAssignment
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
    hard_gate_report_id: str | None = None
    submitted_hard_gate_measurements: tuple[GateMeasurement, ...] = ()
    submitted_improvement_percentage_points: float | None = None
    verified_improvement_percentage_points: float | None = None
    verified_hard_gate_measurements: tuple[GateMeasurement, ...] = ()
    serving_status: Literal["blocked"] = "blocked"

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        artifact_id: str,
        evaluation_report_id: str,
        policy_version_id: str,
        status: Literal["passed", "failed"],
        failed_gates: tuple[str, ...],
        hard_gate_evidence_id: str | None = None,
        hard_gate_evidence_refs: tuple[str, ...] = (),
        hard_gate_report_id: str | None = None,
        submitted_hard_gate_measurements: tuple[GateMeasurement, ...] = (),
        submitted_improvement_percentage_points: float | None = None,
        verified_improvement_percentage_points: float | None = None,
        verified_hard_gate_measurements: tuple[GateMeasurement, ...] = (),
    ) -> GateDecision:
        identifiers = (candidate_id, artifact_id, evaluation_report_id, policy_version_id)
        optional_identifiers = (hard_gate_evidence_id, hard_gate_report_id)
        measurement_groups = (
            submitted_hard_gate_measurements,
            verified_hard_gate_measurements,
        )
        improvements = (
            submitted_improvement_percentage_points,
            verified_improvement_percentage_points,
        )
        if (
            any(not isinstance(value, str) or not value for value in identifiers)
            or status not in {"passed", "failed"}
            or not isinstance(failed_gates, tuple)
            or any(not isinstance(value, str) or not value for value in failed_gates)
            or len(set(failed_gates)) != len(failed_gates)
            or (status == "passed") != (not failed_gates)
            or any(
                value is not None and (not isinstance(value, str) or not value)
                for value in optional_identifiers
            )
            or not isinstance(hard_gate_evidence_refs, tuple)
            or any(not isinstance(value, str) or not value for value in hard_gate_evidence_refs)
            or len(set(hard_gate_evidence_refs)) != len(hard_gate_evidence_refs)
            or any(
                not cls._measurements_are_valid(measurements) for measurements in measurement_groups
            )
            or any(
                value is not None
                and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not isfinite(value)
                )
                for value in improvements
            )
        ):
            raise ValueError("gate_decision_schema_invalid")
        if status == "passed":
            verified_by_name = {
                measurement.name: measurement.value
                for measurement in verified_hard_gate_measurements
            }
            approved_thresholds = dict(_BOOTSTRAP_THRESHOLDS_V1)
            if (
                policy_version_id != BOOTSTRAP_GATE_POLICY_V1.policy_version_id
                or hard_gate_evidence_id is None
                or hard_gate_report_id is None
                or hard_gate_evidence_refs != (hard_gate_report_id,)
                or submitted_hard_gate_measurements != verified_hard_gate_measurements
                or set(verified_by_name) != set(approved_thresholds)
                or any(
                    not threshold.passes(verified_by_name[name])
                    for name, threshold in approved_thresholds.items()
                )
                or submitted_improvement_percentage_points is None
                or verified_improvement_percentage_points is None
                or submitted_improvement_percentage_points != verified_improvement_percentage_points
                or verified_improvement_percentage_points < 1.0
            ):
                raise ValueError("gate_decision_schema_invalid")
        normalized_submitted_improvement = (
            float(submitted_improvement_percentage_points)
            if submitted_improvement_percentage_points is not None
            else None
        )
        normalized_verified_improvement = (
            float(verified_improvement_percentage_points)
            if verified_improvement_percentage_points is not None
            else None
        )
        payload = cls._payload_without_id(
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            evaluation_report_id=evaluation_report_id,
            policy_version_id=policy_version_id,
            status=status,
            failed_gates=failed_gates,
            hard_gate_evidence_id=hard_gate_evidence_id,
            hard_gate_evidence_refs=hard_gate_evidence_refs,
            hard_gate_report_id=hard_gate_report_id,
            submitted_hard_gate_measurements=submitted_hard_gate_measurements,
            submitted_improvement_percentage_points=normalized_submitted_improvement,
            verified_improvement_percentage_points=normalized_verified_improvement,
            verified_hard_gate_measurements=verified_hard_gate_measurements,
        )
        return cls(
            gate_decision_id=_content_id("gate_decision", payload),
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            evaluation_report_id=evaluation_report_id,
            policy_version_id=policy_version_id,
            status=status,
            failed_gates=failed_gates,
            hard_gate_evidence_id=hard_gate_evidence_id,
            hard_gate_evidence_refs=hard_gate_evidence_refs,
            hard_gate_report_id=hard_gate_report_id,
            submitted_hard_gate_measurements=submitted_hard_gate_measurements,
            submitted_improvement_percentage_points=normalized_submitted_improvement,
            verified_improvement_percentage_points=normalized_verified_improvement,
            verified_hard_gate_measurements=verified_hard_gate_measurements,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "gate_decision_id": self.gate_decision_id,
            **self._payload_without_id(
                candidate_id=self.candidate_id,
                artifact_id=self.artifact_id,
                evaluation_report_id=self.evaluation_report_id,
                policy_version_id=self.policy_version_id,
                status=self.status,
                failed_gates=self.failed_gates,
                hard_gate_evidence_id=self.hard_gate_evidence_id,
                hard_gate_evidence_refs=self.hard_gate_evidence_refs,
                hard_gate_report_id=self.hard_gate_report_id,
                submitted_hard_gate_measurements=self.submitted_hard_gate_measurements,
                submitted_improvement_percentage_points=(
                    self.submitted_improvement_percentage_points
                ),
                verified_improvement_percentage_points=(
                    self.verified_improvement_percentage_points
                ),
                verified_hard_gate_measurements=self.verified_hard_gate_measurements,
            ),
        }

    @classmethod
    def from_payload(cls, payload: object) -> GateDecision:
        expected = {
            "gate_decision_id",
            "candidate_id",
            "artifact_id",
            "evaluation_report_id",
            "policy_version_id",
            "status",
            "failed_gates",
            "hard_gate_evidence_id",
            "hard_gate_evidence_refs",
            "hard_gate_report_id",
            "submitted_hard_gate_measurements",
            "submitted_improvement_percentage_points",
            "verified_improvement_percentage_points",
            "verified_hard_gate_measurements",
            "serving_status",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("gate_decision_schema_invalid")
        try:
            decision = cls.create(
                candidate_id=payload["candidate_id"],
                artifact_id=payload["artifact_id"],
                evaluation_report_id=payload["evaluation_report_id"],
                policy_version_id=payload["policy_version_id"],
                status=payload["status"],
                failed_gates=cls._string_tuple(payload["failed_gates"]),
                hard_gate_evidence_id=payload["hard_gate_evidence_id"],
                hard_gate_evidence_refs=cls._string_tuple(payload["hard_gate_evidence_refs"]),
                hard_gate_report_id=payload["hard_gate_report_id"],
                submitted_hard_gate_measurements=cls._measurements_from_payload(
                    payload["submitted_hard_gate_measurements"]
                ),
                submitted_improvement_percentage_points=payload[
                    "submitted_improvement_percentage_points"
                ],
                verified_improvement_percentage_points=payload[
                    "verified_improvement_percentage_points"
                ],
                verified_hard_gate_measurements=cls._measurements_from_payload(
                    payload["verified_hard_gate_measurements"]
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("gate_decision_schema_invalid") from error
        if (
            payload["serving_status"] != "blocked"
            or not isinstance(payload["gate_decision_id"], str)
            or decision.gate_decision_id != payload["gate_decision_id"]
        ):
            raise ValueError("gate_decision_checksum_mismatch")
        return decision

    @staticmethod
    def _measurements_are_valid(measurements: object) -> bool:
        return (
            isinstance(measurements, tuple)
            and all(
                isinstance(item, GateMeasurement)
                and isinstance(item.name, str)
                and bool(item.name)
                and not isinstance(item.value, bool)
                and isinstance(item.value, (int, float))
                and isfinite(item.value)
                for item in measurements
            )
            and len({item.name for item in measurements}) == len(measurements)
        )

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("gate_decision_schema_invalid")
        return tuple(value)

    @staticmethod
    def _measurements_from_payload(value: object) -> tuple[GateMeasurement, ...]:
        if not isinstance(value, list):
            raise ValueError("gate_decision_schema_invalid")
        measurements: list[GateMeasurement] = []
        for item in value:
            if (
                not isinstance(item, dict)
                or set(item) != {"name", "value"}
                or not isinstance(item["name"], str)
                or isinstance(item["value"], bool)
                or not isinstance(item["value"], (int, float))
            ):
                raise ValueError("gate_decision_schema_invalid")
            measurements.append(GateMeasurement(item["name"], float(item["value"])))
        return tuple(measurements)

    @staticmethod
    def _payload_without_id(
        *,
        candidate_id: str,
        artifact_id: str,
        evaluation_report_id: str,
        policy_version_id: str,
        status: Literal["passed", "failed"],
        failed_gates: tuple[str, ...],
        hard_gate_evidence_id: str | None,
        hard_gate_evidence_refs: tuple[str, ...],
        hard_gate_report_id: str | None,
        submitted_hard_gate_measurements: tuple[GateMeasurement, ...],
        submitted_improvement_percentage_points: float | None,
        verified_improvement_percentage_points: float | None,
        verified_hard_gate_measurements: tuple[GateMeasurement, ...],
    ) -> dict[str, object]:
        return {
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "evaluation_report_id": evaluation_report_id,
            "policy_version_id": policy_version_id,
            "status": status,
            "failed_gates": list(failed_gates),
            "hard_gate_evidence_id": hard_gate_evidence_id,
            "hard_gate_evidence_refs": list(hard_gate_evidence_refs),
            "hard_gate_report_id": hard_gate_report_id,
            "submitted_hard_gate_measurements": [
                {"name": item.name, "value": item.value}
                for item in submitted_hard_gate_measurements
            ],
            "submitted_improvement_percentage_points": (submitted_improvement_percentage_points),
            "verified_improvement_percentage_points": verified_improvement_percentage_points,
            "verified_hard_gate_measurements": [
                {"name": item.name, "value": item.value} for item in verified_hard_gate_measurements
            ],
            "serving_status": "blocked",
        }


@dataclass(frozen=True)
class ApprovalDecision:
    approval_decision_id: str
    candidate_id: str
    artifact_id: str
    evaluation_report_id: str
    policy_version_id: str
    gate_decision_id: str
    approval_policy_version_id: str
    approval_mode: ApprovalMode
    approval_policy_owner_principal_id: str | None
    independent_review: bool
    approver_id: str
    requested_decision: Literal["approved", "rejected"]
    decision: Literal["approved", "rejected"]
    reason: str
    expected_assignment: str
    decided_at: datetime
    expires_at: datetime
    invalidated_reason: str | None

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        artifact_id: str,
        evaluation_report_id: str,
        policy_version_id: str,
        gate_decision_id: str,
        approval_policy_version_id: str,
        approval_mode: ApprovalMode,
        approval_policy_owner_principal_id: str | None,
        independent_review: bool,
        approver_id: str,
        requested_decision: Literal["approved", "rejected"],
        decision: Literal["approved", "rejected"],
        reason: str,
        expected_assignment: str,
        decided_at: datetime,
        expires_at: datetime,
        invalidated_reason: str | None,
    ) -> ApprovalDecision:
        strings = (
            candidate_id,
            artifact_id,
            evaluation_report_id,
            policy_version_id,
            gate_decision_id,
            approval_policy_version_id,
            approver_id,
            expected_assignment,
        )
        if (
            any(not isinstance(value, str) or not value for value in strings)
            or approval_mode not in {"separated_duties", "owner_operated"}
            or (
                approval_mode == "separated_duties"
                and approval_policy_owner_principal_id is not None
            )
            or (
                approval_mode == "owner_operated"
                and (
                    not isinstance(approval_policy_owner_principal_id, str)
                    or not approval_policy_owner_principal_id
                    or independent_review is not False
                )
            )
            or not isinstance(independent_review, bool)
            or requested_decision not in {"approved", "rejected"}
            or decision not in {"approved", "rejected"}
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(decided_at, datetime)
            or decided_at.tzinfo is None
            or not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
            or expires_at != decided_at + timedelta(days=7)
            or (invalidated_reason is not None and not isinstance(invalidated_reason, str))
            or (decision == "approved" and invalidated_reason is not None)
            or (decision == "rejected" and invalidated_reason is None)
            or (
                decision == "approved"
                and approval_mode == "owner_operated"
                and approver_id != approval_policy_owner_principal_id
            )
            or (
                decision == "approved"
                and approval_mode == "separated_duties"
                and independent_review is not True
            )
            or (decision == "approved" and requested_decision != "approved")
        ):
            raise ValueError("approval_decision_schema_invalid")
        payload = {
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "evaluation_report_id": evaluation_report_id,
            "policy_version_id": policy_version_id,
            "gate_decision_id": gate_decision_id,
            "approval_policy_version_id": approval_policy_version_id,
            "approval_mode": approval_mode,
            "approval_policy_owner_principal_id": approval_policy_owner_principal_id,
            "independent_review": independent_review,
            "approver_id": approver_id,
            "requested_decision": requested_decision,
            "decision": decision,
            "reason": reason,
            "expected_assignment": expected_assignment,
            "decided_at": decided_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "invalidated_reason": invalidated_reason,
        }
        return cls(
            approval_decision_id=_content_id("approval_decision", payload),
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            evaluation_report_id=evaluation_report_id,
            policy_version_id=policy_version_id,
            gate_decision_id=gate_decision_id,
            approval_policy_version_id=approval_policy_version_id,
            approval_mode=approval_mode,
            approval_policy_owner_principal_id=approval_policy_owner_principal_id,
            independent_review=independent_review,
            approver_id=approver_id,
            requested_decision=requested_decision,
            decision=decision,
            reason=reason,
            expected_assignment=expected_assignment,
            decided_at=decided_at,
            expires_at=expires_at,
            invalidated_reason=invalidated_reason,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "approval_decision_id": self.approval_decision_id,
            "candidate_id": self.candidate_id,
            "artifact_id": self.artifact_id,
            "evaluation_report_id": self.evaluation_report_id,
            "policy_version_id": self.policy_version_id,
            "gate_decision_id": self.gate_decision_id,
            "approval_policy_version_id": self.approval_policy_version_id,
            "approval_mode": self.approval_mode,
            "approval_policy_owner_principal_id": self.approval_policy_owner_principal_id,
            "independent_review": self.independent_review,
            "approver_id": self.approver_id,
            "requested_decision": self.requested_decision,
            "decision": self.decision,
            "reason": self.reason,
            "expected_assignment": self.expected_assignment,
            "decided_at": self.decided_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "invalidated_reason": self.invalidated_reason,
        }

    @classmethod
    def from_payload(cls, payload: object) -> ApprovalDecision:
        expected = {
            "approval_decision_id",
            "candidate_id",
            "artifact_id",
            "evaluation_report_id",
            "policy_version_id",
            "gate_decision_id",
            "approval_policy_version_id",
            "approval_mode",
            "approval_policy_owner_principal_id",
            "independent_review",
            "approver_id",
            "requested_decision",
            "decision",
            "reason",
            "expected_assignment",
            "decided_at",
            "expires_at",
            "invalidated_reason",
        }
        string_fields = (
            "approval_decision_id",
            "candidate_id",
            "artifact_id",
            "evaluation_report_id",
            "policy_version_id",
            "gate_decision_id",
            "approval_policy_version_id",
            "approver_id",
            "reason",
            "expected_assignment",
            "decided_at",
            "expires_at",
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or any(not isinstance(payload[field], str) for field in string_fields)
            or not isinstance(payload["independent_review"], bool)
            or payload["approval_mode"] not in {"separated_duties", "owner_operated"}
            or payload["requested_decision"] not in {"approved", "rejected"}
            or payload["decision"] not in {"approved", "rejected"}
            or (
                payload["approval_policy_owner_principal_id"] is not None
                and not isinstance(payload["approval_policy_owner_principal_id"], str)
            )
            or (
                payload["invalidated_reason"] is not None
                and not isinstance(payload["invalidated_reason"], str)
            )
        ):
            raise ValueError("approval_decision_schema_invalid")
        rebuilt = cls.create(
            candidate_id=payload["candidate_id"],
            artifact_id=payload["artifact_id"],
            evaluation_report_id=payload["evaluation_report_id"],
            policy_version_id=payload["policy_version_id"],
            gate_decision_id=payload["gate_decision_id"],
            approval_policy_version_id=payload["approval_policy_version_id"],
            approval_mode=cast(ApprovalMode, payload["approval_mode"]),
            approval_policy_owner_principal_id=payload["approval_policy_owner_principal_id"],
            independent_review=payload["independent_review"],
            approver_id=payload["approver_id"],
            requested_decision=cast(Literal["approved", "rejected"], payload["requested_decision"]),
            decision=cast(Literal["approved", "rejected"], payload["decision"]),
            reason=payload["reason"],
            expected_assignment=payload["expected_assignment"],
            decided_at=datetime.fromisoformat(payload["decided_at"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
            invalidated_reason=payload["invalidated_reason"],
        )
        if rebuilt.approval_decision_id != payload["approval_decision_id"]:
            raise ValueError("approval_decision_checksum_mismatch")
        return rebuilt

    def matches_policy(self, policy: ModelApprovalPolicyVersion) -> bool:
        return (
            self.approval_policy_version_id == policy.policy_version_id
            and self.approval_mode == policy.approval_mode
            and self.approval_policy_owner_principal_id == policy.owner_principal_id
        )


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


def _verified_shadow_lineage(
    events: tuple[LifecycleEvent, ...],
    *,
    candidate_id: str,
    artifact_id: str,
    evaluation_report_id: str,
    gate_decision_id: str,
    approval_decision_id: str,
    approval_policy_version_id: str,
    expected_assignment: str,
    not_before: datetime | None = None,
) -> tuple[ShadowRunRecord, ...]:
    lineage: list[ShadowRunRecord] = []
    for event in events:
        if event.event_kind != "ShadowEodRecorded":
            continue
        record = ShadowRunRecord.from_payload(json.loads(event.payload_json))
        evidence = record.evidence
        if evidence.candidate_id != candidate_id:
            continue
        if (
            evidence.artifact_id != artifact_id
            or evidence.evaluation_report_id != evaluation_report_id
            or evidence.gate_decision_id != gate_decision_id
            or evidence.approval_decision_id != approval_decision_id
            or evidence.approval_policy_version_id != approval_policy_version_id
            or evidence.expected_assignment != expected_assignment
        ):
            continue
        previous = lineage[-1].evidence if lineage else None
        verification_flags = (
            evidence.cold_load_checksum_verified,
            evidence.schema_compatible,
            evidence.probability_invariants_verified,
            evidence.comparison_completed,
            evidence.source_policy_verified,
        )
        if (
            record.blocked_reason is not None
            or record.eligible_cycle_count != len(lineage) + 1
            or evidence.previous_shadow_run_id
            != (previous.shadow_run_id if previous is not None else None)
            or (previous is not None and evidence.eligible_eod_date <= previous.eligible_eod_date)
            or not all(verification_flags)
            or evidence.cpu_prediction_seconds > 600
            or event.occurred_at.date() < evidence.eligible_eod_date
            or (
                not_before is not None
                and (
                    evidence.eligible_eod_date < not_before.date() or event.occurred_at < not_before
                )
            )
        ):
            raise ValueError("shadow_history_invalid")
        lineage.append(record)
    return tuple(lineage)


@dataclass(frozen=True)
class ServingAssignment:
    assignment_id: str
    model_family_id: str
    candidate_id: str
    artifact_id: str
    previous_assignment_id: str | None
    readiness_evidence_id: str
    effective_from_batch_id: str
    assigned_at: datetime

    @classmethod
    def create(
        cls,
        *,
        model_family_id: str,
        candidate_id: str,
        artifact_id: str,
        previous_assignment_id: str | None,
        readiness_evidence_id: str,
        effective_from_batch_id: str,
        assigned_at: datetime,
    ) -> ServingAssignment:
        payload = {
            "model_family_id": model_family_id,
            "candidate_id": candidate_id,
            "artifact_id": artifact_id,
            "previous_assignment_id": previous_assignment_id,
            "readiness_evidence_id": readiness_evidence_id,
            "effective_from_batch_id": effective_from_batch_id,
            "assigned_at": assigned_at.isoformat(),
        }
        if any(
            not isinstance(value, str) or not value
            for value in (
                model_family_id,
                candidate_id,
                artifact_id,
                readiness_evidence_id,
                effective_from_batch_id,
            )
        ) or (
            previous_assignment_id is not None
            and (not isinstance(previous_assignment_id, str) or not previous_assignment_id)
        ):
            raise ValueError("serving_assignment_schema_invalid")
        return cls(
            assignment_id=_content_id("serving_assignment", payload),
            model_family_id=model_family_id,
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            previous_assignment_id=previous_assignment_id,
            readiness_evidence_id=readiness_evidence_id,
            effective_from_batch_id=effective_from_batch_id,
            assigned_at=assigned_at,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "model_family_id": self.model_family_id,
            "candidate_id": self.candidate_id,
            "artifact_id": self.artifact_id,
            "previous_assignment_id": self.previous_assignment_id,
            "readiness_evidence_id": self.readiness_evidence_id,
            "effective_from_batch_id": self.effective_from_batch_id,
            "assigned_at": self.assigned_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: object) -> ServingAssignment:
        if not isinstance(payload, dict):
            raise ValueError("serving_assignment_schema_invalid")
        required = {
            "assignment_id",
            "model_family_id",
            "candidate_id",
            "artifact_id",
            "previous_assignment_id",
            "readiness_evidence_id",
            "effective_from_batch_id",
            "assigned_at",
        }
        if set(payload) != required:
            raise ValueError("serving_assignment_schema_invalid")
        try:
            rebuilt = cls.create(
                model_family_id=cast(str, payload["model_family_id"]),
                candidate_id=cast(str, payload["candidate_id"]),
                artifact_id=cast(str, payload["artifact_id"]),
                previous_assignment_id=cast(str | None, payload["previous_assignment_id"]),
                readiness_evidence_id=cast(str, payload["readiness_evidence_id"]),
                effective_from_batch_id=cast(str, payload["effective_from_batch_id"]),
                assigned_at=datetime.fromisoformat(cast(str, payload["assigned_at"])),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("serving_assignment_schema_invalid") from error
        if payload["assignment_id"] != rebuilt.assignment_id:
            raise ValueError("serving_assignment_schema_invalid")
        return rebuilt


@dataclass(frozen=True)
class PinServingAssignment:
    model_family_id: str
    forecast_batch_id: str
    market: Literal["XTAI", "XNAS"]
    information_cutoff: datetime
    started_at: datetime


@dataclass(frozen=True)
class PinnedServingAssignment:
    pin_id: str
    model_family_id: str
    forecast_batch_id: str
    market: Literal["XTAI", "XNAS"]
    information_cutoff: datetime
    started_at: datetime
    assignment: ServingAssignment

    @classmethod
    def create(
        cls,
        request: PinServingAssignment,
        assignment: ServingAssignment,
    ) -> PinnedServingAssignment:
        if (
            not request.model_family_id
            or not request.forecast_batch_id
            or request.market not in {"XTAI", "XNAS"}
            or request.information_cutoff.tzinfo is None
            or request.started_at.tzinfo is None
            or assignment.model_family_id != request.model_family_id
            or assignment.assigned_at > request.started_at
        ):
            raise ValueError("serving_assignment_pin_invalid")
        payload = {
            "model_family_id": request.model_family_id,
            "forecast_batch_id": request.forecast_batch_id,
            "market": request.market,
            "information_cutoff": request.information_cutoff.isoformat(),
            "started_at": request.started_at.isoformat(),
            "assignment_id": assignment.assignment_id,
        }
        return cls(
            pin_id=_content_id("serving_assignment_pin", payload),
            model_family_id=request.model_family_id,
            forecast_batch_id=request.forecast_batch_id,
            market=request.market,
            information_cutoff=request.information_cutoff,
            started_at=request.started_at,
            assignment=assignment,
        )


class AssignmentPinStore(Protocol):
    def get(
        self,
        *,
        model_family_id: str,
        forecast_batch_id: str,
        market: str,
    ) -> PinnedServingAssignment | None: ...

    def put(self, pin: PinnedServingAssignment) -> PinnedServingAssignment: ...

    def assignment_is_active(self, assignment_id: str) -> bool: ...


class InMemoryAssignmentPinStore:
    def __init__(self) -> None:
        self._pins: dict[tuple[str, str, str], PinnedServingAssignment] = {}

    def get(
        self,
        *,
        model_family_id: str,
        forecast_batch_id: str,
        market: str,
    ) -> PinnedServingAssignment | None:
        return self._pins.get((model_family_id, forecast_batch_id, market))

    def put(self, pin: PinnedServingAssignment) -> PinnedServingAssignment:
        key = (pin.model_family_id, pin.forecast_batch_id, pin.market)
        existing = self._pins.get(key)
        if existing is not None and existing != pin:
            raise LifecycleConflict("serving_assignment_pin_conflict")
        self._pins[key] = pin
        return pin

    def assignment_is_active(self, assignment_id: str) -> bool:
        return any(pin.assignment.assignment_id == assignment_id for pin in self._pins.values())


class SqlAlchemyAssignmentPinStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(
        self,
        *,
        model_family_id: str,
        forecast_batch_id: str,
        market: str,
    ) -> PinnedServingAssignment | None:
        with self._engine.connect() as connection:
            payload = connection.execute(
                select(production_serving_assignment_pins.c.payload).where(
                    production_serving_assignment_pins.c.model_family_id == model_family_id,
                    production_serving_assignment_pins.c.forecast_batch_id == forecast_batch_id,
                    production_serving_assignment_pins.c.market == market,
                )
            ).scalar_one_or_none()
        if payload is None:
            return None
        return self._from_payload(payload)

    def put(self, pin: PinnedServingAssignment) -> PinnedServingAssignment:
        payload = self._to_payload(pin)
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    production_serving_assignment_pins.insert().values(
                        pin_id=pin.pin_id,
                        model_family_id=pin.model_family_id,
                        forecast_batch_id=pin.forecast_batch_id,
                        market=pin.market,
                        assignment_id=pin.assignment.assignment_id,
                        payload=payload,
                    )
                )
        except IntegrityError:
            existing = self.get(
                model_family_id=pin.model_family_id,
                forecast_batch_id=pin.forecast_batch_id,
                market=pin.market,
            )
            if existing != pin:
                raise LifecycleConflict("serving_assignment_pin_conflict") from None
            return existing
        return pin

    def assignment_is_active(self, assignment_id: str) -> bool:
        with self._engine.connect() as connection:
            pin_id = connection.execute(
                select(production_serving_assignment_pins.c.pin_id)
                .where(production_serving_assignment_pins.c.assignment_id == assignment_id)
                .limit(1)
            ).scalar_one_or_none()
        return pin_id is not None

    @staticmethod
    def _to_payload(pin: PinnedServingAssignment) -> dict[str, object]:
        return {
            "pin_id": pin.pin_id,
            "model_family_id": pin.model_family_id,
            "forecast_batch_id": pin.forecast_batch_id,
            "market": pin.market,
            "information_cutoff": pin.information_cutoff.isoformat(),
            "started_at": pin.started_at.isoformat(),
            "assignment": pin.assignment.to_payload(),
        }

    @staticmethod
    def _from_payload(payload: object) -> PinnedServingAssignment:
        if not isinstance(payload, dict):
            raise LifecycleConflict("serving_assignment_pin_evidence_invalid")
        try:
            request = PinServingAssignment(
                model_family_id=cast(str, payload["model_family_id"]),
                forecast_batch_id=cast(str, payload["forecast_batch_id"]),
                market=cast(Literal["XTAI", "XNAS"], payload["market"]),
                information_cutoff=datetime.fromisoformat(cast(str, payload["information_cutoff"])),
                started_at=datetime.fromisoformat(cast(str, payload["started_at"])),
            )
            pin = PinnedServingAssignment.create(
                request,
                ServingAssignment.from_payload(payload["assignment"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LifecycleConflict("serving_assignment_pin_evidence_invalid") from error
        if payload.get("pin_id") != pin.pin_id:
            raise LifecycleConflict("serving_assignment_pin_evidence_invalid")
        return pin


class ServingAssignmentResolver:
    def __init__(self, lifecycle_store: LifecycleStore, pin_store: AssignmentPinStore) -> None:
        self._lifecycle_store = lifecycle_store
        self._pin_store = pin_store

    def pin(self, request: PinServingAssignment) -> PinnedServingAssignment:
        existing = self._pin_store.get(
            model_family_id=request.model_family_id,
            forecast_batch_id=request.forecast_batch_id,
            market=request.market,
        )
        if existing is not None:
            return existing
        eligible: list[ServingAssignment] = []
        for event in self._lifecycle_store.events(request.model_family_id):
            if event.event_kind != "ProductionAssignmentCreated":
                continue
            try:
                assignment = ServingAssignment.from_payload(json.loads(event.payload_json))
            except (KeyError, TypeError, ValueError) as error:
                raise LifecycleConflict("serving_assignment_evidence_invalid") from error
            if assignment.assigned_at <= request.started_at:
                eligible.append(assignment)
        if not eligible:
            raise LifecycleConflict("production_assignment_unavailable")
        assignment = eligible[-1]
        activates_next_batch = assignment.effective_from_batch_id == "next-unstarted-eod"
        if (
            request.forecast_batch_id != assignment.effective_from_batch_id
            and not activates_next_batch
            and not self._pin_store.assignment_is_active(assignment.assignment_id)
        ):
            raise LifecycleConflict("production_assignment_not_effective")
        pin = PinnedServingAssignment.create(request, assignment)
        return self._pin_store.put(pin)


@dataclass(frozen=True)
class LifecycleResult:
    status: str
    version: int
    gate_decision: GateDecision | None = None
    approval_decision: ApprovalDecision | None = None
    shadow_evidence: ShadowEvidence | None = None
    serving_assignment: ServingAssignment | None = None


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

    def promote(
        self,
        *,
        command_id: str,
        model_family_id: str,
        expected_version: int,
        promotion_payload: dict[str, object],
        assignment_payload: dict[str, object],
        occurred_at: datetime,
        transition_event_kind: str = "PromotionEventRecorded",
    ) -> tuple[LifecycleEvent, LifecycleEvent]: ...

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
        payload_json = canonical_json(payload)
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

    def promote(
        self,
        *,
        command_id: str,
        model_family_id: str,
        expected_version: int,
        promotion_payload: dict[str, object],
        assignment_payload: dict[str, object],
        occurred_at: datetime,
        transition_event_kind: str = "PromotionEventRecorded",
    ) -> tuple[LifecycleEvent, LifecycleEvent]:
        before = list(self._events.get(model_family_id, ()))
        try:
            promotion = self.append(
                command_id=command_id,
                model_family_id=model_family_id,
                expected_version=expected_version,
                event_kind=transition_event_kind,
                payload=promotion_payload,
                occurred_at=occurred_at,
            )
            assignment = self.append(
                command_id=f"{command_id}:assignment",
                model_family_id=model_family_id,
                expected_version=expected_version + 1,
                event_kind="ProductionAssignmentCreated",
                payload=assignment_payload,
                occurred_at=occurred_at,
            )
        except Exception:
            self._events[model_family_id] = before
            raise
        return promotion, assignment

    def production_assignments(self, model_family_id: str) -> tuple[str, ...]:
        lifecycle_assignments = tuple(
            ServingAssignment.from_payload(json.loads(event.payload_json)).assignment_id
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
                        or canonical_json(existing["payload"]) != canonical_json(payload)
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
                    payload_json=canonical_json(payload),
                    occurred_at=occurred_at,
                )
        except IntegrityError as error:
            raise LifecycleConflict("concurrent_lifecycle_append") from error

    def promote(
        self,
        *,
        command_id: str,
        model_family_id: str,
        expected_version: int,
        promotion_payload: dict[str, object],
        assignment_payload: dict[str, object],
        occurred_at: datetime,
        transition_event_kind: str = "PromotionEventRecorded",
    ) -> tuple[LifecycleEvent, LifecycleEvent]:
        command_ids = (command_id, f"{command_id}:assignment")
        event_specs = (
            (command_ids[0], transition_event_kind, promotion_payload),
            (command_ids[1], "ProductionAssignmentCreated", assignment_payload),
        )
        try:
            with self._engine.begin() as connection:
                existing_rows = (
                    connection.execute(
                        select(model_lifecycle_events).where(
                            model_lifecycle_events.c.command_id.in_(command_ids)
                        )
                    )
                    .mappings()
                    .all()
                )
                if existing_rows:
                    existing = {str(row["command_id"]): dict(row) for row in existing_rows}
                    if set(existing) != set(command_ids):
                        raise LifecycleConflict("command_id_payload_conflict")
                    replayed: list[LifecycleEvent] = []
                    for event_command_id, event_kind, payload in event_specs:
                        row = existing[event_command_id]
                        if (
                            row["model_family_id"] != model_family_id
                            or row["event_kind"] != event_kind
                            or canonical_json(row["payload"]) != canonical_json(payload)
                        ):
                            raise LifecycleConflict("command_id_payload_conflict")
                        replayed.append(self._event_from_row(row))
                    return replayed[0], replayed[1]
                current_version = int(
                    connection.execute(
                        select(
                            func.coalesce(func.max(model_lifecycle_events.c.aggregate_version), 0)
                        ).where(model_lifecycle_events.c.model_family_id == model_family_id)
                    ).scalar_one()
                )
                if expected_version != current_version:
                    raise LifecycleConflict("stale_lifecycle_version")
                recorded: list[LifecycleEvent] = []
                aggregate_id = str(
                    uuid5(NAMESPACE_URL, f"stock-forecasting/model-family/{model_family_id}")
                )
                for offset, (event_command_id, event_kind, payload) in enumerate(event_specs, 1):
                    version = current_version + offset
                    event_payload = {
                        "command_id": event_command_id,
                        "model_family_id": model_family_id,
                        "version": version,
                        "event_kind": event_kind,
                        "payload": payload,
                        "occurred_at": occurred_at.isoformat(),
                    }
                    event_id = _content_id("lifecycle_event", event_payload)
                    connection.execute(
                        model_lifecycle_events.insert().values(
                            event_id=event_id,
                            command_id=event_command_id,
                            model_family_id=model_family_id,
                            aggregate_version=version,
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
                            aggregate_id=aggregate_id,
                            aggregate_version=version,
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
                    recorded.append(
                        LifecycleEvent(
                            event_id=event_id,
                            command_id=event_command_id,
                            model_family_id=model_family_id,
                            version=version,
                            event_kind=event_kind,
                            payload_json=canonical_json(payload),
                            occurred_at=occurred_at,
                        )
                    )
                return recorded[0], recorded[1]
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
            ServingAssignment.from_payload(json.loads(event.payload_json)).assignment_id
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
            payload_json=canonical_json(payload),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
        )


class ModelLifecycle:
    _gate_names = _GATE_NAMES

    def __init__(
        self,
        store: LifecycleStore,
        *,
        policy_repository: GatePolicyRepository | None = None,
        evidence_repository: GateEvidenceRepository | None = None,
        evaluation_report_repository: EvaluationReportRepository | None = None,
        candidate_artifact_repository: CandidateArtifactRepository | None = None,
        approval_policy: ModelApprovalPolicyVersion | None = None,
        formal_qualification_verifier: FormalQualificationVerifier | None = None,
        shadow_eligibility_verifier: ShadowEligibilityVerifier | None = None,
        shadow_run_verifier: ShadowRunVerifier | None = None,
        promotion_readiness_verifier: PromotionReadinessVerifier | None = None,
    ) -> None:
        self._store = store
        self._policy_repository = policy_repository or InMemoryGatePolicyRepository(
            (BOOTSTRAP_GATE_POLICY_V1,)
        )
        self._evidence_repository = evidence_repository or UnavailableGateEvidenceRepository()
        self._evaluation_report_repository = (
            evaluation_report_repository or InMemoryEvaluationReportRepository()
        )
        self._candidate_artifact_repository = (
            candidate_artifact_repository or InMemoryCandidateArtifactRepository()
        )
        submitted_approval_policy = approval_policy or SEPARATED_DUTIES_APPROVAL_POLICY_V1
        verified_approval_policy = ModelApprovalPolicyVersion.from_serialized(
            submitted_approval_policy.policy_version_id,
            submitted_approval_policy.serialized,
        )
        if verified_approval_policy != submitted_approval_policy:
            raise ValueError("model_approval_policy_checksum_mismatch")
        self._approval_policy = verified_approval_policy
        self._formal_qualification_verifier = (
            formal_qualification_verifier or UnavailableFormalQualificationVerifier()
        )
        self._shadow_eligibility_verifier = (
            shadow_eligibility_verifier or UnavailableShadowEligibilityVerifier()
        )
        self._shadow_run_verifier = shadow_run_verifier or UnavailableShadowRunVerifier()
        self._promotion_readiness_verifier = (
            promotion_readiness_verifier or UnavailablePromotionReadinessVerifier()
        )

    def execute(self, command: LifecycleCommand) -> LifecycleResult:
        if isinstance(command, RecordCandidate):
            return self._record_candidate(command)
        if isinstance(command, EvaluateBootstrapCandidate):
            return self._evaluate_bootstrap(command)
        if isinstance(command, DecideApproval):
            return self._decide_approval(command)
        if isinstance(command, RecordShadowEod):
            return self._record_shadow(command)
        if isinstance(command, PromoteProductionAssignment):
            return self._promote(command)
        if isinstance(command, RollbackProductionAssignment):
            return self._rollback(command)
        return self._record_development_failure(command)

    def _record_candidate(self, command: RecordCandidate) -> LifecycleResult:
        bundle = command.candidate_bundle
        if not self._candidate_evidence_is_valid(bundle):
            raise LifecycleConflict("candidate_evidence_invalid")
        report = EvaluationReport.from_serialized(
            bundle.evaluation_report.evaluation_report_id,
            bundle.evaluation_report.serialized,
        )
        if report != bundle.evaluation_report:
            raise LifecycleConflict("evaluation_report_invalid")
        try:
            self._persist_candidate_artifacts(bundle)
            self._evaluation_report_repository.put(report)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise LifecycleConflict("candidate_evidence_persistence_failed") from error
        formal_qualification = self._formal_qualification_is_valid(bundle)
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=bundle.model_family_id,
            expected_version=command.expected_version,
            event_kind="CandidateRecorded",
            payload={
                "candidate_id": bundle.candidate_id,
                "model_family": bundle.primary_artifact.model_family,
                "artifact_id": bundle.primary_artifact.artifact_id,
                "logistic_artifact_ids": [
                    artifact.artifact_id for artifact in bundle.logistic_artifacts
                ],
                "class_prior_artifact_ids": [
                    artifact.artifact_id for artifact in bundle.class_prior_artifacts
                ],
                "evaluation_report_id": report.evaluation_report_id,
                "training_intent_id": bundle.training_intent_id,
                "intent_initiator": bundle.training_intent.initiated_by,
                "training_executor": bundle.training_intent.executed_by,
                "improvement_percentage_points": report.improvement_percentage_points,
                "calibrator_statuses": [item.status for item in bundle.calibrators],
                "class_prior_equal_cell_macro_f1": (report.class_prior_equal_cell_macro_f1),
                "logistic_equal_cell_macro_f1": report.logistic_equal_cell_macro_f1,
                "fold_count": bundle.fold_manifest.fold_count,
                "formal_qualification": formal_qualification,
                "qualification_evidence_id": (
                    bundle.qualification_evidence.qualification_evidence_id
                    if formal_qualification and bundle.qualification_evidence is not None
                    else None
                ),
            },
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(status="candidate_recorded", version=event.version)

    @staticmethod
    def _candidate_evidence_is_valid(bundle: CandidateEvidenceBundle) -> bool:
        try:
            intent = bundle.training_intent
            batch = intent.feature_batch
            report = EvaluationReport.from_serialized(
                bundle.evaluation_report.evaluation_report_id,
                bundle.evaluation_report.serialized,
            )
            logistic = bundle.logistic_artifacts
            class_prior = bundle.class_prior_artifacts
            training_row_ids, validation_row_ids = bundle.fold_manifest.latest_joint_split()
            expected_training_selection_id = training_selection_id_for(
                batch,
                training_row_ids,
                validation_row_ids,
            )
            expected_manifests = (
                batch.feature_batch_id,
                batch.source_policy_manifest_id,
                batch.label_manifest_id,
                batch.fold_manifest_id,
                batch.cost_manifest_id,
            )
            if (
                not bundle.is_content_addressed()
                or not intent.is_content_addressed()
                or not batch.is_content_addressed()
                or len(logistic) != 3
                or len(class_prior) != 3
                or any(
                    artifact.model_family != "regularized_multinomial_logistic"
                    for artifact in logistic
                )
                or any(artifact.model_family != "class_prior" for artifact in class_prior)
                or len({artifact.artifact_id for artifact in (*logistic, *class_prior)}) != 6
                or any(
                    artifact.training_selection_id != expected_training_selection_id
                    for artifact in (*logistic, *class_prior)
                )
                or bundle.primary_artifact != logistic[0]
                or tuple(artifact.seed for artifact in logistic) != intent.preregistered_seeds
                or tuple(artifact.seed for artifact in class_prior) != intent.preregistered_seeds
                or report.logistic_artifact_ids
                != tuple(artifact.artifact_id for artifact in logistic)
                or report.class_prior_artifact_ids
                != tuple(artifact.artifact_id for artifact in class_prior)
                or tuple(item.seed for item in report.seed_results) != intent.preregistered_seeds
                or report.feature_batch_id != batch.feature_batch_id
                or report.source_policy_manifest_id != batch.source_policy_manifest_id
                or report.label_manifest_id != batch.label_manifest_id
                or report.cost_manifest_id != batch.cost_manifest_id
                or report.fold_manifest_id != bundle.fold_manifest.fold_manifest_id
                or batch.fold_manifest_id != bundle.fold_manifest.fold_manifest_id
                or not bundle.fold_manifest.is_content_addressed()
                or not bundle.fold_manifest.matches_feature_batch(batch)
                or bundle.fold_manifest.fold_count != len(bundle.fold_manifest.folds)
                or bundle.calibrators != bundle.primary_artifact.calibrators
                or tuple(item.calibrator_id for item in bundle.calibrators)
                != bundle.primary_artifact.calibrator_ids
                or len(bundle.calibrators) != 6
                or (
                    bundle.qualification_evidence is not None
                    and (
                        intent.execution_purpose != "formal_candidate"
                        or not bundle.qualification_evidence.is_content_addressed()
                        or not bundle.qualification_evidence.binds(intent, bundle.fold_manifest)
                    )
                )
            ):
                return False
            for artifact in (*logistic, *class_prior):
                if (
                    artifact.manifest_ids != expected_manifests
                    or artifact.provenance != intent.provenance
                    or sha256_id(artifact.serialized) != artifact.artifact_id
                    or len(artifact.calibrators) != 6
                    or artifact.calibrator_ids
                    != tuple(item.calibrator_id for item in artifact.calibrators)
                ):
                    return False
                if _cold_load_model_artifact(artifact) != artifact:
                    return False
        except (AttributeError, KeyError, TypeError, ValueError):
            return False
        return True

    def _formal_qualification_is_valid(self, bundle: CandidateEvidenceBundle) -> bool:
        evidence = bundle.qualification_evidence
        if evidence is None:
            return False
        try:
            return self._formal_qualification_verifier.verify(
                evidence,
                bundle.training_intent,
                bundle.fold_manifest,
            )
        except Exception:
            return False

    def _persist_candidate_artifacts(self, bundle: CandidateEvidenceBundle) -> None:
        artifacts = (*bundle.logistic_artifacts, *bundle.class_prior_artifacts)
        for artifact in artifacts:
            self._candidate_artifact_repository.put(
                artifact.artifact_id,
                artifact.serialized,
                object_kind="bootstrap_model_artifact",
            )
        self._candidate_artifact_repository.put(
            bundle.fold_manifest.fold_manifest_id,
            bundle.fold_manifest.serialized,
            object_kind="walk_forward_fold_manifest",
        )
        if bundle.qualification_evidence is not None:
            self._candidate_artifact_repository.put(
                bundle.qualification_evidence.qualification_evidence_id,
                bundle.qualification_evidence.serialized,
                object_kind="formal_candidate_qualification",
            )
        for artifact in artifacts:
            resolved = self._candidate_artifact_repository.resolve(artifact.artifact_id)
            if (
                resolved is None
                or ModelArtifact.from_serialized(artifact.artifact_id, resolved) != artifact
            ):
                raise ValueError("candidate_model_artifact_unresolvable")
        fold_serialized = self._candidate_artifact_repository.resolve(
            bundle.fold_manifest.fold_manifest_id
        )
        if (
            fold_serialized is None
            or type(bundle.fold_manifest).from_serialized(
                bundle.fold_manifest.fold_manifest_id,
                fold_serialized,
            )
            != bundle.fold_manifest
        ):
            raise ValueError("candidate_fold_manifest_unresolvable")
        if bundle.qualification_evidence is not None:
            qualification_serialized = self._candidate_artifact_repository.resolve(
                bundle.qualification_evidence.qualification_evidence_id
            )
            if (
                qualification_serialized is None
                or type(bundle.qualification_evidence).from_serialized(
                    bundle.qualification_evidence.qualification_evidence_id,
                    qualification_serialized,
                )
                != bundle.qualification_evidence
            ):
                raise ValueError("candidate_qualification_evidence_unresolvable")

    def _evaluate_bootstrap(self, command: EvaluateBootstrapCandidate) -> LifecycleResult:
        candidate = self._candidate(command.model_family_id, command.candidate_id)
        failed_gates: list[str] = []
        if self._store.production_assignments(command.model_family_id):
            failed_gates.append("bootstrap_disabled_after_first_production_assignment")
        if candidate["model_family"] != "regularized_multinomial_logistic":
            failed_gates.append("model_family")
        if candidate["formal_qualification"] is not True:
            failed_gates.append("qualification")
        evaluation_report = self._evaluation_report_repository.resolve(
            str(candidate["evaluation_report_id"])
        )
        submitted_prior_score = cast(float, candidate["class_prior_equal_cell_macro_f1"])
        submitted_logistic_score = cast(float, candidate["logistic_equal_cell_macro_f1"])
        submitted_improvement = cast(float, candidate["improvement_percentage_points"])
        evaluation_report_is_valid = (
            evaluation_report is not None
            and evaluation_report.evaluation_report_id == candidate["evaluation_report_id"]
            and abs(submitted_prior_score - evaluation_report.class_prior_equal_cell_macro_f1)
            <= 1e-12
            and abs(submitted_logistic_score - evaluation_report.logistic_equal_cell_macro_f1)
            <= 1e-12
            and abs(submitted_improvement - evaluation_report.improvement_percentage_points)
            <= 1e-12
        )
        verified_improvement = (
            evaluation_report.improvement_percentage_points
            if evaluation_report_is_valid and evaluation_report is not None
            else None
        )
        if not evaluation_report_is_valid:
            failed_gates.append("evaluation_report")
        if verified_improvement is None or verified_improvement < 1.0:
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
        report = self._evidence_repository.resolve(evidence)
        thresholds = dict(policy.thresholds) if policy is not None else {}
        evidence_is_valid = (
            policy is not None
            and report is not None
            and evidence.evidence_kind == "formal_evidence"
            and evidence.policy_version_id == command.policy_version_id
            and evidence.evaluation_report_id == candidate["evaluation_report_id"]
            and evidence.is_content_addressed()
            and report.policy_version_id == command.policy_version_id
            and report.evaluation_report_id == candidate["evaluation_report_id"]
            and report.measurements == evidence.measurements
            and {item.name for item in report.measurements} == set(thresholds)
        )
        if not evidence_is_valid:
            failed_gates.append("hard_gate_evidence")
        else:
            assert report is not None
            failed_categories = {
                threshold.category
                for measurement in report.measurements
                if not (threshold := thresholds[measurement.name]).passes(measurement.value)
            }
            failed_gates.extend(
                category for category in self._gate_names if category in failed_categories
            )
        decision = GateDecision.create(
            candidate_id=command.candidate_id,
            artifact_id=str(candidate["artifact_id"]),
            evaluation_report_id=str(candidate["evaluation_report_id"]),
            policy_version_id=command.policy_version_id,
            status="failed" if failed_gates else "passed",
            failed_gates=tuple(failed_gates),
            hard_gate_evidence_id=evidence.evidence_id,
            hard_gate_evidence_refs=evidence.evidence_refs,
            hard_gate_report_id=report.artifact_id if report is not None else None,
            submitted_hard_gate_measurements=evidence.measurements,
            submitted_improvement_percentage_points=submitted_improvement,
            verified_improvement_percentage_points=verified_improvement,
            verified_hard_gate_measurements=(
                report.measurements if evidence_is_valid and report else ()
            ),
        )
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            event_kind="GateDecisionRecorded",
            payload=decision.to_payload(),
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
        replay = self._approval_replay(command)
        if replay is not None:
            return replay
        candidate = self._candidate(command.model_family_id, command.candidate_id)
        _, gate = self._passed_gate(command.model_family_id, command.candidate_id)
        expires_at = command.occurred_at + timedelta(days=7)
        invalidated_reason: str | None = None
        independent_review = command.approver_id not in {
            candidate["intent_initiator"],
            candidate["training_executor"],
        }
        recorded_independent_review = (
            self._approval_policy.approval_mode == "separated_duties" and independent_review
        )
        if (
            self._approval_policy.approval_mode == "owner_operated"
            and command.approver_id != self._approval_policy.owner_principal_id
        ):
            invalidated_reason = "owner_principal_mismatch"
        elif self._approval_policy.approval_mode == "owner_operated" and independent_review:
            invalidated_reason = "owner_not_training_participant"
        elif self._approval_policy.approval_mode == "separated_duties" and not independent_review:
            invalidated_reason = "duty_separation_violation"
        elif (
            command.artifact_id != gate.artifact_id
            or command.evaluation_report_id != gate.evaluation_report_id
            or command.policy_version_id != gate.policy_version_id
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
        decision = ApprovalDecision.create(
            candidate_id=command.candidate_id,
            artifact_id=command.artifact_id,
            evaluation_report_id=command.evaluation_report_id,
            policy_version_id=command.policy_version_id,
            gate_decision_id=gate.gate_decision_id,
            approval_policy_version_id=self._approval_policy.policy_version_id,
            approval_mode=self._approval_policy.approval_mode,
            approval_policy_owner_principal_id=self._approval_policy.owner_principal_id,
            independent_review=recorded_independent_review,
            approver_id=command.approver_id,
            requested_decision=command.decision,
            decision=effective_decision,
            reason=command.reason,
            expected_assignment=command.expected_assignment,
            decided_at=command.occurred_at,
            expires_at=expires_at,
            invalidated_reason=invalidated_reason,
        )
        event = self._store.append(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            event_kind="ApprovalDecisionRecorded",
            payload=decision.to_payload(),
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(
            status="approved" if effective_decision == "approved" else "approval_rejected",
            version=event.version,
            approval_decision=decision,
        )

    def _approval_replay(self, command: DecideApproval) -> LifecycleResult | None:
        existing = next(
            (
                event
                for event in self._store.events(command.model_family_id)
                if event.command_id == command.command_id
            ),
            None,
        )
        if existing is None:
            return None
        if existing.event_kind != "ApprovalDecisionRecorded":
            raise LifecycleConflict("command_id_payload_conflict")
        try:
            decision = ApprovalDecision.from_payload(json.loads(existing.payload_json))
        except (KeyError, TypeError, ValueError) as error:
            raise LifecycleConflict("command_id_payload_conflict") from error
        if (
            not decision.matches_policy(self._approval_policy)
            or decision.candidate_id != command.candidate_id
            or decision.artifact_id != command.artifact_id
            or decision.evaluation_report_id != command.evaluation_report_id
            or decision.policy_version_id != command.policy_version_id
            or decision.approver_id != command.approver_id
            or decision.requested_decision != command.decision
            or decision.reason != command.reason
            or decision.expected_assignment != command.expected_assignment
        ):
            raise LifecycleConflict("command_id_payload_conflict")
        return LifecycleResult(
            status="approved" if decision.decision == "approved" else "approval_rejected",
            version=existing.version,
            approval_decision=decision,
        )

    def _has_irreversible_rejection(self, command: DecideApproval) -> bool:
        for event in self._store.events(command.model_family_id):
            if event.event_kind != "ApprovalDecisionRecorded":
                continue
            payload = json.loads(event.payload_json)
            if isinstance(payload, dict) and "approval_decision_id" in payload:
                try:
                    decision = ApprovalDecision.from_payload(payload)
                except (KeyError, TypeError, ValueError) as error:
                    raise LifecycleConflict("approval_decision_evidence_invalid") from error
                if (
                    decision.candidate_id == command.candidate_id
                    and decision.artifact_id == command.artifact_id
                    and decision.evaluation_report_id == command.evaluation_report_id
                    and decision.policy_version_id == command.policy_version_id
                    and decision.requested_decision == "rejected"
                    and decision.invalidated_reason == "approver_rejected"
                ):
                    return True
                continue
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
    ) -> tuple[LifecycleEvent, GateDecision]:
        for event in reversed(self._store.events(model_family_id)):
            if event.event_kind != "GateDecisionRecorded":
                continue
            try:
                decision = GateDecision.from_payload(json.loads(event.payload_json))
            except (KeyError, TypeError, ValueError) as error:
                raise LifecycleConflict("gate_decision_evidence_invalid") from error
            if decision.candidate_id != candidate_id:
                continue
            if decision.status != "passed":
                raise LifecycleConflict("candidate_gate_not_passed")
            return event, decision
        raise LifecycleConflict("candidate_gate_not_evaluated")

    def _record_shadow(self, command: RecordShadowEod) -> LifecycleResult:
        replay = self._shadow_replay(command)
        if replay is not None:
            return replay
        candidate = self._candidate(command.model_family_id, command.candidate_id)
        evidence = command.evidence
        if not evidence.is_content_addressed():
            raise LifecycleConflict("shadow_evidence_checksum_mismatch")
        try:
            approval_payload = self._current_approval(command.model_family_id, command.candidate_id)
        except LifecycleConflict as error:
            if str(error) != "approval_decision_evidence_invalid":
                raise
            approval_payload = {}
            approval_evidence_invalid = True
        else:
            approval_evidence_invalid = False
        approval: ApprovalDecision | None = None
        if "approval_decision_id" in approval_payload:
            try:
                approval = ApprovalDecision.from_payload(approval_payload)
            except (TypeError, ValueError):
                approval_evidence_invalid = True
        try:
            _, current_gate = self._passed_gate(command.model_family_id, command.candidate_id)
            current_gate_passed = True
        except LifecycleConflict:
            current_gate = None
            current_gate_passed = False
        try:
            prior_records = _verified_shadow_lineage(
                self._store.events(command.model_family_id),
                candidate_id=command.candidate_id,
                artifact_id=evidence.artifact_id,
                evaluation_report_id=evidence.evaluation_report_id,
                gate_decision_id=evidence.gate_decision_id,
                approval_decision_id=evidence.approval_decision_id,
                approval_policy_version_id=evidence.approval_policy_version_id,
                expected_assignment=evidence.expected_assignment,
                not_before=approval.decided_at if approval is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            prior_records = ()
            shadow_history_invalid = True
        else:
            shadow_history_invalid = False
        previous = prior_records[-1].evidence if prior_records else None
        blocked_reason: str | None = None
        if shadow_history_invalid:
            blocked_reason = "shadow_history_invalid"
        elif approval_evidence_invalid:
            blocked_reason = "approval_evidence_invalid"
        elif approval is None:
            blocked_reason = "approval_policy_unbound"
        elif not current_gate_passed:
            blocked_reason = "hard_gate_vetoed"
        elif current_gate is None or (
            approval.gate_decision_id != current_gate.gate_decision_id
            or approval.policy_version_id != current_gate.policy_version_id
        ):
            blocked_reason = "gate_lineage_changed"
        elif approval.decision != "approved" or approval.invalidated_reason is not None:
            blocked_reason = "approval_not_valid"
        elif command.occurred_at >= approval.expires_at:
            blocked_reason = "approval_expired"
        elif (
            evidence.eligible_eod_date < approval.decided_at.date()
            or command.occurred_at < approval.decided_at
        ):
            blocked_reason = "shadow_before_approval"
        elif approval.expected_assignment != self._current_assignment(command.model_family_id):
            blocked_reason = "expected_assignment_changed"
        elif (
            evidence.candidate_id != command.candidate_id
            or evidence.artifact_id != candidate["artifact_id"]
            or evidence.evaluation_report_id != candidate["evaluation_report_id"]
            or evidence.gate_decision_id != approval.gate_decision_id
            or evidence.approval_decision_id != approval.approval_decision_id
            or evidence.approval_policy_version_id != approval.approval_policy_version_id
            or evidence.expected_assignment != approval.expected_assignment
        ):
            blocked_reason = "shadow_evidence_binding_mismatch"
        elif set(evidence.markets) != {"XTAI", "XNAS"}:
            blocked_reason = "incomplete_market_shadow"
        elif command.occurred_at.date() < evidence.eligible_eod_date:
            blocked_reason = "shadow_eod_not_observed"
        elif not self._is_eligible_shadow_eod(evidence):
            blocked_reason = "shadow_eod_not_eligible"
        elif not self._is_verified_shadow_run(evidence):
            blocked_reason = "shadow_run_not_verified"
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
            record.evidence.shadow_run_id == evidence.shadow_run_id
            or record.evidence.eligible_eod_date == evidence.eligible_eod_date
            for record in prior_records
        ):
            blocked_reason = "duplicate_shadow_run"
        elif evidence.previous_shadow_run_id != (
            previous.shadow_run_id if previous is not None else None
        ):
            blocked_reason = "shadow_sequence_broken"
        elif previous is not None and evidence.eligible_eod_date <= previous.eligible_eod_date:
            blocked_reason = "shadow_date_not_increasing"
        elif len(prior_records) >= 5:
            blocked_reason = "shadow_already_complete"
        completed_cycles = len(prior_records)
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
            payload=ShadowRunRecord.create(
                evidence,
                eligible_cycle_count=eligible_cycle_count,
                blocked_reason=blocked_reason,
            ).to_payload(),
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

    def _shadow_replay(self, command: RecordShadowEod) -> LifecycleResult | None:
        existing = next(
            (
                event
                for event in self._store.events(command.model_family_id)
                if event.command_id == command.command_id
            ),
            None,
        )
        if existing is None:
            return None
        if existing.event_kind not in {"ShadowEodRecorded", "ShadowEodBlocked"}:
            raise LifecycleConflict("command_id_payload_conflict")
        try:
            record = ShadowRunRecord.from_payload(json.loads(existing.payload_json))
        except (KeyError, TypeError, ValueError) as error:
            raise LifecycleConflict("command_id_payload_conflict") from error
        is_blocked = record.blocked_reason is not None
        if (
            record.evidence != command.evidence
            or record.evidence.candidate_id != command.candidate_id
            or (existing.event_kind == "ShadowEodBlocked") != is_blocked
            or (not is_blocked and record.eligible_cycle_count == 0)
        ):
            raise LifecycleConflict("command_id_payload_conflict")
        return LifecycleResult(
            status=(
                "shadow_blocked"
                if is_blocked
                else "shadow_complete"
                if record.eligible_cycle_count == 5
                else "shadow_recorded"
            ),
            version=existing.version,
            shadow_evidence=ShadowEvidence(
                shadow_run_id=record.evidence.shadow_run_id,
                candidate_id=record.evidence.candidate_id,
                eligible_cycle_count=record.eligible_cycle_count,
                blocked_reason=record.blocked_reason,
            ),
        )

    def _is_eligible_shadow_eod(self, evidence: ShadowRunEvidence) -> bool:
        try:
            return self._shadow_eligibility_verifier.verify_eligible_eod(evidence)
        except Exception:
            return False

    def _is_verified_shadow_run(self, evidence: ShadowRunEvidence) -> bool:
        try:
            return self._shadow_run_verifier.verify_shadow_run(evidence)
        except Exception:
            return False

    def _promote(self, command: PromoteProductionAssignment) -> LifecycleResult:
        replay = self._assignment_transition_replay(command)
        if replay is not None:
            return replay
        candidate = self._candidate(command.model_family_id, command.candidate_id)
        readiness = command.readiness
        try:
            _, gate = self._passed_gate(command.model_family_id, command.candidate_id)
            approval = ApprovalDecision.from_payload(
                self._current_approval(command.model_family_id, command.candidate_id)
            )
            serialized = self._candidate_artifact_repository.resolve(str(candidate["artifact_id"]))
            if serialized is None:
                raise ValueError("candidate_artifact_unavailable")
            artifact = ModelArtifact.from_serialized(str(candidate["artifact_id"]), serialized)
            if _cold_load_model_artifact(artifact) != artifact:
                raise ValueError("candidate_artifact_cold_load_failed")
            shadows = _verified_shadow_lineage(
                self._store.events(command.model_family_id),
                candidate_id=command.candidate_id,
                artifact_id=artifact.artifact_id,
                evaluation_report_id=str(candidate["evaluation_report_id"]),
                gate_decision_id=gate.gate_decision_id,
                approval_decision_id=approval.approval_decision_id,
                approval_policy_version_id=approval.approval_policy_version_id,
                expected_assignment=approval.expected_assignment,
                not_before=approval.decided_at,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise LifecycleConflict("promotion_evidence_invalid") from error
        current_assignment = self._current_assignment(command.model_family_id)
        expected_rollback = None if current_assignment == "unassigned" else current_assignment
        rollback_compatible = current_assignment == "unassigned"
        if current_assignment != "unassigned":
            try:
                rollback_assignment = next(
                    ServingAssignment.from_payload(json.loads(event.payload_json))
                    for event in self._store.events(command.model_family_id)
                    if event.event_kind == "ProductionAssignmentCreated"
                    and json.loads(event.payload_json)["assignment_id"] == current_assignment
                )
                rollback_serialized = self._candidate_artifact_repository.resolve(
                    rollback_assignment.artifact_id
                )
                if rollback_serialized is None:
                    raise ValueError("rollback_artifact_unavailable")
                rollback_artifact = ModelArtifact.from_serialized(
                    rollback_assignment.artifact_id,
                    rollback_serialized,
                )
                rollback_compatible = (
                    _cold_load_model_artifact(rollback_artifact) == rollback_artifact
                    and rollback_artifact.provenance.feature_schema_id
                    == artifact.provenance.feature_schema_id
                    and rollback_artifact.provenance.runtime_id == artifact.provenance.runtime_id
                    and rollback_artifact.manifest_ids[1] == artifact.manifest_ids[1]
                )
            except (KeyError, StopIteration, TypeError, ValueError):
                rollback_compatible = False
        bindings_valid = (
            readiness.is_content_addressed()
            and readiness.candidate_id == command.candidate_id
            and readiness.artifact_id == artifact.artifact_id
            and readiness.evaluation_report_id == candidate["evaluation_report_id"]
            and readiness.feature_schema_id == artifact.provenance.feature_schema_id
            and readiness.runtime_id == artifact.provenance.runtime_id
            and readiness.source_policy_manifest_id == artifact.manifest_ids[1]
            and readiness.rollback_assignment_id == expected_rollback
            and rollback_compatible
            and command.expected_assignment == current_assignment
            and approval.expected_assignment == current_assignment
            and approval.decision == "approved"
            and approval.invalidated_reason is None
            and command.occurred_at < approval.expires_at
            and len(shadows) == 5
        )
        try:
            externally_verified = self._promotion_readiness_verifier.verify(readiness)
        except Exception:
            externally_verified = False
        if not bindings_valid or not externally_verified:
            raise LifecycleConflict("promotion_readiness_invalid")
        assignment = ServingAssignment.create(
            model_family_id=command.model_family_id,
            candidate_id=command.candidate_id,
            artifact_id=artifact.artifact_id,
            previous_assignment_id=expected_rollback,
            readiness_evidence_id=readiness.evidence_id,
            effective_from_batch_id=readiness.effective_from_batch_id,
            assigned_at=command.occurred_at,
        )
        promotion_core: dict[str, object] = {
            "model_family_id": command.model_family_id,
            "candidate_id": command.candidate_id,
            "artifact_id": artifact.artifact_id,
            "evaluation_report_id": str(candidate["evaluation_report_id"]),
            "gate_decision_id": gate.gate_decision_id,
            "approval_decision_id": approval.approval_decision_id,
            "readiness_evidence_id": readiness.evidence_id,
            "previous_assignment_id": expected_rollback,
            "assignment_id": assignment.assignment_id,
            "effective_from_batch_id": readiness.effective_from_batch_id,
            "promoted_at": command.occurred_at.isoformat(),
        }
        promotion_payload = {
            "promotion_event_id": _content_id("promotion_event", promotion_core),
            **promotion_core,
        }
        _, assignment_event = self._store.promote(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            promotion_payload=promotion_payload,
            assignment_payload=assignment.to_payload(),
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(
            status="promoted",
            version=assignment_event.version,
            serving_assignment=assignment,
        )

    def _current_approval(self, model_family_id: str, candidate_id: str) -> dict[str, object]:
        for event in reversed(self._store.events(model_family_id)):
            if event.event_kind != "ApprovalDecisionRecorded":
                continue
            payload = json.loads(event.payload_json)
            if "approval_decision_id" in payload:
                try:
                    decision = ApprovalDecision.from_payload(payload)
                except (KeyError, TypeError, ValueError) as error:
                    raise LifecycleConflict("approval_decision_evidence_invalid") from error
                if decision.candidate_id == candidate_id:
                    return decision.to_payload()
                continue
            if payload["candidate_id"] == candidate_id:
                return dict(payload)
        raise LifecycleConflict("candidate_not_approved")

    def _rollback(self, command: RollbackProductionAssignment) -> LifecycleResult:
        replay = self._assignment_transition_replay(command)
        if replay is not None:
            return replay
        assignments: list[ServingAssignment] = []
        try:
            for event in self._store.events(command.model_family_id):
                if event.event_kind == "ProductionAssignmentCreated":
                    assignments.append(
                        ServingAssignment.from_payload(json.loads(event.payload_json))
                    )
            current = assignments[-1]
            target = next(
                item
                for item in assignments
                if item.assignment_id == command.rollback_target_assignment_id
            )
            serialized = self._candidate_artifact_repository.resolve(target.artifact_id)
            if serialized is None:
                raise ValueError("rollback_artifact_unavailable")
            artifact = ModelArtifact.from_serialized(target.artifact_id, serialized)
            if _cold_load_model_artifact(artifact) != artifact:
                raise ValueError("rollback_artifact_cold_load_failed")
            current_serialized = self._candidate_artifact_repository.resolve(current.artifact_id)
            if current_serialized is None:
                raise ValueError("current_artifact_unavailable")
            current_artifact = ModelArtifact.from_serialized(
                current.artifact_id,
                current_serialized,
            )
            if (
                _cold_load_model_artifact(current_artifact) != current_artifact
                or artifact.provenance.feature_schema_id
                != current_artifact.provenance.feature_schema_id
                or artifact.provenance.runtime_id != current_artifact.provenance.runtime_id
                or artifact.manifest_ids[1] != current_artifact.manifest_ids[1]
            ):
                raise ValueError("rollback_artifact_incompatible")
            try:
                current_source_policy_verified = (
                    self._promotion_readiness_verifier.verify_current_source_policy(
                        artifact.manifest_ids[1]
                    )
                )
            except Exception:
                current_source_policy_verified = False
            if not current_source_policy_verified:
                raise ValueError("rollback_source_policy_inactive")
            if (
                current.assignment_id != command.expected_assignment
                or current.previous_assignment_id != target.assignment_id
                or target.assigned_at >= current.assigned_at
                or not command.effective_from_batch_id
                or command.occurred_at.tzinfo is None
            ):
                raise ValueError("rollback_lineage_invalid")
        except (IndexError, KeyError, StopIteration, TypeError, ValueError) as error:
            raise LifecycleConflict("rollback_target_invalid") from error
        rollback_core: dict[str, object] = {
            "model_family_id": command.model_family_id,
            "from_assignment_id": current.assignment_id,
            "rollback_target_assignment_id": target.assignment_id,
            "artifact_id": target.artifact_id,
            "effective_from_batch_id": command.effective_from_batch_id,
            "rolled_back_at": command.occurred_at.isoformat(),
        }
        rollback_event_id = _content_id("rollback_event", rollback_core)
        assignment = ServingAssignment.create(
            model_family_id=command.model_family_id,
            candidate_id=target.candidate_id,
            artifact_id=target.artifact_id,
            previous_assignment_id=current.assignment_id,
            readiness_evidence_id=rollback_event_id,
            effective_from_batch_id=command.effective_from_batch_id,
            assigned_at=command.occurred_at,
        )
        _, assignment_event = self._store.promote(
            command_id=command.command_id,
            model_family_id=command.model_family_id,
            expected_version=command.expected_version,
            promotion_payload={"rollback_event_id": rollback_event_id, **rollback_core},
            assignment_payload=assignment.to_payload(),
            occurred_at=command.occurred_at,
            transition_event_kind="RollbackEventRecorded",
        )
        return LifecycleResult(
            status="rolled_back",
            version=assignment_event.version,
            serving_assignment=assignment,
        )

    def _assignment_transition_replay(
        self,
        command: PromoteProductionAssignment | RollbackProductionAssignment,
    ) -> LifecycleResult | None:
        events = self._store.events(command.model_family_id)
        transition = next(
            (event for event in events if event.command_id == command.command_id),
            None,
        )
        assignment_event = next(
            (event for event in events if event.command_id == f"{command.command_id}:assignment"),
            None,
        )
        if transition is None and assignment_event is None:
            return None
        if transition is None or assignment_event is None:
            raise LifecycleConflict("command_id_payload_conflict")
        try:
            transition_payload = cast(dict[str, object], json.loads(transition.payload_json))
            assignment = ServingAssignment.from_payload(json.loads(assignment_event.payload_json))
        except (KeyError, TypeError, ValueError) as error:
            raise LifecycleConflict("command_id_payload_conflict") from error
        effective_from_batch_id = (
            command.readiness.effective_from_batch_id
            if isinstance(command, PromoteProductionAssignment)
            else command.effective_from_batch_id
        )
        common_valid = (
            transition.version == command.expected_version + 1
            and assignment_event.version == command.expected_version + 2
            and transition.occurred_at == command.occurred_at
            and assignment_event.occurred_at == command.occurred_at
            and assignment.model_family_id == command.model_family_id
            and assignment.assigned_at == command.occurred_at
            and assignment.effective_from_batch_id == effective_from_batch_id
            and assignment_event.event_kind == "ProductionAssignmentCreated"
        )
        if isinstance(command, PromoteProductionAssignment):
            expected_previous = (
                None if command.expected_assignment == "unassigned" else command.expected_assignment
            )
            valid = (
                common_valid
                and command.readiness.is_content_addressed()
                and transition.event_kind == "PromotionEventRecorded"
                and transition_payload.get("model_family_id") == command.model_family_id
                and transition_payload.get("candidate_id") == command.candidate_id
                and transition_payload.get("artifact_id") == command.readiness.artifact_id
                and transition_payload.get("evaluation_report_id")
                == command.readiness.evaluation_report_id
                and transition_payload.get("readiness_evidence_id") == command.readiness.evidence_id
                and transition_payload.get("previous_assignment_id") == expected_previous
                and transition_payload.get("effective_from_batch_id")
                == command.readiness.effective_from_batch_id
                and transition_payload.get("promoted_at") == command.occurred_at.isoformat()
                and assignment.candidate_id == command.candidate_id
                and assignment.artifact_id == command.readiness.artifact_id
                and assignment.previous_assignment_id == expected_previous
                and assignment.readiness_evidence_id == command.readiness.evidence_id
            )
            status = "promoted"
        else:
            rollback_core: dict[str, object] = {
                "model_family_id": command.model_family_id,
                "from_assignment_id": command.expected_assignment,
                "rollback_target_assignment_id": command.rollback_target_assignment_id,
                "artifact_id": assignment.artifact_id,
                "effective_from_batch_id": command.effective_from_batch_id,
                "rolled_back_at": command.occurred_at.isoformat(),
            }
            rollback_event_id = _content_id("rollback_event", rollback_core)
            valid = (
                common_valid
                and transition.event_kind == "RollbackEventRecorded"
                and transition_payload == {"rollback_event_id": rollback_event_id, **rollback_core}
                and assignment.previous_assignment_id == command.expected_assignment
                and assignment.readiness_evidence_id == rollback_event_id
            )
            status = "rolled_back"
        if not valid:
            raise LifecycleConflict("command_id_payload_conflict")
        return LifecycleResult(
            status=status,
            version=assignment_event.version,
            serving_assignment=assignment,
        )

    def _record_development_failure(self, command: RecordDevelopmentGateFailure) -> LifecycleResult:
        decision = GateDecision.create(
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
            payload=decision.to_payload(),
            occurred_at=command.occurred_at,
        )
        return LifecycleResult(
            status="gate_failed",
            version=event.version,
            gate_decision=decision,
        )


class ModelGovernanceQuery:
    def __init__(
        self,
        store: LifecycleStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

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
        gate_payload = self._latest_payload(events, "GateDecisionRecorded", candidate_id)
        gate: GateDecision | None = None
        gate_projection: dict[str, object]
        if gate_payload is None:
            gate_projection = {"status": "not_evaluated"}
        else:
            try:
                gate = GateDecision.from_payload(gate_payload)
            except (KeyError, TypeError, ValueError):
                gate_projection = {"status": "gate_evidence_invalid"}
            else:
                gate_projection = {
                    "status": gate.status,
                    "policy_version_id": gate.policy_version_id,
                    "failed_gates": gate.failed_gates,
                    "hard_gate_evidence_id": gate.hard_gate_evidence_id,
                    "hard_gate_evidence_refs": gate.hard_gate_evidence_refs,
                }
        approval = self._latest_payload(events, "ApprovalDecisionRecorded", candidate_id)
        assignments = self._store.production_assignments(model_family_id)
        shadow_count = self._current_lineage_shadow_count(
            events,
            candidate_id,
            gate,
            approval,
        )
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
            "gate": gate_projection,
            "approval": self._approval_projection(
                approval,
                candidate,
                gate,
                gate_projection,
                evaluated_at=self._clock(),
                current_assignment=assignments[-1] if assignments else "unassigned",
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
            if event_kind == "GateDecisionRecorded" and "gate_decision_id" in payload:
                try:
                    decision = GateDecision.from_payload(payload)
                except (KeyError, TypeError, ValueError):
                    return payload
                if decision.candidate_id == candidate_id:
                    return decision.to_payload()
                continue
            if event_kind == "ApprovalDecisionRecorded" and "approval_decision_id" in payload:
                try:
                    approval = ApprovalDecision.from_payload(payload)
                except (KeyError, TypeError, ValueError):
                    return payload
                if approval.candidate_id == candidate_id:
                    return approval.to_payload()
                continue
            if payload["candidate_id"] == candidate_id:
                return payload
        return None

    @staticmethod
    def _approval_projection(
        approval: dict[str, object] | None,
        candidate: dict[str, object],
        current_gate: GateDecision | None,
        gate_projection: dict[str, object],
        *,
        evaluated_at: datetime,
        current_assignment: str,
    ) -> dict[str, object]:
        if approval is None:
            return {
                "status": (
                    "blocked_by_gate"
                    if gate_projection["status"] in {"failed", "gate_evidence_invalid"}
                    else "awaiting_approval"
                )
            }
        if "approval_decision_id" in approval:
            try:
                decision = ApprovalDecision.from_payload(approval)
            except (TypeError, ValueError):
                return {"status": "approval_evidence_invalid"}
            if (
                current_gate is None
                or decision.gate_decision_id != current_gate.gate_decision_id
                or decision.policy_version_id != current_gate.policy_version_id
            ):
                status = "gate_lineage_changed"
            elif decision.decision == "approved" and evaluated_at >= decision.expires_at:
                status = "approval_expired"
            elif (
                decision.decision == "approved"
                and decision.expected_assignment != current_assignment
            ):
                status = "expected_assignment_changed"
            else:
                status = "approved" if decision.decision == "approved" else "rejected"
            return {
                "status": status,
                "approval_policy_version_id": decision.approval_policy_version_id,
                "approval_mode": decision.approval_mode,
                "approval_policy_owner_principal_id": (decision.approval_policy_owner_principal_id),
                "independent_review": decision.independent_review,
            }
        return {
            "status": "approved" if approval.get("decision") == "approved" else "rejected",
            "approval_policy_version_id": None,
            "approval_mode": "separated_duties",
            "approval_policy_owner_principal_id": None,
            "independent_review": approval.get("approver_id")
            not in {candidate["intent_initiator"], candidate["training_executor"]},
        }

    @staticmethod
    def _current_lineage_shadow_count(
        events: tuple[LifecycleEvent, ...],
        candidate_id: str,
        gate: GateDecision | None,
        approval_payload: dict[str, object] | None,
    ) -> int:
        if (
            gate is None
            or approval_payload is None
            or "approval_decision_id" not in approval_payload
        ):
            return 0
        try:
            approval = ApprovalDecision.from_payload(approval_payload)
        except (KeyError, TypeError, ValueError):
            return 0
        if approval.gate_decision_id != gate.gate_decision_id:
            return 0
        try:
            records = _verified_shadow_lineage(
                events,
                candidate_id=candidate_id,
                artifact_id=approval.artifact_id,
                evaluation_report_id=approval.evaluation_report_id,
                gate_decision_id=gate.gate_decision_id,
                approval_decision_id=approval.approval_decision_id,
                approval_policy_version_id=approval.approval_policy_version_id,
                expected_assignment=approval.expected_assignment,
                not_before=approval.decided_at,
            )
        except (KeyError, TypeError, ValueError):
            return 0
        return len(records)
