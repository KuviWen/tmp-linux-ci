import hashlib
import json

import pytest

from stock_forecasting.forecasting import (
    ClassPriorTrendForecaster,
    FeatureBatch,
    FeatureRow,
    ModelArtifact,
    PredictionRequest,
    RegularizedMultinomialLogisticTrendForecaster,
    TrainingRequest,
)


def test_class_prior_artifact_loads_offline_and_predicts_training_priors() -> None:
    batch = FeatureBatch(
        feature_batch_id="feature-batch-001",
        source_policy_manifest_id="source-policy-v1",
        label_manifest_id="label-v1",
        fold_manifest_id="fold-v1",
        cost_manifest_id="cost-v1",
        rows=(
            FeatureRow("row-1", "XTAI", 1, (0.0, 1.0), "up"),
            FeatureRow("row-2", "XTAI", 1, (1.0, 0.0), "up"),
            FeatureRow("row-3", "XTAI", 1, (0.5, 0.5), "flat"),
            FeatureRow("row-4", "XTAI", 1, (-1.0, 0.0), "down"),
        ),
    )
    request = TrainingRequest(
        feature_batch=batch,
        training_row_ids=("row-1", "row-2", "row-3", "row-4"),
        validation_row_ids=(),
        seed=17,
    )

    artifact = ClassPriorTrendForecaster().train(request)
    offline = ClassPriorTrendForecaster.load(artifact.serialized)
    forecast = offline.predict(
        PredictionRequest(
            artifact=artifact,
            rows=(FeatureRow("unseen", "XTAI", 1, (9.0, 9.0), None),),
        )
    )

    assert artifact.artifact_id.startswith("sha256:")
    assert artifact.manifest_ids == (
        "feature-batch-001",
        "source-policy-v1",
        "label-v1",
        "fold-v1",
        "cost-v1",
    )
    assert forecast.predictions[0].probabilities == {
        "up": 0.5,
        "flat": 0.25,
        "down": 0.25,
    }


def test_logistic_artifact_loads_offline_and_prediction_is_order_invariant() -> None:
    rows = (
        FeatureRow("up-1", "XTAI", 1, (2.0, 1.0), "up"),
        FeatureRow("up-2", "XTAI", 1, (1.8, 0.8), "up"),
        FeatureRow("flat-1", "XTAI", 1, (0.0, 2.0), "flat"),
        FeatureRow("flat-2", "XTAI", 1, (0.2, 1.8), "flat"),
        FeatureRow("down-1", "XTAI", 1, (-2.0, -1.0), "down"),
        FeatureRow("down-2", "XTAI", 1, (-1.8, -0.8), "down"),
    )
    batch = FeatureBatch(
        feature_batch_id="feature-batch-002",
        source_policy_manifest_id="source-policy-v1",
        label_manifest_id="label-v1",
        fold_manifest_id="fold-v1",
        cost_manifest_id="cost-v1",
        rows=rows,
    )
    request = TrainingRequest(
        feature_batch=batch,
        training_row_ids=tuple(row.row_id for row in rows),
        validation_row_ids=(),
        seed=29,
    )

    artifact = RegularizedMultinomialLogisticTrendForecaster().train(request)
    offline = RegularizedMultinomialLogisticTrendForecaster.load(artifact.serialized)
    forward = offline.predict(PredictionRequest(artifact=artifact, rows=rows))
    reversed_batch = offline.predict(
        PredictionRequest(artifact=artifact, rows=tuple(reversed(rows)))
    )

    assert artifact.manifest_ids == (
        "feature-batch-002",
        "source-policy-v1",
        "label-v1",
        "fold-v1",
        "cost-v1",
    )
    by_id = {item.row_id: item.probabilities for item in forward.predictions}
    assert by_id == {item.row_id: item.probabilities for item in reversed_batch.predictions}
    assert by_id["up-1"]["up"] > by_id["up-1"]["flat"]
    assert by_id["flat-1"]["flat"] > by_id["flat-1"]["up"]
    assert by_id["down-1"]["down"] > by_id["down-1"]["flat"]
    for probabilities in by_id.values():
        values = (
            probabilities["up"],
            probabilities["flat"],
            probabilities["down"],
        )
        assert abs(sum(values) - 1.0) < 1e-9
        assert all(0.0 <= value <= 1.0 for value in values)


def test_logistic_class_weights_are_fit_on_training_rows_and_bounded() -> None:
    rows = tuple(
        FeatureRow(f"up-{index}", "XTAI", 1, (float(index), 1.0), "up") for index in range(8)
    ) + (
        FeatureRow("flat-only", "XTAI", 1, (0.0, 2.0), "flat"),
        FeatureRow("down-only", "XTAI", 1, (-1.0, -1.0), "down"),
        FeatureRow("validation-flat", "XTAI", 1, (100.0, 100.0), "flat"),
    )
    batch = FeatureBatch(
        feature_batch_id="feature-batch-imbalanced",
        source_policy_manifest_id="source-policy-v1",
        label_manifest_id="label-v1",
        fold_manifest_id="fold-v1",
        cost_manifest_id="cost-v1",
        rows=rows,
    )
    artifact = RegularizedMultinomialLogisticTrendForecaster().train(
        TrainingRequest(
            feature_batch=batch,
            training_row_ids=tuple(row.row_id for row in rows[:-1]),
            validation_row_ids=(),
            seed=17,
        )
    )

    payload = json.loads(artifact.serialized)

    assert payload["class_weights"] == {"up": 0.5, "flat": 2.0, "down": 2.0}
    assert payload["normalizer"]["means"] != [100.0, 100.0]


def test_logistic_offline_artifact_applies_bound_market_horizon_temperature() -> None:
    payload = {
        "model_family": "regularized_multinomial_logistic",
        "seed": 17,
        "manifest_ids": ["feature", "source", "label", "fold", "cost"],
        "training_selection_id": "sha256:selection",
        "normalizer": {"means": [0.0], "scales": [1.0]},
        "class_weights": {"up": 1.0, "flat": 1.0, "down": 1.0},
        "regularization": 0.05,
        "weights": [[2.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        "calibrators": [
            {
                "calibrator_id": "sha256:literal-temperature",
                "market": "XTAI",
                "horizon_sessions": 1,
                "temperature": 2.0,
                "fit_method": "temperature_scaling",
            }
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    artifact = ModelArtifact(
        artifact_id=f"sha256:{hashlib.sha256(serialized).hexdigest()}",
        model_family="regularized_multinomial_logistic",
        seed=17,
        manifest_ids=("feature", "source", "label", "fold", "cost"),
        training_selection_id="sha256:selection",
        model_parameters_id="sha256:parameters",
        serialized=serialized,
        calibrator_ids=("sha256:literal-temperature",),
    )

    forecast = RegularizedMultinomialLogisticTrendForecaster.load(serialized).predict(
        PredictionRequest(
            artifact,
            (FeatureRow("literal-row", "XTAI", 1, (1.0,), None),),
        )
    )

    assert forecast.predictions[0].probabilities == pytest.approx(
        {"up": 0.5761168848, "flat": 0.2119415576, "down": 0.2119415576}
    )
