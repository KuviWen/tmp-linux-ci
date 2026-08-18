import json
from dataclasses import replace
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

from stock_forecasting.contracts import HistoricalTrainingLineage
from stock_forecasting.forecast_lab import (
    FoldManifest,
    ForecastLab,
    HistoricalClaimRef,
    Market,
    TrainingIntentRef,
)
from stock_forecasting.forecasting import (
    ArtifactProvenance,
    FeatureBatch,
    FeatureRow,
    PredictionRequest,
    RegularizedMultinomialLogisticTrendForecaster,
)
from stock_forecasting.formal_cost_scenario import (
    ObjectFormalCostScenarioVerifier,
    load_conservative_cost_scenario,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from tests.modeling_support import engineering_model_history

_FEATURE_SCHEMA_ID = "feature-schema:price-baseline-v1"
_RUNTIME_ID = "runtime:cpython-3.12-safe-json-v1"
_CODE_PROVENANCE = "git:ticket-09-test-fixture"
_PROVENANCE = ArtifactProvenance(_FEATURE_SCHEMA_ID, _RUNTIME_ID, _CODE_PROVENANCE)


def test_develop_requires_the_exact_preregistered_training_intent() -> None:
    lab = ForecastLab()
    draft = TrainingIntentRef(
        training_intent_id="",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=engineering_model_history(),
        preregistered_seeds=(17, 29, 43),
        provenance=ArtifactProvenance(
            "feature-schema:price-baseline-v1",
            "runtime:cpython-3.12-safe-json-v1",
            "git:ticket-09-review-fixture",
        ),
        execution_purpose="engineering_acceptance",
    )
    intent = lab.preregister(draft)

    assert intent.training_intent_id.startswith("sha256:")
    assert intent.feature_batch.feature_batch_id.startswith("sha256:")
    assert intent.feature_batch.fold_manifest_id.startswith("sha256:")
    developed = lab.develop(intent)
    assert developed.status == "developed"
    assert developed.candidate_bundle is not None
    artifact_payload = json.loads(developed.candidate_bundle.primary_artifact.serialized)
    assert artifact_payload["feature_schema_id"] == "feature-schema:price-baseline-v1"
    assert artifact_payload["runtime_id"] == "runtime:cpython-3.12-safe-json-v1"
    assert artifact_payload["code_provenance"] == "git:ticket-09-review-fixture"

    tampered_rows = (
        replace(intent.feature_batch.rows[0], values=(999.0, -999.0)),
        *intent.feature_batch.rows[1:],
    )
    tampered_batch = replace(intent.feature_batch, rows=tampered_rows).with_content_id()
    replay = lab.develop(replace(intent, feature_batch=tampered_batch))

    assert replay.status == "blocked"
    assert replay.blocked_reasons == ("training_intent_mismatch",)
    assert replay.candidate_bundle is None

    changed_code = lab.develop(
        replace(
            intent,
            provenance=ArtifactProvenance(
                "feature-schema:price-baseline-v1",
                "runtime:cpython-3.12-safe-json-v1",
                "git:different-code",
            ),
        )
    )

    assert changed_code.status == "blocked"
    assert changed_code.blocked_reasons == ("training_intent_mismatch",)
    assert changed_code.candidate_bundle is None

    invalid_provenance = lab.develop(
        replace(intent, provenance=cast(ArtifactProvenance, {"code_provenance": 17}))
    )

    assert invalid_provenance.status == "blocked"
    assert invalid_provenance.blocked_reasons == ("training_intent_provenance_invalid",)
    assert invalid_provenance.candidate_bundle is None


@pytest.mark.parametrize(
    "seeds",
    (
        (17, 17, 43),
        cast(tuple[int, int, int], (17, 29)),
        (True, 29, 43),
        cast(tuple[int, int, int], (17.5, 29, 43)),
        cast(tuple[int, int, int], ([17], 29, 43)),
    ),
)
def test_develop_requires_exactly_three_distinct_preregistered_seeds(
    seeds: tuple[int, int, int],
) -> None:
    lab = ForecastLab()
    intent = lab.preregister(
        TrainingIntentRef(
            training_intent_id="",
            model_family_id="dual-market-price-baseline-v1",
            initiated_by="model-operator-a",
            executed_by="model-operator-b",
            created_at=datetime(2026, 8, 17, tzinfo=UTC),
            feature_batch=engineering_model_history(),
            preregistered_seeds=seeds,
            provenance=_PROVENANCE,
            execution_purpose="engineering_acceptance",
        )
    )

    outcome = lab.develop(intent)

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == ("invalid_preregistered_seeds",)
    assert outcome.candidate_bundle is None


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
        provenance=_PROVENANCE,
    )

    outcome = ForecastLab().develop(intent)

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == ("insufficient_class_support",)
    assert outcome.candidate_bundle is None


