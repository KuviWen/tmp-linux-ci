from datetime import UTC, datetime, timedelta
from io import BytesIO

from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import Application, build_test_application
from stock_forecasting.authorization import (
    LocalApiKeyIdentity,
    build_fixture_authorization_policy,
)
from stock_forecasting.forecast_lab import (
    CandidateEvidenceBundle,
    FoldManifest,
    FormalQualificationEvidence,
    TrainingIntentRef,
)
from stock_forecasting.model_governance import (
    BOOTSTRAP_GATE_POLICY_V1,
    SEPARATED_DUTIES_APPROVAL_POLICY_V1,
    EvaluateBootstrapCandidate,
    ModelApprovalPolicyVersion,
    RecordCandidate,
)
from tests.modeling_support import (
    lifecycle_candidate_bundle,
    passing_hard_gate_evidence,
    passing_hard_gate_report,
)


class _VerifiedFormalQualification:
    def verify(
        self,
        evidence: FormalQualificationEvidence,
        intent: TrainingIntentRef,
        fold_manifest: FoldManifest,
    ) -> bool:
        return evidence.is_content_addressed() and evidence.binds(intent, fold_manifest)


def _governance_application(
    *, owner_operated: bool = False
) -> tuple[Application, LocalApiKeyIdentity, CandidateEvidenceBundle]:
    now = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="model-governance-reader",
        environment="development",
        scopes={"model_governance.read", "model_governance.approve"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=2),
    )
    approval_policy = (
        ModelApprovalPolicyVersion.create(
            policy_name="owner-operated-model-approval-v1",
            approval_mode="owner_operated",
            owner_principal_id=identity.context.principal_id,
        )
        if owner_operated
        else None
    )
    application = build_test_application(
        observed_at=now,
        local_identity=identity,
        model_approval_policy=approval_policy,
        formal_qualification_verifier=_VerifiedFormalQualification(),
    )
    bundle = lifecycle_candidate_bundle(
        model_family_id="dual-market-price-baseline-v1",
        logistic_macro_f1=0.812,
        intent_initiator=(identity.context.principal_id if owner_operated else "model-operator-a"),
        training_executor=(identity.context.principal_id if owner_operated else "model-operator-b"),
    )
    evaluation = bundle.evaluation_report
    report = passing_hard_gate_report(evaluation.evaluation_report_id)
    application.governance_object_repository.put_verified(
        BytesIO(report.serialized),
        expected_checksum=report.artifact_id.removeprefix("sha256:"),
        metadata={"content_type": "application/json", "object_kind": "gate_report"},
    )
    application.model_lifecycle.execute(
        RecordCandidate(
            command_id="record-research-candidate",
            candidate_bundle=bundle,
            expected_version=0,
            occurred_at=now - timedelta(hours=1),
        )
    )
    application.model_lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-research-candidate",
            model_family_id="dual-market-price-baseline-v1",
            candidate_id=bundle.candidate_id,
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=passing_hard_gate_evidence(evaluation.evaluation_report_id),
            expected_version=1,
            occurred_at=now,
        )
    )
    return application, identity, bundle


def test_governance_backtest_rest_and_ui_share_the_lifecycle_read_model() -> None:
    application, identity, bundle = _governance_application()
    evaluation_id = bundle.evaluation_report.evaluation_report_id
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {
        "Authorization": identity.credential.authorization_header(),
        "X-Request-ID": "trace-governance-read",
    }

    rest = client.get(
        "/api/v1/research/model-families/dual-market-price-baseline-v1/backtests",
        headers=headers,
    )
    page = client.get(
        "/research/model-families/dual-market-price-baseline-v1/backtests",
        headers=headers,
    )

    assert rest.status_code == 200
    assert rest.json() == {
        "model_family_id": "dual-market-price-baseline-v1",
        "candidate": {
            "candidate_id": bundle.candidate_id,
            "model_family": "regularized_multinomial_logistic",
            "artifact_id": bundle.primary_artifact.artifact_id,
            "formal_qualification": True,
        },
        "baseline": {
            "model_family": "class_prior",
            "equal_cell_macro_f1": 0.4,
        },
        "evaluation": {
            "evaluation_report_id": evaluation_id,
            "logistic_equal_cell_macro_f1": 0.812,
            "improvement_percentage_points": (0.812 - 0.4) * 100,
        },
        "calibration": {"sufficient": 6, "required": 6},
        "support": {"fold_count": 16},
        "gate": {
            "status": "passed",
            "policy_version_id": BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            "failed_gates": [],
            "hard_gate_evidence_id": passing_hard_gate_evidence(evaluation_id).evidence_id,
            "hard_gate_evidence_refs": [passing_hard_gate_report(evaluation_id).artifact_id],
        },
        "approval": {"status": "awaiting_approval"},
        "shadow": {"eligible_cycle_count": 0, "required": 5},
        "serving": {
            "status": "blocked",
            "production_assignment_id": None,
        },
    }
    assert page.status_code == 200
    assert "regularized_multinomial_logistic" in page.text
    assert "class_prior" in page.text
    assert "6 / 6" in page.text
    assert "Gate passed" in page.text
    assert "Serving blocked" in page.text
    assert "0 / 5" in page.text
    audits = application.security_audit.list_events(trace_id="trace-governance-read")
    assert len(audits) == 2
    assert {event["action"] for event in audits} == {"model_governance.read"}
    assert {event["outcome"] for event in audits} == {"allowed"}


