from dataclasses import replace

import pytest

from stock_forecasting.evaluation_report import EvaluationReport, SeedArtifactEvaluation


def _seed_result(seed: int, score: float) -> SeedArtifactEvaluation:
    return SeedArtifactEvaluation(
        seed=seed,
        logistic_artifact_id=f"sha256:logistic-{seed}",
        class_prior_artifact_id=f"sha256:class-prior-{seed}",
        logistic_macro_f1=score,
    )


def test_evaluation_report_cold_loads_exact_three_seed_artifact_comparisons() -> None:
    report = EvaluationReport.create(
        class_prior_equal_cell_macro_f1=0.40,
        logistic_equal_cell_macro_f1=0.52,
        seed_results=(
            _seed_result(17, 0.50),
            _seed_result(29, 0.52),
            _seed_result(43, 0.54),
        ),
        feature_batch_id="sha256:feature-batch",
        source_policy_manifest_id="sha256:source-policy",
        label_manifest_id="sha256:label",
        cost_manifest_id="sha256:cost",
        fold_manifest_id="sha256:fold",
    )

    loaded = EvaluationReport.from_serialized(report.evaluation_report_id, report.serialized)

    assert loaded == report
    assert loaded.seed_macro_f1 == (0.50, 0.52, 0.54)
    assert loaded.logistic_artifact_ids == (
        "sha256:logistic-17",
        "sha256:logistic-29",
        "sha256:logistic-43",
    )
    assert loaded.class_prior_artifact_ids == (
        "sha256:class-prior-17",
        "sha256:class-prior-29",
        "sha256:class-prior-43",
    )


@pytest.mark.parametrize(
    "seed_results",
    (
        (_seed_result(17, 0.50), _seed_result(29, 0.52)),
        (_seed_result(17, 0.50), _seed_result(17, 0.52), _seed_result(43, 0.54)),
        (
            replace(_seed_result(17, 0.50), seed=True),
            _seed_result(29, 0.52),
            _seed_result(43, 0.54),
        ),
    ),
)
def test_evaluation_report_requires_three_distinct_non_boolean_seed_artifact_results(
    seed_results: tuple[SeedArtifactEvaluation, ...],
) -> None:
    with pytest.raises(ValueError, match="evaluation_report_invalid"):
        EvaluationReport.create(
            class_prior_equal_cell_macro_f1=0.40,
            logistic_equal_cell_macro_f1=0.52,
            seed_results=seed_results,
            feature_batch_id="sha256:feature-batch",
            source_policy_manifest_id="sha256:source-policy",
            label_manifest_id="sha256:label",
            cost_manifest_id="sha256:cost",
            fold_manifest_id="sha256:fold",
        )