@pytest.mark.parametrize(
    ("history_end", "expected_reason"),
    (
        (date(2025, 4, 1), "insufficient_statistical_support"),
        (date(2025, 7, 1), "unverified_source_basis"),
    ),
)
def test_formal_candidate_requires_six_joint_statistical_test_quarters(
    history_end: date,
    expected_reason: str,
) -> None:
    batch = engineering_model_history()
    bounded_history = replace(
        batch,
        feature_batch_id=f"feature-batch-before-{history_end.isoformat()}",
        rows=tuple(
            row
            for row in batch.rows
            if row.session_date is not None and row.session_date < history_end
        ),
    )
    intent = TrainingIntentRef(
        training_intent_id="intent-insufficient-statistical-support",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=bounded_history,
        preregistered_seeds=(17, 29, 43),
        provenance=_PROVENANCE,
        execution_purpose="formal_candidate",
    )

    lab = ForecastLab()
    intent = lab.preregister(intent)
    outcome = lab.develop(intent)

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == (expected_reason,)
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
        provenance=_PROVENANCE,
        execution_purpose="engineering_acceptance",
    )

    lab = ForecastLab()
    outcome = lab.develop(lab.preregister(intent))

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
    assert all(item.fit_method == "temperature_scaling" for item in bundle.calibrators)
    assert all(item.post_nll <= item.pre_nll for item in bundle.calibrators)
    assert any(item.post_nll < item.pre_nll for item in bundle.calibrators)
    assert bundle.primary_artifact.calibrator_ids == tuple(
        item.calibrator_id for item in bundle.calibrators
    )
    assert bundle.evaluation_report.logistic_artifact_ids == tuple(
        artifact.artifact_id for artifact in bundle.logistic_artifacts
    )
    assert bundle.evaluation_report.class_prior_artifact_ids == tuple(
        artifact.artifact_id for artifact in bundle.class_prior_artifacts
    )
    assert tuple(item.seed for item in bundle.evaluation_report.seed_results) == (17, 29, 43)
    assert (
        bundle.evaluation_report.feature_batch_id
        == bundle.training_intent.feature_batch.feature_batch_id
    )
    assert bundle.is_content_addressed()
    all_artifacts = bundle.logistic_artifacts + bundle.class_prior_artifacts
    assert all(len(artifact.calibrator_ids) == 6 for artifact in all_artifacts)
    for artifact in all_artifacts:
        serialized_calibrator_ids = tuple(
            item["calibrator_id"] for item in json.loads(artifact.serialized)["calibrators"]
        )
        assert serialized_calibrator_ids == artifact.calibrator_ids
    assert (
        bundle.class_prior_artifacts[0].calibrator_ids
        != bundle.logistic_artifacts[0].calibrator_ids
    )
    assert len({artifact.calibrator_ids for artifact in bundle.logistic_artifacts}) == 3
    assert bundle.evaluation_report.logistic_equal_cell_macro_f1 >= 0.99
    assert bundle.evaluation_report.class_prior_equal_cell_macro_f1 < 0.50
    assert bundle.evaluation_report.improvement_percentage_points >= 1.0


def test_fold_manifest_rejects_nonfixed_or_unordered_session_boundaries() -> None:
    lab = ForecastLab()
    intent = lab.preregister(
        TrainingIntentRef(
            training_intent_id="",
            model_family_id="fold-boundary-validation",
            initiated_by="model-operator-a",
            executed_by="model-operator-b",
            created_at=datetime(2026, 8, 17, tzinfo=UTC),
            feature_batch=engineering_model_history(),
            preregistered_seeds=(17, 29, 43),
            provenance=_PROVENANCE,
            execution_purpose="engineering_acceptance",
        )
    )
    outcome = lab.develop(intent)
    assert outcome.candidate_bundle is not None
    manifest = outcome.candidate_bundle.fold_manifest

    with pytest.raises(ValueError, match="fold_manifest_schema_invalid"):
        FoldManifest.create(
            folds=manifest.folds,
            actual_history_start=manifest.actual_history_start,
            actual_history_end=manifest.actual_history_end,
            purge_sessions=19,
            embargo_sessions=20,
        )

    with pytest.raises(ValueError, match="fold_manifest_schema_invalid"):
        FoldManifest.create(
            folds=(
                replace(
                    manifest.folds[0],
                    purge_session_dates=tuple(reversed(manifest.folds[0].purge_session_dates)),
                ),
                *manifest.folds[1:],
            ),
            actual_history_start=manifest.actual_history_start,
            actual_history_end=manifest.actual_history_end,
        )


