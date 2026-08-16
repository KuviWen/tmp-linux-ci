from dataclasses import replace
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import LocalApiKeyIdentity
from stock_forecasting.bootstrap_workflow import (
    BootstrapGovernanceCommand,
    BootstrapGovernanceWorkflow,
)
from stock_forecasting.forecast_lab import ForecastLab, TrainingIntentRef
from stock_forecasting.model_governance import HardGateEvidence, RecordShadowEod
from tests.modeling_support import engineering_model_history


def _passing_hard_gates() -> HardGateEvidence:
    return HardGateEvidence(
        qualification=True,
        point_in_time=True,
        leakage=True,
        calibration=True,
        economics=True,
        stability=True,
        coverage=True,
        operational=True,
        security=True,
        reproducibility=True,
    )


def test_bootstrap_tracer_reaches_five_shadows_but_never_production() -> None:
    now = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    approver = LocalApiKeyIdentity.issue(
        owner="ticket-09-separated-approver",
        environment="development",
        scopes={"model_governance.read", "model_governance.approve"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=2),
    )
    application = build_test_application(observed_at=now, local_identity=approver)
    intent = TrainingIntentRef(
        training_intent_id="intent-ticket-09-engineering",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=now - timedelta(hours=2),
        feature_batch=engineering_model_history(),
        preregistered_seeds=(17, 29, 43),
        source_basis_verified=False,
        execution_purpose="engineering_acceptance",
    )
    workflow = BootstrapGovernanceWorkflow(ForecastLab(), application.model_lifecycle)

    candidate = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-engineering",
            intent=intent,
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_passing_hard_gates(),
            expected_version=0,
            occurred_at=now - timedelta(hours=1),
        )
    )

    assert candidate.status == "awaiting_approval"
    assert candidate.candidate_bundle is not None
    bundle = candidate.candidate_bundle
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    authorization = approver.credential.authorization_header()
    approval = client.post(
        "/api/v1/governance/approval-decisions",
        headers={
            "Authorization": authorization,
            "Idempotency-Key": "ticket-09-approval",
            "If-Match": '"2"',
        },
        json={
            "model_family_id": intent.model_family_id,
            "candidate_id": bundle.candidate_id,
            "artifact_id": bundle.primary_artifact.artifact_id,
            "evaluation_report_id": bundle.evaluation_report.evaluation_report_id,
            "policy_version_id": "bootstrap-gate-policy-v1",
            "decision": "approved",
            "reason": "Engineering evidence approved for controlled shadow only.",
            "expected_assignment": "production:dual-market-price-baseline-v1",
        },
    )
    assert approval.status_code == 201
    assert approval.json()["status"] == "approved"

    shadow = None
    for cycle in range(1, 6):
        shadow = application.model_lifecycle.execute(
            RecordShadowEod(
                command_id=f"ticket-09-shadow-{cycle}",
                model_family_id=intent.model_family_id,
                candidate_id=bundle.candidate_id,
                shadow_run_id=f"ticket-09-shadow-run-{cycle}",
                market_eligibility=("XTAI", "XNAS"),
                expected_version=cycle + 2,
                occurred_at=now + timedelta(days=cycle),
            )
        )
    assert shadow is not None
    assert shadow.status == "shadow_complete"

    rest = client.get(
        f"/api/v1/research/model-families/{intent.model_family_id}/backtests",
        headers={"Authorization": authorization},
    )
    page = client.get(
        f"/research/model-families/{intent.model_family_id}/backtests",
        headers={"Authorization": authorization},
    )
    assert rest.status_code == 200
    assert rest.json()["shadow"] == {"eligible_cycle_count": 5, "required": 5}
    assert rest.json()["serving"] == {
        "status": "blocked",
        "production_assignment_id": None,
    }
    assert rest.json()["candidate"]["formal_qualification"] is False
    assert "5 / 5" in page.text
    assert application.model_lifecycle_store.production_assignments(intent.model_family_id) == ()

    formal = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-formal-blocked",
            intent=replace(
                intent,
                training_intent_id="intent-ticket-09-formal-blocked",
                model_family_id="dual-market-price-baseline-formal-v1",
                execution_purpose="formal_candidate",
            ),
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_passing_hard_gates(),
            expected_version=0,
            occurred_at=now,
        )
    )
    assert formal.status == "blocked"
    assert formal.gate_decision is not None
    assert formal.gate_decision.failed_gates == ("unverified_source_basis",)

    malformed_rows = (
        replace(intent.feature_batch.rows[0], values=(1.0,)),
    ) + intent.feature_batch.rows[1:]
    model_failure = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-logistic-failure",
            intent=replace(
                intent,
                training_intent_id="intent-ticket-09-logistic-failure",
                model_family_id="dual-market-price-baseline-model-failure-v1",
                feature_batch=replace(intent.feature_batch, rows=malformed_rows),
            ),
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_passing_hard_gates(),
            expected_version=0,
            occurred_at=now,
        )
    )
    assert model_failure.status == "blocked"
    assert model_failure.gate_decision is not None
    assert model_failure.gate_decision.failed_gates == ("logistic_training_failed",)
