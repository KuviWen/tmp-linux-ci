import hashlib
import json
from datetime import UTC, date, datetime
from io import BytesIO

import pytest
from sqlalchemy import create_engine, func, select

from stock_forecasting.model_governance import (
    BOOTSTRAP_GATE_POLICY_V1,
    BootstrapGatePolicyVersion,
    DecideApproval,
    EvaluateBootstrapCandidate,
    GateMeasurement,
    HardGateEvidence,
    HardGateReportArtifact,
    InMemoryLifecycleStore,
    LifecycleConflict,
    ModelLifecycle,
    ObjectGatePolicyRepository,
    RecordCandidate,
    RecordShadowEod,
    ShadowRunEvidence,
    SqlAlchemyLifecycleStore,
)
from stock_forecasting.platform.outbox_relay import outbox_dispatch, outbox_events
from stock_forecasting.platform.schema import metadata
from tests.modeling_support import passing_hard_gate_evidence


class _VerifiedEvidenceRepository:
    def resolve(self, evidence: HardGateEvidence) -> HardGateReportArtifact | None:
        report = HardGateReportArtifact.create(
            policy_version_id=evidence.policy_version_id,
            evaluation_report_id=evidence.evaluation_report_id,
            measurements=evidence.measurements,
        )
        return report if evidence.evidence_refs == (report.artifact_id,) else None


def _verified_lifecycle(
    store: InMemoryLifecycleStore | SqlAlchemyLifecycleStore,
) -> ModelLifecycle:
    return ModelLifecycle(store, evidence_repository=_VerifiedEvidenceRepository())


def _hard_gates(
    candidate_id: str,
    *,
    overrides: dict[str, float] | None = None,
) -> HardGateEvidence:
    return passing_hard_gate_evidence(
        f"sha256:evaluation-{candidate_id}",
        overrides=overrides,
    )


def _shadow_evidence(
    cycle: int,
    *,
    previous_shadow_run_id: str | None,
) -> ShadowRunEvidence:
    return ShadowRunEvidence.create(
        shadow_run_id=f"shadow-run-{cycle}",
        eligible_eod_date=date(2026, 8, 18 + cycle),
        previous_shadow_run_id=previous_shadow_run_id,
        markets=("XTAI", "XNAS"),
        cold_load_checksum_verified=True,
        schema_compatible=True,
        probability_invariants_verified=True,
        comparison_completed=True,
        source_policy_verified=True,
        cpu_prediction_seconds=420.0,
    )