def test_separated_approver_posts_an_exact_idempotent_approval_decision() -> None:
    application, identity, bundle = _governance_application()
    evaluation_id = bundle.evaluation_report.evaluation_report_id
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {
        "Authorization": identity.credential.authorization_header(),
        "Idempotency-Key": "approve-research-candidate",
        "If-Match": '"2"',
        "X-Request-ID": "trace-governance-approve",
    }
    body = {
        "model_family_id": "dual-market-price-baseline-v1",
        "candidate_id": bundle.candidate_id,
        "artifact_id": bundle.primary_artifact.artifact_id,
        "evaluation_report_id": evaluation_id,
        "policy_version_id": BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
        "decision": "approved",
        "reason": "Exact bootstrap evidence reviewed for shadow only.",
        "expected_assignment": "unassigned",
    }

    first = client.post(
        "/api/v1/governance/approval-decisions",
        headers=headers,
        json=body,
    )
    application._fixed_security_time = datetime(2026, 8, 17, 2, 30, tzinfo=UTC)
    replay = client.post(
        "/api/v1/governance/approval-decisions",
        headers=headers,
        json=body,
    )
    read_model = client.get(
        "/api/v1/research/model-families/dual-market-price-baseline-v1/backtests",
        headers={"Authorization": identity.credential.authorization_header()},
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert first.json() == replay.json()
    assert first.json()["status"] == "approved"
    assert first.json()["decision"]["approver_id"] == identity.context.principal_id
    assert first.json()["decision"]["artifact_id"] == bundle.primary_artifact.artifact_id
    assert first.json()["decision"]["expected_assignment"] == ("unassigned")
    assert first.json()["decision"]["approval_policy_version_id"] == (
        SEPARATED_DUTIES_APPROVAL_POLICY_V1.policy_version_id
    )
    assert first.json()["decision"]["independent_review"] is True
    assert read_model.json()["approval"] == {
        "status": "approved",
        "approval_policy_version_id": (SEPARATED_DUTIES_APPROVAL_POLICY_V1.policy_version_id),
        "approval_mode": "separated_duties",
        "approval_policy_owner_principal_id": None,
        "independent_review": True,
    }
    approval_audits = application.security_audit.list_events(trace_id="trace-governance-approve")
    assert len(approval_audits) == 2
    assert {event["action"] for event in approval_audits} == {"model_governance.approve"}


def test_designated_owner_self_approval_is_disclosed_through_rest_and_ui() -> None:
    application, identity, bundle = _governance_application(owner_operated=True)
    evaluation_id = bundle.evaluation_report.evaluation_report_id
    approval_policy = ModelApprovalPolicyVersion.create(
        policy_name="owner-operated-model-approval-v1",
        approval_mode="owner_operated",
        owner_principal_id=identity.context.principal_id,
    )
    assert (
        application.governance_object_repository.open_by_id(
            approval_policy.policy_version_id
        ).read()
        == approval_policy.serialized
    )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    authorization = identity.credential.authorization_header()

    response = client.post(
        "/api/v1/governance/approval-decisions",
        headers={
            "Authorization": authorization,
            "Idempotency-Key": "owner-approve-research-candidate",
            "If-Match": '"2"',
        },
        json={
            "model_family_id": "dual-market-price-baseline-v1",
            "candidate_id": bundle.candidate_id,
            "artifact_id": bundle.primary_artifact.artifact_id,
            "evaluation_report_id": evaluation_id,
            "policy_version_id": BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            "decision": "approved",
            "reason": "Owner accepts the exact evidence and lack of independent review.",
            "expected_assignment": "unassigned",
        },
    )
    read_model = client.get(
        "/api/v1/research/model-families/dual-market-price-baseline-v1/backtests",
        headers={"Authorization": authorization},
    )
    page = client.get(
        "/research/model-families/dual-market-price-baseline-v1/backtests",
        headers={"Authorization": authorization},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "approved"
    decision = response.json()["decision"]
    assert {
        "approval_policy_version_id": decision["approval_policy_version_id"],
        "approval_mode": decision["approval_mode"],
        "approval_policy_owner_principal_id": decision["approval_policy_owner_principal_id"],
        "independent_review": decision["independent_review"],
    } == {
        "approval_policy_version_id": approval_policy.policy_version_id,
        "approval_mode": "owner_operated",
        "approval_policy_owner_principal_id": identity.context.principal_id,
        "independent_review": False,
    }
    assert read_model.json()["approval"] == {
        "status": "approved",
        "approval_policy_version_id": approval_policy.policy_version_id,
        "approval_mode": "owner_operated",
        "approval_policy_owner_principal_id": identity.context.principal_id,
        "independent_review": False,
    }
    assert page.status_code == 200
    assert "Owner self-approved; no independent review" in page.text
    assert "擁有者自行核准；無獨立審查" in page.text
    assert (
        f"<dt>Approval policy version</dt><dd>{approval_policy.policy_version_id}</dd>" in page.text
    )
    assert "<dt>Approval mode</dt><dd>owner_operated</dd>" in page.text
    assert f"<dt>Approval policy owner</dt><dd>{identity.context.principal_id}</dd>" in page.text
    assert "<dt>Independent review</dt><dd>false</dd>" in page.text


def test_designated_owner_rejection_still_discloses_no_independent_review() -> None:
    application, identity, bundle = _governance_application(owner_operated=True)
    evaluation_id = bundle.evaluation_report.evaluation_report_id
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    authorization = identity.credential.authorization_header()

    response = client.post(
        "/api/v1/governance/approval-decisions",
        headers={
            "Authorization": authorization,
            "Idempotency-Key": "owner-reject-research-candidate",
            "If-Match": '"2"',
        },
        json={
            "model_family_id": "dual-market-price-baseline-v1",
            "candidate_id": bundle.candidate_id,
            "artifact_id": bundle.primary_artifact.artifact_id,
            "evaluation_report_id": evaluation_id,
            "policy_version_id": BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            "decision": "rejected",
            "reason": "Owner rejects the exact evidence without independent review.",
            "expected_assignment": "unassigned",
        },
    )
    page = client.get(
        "/research/model-families/dual-market-price-baseline-v1/backtests",
        headers={"Authorization": authorization},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "approval_rejected"
    assert response.json()["decision"]["independent_review"] is False
    assert page.status_code == 200
    assert "Rejected under owner-operated policy; no independent review" in page.text
    assert "依擁有者操作政策拒絕；無獨立審查" in page.text
    assert "<dt>Approval mode</dt><dd>owner_operated</dd>" in page.text
    assert "<dt>Independent review</dt><dd>false</dd>" in page.text


def test_invalidated_owner_operated_attempt_is_not_attributed_to_an_owner_rejection() -> None:
    application, identity, bundle = _governance_application(owner_operated=True)
    evaluation_id = bundle.evaluation_report.evaluation_report_id
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    authorization = identity.credential.authorization_header()

    response = client.post(
        "/api/v1/governance/approval-decisions",
        headers={
            "Authorization": authorization,
            "Idempotency-Key": "invalidate-owner-approval-attempt",
            "If-Match": '"2"',
        },
        json={
            "model_family_id": "dual-market-price-baseline-v1",
            "candidate_id": bundle.candidate_id,
            "artifact_id": "sha256:not-the-gated-artifact",
            "evaluation_report_id": evaluation_id,
            "policy_version_id": BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            "decision": "approved",
            "reason": "Attempted approval does not match the gated artifact.",
            "expected_assignment": "unassigned",
        },
    )
    page = client.get(
        "/research/model-families/dual-market-price-baseline-v1/backtests",
        headers={"Authorization": authorization},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "approval_rejected"
    assert response.json()["decision"]["invalidated_reason"] == "evidence_reference_mismatch"
    assert page.status_code == 200
    assert "Rejected under owner-operated policy; no independent review" in page.text
    assert "依擁有者操作政策拒絕；無獨立審查" in page.text
    assert "Owner rejected" not in page.text


def test_governance_scope_cannot_bypass_a_missing_action_grant() -> None:
    now = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="model-governance-denied",
        environment="development",
        scopes={"model_governance.read"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=2),
    )
    application = build_test_application(
        observed_at=now,
        local_identity=identity,
        authorization_policy_override=build_fixture_authorization_policy(
            identity.context,
            grant_actions=frozenset(),
        ),
    )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))

    response = client.get(
        "/api/v1/research/model-families/blocked-family/backtests",
        headers={
            "Authorization": identity.credential.authorization_header(),
            "X-Request-ID": "trace-governance-denied",
        },
    )

    assert response.status_code == 403
    audit = application.security_audit.list_events(trace_id="trace-governance-denied")
    assert len(audit) == 1
    assert audit[0]["action"] == "model_governance.read"
    assert audit[0]["outcome"] == "denied"
    assert audit[0]["reason_code"] == "action_grant_missing"