def test_fold_manifest_membership_is_verified_against_the_feature_batch() -> None:
    lab = ForecastLab()
    intent = lab.preregister(
        TrainingIntentRef(
            training_intent_id="",
            model_family_id="fold-membership-validation",
            initiated_by="model-operator-a",
            executed_by="model-operator-b",
            created_at=datetime(2026, 8, 17, tzinfo=UTC),
            feature_batch=engineering_model_history(),
            preregistered_seeds=(17, 29, 43),
            provenance=_PROVENANCE,
            execution_purpose="engineering_acceptance",
        )
    )
    outcome = lab.develop(intent)
    assert outcome.candidate_bundle is not None
    bundle = outcome.candidate_bundle
    original = bundle.fold_manifest
    tampered = FoldManifest.create(
        folds=(
            replace(
                original.folds[0],
                training_row_ids=original.folds[0].training_row_ids[1:],
            ),
            *original.folds[1:],
        ),
        actual_history_start=original.actual_history_start,
        actual_history_end=original.actual_history_end,
    )

    assert tampered.is_content_addressed()
    assert not tampered.matches_feature_batch(bundle.training_intent.feature_batch)


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
        provenance=_PROVENANCE,
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

    lab = ForecastLab()
    original = lab.develop(lab.preregister(intent)).candidate_bundle
    changed = lab.develop(lab.preregister(changed_intent)).candidate_bundle

    assert original is not None
    assert changed is not None
    comparison_rows = original_batch.rows[:6]
    for original_artifact, changed_artifact in zip(
        original.logistic_artifacts,
        changed.logistic_artifacts,
        strict=True,
    ):
        original_predictions = RegularizedMultinomialLogisticTrendForecaster.load(
            original_artifact.serialized
        ).predict(PredictionRequest(original_artifact, comparison_rows))
        changed_predictions = RegularizedMultinomialLogisticTrendForecaster.load(
            changed_artifact.serialized
        ).predict(PredictionRequest(changed_artifact, comparison_rows))
        assert original_predictions.predictions == changed_predictions.predictions
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
        provenance=_PROVENANCE,
        execution_purpose="engineering_acceptance",
    )

    lab = ForecastLab()
    outcome = lab.develop(lab.preregister(intent))

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == ("insufficient_calibration_support",)
    assert outcome.candidate_bundle is None


def test_formal_candidate_requires_verified_ticket_08_historical_claim_chain() -> None:
    intent = TrainingIntentRef(
        training_intent_id="intent-unverified-formal-history",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=engineering_model_history(),
        preregistered_seeds=(17, 29, 43),
        provenance=_PROVENANCE,
    )

    lab = ForecastLab()
    outcome = lab.develop(lab.preregister(intent))

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == ("unverified_source_basis",)
    assert outcome.candidate_bundle is None


