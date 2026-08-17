from datetime import date, timedelta

from stock_forecasting.forecasting import FeatureBatch, FeatureRow, TrendLabel
from stock_forecasting.model_governance import (
    BOOTSTRAP_GATE_POLICY_V1,
    GateMeasurement,
    HardGateEvidence,
    HardGateReportArtifact,
)


def passing_hard_gate_report(
    evaluation_report_id: str,
    *,
    overrides: dict[str, float] | None = None,
) -> HardGateReportArtifact:
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
    measurements.update(overrides or {})
    return HardGateReportArtifact.create(
        policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
        evaluation_report_id=evaluation_report_id,
        measurements=tuple(GateMeasurement(name, value) for name, value in measurements.items()),
    )


def passing_hard_gate_evidence(
    evaluation_report_id: str,
    *,
    overrides: dict[str, float] | None = None,
) -> HardGateEvidence:
    report = passing_hard_gate_report(evaluation_report_id, overrides=overrides)
    return HardGateEvidence.create(
        evidence_kind="formal_evidence",
        policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
        evaluation_report_id=evaluation_report_id,
        evidence_refs=(report.artifact_id,),
        measurements=report.measurements,
    )


def engineering_model_history() -> FeatureBatch:
    rows: list[FeatureRow] = []
    labels: tuple[TrendLabel, ...] = ("up", "flat", "down")
    values = {"up": (2.0, 0.0), "flat": (0.0, 2.0), "down": (-2.0, -2.0)}
    for year in (2023, 2024, 2025):
        for quarter in range(1, 5):
            month = (quarter - 1) * 3 + 1
            cursor = date(year, month, 1)
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
                                row_id=(f"{market}-{horizon}-{session_date.isoformat()}"),
                                market=market,
                                horizon_sessions=horizon,
                                values=values[label],
                                label=label,
                                session_date=session_date,
                            )
                        )
    return FeatureBatch(
        feature_batch_id="feature-batch-engineering-12q",
        source_policy_manifest_id="source-policy-engineering-v1",
        label_manifest_id="label-v1",
        fold_manifest_id="fold-plan-v1",
        cost_manifest_id="cost-zero-fee-v1",
        rows=tuple(rows),
    )
