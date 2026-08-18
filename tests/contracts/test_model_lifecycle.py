import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, time
from io import BytesIO
from typing import Literal

import pytest
from sqlalchemy import create_engine, func, select

from stock_forecasting.evaluation_report import EvaluationReport
from stock_forecasting.forecast_lab import (
    CandidateEvidenceBundle,
    FoldManifest,
    FormalQualificationEvidence,
    TrainingIntentRef,
)
from stock_forecasting.model_governance import (
    BOOTSTRAP_GATE_POLICY_V1,
    BootstrapGatePolicyVersion,
    DecideApproval,
    EvaluateBootstrapCandidate,
    GateMeasurement,
    HardGateEvidence,
    HardGateReportArtifact,
    InMemoryEvaluationReportRepository,
    InMemoryLifecycleStore,
    LifecycleConflict,
    ModelApprovalPolicyVersion,
    ModelGovernanceQuery,
    ModelLifecycle,
    ObjectGatePolicyRepository,
    RecordCandidate,
    RecordShadowEod,
    ShadowEligibilityVerifier,
    ShadowRunEvidence,
    ShadowRunVerifier,
    SqlAlchemyLifecycleStore,
)
from stock_forecasting.platform.outbox_relay import outbox_dispatch, outbox_events
from stock_forecasting.platform.schema import metadata
from tests.modeling_support import (
    engineering_lifecycle_candidate_bundle,
    lifecycle_candidate_bundle,
    passing_hard_gate_evidence,
)

_RECORDED_EVALUATION_REPORTS: dict[str, EvaluationReport] = {}
_RECORDED_CANDIDATES: dict[str, CandidateEvidenceBundle] = {}
_SHADOW_BINDING: dict[str, str] = {}


class _VerifiedEvidenceRepository:
    def resolve(self, evidence: HardGateEvidence) -> HardGateReportArtifact | None:
        report = HardGateReportArtifact.create(
            policy_version_id=evidence.policy_version_id,
            evaluation_report_id=evidence.evaluation_report_id,
            measurements=evidence.measurements,
        )
        return report if evidence.evidence_refs == (report.artifact_id,) else None


class _JointMarketEodVerifier:
    def __init__(self, eligible_dates: frozenset[date]) -> None:
        self._eligible_dates = eligible_dates

    def verify_eligible_eod(self, evidence: ShadowRunEvidence) -> bool:
        return evidence.eligible_eod_date in self._eligible_dates and set(evidence.markets) == {
            "XTAI",
            "XNAS",
        }


class _VerifiedShadowRunVerifier:
    def verify_shadow_run(self, evidence: ShadowRunEvidence) -> bool:
        return True


class _VerifiedFormalQualification:
    def verify(
        self,
        evidence: FormalQualificationEvidence,
        intent: TrainingIntentRef,
        fold_manifest: FoldManifest,
    ) -> bool:
        return evidence.is_content_addressed() and evidence.binds(intent, fold_manifest)


def _verified_lifecycle(
    store: InMemoryLifecycleStore | SqlAlchemyLifecycleStore,
    *,
    shadow_eligibility_verifier: ShadowEligibilityVerifier | None = None,
    shadow_run_verifier: ShadowRunVerifier | None = None,
    evaluation_report_repository: InMemoryEvaluationReportRepository | None = None,
) -> ModelLifecycle:
    return ModelLifecycle(
        store,
        evidence_repository=_VerifiedEvidenceRepository(),
        evaluation_report_repository=evaluation_report_repository,
        formal_qualification_verifier=_VerifiedFormalQualification(),
        shadow_eligibility_verifier=shadow_eligibility_verifier,
        shadow_run_verifier=shadow_run_verifier,
    )


def _hard_gates(
    candidate_id: str,
    *,
    overrides: dict[str, float] | None = None,
) -> HardGateEvidence:
    return passing_hard_gate_evidence(
        _RECORDED_EVALUATION_REPORTS[candidate_id].evaluation_report_id,
        overrides=overrides,
    )


def _evaluation_report_id(candidate_id: str) -> str:
    return _RECORDED_EVALUATION_REPORTS[candidate_id].evaluation_report_id


def _candidate_id(candidate_id: str) -> str:
    return _RECORDED_CANDIDATES[candidate_id].candidate_id


def _artifact_id(candidate_id: str) -> str:
    return _RECORDED_CANDIDATES[candidate_id].primary_artifact.artifact_id


def _shadow_evidence(
    cycle: int,
    *,
    previous_shadow_run_id: str | None,
) -> ShadowRunEvidence:
    eligible_dates = (
        date(2026, 8, 18),
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 21),
        date(2026, 8, 24),
    )
    return _bound_shadow_evidence(
        shadow_run_id=f"shadow-run-{cycle}",
        eligible_eod_date=eligible_dates[cycle - 1],
        previous_shadow_run_id=previous_shadow_run_id,
    )


def _bound_shadow_evidence(
    *,
    shadow_run_id: str,
    eligible_eod_date: date,
    previous_shadow_run_id: str | None,
    cpu_prediction_seconds: float = 420.0,
    candidate_id: str | None = None,
) -> ShadowRunEvidence:
    return ShadowRunEvidence.create(
        shadow_run_id=shadow_run_id,
        candidate_id=candidate_id or _SHADOW_BINDING["candidate_id"],
        artifact_id=_SHADOW_BINDING["artifact_id"],
        evaluation_report_id=_SHADOW_BINDING["evaluation_report_id"],
        gate_decision_id=_SHADOW_BINDING["gate_decision_id"],
        approval_decision_id=_SHADOW_BINDING["approval_decision_id"],
        approval_policy_version_id=_SHADOW_BINDING["approval_policy_version_id"],
        expected_assignment=_SHADOW_BINDING["expected_assignment"],
        eligible_eod_date=eligible_eod_date,
        previous_shadow_run_id=previous_shadow_run_id,
        markets=("XTAI", "XNAS"),
        cold_load_checksum_verified=True,
        schema_compatible=True,
        probability_invariants_verified=True,
        comparison_completed=True,
        source_policy_verified=True,
        cpu_prediction_seconds=cpu_prediction_seconds,
    )