def test_bootstrap_gate_rejects_unqualified_candidate_and_tampered_metric_evidence() -> None:
    lifecycle = ModelLifecycle(InMemoryLifecycleStore())
    _record_candidate(
        lifecycle,
        candidate_id="candidate-unqualified-evidence",
        model_family_id="family-unqualified-evidence",
        improvement=12.0,
        formal_qualification=False,
    )
    evidence = HardGateEvidence.create(
        evidence_kind="formal_evidence",
        policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
        evaluation_report_id="sha256:evaluation-candidate-unqualified-evidence",
        evidence_refs=("sha256:gate-report",),
        measurements=(GateMeasurement("qualification.manifest_fraction", 1.0),),
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-unqualified-evidence",
            model_family_id="family-unqualified-evidence",
            candidate_id="candidate-unqualified-evidence",
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
    )
    candidate_id = "candidate-invalid-policy"
    model_family_id = "family-invalid-policy"
    _record_candidate(
        lifecycle,
        candidate_id=candidate_id,
        model_family_id=model_family_id,
        improvement=12.0,
    )
    report = HardGateReportArtifact.create(
        policy_version_id=policy_id,
        evaluation_report_id=f"sha256:evaluation-{candidate_id}",
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
            candidate_id=candidate_id,
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
    lifecycle = ModelLifecycle(InMemoryLifecycleStore())
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
            candidate_id="candidate-unresolved-evidence",
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
            candidate_id=candidate_id,
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


def _record_candidate(
    lifecycle: ModelLifecycle,
    *,
    candidate_id: str,
    model_family_id: str,
    improvement: float,
    formal_qualification: bool = True,
) -> None:
    result = lifecycle.execute(
        RecordCandidate(
            command_id=f"record-{candidate_id}",
            model_family_id=model_family_id,
            candidate_id=candidate_id,
            model_family="regularized_multinomial_logistic",
            artifact_id=f"sha256:artifact-{candidate_id}",
            evaluation_report_id=f"sha256:evaluation-{candidate_id}",
            training_intent_id=f"intent-{candidate_id}",
            intent_initiator="model-operator-a",
            training_executor="model-operator-b",
            improvement_percentage_points=improvement,
            calibrator_statuses=("sufficient_data",) * 6,
            formal_qualification=formal_qualification,
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
            candidate_id="candidate-pass",
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
            candidate_id="candidate-fail",
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
            candidate_id="candidate-approval-conflict",
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
            candidate_id="candidate-approval-conflict",
            artifact_id="sha256:artifact-candidate-approval-conflict",
            evaluation_report_id="sha256:evaluation-candidate-approval-conflict",
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
            candidate_id="candidate-approved",
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
            candidate_id="candidate-approved",
            artifact_id="sha256:artifact-candidate-approved",
            evaluation_report_id="sha256:evaluation-candidate-approved",
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
    assert approved.approval_decision.artifact_id == ("sha256:artifact-candidate-approved")
    assert approved.approval_decision.expected_assignment == ("unassigned")
    assert approved.approval_decision.expires_at == datetime(2026, 8, 25, 2, 0, tzinfo=UTC)
    assert approved.approval_decision.invalidated_reason is None

    stale_assignment = lifecycle.execute(
        DecideApproval(
            command_id="approval-stale-assignment",
            model_family_id="family-approved",
            candidate_id="candidate-approved",
            artifact_id="sha256:artifact-candidate-approved",
            evaluation_report_id="sha256:evaluation-candidate-approved",
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
            candidate_id="candidate-rejected",
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
            candidate_id="candidate-rejected",
            artifact_id="sha256:artifact-candidate-rejected",
            evaluation_report_id="sha256:evaluation-candidate-rejected",
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
            candidate_id="candidate-rejected",
            artifact_id="sha256:artifact-candidate-rejected",
            evaluation_report_id="sha256:evaluation-candidate-rejected",
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


def _approved_lifecycle() -> tuple[ModelLifecycle, InMemoryLifecycleStore]:
    store = InMemoryLifecycleStore()
    lifecycle = _verified_lifecycle(store)
    _record_candidate(
        lifecycle,
        candidate_id="candidate-shadow",
        model_family_id="family-shadow",
        improvement=12.0,
    )
    lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-shadow",
            model_family_id="family-shadow",
            candidate_id="candidate-shadow",
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_hard_gates("candidate-shadow"),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )
    lifecycle.execute(
        DecideApproval(
            command_id="approval-shadow",
            model_family_id="family-shadow",
            candidate_id="candidate-shadow",
            artifact_id="sha256:artifact-candidate-shadow",
            evaluation_report_id="sha256:evaluation-candidate-shadow",
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id="model-approver-c",
            decision="approved",
            reason="approved for five controlled shadow cycles",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )
    return lifecycle, store


def test_five_joint_market_shadows_complete_without_production_assignment() -> None:
    lifecycle, store = _approved_lifecycle()

    outcome = None
    for cycle in range(1, 6):
        outcome = lifecycle.execute(
            RecordShadowEod(
                command_id=f"shadow-{cycle}",
                model_family_id="family-shadow",
                candidate_id="candidate-shadow",
                evidence=_shadow_evidence(
                    cycle,
                    previous_shadow_run_id=(f"shadow-run-{cycle - 1}" if cycle > 1 else None),
                ),
                expected_version=cycle + 2,
                occurred_at=datetime(2026, 8, 18 + cycle, 2, 0, tzinfo=UTC),
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
            candidate_id="candidate-shadow",
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
            candidate_id="candidate-shadow",
            evidence=ShadowRunEvidence.create(
                shadow_run_id="shadow-run-expired",
                eligible_eod_date=date(2026, 8, 25),
                previous_shadow_run_id=None,
                markets=("XTAI", "XNAS"),
                cold_load_checksum_verified=True,
                schema_compatible=True,
                probability_invariants_verified=True,
                comparison_completed=True,
                source_policy_verified=True,
                cpu_prediction_seconds=420.0,
            ),
            expected_version=3,
            occurred_at=datetime(2026, 8, 25, 2, 0, tzinfo=UTC),
        )
    )

    assert blocked.status == "shadow_blocked"
    assert blocked.shadow_evidence is not None
    assert blocked.shadow_evidence.blocked_reason == "approval_expired"
    assert store.events("family-shadow")[-1].event_kind == "ShadowEodBlocked"


def test_bootstrap_policy_is_permanently_disabled_after_first_assignment() -> None:
    store = InMemoryLifecycleStore(
        historical_production_assignments={
            "family-ever-assigned": ("assignment-created-by-ticket-10",)
        }
    )
    lifecycle = _verified_lifecycle(store)
    _record_candidate(
        lifecycle,
        candidate_id="candidate-after-assignment",
        model_family_id="family-ever-assigned",
        improvement=20.0,
    )

    result = lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-after-assignment",
            model_family_id="family-ever-assigned",
            candidate_id="candidate-after-assignment",
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
    first_lifecycle = _verified_lifecycle(SqlAlchemyLifecycleStore(engine))
    _record_candidate(
        first_lifecycle,
        candidate_id="candidate-sql",
        model_family_id="family-sql",
        improvement=5.0,
    )

    second_store = SqlAlchemyLifecycleStore(engine)
    second_lifecycle = _verified_lifecycle(second_store)
    result = second_lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-sql",
            model_family_id="family-sql",
            candidate_id="candidate-sql",
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
