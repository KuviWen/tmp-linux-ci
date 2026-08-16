from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from stock_forecasting.authorization import LocalApiKeyIdentity
from stock_forecasting.bootstrap_workflow import (
    BootstrapGovernanceCommand,
    BootstrapGovernanceWorkflow,
)
from stock_forecasting.forecast_lab import ForecastLab, TrainingIntentRef
from stock_forecasting.forecasting import FeatureBatch, FeatureRow, TrendLabel
from stock_forecasting.model_governance import (
    HardGateEvidence,
    ModelLifecycle,
    RecordShadowEod,
    SqlAlchemyLifecycleStore,
)
from stock_forecasting.platform.state_store import StateStore


def _engineering_model_history() -> FeatureBatch:
    rows: list[FeatureRow] = []
    labels: tuple[TrendLabel, ...] = ("up", "flat", "down")
    values = {"up": (2.0, 0.0), "flat": (0.0, 2.0), "down": (-2.0, -2.0)}
    for year in (2023, 2024, 2025):
        for quarter in range(1, 5):
            cursor = date(year, ((quarter - 1) * 3) + 1, 1)
            sessions: list[date] = []
            while len(sessions) < 21:
                if cursor.weekday() < 5:
                    sessions.append(cursor)
                cursor += timedelta(days=1)
            for session_index, session_date in enumerate(sessions):
                label = labels[session_index % len(labels)]
                for market in ("XTAI", "XNAS"):
                    for horizon in (1, 5, 20):
                        rows.append(
                            FeatureRow(
                                row_id=f"{market}-{horizon}-{session_date.isoformat()}",
                                market=market,
                                horizon_sessions=horizon,
                                values=values[label],
                                label=label,
                                session_date=session_date,
                            )
                        )
    return FeatureBatch(
        feature_batch_id="ticket-09-engineering-feature-batch-12q",
        source_policy_manifest_id="ticket-09-engineering-source-policy-v1",
        label_manifest_id="trend-label-v1",
        fold_manifest_id="ticket-09-fold-plan-v1",
        cost_manifest_id="ticket-09-zero-fee-cost-v1",
        rows=tuple(rows),
    )


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


def _request(
    *,
    base_url: str,
    path: str,
    identity: LocalApiKeyIdentity,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    content = None
    request_headers = {
        "Authorization": identity.credential.authorization_header(),
        **(headers or {}),
    }
    if payload is not None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=content,
        headers=request_headers,
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - deployed fixed base URL
        return response.status, response.read().decode("utf-8")


def run_ticket_09_acceptance(
    *,
    database_url: str,
    object_root: Path,
    observed_at: datetime,
    base_url: str,
    key_file: Path,
) -> dict[str, object]:
    del object_root
    identity = LocalApiKeyIdentity.load(key_file)
    store = StateStore(database_url, create_schema=False)
    lifecycle_store = SqlAlchemyLifecycleStore(store.engine)
    lifecycle = ModelLifecycle(lifecycle_store)
    workflow = BootstrapGovernanceWorkflow(ForecastLab(), lifecycle)
    governance_time = datetime.now(UTC)
    intent = TrainingIntentRef(
        training_intent_id="ticket-09-engineering-intent-v1",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="ticket-09-model-operator-a",
        executed_by="ticket-09-model-operator-b",
        created_at=observed_at,
        feature_batch=_engineering_model_history(),
        preregistered_seeds=(17, 29, 43),
        source_basis_verified=False,
        execution_purpose="engineering_acceptance",
    )
    candidate = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-engineering-v1",
            intent=intent,
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_passing_hard_gates(),
            expected_version=0,
            occurred_at=governance_time,
        )
    )
    bundle = candidate.candidate_bundle
    if bundle is None:
        return {
            "ticket": "09",
            "status": "failed",
            "reason": "engineering_candidate_missing",
        }
    approval_status, approval_text = _request(
        base_url=base_url,
        path="/api/v1/governance/approval-decisions",
        identity=identity,
        method="POST",
        headers={"Idempotency-Key": "ticket-09-approval-v1", "If-Match": '"2"'},
        payload={
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
    approval = json.loads(approval_text)
    shadow = None
    for cycle in range(1, 6):
        shadow = lifecycle.execute(
            RecordShadowEod(
                command_id=f"ticket-09-shadow-{cycle}",
                model_family_id=intent.model_family_id,
                candidate_id=bundle.candidate_id,
                shadow_run_id=f"ticket-09-shadow-run-{cycle}",
                market_eligibility=("XTAI", "XNAS"),
                expected_version=cycle + 2,
                occurred_at=governance_time + timedelta(days=cycle),
            )
        )
    rest_status, rest_text = _request(
        base_url=base_url,
        path=(f"/api/v1/research/model-families/{intent.model_family_id}/backtests"),
        identity=identity,
    )
    page_status, page_text = _request(
        base_url=base_url,
        path=f"/research/model-families/{intent.model_family_id}/backtests",
        identity=identity,
    )
    read_model = json.loads(rest_text)
    formal = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-formal-blocked-v1",
            intent=replace(
                intent,
                training_intent_id="ticket-09-formal-blocked-intent-v1",
                model_family_id="dual-market-price-baseline-formal-v1",
                execution_purpose="formal_candidate",
            ),
            policy_version_id="bootstrap-gate-policy-v1",
            hard_gates=_passing_hard_gates(),
            expected_version=0,
            occurred_at=governance_time,
        )
    )
    checks = {
        "shared_forecaster_evidence": (
            len(bundle.logistic_artifacts) == 3
            and len(bundle.class_prior_artifacts) == 3
            and len(bundle.calibrators) == 6
        ),
        "walk_forward_contract": (
            bundle.fold_manifest.fold_count == 16
            and all(
                len(fold.purge_session_dates) == 20 and len(fold.embargo_session_dates) == 20
                for fold in bundle.fold_manifest.folds
            )
        ),
        "bootstrap_gate": (
            candidate.status == "awaiting_approval"
            and bundle.evaluation_report.improvement_percentage_points >= 1.0
        ),
        "separated_approval": approval_status == 201 and approval.get("status") == "approved",
        "five_joint_shadows": shadow is not None and shadow.status == "shadow_complete",
        "research_rest": rest_status == 200
        and read_model.get("shadow") == {"eligible_cycle_count": 5, "required": 5},
        "research_ui": page_status == 200
        and "5 / 5" in page_text
        and "Serving blocked" in page_text,
        "not_production": read_model.get("serving")
        == {"status": "blocked", "production_assignment_id": None},
        "formal_source_fail_closed": formal.gate_decision is not None
        and formal.gate_decision.failed_gates == ("unverified_source_basis",),
    }
    return {
        "ticket": "09",
        "status": "passed" if all(checks.values()) else "failed",
        "evidence_kind": "engineering_acceptance",
        "formal_model_qualification": "not_claimed",
        "checks": checks,
        "candidate_id": bundle.candidate_id,
        "evaluation_report_id": bundle.evaluation_report.evaluation_report_id,
        "trace_ids": ["P2-TRACE-MODEL-01", "GATE-MODEL-01"],
    }