def test_bootstrap_gate_rejects_unqualified_candidate_and_tampered_metric_evidence() -> None:
    lifecycle = ModelLifecycle(InMemoryLifecycleStore())
    _record_candidate(
        lifecycle,
        candidate_id="candidate-unqualified-evidence",
        model_family_id="family-unqualified-evidence",
        improvement=12.0,
        qualification="unqualified",
    )
    evidence = HardGateEvidence.create(
        evidence_kind="formal_evidence",
        policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
        evaluation_report_id=_evaluation_report_id("candidate-unqualified-evidence"),
        evidence_refs=("sha256:gate-report",),
        measurements=(GateMeasurement("qualification.manifest_fraction", 1.0),),
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-unqualified-evidence",
            model_family_id="family-unqualified-evidence",
            candidate_id=_candidate_id("candidate-unqualified-evidence"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=evidence,
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "gate_failed"
    assert result.gate_decision is not None
    assert "qualification" in result.gate_decision.failed_gates
    assert "hard_gate_evidence" in result.gate_decision.failed_gates


def test_bootstrap_policy_is_an_immutable_content_addressed_artifact() -> None:
    assert BOOTSTRAP_GATE_POLICY_V1.policy_version_id == (
        f"sha256:{hashlib.sha256(BOOTSTRAP_GATE_POLICY_V1.serialized).hexdigest()}"
    )
    assert BOOTSTRAP_GATE_POLICY_V1.thresholds


@pytest.mark.parametrize(
    "threshold",
    [
        {"name": "metric", "category": "unknown", "comparison": "at_most", "limit": 0.0},
        {"name": "metric", "category": "security", "comparison": "unknown", "limit": 0.0},
        {"name": "metric", "category": "security", "comparison": "at_most"},
    ],
)
def test_bootstrap_policy_parser_rejects_unknown_or_incomplete_thresholds(
    threshold: dict[str, object],
) -> None:
    serialized = json.dumps(
        {"policy_name": "invalid-policy", "thresholds": [threshold]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    policy_id = f"sha256:{hashlib.sha256(serialized).hexdigest()}"

    with pytest.raises(ValueError, match="gate_policy_schema_invalid"):
        BootstrapGatePolicyVersion.from_serialized(policy_id, serialized)


def test_lifecycle_fails_closed_when_policy_uses_an_unknown_gate_category() -> None:
    serialized = json.dumps(
        {
            "policy_name": "unknown-category-policy",
            "thresholds": [
                {
                    "name": "unknown.metric",
                    "category": "not-a-gate",
                    "comparison": "at_most",
                    "limit": 0.0,
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    policy_id = f"sha256:{hashlib.sha256(serialized).hexdigest()}"

    class PolicyObjects:
        def open_by_id(self, object_id: str) -> BytesIO:
            assert object_id == policy_id
            return BytesIO(serialized)

    store = InMemoryLifecycleStore()
    lifecycle = ModelLifecycle(
        store,
        policy_repository=ObjectGatePolicyRepository(PolicyObjects()),
        evidence_repository=_VerifiedEvidenceRepository(),
        formal_qualification_verifier=_VerifiedFormalQualification(),
    )
    candidate_id = "candidate-invalid-policy"
    model_family_id = "family-invalid-policy"
    _record_candidate(
        lifecycle,
        candidate_id=candidate_id,
        model_family_id=model_family_id,
        improvement=12.0,
    )
    recorded_candidate_id = _candidate_id(candidate_id)
    report = HardGateReportArtifact.create(
        policy_version_id=policy_id,
        evaluation_report_id=_evaluation_report_id(candidate_id),
        measurements=(GateMeasurement("unknown.metric", 1.0),),
    )
    evidence = HardGateEvidence.create(
        evidence_kind="formal_evidence",
        policy_version_id=policy_id,
        evaluation_report_id=report.evaluation_report_id,
        evidence_refs=(report.artifact_id,),
        measurements=report.measurements,
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-invalid-policy",
            model_family_id=model_family_id,
            candidate_id=recorded_candidate_id,
            policy_version_id=policy_id,
            hard_gates=evidence,
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "gate_failed"
    assert result.gate_decision is not None
    assert result.gate_decision.failed_gates == ("hard_gate_evidence",)


def test_bootstrap_gate_rejects_unresolved_artifact_references() -> None:
    lifecycle = ModelLifecycle(
        InMemoryLifecycleStore(),
        formal_qualification_verifier=_VerifiedFormalQualification(),
    )
    _record_candidate(
        lifecycle,
        candidate_id="candidate-unresolved-evidence",
        model_family_id="family-unresolved-evidence",
        improvement=12.0,
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-unresolved-evidence",
            model_family_id="family-unresolved-evidence",
            candidate_id=_candidate_id("candidate-unresolved-evidence"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-unresolved-evidence"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "gate_failed"
    assert result.gate_decision is not None
    assert result.gate_decision.failed_gates == ("hard_gate_evidence",)


def test_bootstrap_gate_rejects_measurements_that_do_not_match_the_resolved_report() -> None:
    lifecycle = _verified_lifecycle(InMemoryLifecycleStore())
    candidate_id = "candidate-report-mismatch"
    model_family_id = "family-report-mismatch"
    _record_candidate(
        lifecycle,
        candidate_id=candidate_id,
        model_family_id=model_family_id,
        improvement=12.0,
    )
    recorded_candidate_id = _candidate_id(candidate_id)
    valid = _hard_gates(candidate_id)
    tampered = HardGateEvidence.create(
        evidence_kind=valid.evidence_kind,
        policy_version_id=valid.policy_version_id,
        evaluation_report_id=valid.evaluation_report_id,
        evidence_refs=valid.evidence_refs,
        measurements=tuple(
            GateMeasurement(item.name, 1.0)
            if item.name == "security.critical_finding_count"
            else item
            for item in valid.measurements
        ),
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-report-mismatch",
            model_family_id=model_family_id,
            candidate_id=recorded_candidate_id,
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=tampered,
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "gate_failed"
    assert result.gate_decision is not None
    assert result.gate_decision.failed_gates == ("hard_gate_evidence",)


def test_in_memory_store_matches_sql_command_replay_and_conflict_contract() -> None:
    store = InMemoryLifecycleStore()
    lifecycle = ModelLifecycle(store)
    _record_candidate(
        lifecycle,
        candidate_id="candidate-replay",
        model_family_id="family-replay",
        improvement=2.0,
    )

    _record_candidate(
        lifecycle,
        candidate_id="candidate-replay",
        model_family_id="family-replay",
        improvement=2.0,
    )

    assert len(store.events("family-replay")) == 1
    with pytest.raises(LifecycleConflict, match="command_id_payload_conflict"):
        _record_candidate(
            lifecycle,
            candidate_id="candidate-replay",
            model_family_id="family-replay",
            improvement=3.0,
        )


def test_sql_store_replays_canonical_tuple_payload_without_conflict() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    store = SqlAlchemyLifecycleStore(engine)
    payload: dict[str, object] = {
        "candidate_id": "candidate-sql-replay",
        "calibrator_statuses": ("sufficient_data",) * 6,
        "markets": ("XNAS", "XTAI"),
    }
    occurred_at = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)

    first = store.append(
        command_id="sql-replay",
        model_family_id="family-sql-replay",
        expected_version=0,
        event_kind="CandidateRecorded",
        payload=payload,
        occurred_at=occurred_at,
    )
    replay = store.append(
        command_id="sql-replay",
        model_family_id="family-sql-replay",
        expected_version=0,
        event_kind="CandidateRecorded",
        payload=payload,
        occurred_at=occurred_at,
    )

    assert replay == first
    assert len(store.events("family-sql-replay")) == 1


def test_candidate_recording_rejects_a_tampered_evaluation_report() -> None:
    lifecycle = _verified_lifecycle(InMemoryLifecycleStore())
    bundle = lifecycle_candidate_bundle(
        model_family_id="family-tampered-report",
        logistic_macro_f1=0.52,
    )
    tampered = replace(
        bundle,
        evaluation_report=replace(
            bundle.evaluation_report,
            logistic_equal_cell_macro_f1=0.99,
        ),
    ).with_content_id()

    with pytest.raises(LifecycleConflict, match="evaluation_report_invalid"):
        lifecycle.execute(
            RecordCandidate(
                command_id="record-tampered-report",
                candidate_bundle=tampered,
                expected_version=0,
                occurred_at=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
            )
        )


def test_candidate_recording_rejects_tampered_fold_contents_with_a_stale_id() -> None:
    lifecycle = ModelLifecycle(InMemoryLifecycleStore())
    bundle = engineering_lifecycle_candidate_bundle(
        model_family_id="family-tampered-fold",
        logistic_macro_f1=0.52,
    )
    first_fold = bundle.fold_manifest.folds[0]
    tampered_fold_manifest = replace(
        bundle.fold_manifest,
        folds=(
            replace(first_fold, test_row_ids=first_fold.test_row_ids[1:]),
            *bundle.fold_manifest.folds[1:],
        ),
    )
    tampered = replace(
        bundle,
        candidate_id="",
        fold_manifest=tampered_fold_manifest,
    ).with_content_id()

    with pytest.raises(LifecycleConflict, match="candidate_evidence_invalid"):
        lifecycle.execute(
            RecordCandidate(
                command_id="record-tampered-fold",
                candidate_bundle=tampered,
                expected_version=0,
                occurred_at=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
            )
        )


def test_candidate_recording_compares_model_wrapper_to_its_serialized_bytes() -> None:
    lifecycle = ModelLifecycle(InMemoryLifecycleStore())
    bundle = engineering_lifecycle_candidate_bundle(
        model_family_id="family-wrapped-model-bytes",
        logistic_macro_f1=0.52,
    )
    declared_seed_17 = bundle.logistic_artifacts[0]
    serialized_seed_29 = bundle.logistic_artifacts[1]
    contradictory = replace(
        declared_seed_17,
        artifact_id=serialized_seed_29.artifact_id,
        serialized=serialized_seed_29.serialized,
    )
    logistic_artifacts = (contradictory, *bundle.logistic_artifacts[1:])
    report = EvaluationReport.create(
        class_prior_equal_cell_macro_f1=(bundle.evaluation_report.class_prior_equal_cell_macro_f1),
        logistic_equal_cell_macro_f1=bundle.evaluation_report.logistic_equal_cell_macro_f1,
        seed_results=tuple(
            replace(item, logistic_artifact_id=artifact.artifact_id)
            for item, artifact in zip(
                bundle.evaluation_report.seed_results,
                logistic_artifacts,
                strict=True,
            )
        ),
        feature_batch_id=bundle.evaluation_report.feature_batch_id,
        source_policy_manifest_id=bundle.evaluation_report.source_policy_manifest_id,
        label_manifest_id=bundle.evaluation_report.label_manifest_id,
        cost_manifest_id=bundle.evaluation_report.cost_manifest_id,
        fold_manifest_id=bundle.evaluation_report.fold_manifest_id,
    )
    tampered = replace(
        bundle,
        candidate_id="",
        primary_artifact=contradictory,
        logistic_artifacts=logistic_artifacts,
        calibrators=contradictory.calibrators,
        evaluation_report=report,
    ).with_content_id()

    with pytest.raises(LifecycleConflict, match="candidate_evidence_invalid"):
        lifecycle.execute(
            RecordCandidate(
                command_id="record-wrapped-model-bytes",
                candidate_bundle=tampered,
                expected_version=0,
                occurred_at=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
            )
        )


def test_candidate_qualification_fails_closed_without_a_repository_backed_verifier() -> None:
    store = InMemoryLifecycleStore()
    lifecycle = ModelLifecycle(store)
    bundle = lifecycle_candidate_bundle(
        model_family_id="family-unverified-qualification",
        logistic_macro_f1=0.52,
    )

    lifecycle.execute(
        RecordCandidate(
            command_id="record-unverified-qualification",
            candidate_bundle=bundle,
            expected_version=0,
            occurred_at=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
        )
    )

    recorded = json.loads(store.events("family-unverified-qualification")[0].payload_json)
    assert recorded["formal_qualification"] is False


def test_approval_replay_rejects_a_corrupted_current_decision_event() -> None:
    store = InMemoryLifecycleStore()
    approval_policy = ModelApprovalPolicyVersion.create(
        policy_name="owner-operated-model-approval-v1",
        approval_mode="owner_operated",
        owner_principal_id="owner-a",
    )
    lifecycle = ModelLifecycle(store, approval_policy=approval_policy)
    command = DecideApproval(
        command_id="corrupted-approval-replay",
        model_family_id="family-corrupted-approval",
        candidate_id="candidate-corrupted-approval",
        artifact_id="sha256:artifact",
        evaluation_report_id="sha256:evaluation",
        policy_version_id="sha256:gate-policy",
        approver_id="owner-a",
        decision="approved",
        reason="Exact evidence reviewed.",
        expected_assignment="unassigned",
        expected_version=0,
        occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
    )
    store.append(
        command_id=command.command_id,
        model_family_id=command.model_family_id,
        expected_version=0,
        event_kind="ApprovalDecisionRecorded",
        payload={
            "approval_decision_id": "sha256:forged",
            "candidate_id": command.candidate_id,
            "artifact_id": command.artifact_id,
            "evaluation_report_id": command.evaluation_report_id,
            "policy_version_id": command.policy_version_id,
            "gate_decision_id": "sha256:gate-decision",
            "approval_policy_version_id": approval_policy.policy_version_id,
            "approval_mode": "owner_operated",
            "approval_policy_owner_principal_id": "owner-a",
            "independent_review": "false",
            "approver_id": command.approver_id,
            "requested_decision": command.decision,
            "decision": "approved",
            "reason": command.reason,
            "expected_assignment": command.expected_assignment,
            "expires_at": "2026-08-25T02:00:00+00:00",
            "invalidated_reason": None,
        },
        occurred_at=command.occurred_at,
    )

    with pytest.raises(LifecycleConflict, match="command_id_payload_conflict"):
        lifecycle.execute(command)


def _record_candidate(
    lifecycle: ModelLifecycle,
    *,
    candidate_id: str,
    model_family_id: str,
    improvement: float,
    actual_improvement: float | None = None,
    qualification: Literal["verified", "unqualified"] = "verified",
    intent_initiator: str = "model-operator-a",
    training_executor: str = "model-operator-b",
) -> None:
    verified_improvement = improvement if actual_improvement is None else actual_improvement
    bundle_factory = (
        lifecycle_candidate_bundle
        if qualification == "verified"
        else engineering_lifecycle_candidate_bundle
    )
    bundle = bundle_factory(
        model_family_id=model_family_id,
        logistic_macro_f1=0.4 + verified_improvement / 100,
        intent_initiator=intent_initiator,
        training_executor=training_executor,
    )
    _RECORDED_EVALUATION_REPORTS[candidate_id] = bundle.evaluation_report
    _RECORDED_CANDIDATES[candidate_id] = bundle
    result = lifecycle.execute(
        RecordCandidate(
            command_id=f"record-{candidate_id}",
            candidate_bundle=bundle,
            expected_version=0,
            occurred_at=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
        )
    )
    assert result.status == "candidate_recorded"


def test_bootstrap_gate_requires_one_point_and_every_absolute_hard_gate() -> None:
    store = InMemoryLifecycleStore()
    lifecycle = _verified_lifecycle(store)
    _record_candidate(
        lifecycle,
        candidate_id="candidate-pass",
        model_family_id="family-pass",
        improvement=1.0,
    )

    passed = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-pass",
            model_family_id="family-pass",
            candidate_id=_candidate_id("candidate-pass"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-pass"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert passed.status == "gate_passed"
    assert passed.gate_decision is not None
    assert passed.gate_decision.failed_gates == ()
    assert passed.gate_decision.serving_status == "blocked"
    recorded_gate = json.loads(store.events("family-pass")[-1].payload_json)
    assert recorded_gate["hard_gate_report_id"] == _hard_gates("candidate-pass").evidence_refs[0]
    assert (
        recorded_gate["verified_hard_gate_measurements"]
        == recorded_gate["submitted_hard_gate_measurements"]
    )

    _record_candidate(
        lifecycle,
        candidate_id="candidate-fail",
        model_family_id="family-fail",
        improvement=0.99,
    )
    failed = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-fail",
            model_family_id="family-fail",
            candidate_id=_candidate_id("candidate-fail"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates(
                "candidate-fail",
                overrides={"security.critical_finding_count": 1.0},
            ),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )
    assert failed.status == "gate_failed"
    assert failed.gate_decision is not None
    assert failed.gate_decision.failed_gates == (
        "minimum_improvement",
        "security",
    )
    assert failed.gate_decision.serving_status == "blocked"
    assert store.production_assignments("family-pass") == ()
    assert store.production_assignments("family-fail") == ()
    assert tuple(event.event_kind for event in store.events("family-fail")) == (
        "CandidateRecorded",
        "GateDecisionRecorded",
    )


def test_bootstrap_gate_recomputes_improvement_from_recorded_scores() -> None:
    store = InMemoryLifecycleStore()
    lifecycle = _verified_lifecycle(store)
    _record_candidate(
        lifecycle,
        candidate_id="candidate-self-reported-improvement",
        model_family_id="family-self-reported-improvement",
        improvement=12.0,
        actual_improvement=0.0,
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-self-reported-improvement",
            model_family_id="family-self-reported-improvement",
            candidate_id=_candidate_id("candidate-self-reported-improvement"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-self-reported-improvement"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "gate_failed"
    assert result.gate_decision is not None
    assert "minimum_improvement" in result.gate_decision.failed_gates


def test_approval_binds_exact_evidence_and_enforces_duty_separation() -> None:
    lifecycle = _verified_lifecycle(InMemoryLifecycleStore())
    _record_candidate(
        lifecycle,
        candidate_id="candidate-approval-conflict",
        model_family_id="family-approval-conflict",
        improvement=12.0,
    )
    lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-approval-conflict",
            model_family_id="family-approval-conflict",
            candidate_id=_candidate_id("candidate-approval-conflict"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-approval-conflict"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    rejected = lifecycle.execute(
        DecideApproval(
            command_id="approval-conflicted-actor",
            model_family_id="family-approval-conflict",
            candidate_id=_candidate_id("candidate-approval-conflict"),
            artifact_id=_artifact_id("candidate-approval-conflict"),
            evaluation_report_id=_evaluation_report_id("candidate-approval-conflict"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="model-operator-a",
            decision="approved",
            reason="ready for controlled shadow",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )

    assert rejected.status == "approval_rejected"
    assert rejected.approval_decision is not None
    assert rejected.approval_decision.invalidated_reason == ("duty_separation_violation")

    lifecycle = _verified_lifecycle(InMemoryLifecycleStore())
    _record_candidate(
        lifecycle,
        candidate_id="candidate-approved",
        model_family_id="family-approved",
        improvement=12.0,
    )
    lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-approved",
            model_family_id="family-approved",
            candidate_id=_candidate_id("candidate-approved"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-approved"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )
    approved = lifecycle.execute(
        DecideApproval(
            command_id="approval-separated-actor",
            model_family_id="family-approved",
            candidate_id=_candidate_id("candidate-approved"),
            artifact_id=_artifact_id("candidate-approved"),
            evaluation_report_id=_evaluation_report_id("candidate-approved"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="model-approver-c",
            decision="approved",
            reason="exact evidence reviewed for shadow only",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )

    assert approved.status == "approved"
    assert approved.approval_decision is not None
    assert approved.approval_decision.artifact_id == _artifact_id("candidate-approved")
    assert approved.approval_decision.expected_assignment == ("unassigned")
    assert approved.approval_decision.expires_at == datetime(2026, 8, 25, 2, 0, tzinfo=UTC)
    assert approved.approval_decision.invalidated_reason is None

    stale_assignment = lifecycle.execute(
        DecideApproval(
            command_id="approval-stale-assignment",
            model_family_id="family-approved",
            candidate_id=_candidate_id("candidate-approved"),
            artifact_id=_artifact_id("candidate-approved"),
            evaluation_report_id=_evaluation_report_id("candidate-approved"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="model-approver-d",
            decision="approved",
            reason="reviewed against a stale assignment",
            expected_assignment="production:stale-assignment",
            expected_version=3,
            occurred_at=datetime(2026, 8, 18, 3, 0, tzinfo=UTC),
        )
    )

    assert stale_assignment.status == "approval_rejected"
    assert stale_assignment.approval_decision is not None
    assert stale_assignment.approval_decision.invalidated_reason == ("expected_assignment_changed")


def test_owner_operated_approval_binds_the_designated_owner_policy() -> None:
    owner_id = "model-owner-a"
    approval_policy = ModelApprovalPolicyVersion.create(
        policy_name="owner-operated-model-approval-v1",
        approval_mode="owner_operated",
        owner_principal_id=owner_id,
    )
    assert approval_policy.policy_version_id == (
        f"sha256:{hashlib.sha256(approval_policy.serialized).hexdigest()}"
    )
    assert (
        ModelApprovalPolicyVersion.from_serialized(
            approval_policy.policy_version_id,
            approval_policy.serialized,
        )
        == approval_policy
    )
    lifecycle = ModelLifecycle(
        InMemoryLifecycleStore(),
        evidence_repository=_VerifiedEvidenceRepository(),
        approval_policy=approval_policy,
        formal_qualification_verifier=_VerifiedFormalQualification(),
    )
    _record_candidate(
        lifecycle,
        candidate_id="candidate-owner-operated",
        model_family_id="family-owner-operated",
        improvement=12.0,
        intent_initiator=owner_id,
        training_executor=owner_id,
    )
    lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-owner-operated",
            model_family_id="family-owner-operated",
            candidate_id=_candidate_id("candidate-owner-operated"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-owner-operated"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    approved = lifecycle.execute(
        DecideApproval(
            command_id="approval-owner-operated",
            model_family_id="family-owner-operated",
            candidate_id=_candidate_id("candidate-owner-operated"),
            artifact_id=_artifact_id("candidate-owner-operated"),
            evaluation_report_id=_evaluation_report_id("candidate-owner-operated"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id=owner_id,
            decision="approved",
            reason="Owner accepts the exact evidence and lack of independent review.",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )

    assert approved.status == "approved"
    assert approved.approval_decision is not None
    assert approved.approval_decision.approval_policy_version_id == (
        approval_policy.policy_version_id
    )
    assert approved.approval_decision.approval_mode == "owner_operated"
    assert approved.approval_decision.approval_policy_owner_principal_id == owner_id
    assert approved.approval_decision.independent_review is False

    outsider = lifecycle.execute(
        DecideApproval(
            command_id="approval-owner-operated-outsider",
            model_family_id="family-owner-operated",
            candidate_id=_candidate_id("candidate-owner-operated"),
            artifact_id=_artifact_id("candidate-owner-operated"),
            evaluation_report_id=_evaluation_report_id("candidate-owner-operated"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="different-principal",
            decision="approved",
            reason="Attempted approval by a principal not named in the policy.",
            expected_assignment="unassigned",
            expected_version=3,
            occurred_at=datetime(2026, 8, 18, 3, 0, tzinfo=UTC),
        )
    )

    assert outsider.status == "approval_rejected"
    assert outsider.approval_decision is not None
    assert outsider.approval_decision.invalidated_reason == "owner_principal_mismatch"


def test_owner_operated_approval_requires_owner_training_participation() -> None:
    owner_id = "model-owner-a"
    lifecycle = ModelLifecycle(
        InMemoryLifecycleStore(),
        evidence_repository=_VerifiedEvidenceRepository(),
        approval_policy=ModelApprovalPolicyVersion.create(
            policy_name="owner-operated-model-approval-v1",
            approval_mode="owner_operated",
            owner_principal_id=owner_id,
        ),
        formal_qualification_verifier=_VerifiedFormalQualification(),
    )
    _record_candidate(
        lifecycle,
        candidate_id="candidate-trained-by-others",
        model_family_id="family-trained-by-others",
        improvement=12.0,
        intent_initiator="model-operator-a",
        training_executor="model-operator-b",
    )
    lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-trained-by-others",
            model_family_id="family-trained-by-others",
            candidate_id=_candidate_id("candidate-trained-by-others"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-trained-by-others"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    result = lifecycle.execute(
        DecideApproval(
            command_id="approval-trained-by-others",
            model_family_id="family-trained-by-others",
            candidate_id=_candidate_id("candidate-trained-by-others"),
            artifact_id=_artifact_id("candidate-trained-by-others"),
            evaluation_report_id=_evaluation_report_id("candidate-trained-by-others"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id=owner_id,
            decision="approved",
            reason="Owner cannot self-approve training performed entirely by others.",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "approval_rejected"
    assert result.approval_decision is not None
    assert result.approval_decision.invalidated_reason == "owner_not_training_participant"


@pytest.mark.parametrize(
    ("approval_mode", "owner_principal_id"),
    (("owner_operated", None), ("separated_duties", "unexpected-owner")),
)
def test_model_approval_policy_rejects_ambiguous_owner_configuration(
    approval_mode: Literal["owner_operated", "separated_duties"],
    owner_principal_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="model_approval_policy_schema_invalid"):
        ModelApprovalPolicyVersion.create(
            policy_name="invalid-model-approval-policy",
            approval_mode=approval_mode,
            owner_principal_id=owner_principal_id,
        )


def test_model_approval_policy_loader_rejects_unknown_shape_and_stale_id() -> None:
    policy = ModelApprovalPolicyVersion.create(
        policy_name="owner-operated-model-approval-v1",
        approval_mode="owner_operated",
        owner_principal_id="model-owner-a",
    )
    unknown_shape = json.dumps(
        {
            **json.loads(policy.serialized),
            "allow_self_approval": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    with pytest.raises(ValueError, match="model_approval_policy_schema_invalid"):
        ModelApprovalPolicyVersion.from_serialized(policy.policy_version_id, unknown_shape)
    with pytest.raises(ValueError, match="model_approval_policy_checksum_mismatch"):
        ModelApprovalPolicyVersion.from_serialized("sha256:stale", policy.serialized)


def test_lifecycle_rejects_runtime_policy_fields_that_disagree_with_serialized_policy() -> None:
    policy = ModelApprovalPolicyVersion.create(
        policy_name="separated-duties-model-approval-v1",
        approval_mode="separated_duties",
        owner_principal_id=None,
    )
    contradictory = replace(
        policy,
        approval_mode="owner_operated",
        owner_principal_id="model-owner-a",
    )

    with pytest.raises(ValueError, match="model_approval_policy_checksum_mismatch"):
        ModelLifecycle(InMemoryLifecycleStore(), approval_policy=contradictory)


def test_governance_artifact_identity_uses_canonical_utf8_bytes() -> None:
    policy = ModelApprovalPolicyVersion.create(
        policy_name="單一擁有者核准",
        approval_mode="owner_operated",
        owner_principal_id="擁有者甲",
    )

    assert policy.serialized == bytes(
        '{"approval_mode":"owner_operated","owner_principal_id":"擁有者甲",'
        '"policy_name":"單一擁有者核准"}',
        "utf-8",
    )
    assert policy.policy_version_id == (
        "sha256:d20a8e062af65d11c144439d7ea053f42d5daef4165b57dced4debf8db627d37"
    )


def test_governance_query_preserves_honest_disclosure_for_legacy_approvals() -> None:
    store = InMemoryLifecycleStore()
    lifecycle = _verified_lifecycle(store)
    _record_candidate(
        lifecycle,
        candidate_id="legacy-approved-candidate",
        model_family_id="legacy-approved-family",
        improvement=12.0,
    )
    store.append(
        command_id="legacy-approval",
        model_family_id="legacy-approved-family",
        expected_version=1,
        event_kind="ApprovalDecisionRecorded",
        payload={
            "candidate_id": _candidate_id("legacy-approved-candidate"),
            "artifact_id": _artifact_id("legacy-approved-candidate"),
            "evaluation_report_id": _evaluation_report_id("legacy-approved-candidate"),
            "policy_version_id": BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            "approver_id": "model-approver-c",
            "requested_decision": "approved",
            "decision": "approved",
            "reason": "Legacy separated review.",
            "expected_assignment": "unassigned",
            "expires_at": "2026-08-25T02:00:00+00:00",
            "invalidated_reason": None,
        },
        occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
    )

    approval = ModelGovernanceQuery(store).get_backtest("legacy-approved-family")["approval"]

    assert approval == {
        "status": "approved",
        "approval_policy_version_id": None,
        "approval_mode": "separated_duties",
        "approval_policy_owner_principal_id": None,
        "independent_review": True,
    }

    _SHADOW_BINDING.clear()
    _SHADOW_BINDING.update(
        {
            "candidate_id": _candidate_id("legacy-approved-candidate"),
            "artifact_id": _artifact_id("legacy-approved-candidate"),
            "evaluation_report_id": _evaluation_report_id("legacy-approved-candidate"),
            "gate_decision_id": "unavailable:legacy-gate-decision",
            "approval_decision_id": "unavailable:legacy-approval-decision",
            "approval_policy_version_id": "unavailable:legacy-approval-policy",
            "expected_assignment": "unassigned",
        }
    )

    shadow = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-after-legacy-approval",
            model_family_id="legacy-approved-family",
            candidate_id=_candidate_id("legacy-approved-candidate"),
            evidence=_shadow_evidence(1, previous_shadow_run_id=None),
            expected_version=2,
            occurred_at=datetime(2026, 8, 19, 2, 0, tzinfo=UTC),
        )
    )

    assert shadow.status == "shadow_blocked"
    assert shadow.shadow_evidence is not None
    assert shadow.shadow_evidence.blocked_reason == "approval_policy_unbound"


def test_rejection_of_exact_evidence_cannot_be_reversed_by_a_later_approval() -> None:
    lifecycle = _verified_lifecycle(InMemoryLifecycleStore())
    _record_candidate(
        lifecycle,
        candidate_id="candidate-rejected",
        model_family_id="family-rejected",
        improvement=12.0,
    )
    lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-rejected",
            model_family_id="family-rejected",
            candidate_id=_candidate_id("candidate-rejected"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-rejected"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )
    first = lifecycle.execute(
        DecideApproval(
            command_id="reject-exact-evidence",
            model_family_id="family-rejected",
            candidate_id=_candidate_id("candidate-rejected"),
            artifact_id=_artifact_id("candidate-rejected"),
            evaluation_report_id=_evaluation_report_id("candidate-rejected"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="model-approver-c",
            decision="rejected",
            reason="evidence is not acceptable",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )
    reversal = lifecycle.execute(
        DecideApproval(
            command_id="attempt-reversal",
            model_family_id="family-rejected",
            candidate_id=_candidate_id("candidate-rejected"),
            artifact_id=_artifact_id("candidate-rejected"),
            evaluation_report_id=_evaluation_report_id("candidate-rejected"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="model-approver-d",
            decision="approved",
            reason="attempting to reverse the same decision",
            expected_assignment="unassigned",
            expected_version=3,
            occurred_at=datetime(2026, 8, 19, 2, 0, tzinfo=UTC),
        )
    )

    assert first.status == "approval_rejected"
    assert first.approval_decision is not None
    assert first.approval_decision.invalidated_reason == "approver_rejected"
    assert reversal.status == "approval_rejected"
    assert reversal.approval_decision is not None
    assert reversal.approval_decision.invalidated_reason == "prior_rejection_irreversible"


def _approved_lifecycle(
    *,
    shadow_eligibility_verifier: ShadowEligibilityVerifier | None = None,
    shadow_run_verifier: ShadowRunVerifier | None = None,
) -> tuple[ModelLifecycle, InMemoryLifecycleStore]:
    store = InMemoryLifecycleStore()
    verifier = shadow_eligibility_verifier or _JointMarketEodVerifier(
        frozenset(
            {
                date(2026, 8, 18),
                date(2026, 8, 19),
                date(2026, 8, 20),
                date(2026, 8, 21),
                date(2026, 8, 24),
            }
        )
    )
    lifecycle = _verified_lifecycle(
        store,
        shadow_eligibility_verifier=verifier,
        shadow_run_verifier=shadow_run_verifier or _VerifiedShadowRunVerifier(),
    )
    _record_candidate(
        lifecycle,
        candidate_id=("candidate-shadow"),
        model_family_id="family-shadow",
        improvement=12.0,
    )
    lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-shadow",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-shadow"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )
    approval = lifecycle.execute(
        DecideApproval(
            command_id="approval-shadow",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            artifact_id=_artifact_id("candidate-shadow"),
            evaluation_report_id=_evaluation_report_id("candidate-shadow"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="model-approver-c",
            decision="approved",
            reason="approved for five controlled shadow cycles",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )
    assert approval.approval_decision is not None
    _SHADOW_BINDING.clear()
    _SHADOW_BINDING.update(
        {
            "candidate_id": _candidate_id("candidate-shadow"),
            "artifact_id": _artifact_id("candidate-shadow"),
            "evaluation_report_id": _evaluation_report_id("candidate-shadow"),
            "gate_decision_id": approval.approval_decision.gate_decision_id,
            "approval_decision_id": approval.approval_decision.approval_decision_id,
            "approval_policy_version_id": (approval.approval_decision.approval_policy_version_id),
            "expected_assignment": "unassigned",
        }
    )
    return lifecycle, store


def test_closed_market_date_cannot_count_as_an_eligible_shadow_eod() -> None:
    lifecycle, _ = _approved_lifecycle(
        shadow_eligibility_verifier=_JointMarketEodVerifier(frozenset({date(2026, 8, 21)}))
    )

    result = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-closed-market-date",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_bound_shadow_evidence(
                shadow_run_id="shadow-run-closed-market",
                eligible_eod_date=date(2026, 8, 22),
                previous_shadow_run_id=None,
            ),
            expected_version=3,
            occurred_at=datetime(2026, 8, 22, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "shadow_blocked"
    assert result.shadow_evidence is not None
    assert result.shadow_evidence.blocked_reason == "shadow_eod_not_eligible"


def test_shadow_calendar_verification_failure_is_fail_closed() -> None:
    class FailingVerifier:
        def verify_eligible_eod(self, evidence: ShadowRunEvidence) -> bool:
            raise RuntimeError("calendar_provider_unavailable")

    lifecycle, _ = _approved_lifecycle(shadow_eligibility_verifier=FailingVerifier())

    result = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-calendar-unavailable",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_shadow_evidence(1, previous_shadow_run_id=None),
            expected_version=3,
            occurred_at=datetime(2026, 8, 18, 3, 0, tzinfo=UTC),
        )
    )

    assert result.status == "shadow_blocked"
    assert result.shadow_evidence is not None
    assert result.shadow_evidence.blocked_reason == "shadow_eod_not_eligible"


def test_shadow_run_cannot_be_credited_to_a_different_candidate() -> None:
    lifecycle, _ = _approved_lifecycle()

    result = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-wrong-candidate-binding",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_bound_shadow_evidence(
                shadow_run_id="shadow-run-wrong-candidate",
                eligible_eod_date=date(2026, 8, 18),
                previous_shadow_run_id=None,
                candidate_id="candidate-other",
            ),
            expected_version=3,
            occurred_at=datetime(2026, 8, 18, 3, 0, tzinfo=UTC),
        )
    )

    assert result.status == "shadow_blocked"
    assert result.shadow_evidence is not None
    assert result.shadow_evidence.blocked_reason == "shadow_evidence_binding_mismatch"


def test_shadow_run_requires_independent_verification() -> None:
    class UnverifiedRun:
        def verify_shadow_run(self, evidence: ShadowRunEvidence) -> bool:
            return False

    lifecycle, _ = _approved_lifecycle(shadow_run_verifier=UnverifiedRun())

    result = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-unverified-run",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_shadow_evidence(1, previous_shadow_run_id=None),
            expected_version=3,
            occurred_at=datetime(2026, 8, 18, 3, 0, tzinfo=UTC),
        )
    )

    assert result.status == "shadow_blocked"
    assert result.shadow_evidence is not None
    assert result.shadow_evidence.blocked_reason == "shadow_run_not_verified"


@pytest.mark.parametrize("latency", [-1.0, float("nan")])
def test_shadow_evidence_rejects_invalid_cpu_latency(latency: float) -> None:
    _approved_lifecycle()

    with pytest.raises(ValueError, match="shadow_cpu_prediction_seconds_invalid"):
        _bound_shadow_evidence(
            shadow_run_id="shadow-invalid-latency",
            eligible_eod_date=date(2026, 8, 18),
            previous_shadow_run_id=None,
            cpu_prediction_seconds=latency,
        )


def test_five_joint_market_shadows_complete_without_production_assignment() -> None:
    lifecycle, store = _approved_lifecycle()

    outcome = None
    for cycle in range(1, 6):
        outcome = lifecycle.execute(
            RecordShadowEod(
                command_id=f"shadow-{cycle}",
                model_family_id="family-shadow",
                candidate_id=_candidate_id("candidate-shadow"),
                evidence=_shadow_evidence(
                    cycle,
                    previous_shadow_run_id=(f"shadow-run-{cycle - 1}" if cycle > 1 else None),
                ),
                expected_version=cycle + 2,
                occurred_at=datetime.combine(
                    _shadow_evidence(
                        cycle,
                        previous_shadow_run_id=(f"shadow-run-{cycle - 1}" if cycle > 1 else None),
                    ).eligible_eod_date,
                    time(3, 0),
                    tzinfo=UTC,
                ),
            )
        )

    assert outcome is not None
    assert outcome.status == "shadow_complete"
    assert outcome.shadow_evidence is not None
    assert outcome.shadow_evidence.eligible_cycle_count == 5
    assert outcome.shadow_evidence.production_history_written is False
    assert store.production_assignments("family-shadow") == ()

    duplicate = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-duplicate-with-new-command",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_shadow_evidence(5, previous_shadow_run_id="shadow-run-4"),
            expected_version=8,
            occurred_at=datetime(2026, 8, 24, 2, 0, tzinfo=UTC),
        )
    )

    assert duplicate.status == "shadow_blocked"
    assert duplicate.shadow_evidence is not None
    assert duplicate.shadow_evidence.eligible_cycle_count == 5
    assert duplicate.shadow_evidence.blocked_reason == "duplicate_shadow_run"


def test_expired_approval_blocks_shadow_and_preserves_failure_evidence() -> None:
    lifecycle, store = _approved_lifecycle()

    blocked = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-after-expiry",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_bound_shadow_evidence(
                shadow_run_id="shadow-run-expired",
                eligible_eod_date=date(2026, 8, 25),
                previous_shadow_run_id=None,
            ),
            expected_version=3,
            occurred_at=datetime(2026, 8, 25, 2, 0, tzinfo=UTC),
        )
    )

    assert blocked.status == "shadow_blocked"
    assert blocked.shadow_evidence is not None
    assert blocked.shadow_evidence.blocked_reason == "approval_expired"
    assert store.events("family-shadow")[-1].event_kind == "ShadowEodBlocked"


def test_later_hard_gate_veto_invalidates_approval_before_shadow() -> None:
    lifecycle, store = _approved_lifecycle()
    veto = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-shadow-later-veto",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates(
                "candidate-shadow",
                overrides={"security.critical_finding_count": 1.0},
            ),
            expected_version=3,
            occurred_at=datetime(2026, 8, 18, 2, 30, tzinfo=UTC),
        )
    )

    blocked = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-after-later-veto",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_shadow_evidence(1, previous_shadow_run_id=None),
            expected_version=4,
            occurred_at=datetime(2026, 8, 18, 3, 0, tzinfo=UTC),
        )
    )

    assert veto.status == "gate_failed"
    assert blocked.status == "shadow_blocked"
    assert blocked.shadow_evidence is not None
    assert blocked.shadow_evidence.blocked_reason == "hard_gate_vetoed"
    assert blocked.shadow_evidence.eligible_cycle_count == 0
    assert store.events("family-shadow")[-1].event_kind == "ShadowEodBlocked"


def test_later_passing_gate_with_different_evidence_invalidates_stale_approval() -> None:
    lifecycle, _ = _approved_lifecycle()
    reevaluated = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-shadow-later-passing-evidence",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates(
                "candidate-shadow",
                overrides={"economics.ic_information_ratio": 0.31},
            ),
            expected_version=3,
            occurred_at=datetime(2026, 8, 18, 2, 30, tzinfo=UTC),
        )
    )

    shadow = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-after-later-passing-evidence",
            model_family_id="family-shadow",
            candidate_id=_candidate_id("candidate-shadow"),
            evidence=_shadow_evidence(1, previous_shadow_run_id=None),
            expected_version=4,
            occurred_at=datetime(2026, 8, 18, 3, 0, tzinfo=UTC),
        )
    )

    assert reevaluated.status == "gate_passed"
    assert shadow.status == "shadow_blocked"
    assert shadow.shadow_evidence is not None
    assert shadow.shadow_evidence.blocked_reason == "gate_lineage_changed"


def test_bootstrap_policy_is_permanently_disabled_after_first_assignment() -> None:
    store = InMemoryLifecycleStore(
        historical_production_assignments={
            "family-ever-assigned": ("assignment-created-by-ticket-10",)
        }
    )
    lifecycle = _verified_lifecycle(store)
    _record_candidate(
        lifecycle,
        candidate_id=("candidate-after-assignment"),
        model_family_id="family-ever-assigned",
        improvement=20.0,
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-after-assignment",
            model_family_id="family-ever-assigned",
            candidate_id=_candidate_id("candidate-after-assignment"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-after-assignment"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "gate_failed"
    assert result.gate_decision is not None
    assert result.gate_decision.failed_gates == (
        "bootstrap_disabled_after_first_production_assignment",
    )


def test_sql_lifecycle_store_preserves_append_only_state_across_instances() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    evaluation_reports = InMemoryEvaluationReportRepository()
    first_lifecycle = _verified_lifecycle(
        SqlAlchemyLifecycleStore(engine),
        evaluation_report_repository=evaluation_reports,
    )
    _record_candidate(
        first_lifecycle,
        candidate_id=("candidate-sql"),
        model_family_id="family-sql",
        improvement=5.0,
    )

    second_store = SqlAlchemyLifecycleStore(engine)
    second_lifecycle = _verified_lifecycle(
        second_store,
        evaluation_report_repository=evaluation_reports,
    )
    result = second_lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-sql",
            model_family_id="family-sql",
            candidate_id=_candidate_id("candidate-sql"),
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-sql"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert result.status == "gate_passed"
    assert tuple(event.version for event in second_store.events("family-sql")) == (
        1,
        2,
    )
    assert tuple(event.event_kind for event in second_store.events("family-sql")) == (
        "CandidateRecorded",
        "GateDecisionRecorded",
    )
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(outbox_events)).scalar_one() == 2
        assert (
            connection.execute(select(func.count()).select_from(outbox_dispatch)).scalar_one() == 2
        )