def test_formal_candidate_consumes_both_ticket_08_verified_claim_chains(tmp_path: Path) -> None:
    verified_lineages: list[HistoricalTrainingLineage] = []

    class VerifiedClaimBoundary:
        def verify_training_lineage(
            self,
            *,
            lineage: HistoricalTrainingLineage,
            feature_batch_id: str,
            source_policy_manifest_id: str,
            label_manifest_id: str,
            fold_manifest_id: str,
            feature_rows_digest: str,
        ) -> bool:
            verified_lineages.append(lineage)
            return (
                feature_batch_id.startswith("sha256:")
                and lineage.source_policy_manifest_id == source_policy_manifest_id
                and lineage.label_manifest_id == label_manifest_id
                and lineage.fold_manifest_id == fold_manifest_id
                and lineage.feature_rows_digest == feature_rows_digest
            )

    batch = engineering_model_history()
    scenario = load_conservative_cost_scenario()
    cost_objects = FilesystemObjectRepository(tmp_path / "governance-objects")
    cost_objects.put_verified(
        BytesIO(scenario.serialized),
        expected_checksum=scenario.cost_manifest_id.removeprefix("sha256:"),
        metadata={"object_kind": "formal_cost_scenario"},
    )
    cost_verifier = ObjectFormalCostScenarioVerifier(
        cost_objects,
        approved_manifest_ids=frozenset({scenario.cost_manifest_id}),
    )
    batch = replace(batch, cost_manifest_id=scenario.cost_manifest_id)

    def lineage(market: Market) -> HistoricalTrainingLineage:
        return HistoricalTrainingLineage(
            market=market,
            claim_id=f"sha256:claim-{market.lower()}",
            dataset_version_id=f"sha256:dataset-{market.lower()}",
            adjustment_version_id=f"sha256:adjustment-{market.lower()}",
            mature_labels_id=f"sha256:labels-{market.lower()}",
            feature_snapshot_id=f"sha256:snapshot-{market.lower()}",
            qualification_fold_manifest_id=f"sha256:qualification-fold-{market.lower()}",
            source_policy_id=f"sha256:source-policy-{market.lower()}",
            source_policy_manifest_id=batch.source_policy_manifest_id,
            label_manifest_id=batch.label_manifest_id,
            fold_manifest_id=batch.fold_manifest_id,
            feature_rows_digest=batch.market_rows_digest(market),
        )

    lineages = (lineage("XTAI"), lineage("XNAS"))
    qualified_batch = replace(batch, historical_lineage=lineages).with_content_id()

    def claim_ref(item: HistoricalTrainingLineage) -> HistoricalClaimRef:
        return HistoricalClaimRef(
            market=item.market,
            claim_id=item.claim_id,
        )

    intent = TrainingIntentRef(
        training_intent_id="intent-verified-formal-history",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=qualified_batch,
        preregistered_seeds=(17, 29, 43),
        provenance=_PROVENANCE,
        historical_claims=tuple(claim_ref(item) for item in lineages),
    )
    intent = ForecastLab().preregister(intent)

    unverified_cost = ForecastLab(
        historical_claim_verifier=VerifiedClaimBoundary(),
    ).develop(intent)

    assert unverified_cost.status == "blocked"
    assert unverified_cost.blocked_reasons == ("unverified_cost_scenario",)
    assert unverified_cost.candidate_bundle is None
    verified_lineages.clear()

    outcome = ForecastLab(
        historical_claim_verifier=VerifiedClaimBoundary(),
        cost_scenario_verifier=cost_verifier,
    ).develop(intent)

    assert outcome.status == "developed"
    assert outcome.candidate_bundle is not None
    assert outcome.candidate_bundle.formal_qualification is True
    expected_batch = intent.feature_batch
    assert verified_lineages == list(expected_batch.historical_lineage)
    for artifact in (
        outcome.candidate_bundle.logistic_artifacts + outcome.candidate_bundle.class_prior_artifacts
    ):
        assert artifact.manifest_ids == (
            expected_batch.feature_batch_id,
            expected_batch.source_policy_manifest_id,
            expected_batch.label_manifest_id,
            expected_batch.fold_manifest_id,
            expected_batch.cost_manifest_id,
        )

    tampered_rows = (
        replace(qualified_batch.rows[0], values=(999.0, -999.0)),
        *qualified_batch.rows[1:],
    )
    tampered_lab = ForecastLab(historical_claim_verifier=VerifiedClaimBoundary())
    tampered_intent = tampered_lab.preregister(
        replace(
            intent,
            training_intent_id="",
            feature_batch=replace(qualified_batch, rows=tampered_rows),
        )
    )
    tampered = tampered_lab.develop(tampered_intent)
    assert tampered.status == "blocked"
    assert tampered.blocked_reasons == ("unverified_source_basis",)


def test_verified_claim_ids_cannot_qualify_an_unrelated_feature_batch() -> None:
    class PermissiveClaimBoundary:
        def verify_training_lineage(
            self,
            *,
            lineage: HistoricalTrainingLineage,
            feature_batch_id: str,
            source_policy_manifest_id: str,
            label_manifest_id: str,
            fold_manifest_id: str,
            feature_rows_digest: str,
        ) -> bool:
            return True

    intent = TrainingIntentRef(
        training_intent_id="intent-unbound-formal-history",
        model_family_id="dual-market-price-baseline-v1",
        initiated_by="model-operator-a",
        executed_by="model-operator-b",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        feature_batch=engineering_model_history(),
        preregistered_seeds=(17, 29, 43),
        provenance=_PROVENANCE,
        historical_claims=(
            HistoricalClaimRef("XTAI", "sha256:claim-xtai"),
            HistoricalClaimRef("XNAS", "sha256:claim-xnas"),
        ),
    )

    lab = ForecastLab(historical_claim_verifier=PermissiveClaimBoundary())
    outcome = lab.develop(lab.preregister(intent))

    assert outcome.status == "blocked"
    assert outcome.blocked_reasons == ("unverified_source_basis",)
