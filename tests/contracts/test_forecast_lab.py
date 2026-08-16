from dataclasses import replace
from datetime import UTC, date, datetime

from stock_forecasting.forecast_lab import ForecastLab, TrainingIntentRef
from stock_forecasting.forecasting import FeatureBatch, FeatureRow
from tests.modeling_support import engineering_model_history


def test_forecast_lab_blocks_candidate_when_class_support_is_incomplete() -> None:
    batch = FeatureBatch(
        feature_batch_id="feature-batch-incomplete",
        source_policy_manifest_id="source-policy-qualified-v1",
        label_manifest_id="label-v1",
        fold_manifest_id="fold-plan-v1",
        cost_manifest_id="cost-v1",
        rows=(
            FeatureRow(
                "xtai-up",
                "XTAI",
                1,
                (1.0, 0.0),
                "up",
                session_date=date(2025, 1, 2),
            ),
            FeatureRow(
                "xnas-up",
                "XNAS",
                1,
                (1.0, 0.0),
                "up",
                session_date=date(2025, 1, 2),
            ),
        ),
    )
    intent = TrainingIntentRef(
        training_intent_id="intent-incomplete",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=batch,
        preregistered_seeds=(17, 29, 43),
        source_basis_verified=True,
    )

    outcome = ForecastLab().develop(intent)

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == ("insufficient_class_support",)
    assert outcome.candidate_bundle is None


def test_forecast_lab_builds_reproducible_dual_market_bootstrap_evidence() -> None:
    intent = TrainingIntentRef(
        training_intent_id="intent-engineering-success",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=engineering_model_history(),
        preregistered_seeds=(17, 29, 43),
        source_basis_verified=False,
        execution_purpose="engineering_acceptance",
    )

    outcome = ForecastLab().develop(intent)

    assert outcome.status == "developed"
    assert outcome.blocked_reasons == ()
    assert outcome.candidate_bundle is not None
    bundle = outcome.candidate_bundle
    assert bundle.formal_qualification is False
    assert bundle.fold_manifest.fold_count == 16
    assert bundle.fold_manifest.actual_history_start == date(2023, 1, 2)
    assert bundle.fold_manifest.actual_history_end == date(2025, 10, 29)
    tested_quarters_by_market = {
        market: [fold.test_quarter for fold in bundle.fold_manifest.folds if fold.market == market]
        for market in ("XTAI", "XNAS")
    }
    assert tested_quarters_by_market == {
        "XTAI": [
            "2024-Q1",
            "2024-Q2",
            "2024-Q3",
            "2024-Q4",
            "2025-Q1",
            "2025-Q2",
            "2025-Q3",
            "2025-Q4",
        ],
        "XNAS": [
            "2024-Q1",
            "2024-Q2",
            "2024-Q3",
            "2024-Q4",
            "2025-Q1",
            "2025-Q2",
            "2025-Q3",
            "2025-Q4",
        ],
    }
    for fold in bundle.fold_manifest.folds:
        assert len(fold.purge_session_dates) == 20
        assert len(fold.embargo_session_dates) == 20
        assert set(fold.training_row_ids).isdisjoint(fold.validation_row_ids)
        assert set(fold.training_row_ids).isdisjoint(fold.test_row_ids)
        assert set(fold.validation_row_ids).isdisjoint(fold.test_row_ids)
    assert tuple(artifact.seed for artifact in bundle.logistic_artifacts) == (17, 29, 43)
    assert len(bundle.calibrators) == 6
    assert all(item.status == "sufficient_data" for item in bundle.calibrators)
    assert bundle.primary_artifact.calibrator_ids == tuple(
        item.calibrator_id for item in bundle.calibrators
    )
    assert bundle.primary_artifact.evaluation_report_id == (
        bundle.evaluation_report.evaluation_report_id
    )
    assert bundle.evaluation_report.logistic_equal_cell_macro_f1 >= 0.99
    assert bundle.evaluation_report.class_prior_equal_cell_macro_f1 < 0.50
    assert bundle.evaluation_report.improvement_percentage_points >= 1.0


def test_latest_test_quarter_cannot_influence_fitted_artifact_or_calibrators() -> None:
    original_batch = engineering_model_history()
    intent = TrainingIntentRef(
        training_intent_id="intent-test-isolation",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=original_batch,
        preregistered_seeds=(17, 29, 43),
        source_basis_verified=False,
        execution_purpose="engineering_acceptance",
    )
    changed_rows = tuple(
        replace(row, values=(99.0, -99.0), label="down")
        if row.session_date is not None and row.session_date >= date(2025, 10, 1)
        else row
        for row in original_batch.rows
    )
    changed_intent = replace(
        intent,
        feature_batch=replace(original_batch, rows=changed_rows),
    )

    original = ForecastLab().develop(intent).candidate_bundle
    changed = ForecastLab().develop(changed_intent).candidate_bundle

    assert original is not None
    assert changed is not None
    assert tuple(
        artifact.training_selection_id for artifact in original.logistic_artifacts
    ) == tuple(artifact.training_selection_id for artifact in changed.logistic_artifacts)
    assert tuple(artifact.model_parameters_id for artifact in original.logistic_artifacts) == tuple(
        artifact.model_parameters_id for artifact in changed.logistic_artifacts
    )
    assert original.calibrators == changed.calibrators
    assert (
        original.evaluation_report.evaluation_report_id
        != changed.evaluation_report.evaluation_report_id
    )


def test_insufficient_latest_validation_classes_block_all_candidate_artifacts() -> None:
    batch = engineering_model_history()
    calibration_dates = {
        row.session_date
        for row in batch.rows
        if row.session_date is not None
        and (
            date(2025, 4, 2) <= row.session_date <= date(2025, 4, 29)
            or row.session_date == date(2025, 7, 1)
        )
    }
    rows = tuple(
        replace(row, label="up") if row.session_date in calibration_dates else row
        for row in batch.rows
    )
    intent = TrainingIntentRef(
        training_intent_id="intent-calibration-insufficient",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=replace(batch, rows=rows),
        preregistered_seeds=(17, 29, 43),
        source_basis_verified=False,
        execution_purpose="engineering_acceptance",
    )

    outcome = ForecastLab().develop(intent)

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == ("insufficient_calibration_support",)
    assert outcome.candidate_bundle is None
