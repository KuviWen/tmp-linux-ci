from datetime import UTC, datetime

from sqlalchemy import create_engine

from stock_forecasting.model_governance import (
    DecideApproval,
    EvaluateBootstrapCandidate,
    HardGateEvidence,
    InMemoryLifecycleStore,
    ModelLifecycle,
    RecordCandidate,
    RecordShadowEod,
    SqlAlchemyLifecycleStore,
)
from stock_forecasting.platform.schema import metadata


def _hard_gates(**overrides: bool) -> HardGateEvidence:
    values = {
        "qualification": True,
        "point_in_time": True,
        "leakage": True,
        "calibration": True,
        "economics": True,
        "stability": True,
        "coverage": True,
        "operational": True,
        "security": True,
        "reproducibility": True,
    }
    values.update(overrides)
    return HardGateEvidence(**values)


def _record_candidate(
    lifecycle: ModelLifecycle,
    *,
    candidate_id: str,
    model_family_id: str,
    improvement: float,
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
            expected_version=0,
            occurred_at=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
        )
    )
    assert result.status == "candidate_recorded"


def test_bootstrap_gate_requires_one_point_and_every_absolute_hard_gate() -> None:
    store = InMemoryLifecycleStore()
    lifecycle = ModelLifecycle(store)
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
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_hard_gates(),
            expected_version=1,
            occurred_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )
    )

    assert passed.status == "gate_passed"
    assert passed.gate_decision is not None
    assert passed.gate_decision.failed_gates == ()
    assert passed.gate_decision.serving_status == "blocked"

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
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_hard_gates(security=False),
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
    lifecycle = ModelLifecycle(InMemoryLifecycleStore())
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
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_hard_gates(),
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
            policy_version_id="bootstrap-gate-policy-v1",
            approver_id="model-operator-a",
            decision="approved",
            reason="ready for controlled shadow",
            expected_assignment="production:dual-market-price-baseline-v1",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )

    assert rejected.status == "approval_rejected"
    assert rejected.approval_decision is not None
    assert rejected.approval_decision.invalidated_reason == ("duty_separation_violation")

    lifecycle = ModelLifecycle(InMemoryLifecycleStore())
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
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_hard_gates(),
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
            policy_version_id="bootstrap-gate-policy-v1",
            approver_id="model-approver-c",
            decision="approved",
            reason="exact evidence reviewed for shadow only",
            expected_assignment="production:dual-market-price-baseline-v1",
            expected_version=2,
            occurred_at=datetime(2026, 8, 18, 2, 0, tzinfo=UTC),
        )
    )

    assert approved.status == "approved"
    assert approved.approval_decision is not None
    assert approved.approval_decision.artifact_id == ("sha256:artifact-candidate-approved")
    assert approved.approval_decision.expected_assignment == (
        "production:dual-market-price-baseline-v1"
    )
    assert approved.approval_decision.expires_at == datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
    assert approved.approval_decision.invalidated_reason is None


def _approved_lifecycle() -> tuple[ModelLifecycle, InMemoryLifecycleStore]:
    store = InMemoryLifecycleStore()
    lifecycle = ModelLifecycle(store)
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
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_hard_gates(),
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
            policy_version_id="bootstrap-gate-policy-v1",
            approver_id="model-approver-c",
            decision="approved",
            reason="approved for five controlled shadow cycles",
            expected_assignment="production:dual-market-price-baseline-v1",
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
                shadow_run_id=f"shadow-run-{cycle}",
                market_eligibility=("XTAI", "XNAS"),
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


def test_expired_approval_blocks_shadow_and_preserves_failure_evidence() -> None:
    lifecycle, store = _approved_lifecycle()

    blocked = lifecycle.execute(
        RecordShadowEod(
            command_id="shadow-after-expiry",
            model_family_id="family-shadow",
            candidate_id="candidate-shadow",
            shadow_run_id="shadow-run-expired",
            market_eligibility=("XTAI", "XNAS"),
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
    lifecycle = ModelLifecycle(store)
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
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_hard_gates(),
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
    first_lifecycle = ModelLifecycle(SqlAlchemyLifecycleStore(engine))
    _record_candidate(
        first_lifecycle,
        candidate_id="candidate-sql",
        model_family_id="family-sql",
        improvement=5.0,
    )

    second_store = SqlAlchemyLifecycleStore(engine)
    second_lifecycle = ModelLifecycle(second_store)
    result = second_lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-sql",
            model_family_id="family-sql",
            candidate_id="candidate-sql",
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_hard_gates(),
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
