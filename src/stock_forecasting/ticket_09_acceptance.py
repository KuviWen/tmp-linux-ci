from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
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
    BOOTSTRAP_GATE_POLICY_V1,
    GateMeasurement,
    HardGateEvidence,
    ModelLifecycle,
    ObjectEvaluationReportRepository,
    ObjectGateEvidenceRepository,
    ObjectGatePolicyRepository,
    SqlAlchemyLifecycleStore,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
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


def _engineering_hard_gate_evidence(evaluation_report_id: str) -> HardGateEvidence:
    measurements = {
        "qualification.manifest_fraction": 1.0,
        "point_in_time.contract_fraction": 1.0,
        "leakage.contract_fraction": 1.0,
        "calibration.equal_cell_ece": 0.04,
        "calibration.max_full_support_cell_ece": 0.07,
        "calibration.max_degraded_support_cell_ece": 0.09,
        "calibration.sufficient_calibrator_count": 6.0,
        "calibration.identity_fallback_count": 0.0,
        "calibration.nll_degradation_fraction": 0.0,
        "calibration.brier_degradation_fraction": 0.0,
        "economics.positive_market_rank_ic_count": 2.0,
        "economics.positive_cell_rank_ic_count": 4.0,
        "economics.ic_information_ratio": 0.30,
        "economics.nonnegative_market_excess_count": 2.0,
        "economics.nonnegative_cell_excess_count": 4.0,
        "economics.drawdown_worsening_points": 2.0,
        "stability.noninferior_quarter_count": 6.0,
        "stability.max_consecutive_lagging_quarters": 2.0,
        "stability.seed_macro_f1_std_points": 1.0,
        "stability.worst_seed_delta_points": 0.1,
        "coverage.large_slice_max_decline_points": 2.0,
        "coverage.degraded_coverage_decline_points": 5.0,
        "coverage.degraded_macro_f1_decline_points": 2.0,
        "operational.trainable_parameter_count": 15_000_000.0,
        "operational.cpu_prediction_minutes": 10.0,
        "operational.daily_pipeline_minutes": 120.0,
        "security.safe_artifact_fraction": 1.0,
        "security.critical_finding_count": 0.0,
        "security.artifact_corruption_count": 0.0,
        "reproducibility.sample_replay_fraction": 1.0,
        "reproducibility.cpu_probability_max_delta": 0.000001,
    }
    return HardGateEvidence.create(
        evidence_kind="engineering_example",
        policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
        evaluation_report_id=evaluation_report_id,
        evidence_refs=("sha256:engineering-only-gate-report",),
        measurements=tuple(GateMeasurement(name, value) for name, value in measurements.items()),
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
    identity = LocalApiKeyIdentity.load(key_file)
    store = StateStore(database_url, create_schema=False)
    lifecycle_store = SqlAlchemyLifecycleStore(store.engine)
    governance_object_repository = FilesystemObjectRepository(object_root / "mg")
    governance_object_repository.put_verified(
        BytesIO(BOOTSTRAP_GATE_POLICY_V1.serialized),
        expected_checksum=BOOTSTRAP_GATE_POLICY_V1.policy_version_id.removeprefix("sha256:"),
        metadata={
            "content_type": "application/json",
            "object_kind": "bootstrap_gate_policy_version",
        },
    )
    lifecycle = ModelLifecycle(
        lifecycle_store,
        policy_repository=ObjectGatePolicyRepository(governance_object_repository),
        evidence_repository=ObjectGateEvidenceRepository(governance_object_repository),
        evaluation_report_repository=ObjectEvaluationReportRepository(governance_object_repository),
    )
    lab = ForecastLab()
    workflow = BootstrapGovernanceWorkflow(lab, lifecycle)
    governance_time = datetime.now(UTC)
    intent = TrainingIntentRef(
        training_intent_id="ticket-09-engineering-intent-v1",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="ticket-09-model-operator-a",
        executed_by="ticket-09-model-operator-b",
        created_at=observed_at,
        feature_batch=_engineering_model_history(),
        preregistered_seeds=(17, 29, 43),
        feature_schema_id="feature-schema:price-baseline-v1",
        runtime_id="runtime:ticket-09-compose-acceptance-v1",
        code_provenance="build:ticket-09-compose-acceptance-v1",
        execution_purpose="engineering_acceptance",
    )
    intent = lab.preregister(intent)
    preview = lab.develop(intent).candidate_bundle
    if preview is None:
        return {"ticket": "09", "status": "failed", "reason": "preview_missing"}
    candidate = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-engineering-v1",
            intent=intent,
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_engineering_hard_gate_evidence(
                preview.evaluation_report.evaluation_report_id
            ),
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
    formal_intent = lab.preregister(
        replace(
            intent,
            training_intent_id="",
            model_family_id="dual-market-price-baseline-formal-v1",
            execution_purpose="formal_candidate",
        )
    )
    formal = workflow.execute(
        BootstrapGovernanceCommand(
            command_id_prefix="ticket-09-formal-blocked-v1",
            intent=formal_intent,
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=_engineering_hard_gate_evidence(
                preview.evaluation_report.evaluation_report_id
            ),
            expected_version=0,
            occurred_at=governance_time,
        )
    )
    checks = {
        "lifecycle_ledger_append_only": (
            store.model_lifecycle_events_are_append_only_for_current_role()
        ),
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
        "formal_gate_fail_closed": (
            candidate.status == "blocked"
            and candidate.gate_decision is not None
            and candidate.gate_decision.failed_gates == ("qualification", "hard_gate_evidence")
        ),
        "research_rest": rest_status == 200
        and read_model.get("shadow") == {"eligible_cycle_count": 0, "required": 5},
        "research_ui": page_status == 200
        and "0 / 5" in page_text
        and "Serving blocked" in page_text,
        "approval_and_shadow_not_fabricated": (
            read_model.get("approval") == {"status": "blocked_by_gate"}
            and read_model.get("shadow") == {"eligible_cycle_count": 0, "required": 5}
        ),
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
