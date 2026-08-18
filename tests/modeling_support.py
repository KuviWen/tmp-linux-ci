from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Literal

from stock_forecasting.evaluation_report import EvaluationReport, SeedArtifactEvaluation
from stock_forecasting.forecast_lab import (
    CandidateEvidenceBundle,
    ForecastLab,
    FormalQualificationEvidence,
    TrainingIntentRef,
)
from stock_forecasting.forecasting import (
    ArtifactProvenance,
    FeatureBatch,
    FeatureRow,
    TrendLabel,
)
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


@lru_cache(maxsize=1)
def _lifecycle_candidate_template() -> CandidateEvidenceBundle:
    lab = ForecastLab()
    intent = lab.preregister(
        TrainingIntentRef(
            training_intent_id="",
            model_family_id="lifecycle-candidate-template",
            initiated_by="model-operator-a",
            executed_by="model-operator-b",
            created_at=datetime(2026, 8, 17, tzinfo=UTC),
            feature_batch=engineering_model_history(),
            preregistered_seeds=(17, 29, 43),
            provenance=ArtifactProvenance(
                "feature-schema:price-baseline-v1",
                "runtime:cpython-3.12-safe-json-v1",
                "git:ticket-09-lifecycle-fixture",
            ),
            execution_purpose="engineering_acceptance",
        )
    )
    outcome = lab.develop(intent)
    assert outcome.candidate_bundle is not None
    return outcome.candidate_bundle


def lifecycle_candidate_bundle(
    *,
    model_family_id: str,
    logistic_macro_f1: float,
    intent_initiator: str = "model-operator-a",
    training_executor: str = "model-operator-b",
) -> CandidateEvidenceBundle:
    return _candidate_bundle(
        model_family_id=model_family_id,
        logistic_macro_f1=logistic_macro_f1,
        execution_purpose="formal_candidate",
        intent_initiator=intent_initiator,
        training_executor=training_executor,
    )


def engineering_lifecycle_candidate_bundle(
    *,
    model_family_id: str,
    logistic_macro_f1: float,
    intent_initiator: str = "model-operator-a",
    training_executor: str = "model-operator-b",
) -> CandidateEvidenceBundle:
    return _candidate_bundle(
        model_family_id=model_family_id,
        logistic_macro_f1=logistic_macro_f1,
        execution_purpose="engineering_acceptance",
        intent_initiator=intent_initiator,
        training_executor=training_executor,
    )


def _candidate_bundle(
    *,
    model_family_id: str,
    logistic_macro_f1: float,
    execution_purpose: Literal["formal_candidate", "engineering_acceptance"],
    intent_initiator: str,
    training_executor: str,
) -> CandidateEvidenceBundle:
    template = _lifecycle_candidate_template()
    intent = replace(
        template.training_intent,
        training_intent_id="",
        model_family_id=model_family_id,
        initiated_by=intent_initiator,
        executed_by=training_executor,
        execution_purpose=execution_purpose,
    ).with_content_id()
    report = EvaluationReport.create(
        class_prior_equal_cell_macro_f1=0.4,
        logistic_equal_cell_macro_f1=logistic_macro_f1,
        seed_results=tuple(
            SeedArtifactEvaluation(
                seed=seed,
                logistic_artifact_id=logistic.artifact_id,
                class_prior_artifact_id=prior.artifact_id,
                logistic_macro_f1=logistic_macro_f1,
            )
            for seed, logistic, prior in zip(
                intent.preregistered_seeds,
                template.logistic_artifacts,
                template.class_prior_artifacts,
                strict=True,
            )
        ),
        feature_batch_id=intent.feature_batch.feature_batch_id,
        source_policy_manifest_id=intent.feature_batch.source_policy_manifest_id,
        label_manifest_id=intent.feature_batch.label_manifest_id,
        cost_manifest_id=intent.feature_batch.cost_manifest_id,
        fold_manifest_id=template.fold_manifest.fold_manifest_id,
    )
    return replace(
        template,
        candidate_id="",
        training_intent=intent,
        evaluation_report=report,
        qualification_evidence=(
            FormalQualificationEvidence.create(intent, template.fold_manifest)
            if execution_purpose == "formal_candidate"
            else None
        ),
    ).with_content_id()
