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
from tests.modeling_support import engineering_model_history, passing_hard_gate_evidence


def test_engineering_bootstrap_tracer_fails_closed_before_approval_or_shadow() -> None:
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
        execution_purpose="engineering_acceptance",
    )
    workflow = BootstrapGovernanceWorkflow(ForecastLab(), application.model_lifecycle)
    preview = ForecastLab().develop(intent).candidate_bundle
    assert preview is not None

    candidate = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-engineering",
            intent=intent,
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=passing_hard_gate_evidence(preview.evaluation_report.evaluation_report_id),
            expected_version=0,
            occurred_at=now - timedelta(hours=1),
        )
    )

    assert candidate.status == "blocked"
    assert candidate.candidate_bundle is not None
    assert candidate.gate_decision is not None
    assert candidate.gate_decision.failed_gates == ("qualification",)
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    authorization = approver.credential.authorization_header()

    rest = client.get(
        f"/api/v1/research/model-families/{intent.model_family_id}/backtests",
        headers={"Authorization": authorization},
    )
    page = client.get(
        f"/research/model-families/{intent.model_family_id}/backtests",
        headers={"Authorization": authorization},
    )
    assert rest.status_code == 200
    assert rest.json()["shadow"] == {"eligible_cycle_count": 0, "required": 5}
    assert rest.json()["serving"] == {
        "status": "blocked",
        "production_assignment_id": None,
    }
    assert rest.json()["candidate"]["formal_qualification"] is False
    assert rest.json()["gate"]["status"] == "failed"
    assert "0 / 5" in page.text
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
            hard_gates=passing_hard_gate_evidence(preview.evaluation_report.evaluation_report_id),
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
            hard_gates=passing_hard_gate_evidence(preview.evaluation_report.evaluation_report_id),
            expected_version=0,
            occurred_at=now,
        )
    )
    assert model_failure.status == "blocked"
    assert model_failure.gate_decision is not None
    assert model_failure.gate_decision.failed_gates == ("logistic_training_failed",)
