import hashlib
import json
from collections.abc import Callable

import pytest

from stock_forecasting.forecasting import (
    ClassPriorTrendForecaster,
    FeatureBatch,
    FeatureRow,
    ModelArtifact,
    PredictionRequest,
    RegularizedMultinomialLogisticTrendForecaster,
    TrainingRequest,
    TrendLabel,
)


def _literal_calibrator(*, temperature: float = 1.0) -> dict[str, object]:
    payload: dict[str, object] = {
        "market": "XTAI",
        "horizon_sessions": 1,
        "temperature": temperature,
        "fit_method": "temperature_scaling",
        "sample_count": 9,
        "class_counts": [3, 3, 3],
        "pre_nll": 1.0,
        "post_nll": 1.0,
        "status": "sufficient_data",
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    calibrator_id = hashlib.sha256(b"temperature_calibrator" + serialized).hexdigest()
    return {
        "calibrator_id": f"sha256:{calibrator_id}",
        **payload,
    }


def _literal_logistic_payload() -> dict[str, object]:
    calibrator = _literal_calibrator(temperature=2.0)
    return {
        "artifact_format": "safe-json-v1",
        "model_family": "regularized_multinomial_logistic",
        "seed": 17,
        "manifest_ids": ["feature", "source", "label", "fold", "cost"],
        "training_selection_id": "sha256:selection",
        "normalizers": {
            "XTAI": {
                "iqrs": [1.0],
                "lower_bounds": [-10.0],
                "lower_quantile": 0.01,
                "medians": [0.0],
                "method": "median_iqr_winsorized",
                "upper_bounds": [10.0],
                "upper_quantile": 0.99,
            }
        },
        "class_weights_by_cell": {"XTAI:1": {"up": 1.0, "flat": 1.0, "down": 1.0}},
        "cell_loss_normalizers": {"XTAI:1": 3.0},
        "loss_weighting": "equal_market_horizon_cells",
        "regularization": 0.05,
        "weights": [[2.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        "calibrator_ids": [calibrator["calibrator_id"]],
        "calibrators": [calibrator],
    }


def _literal_class_prior_payload() -> dict[str, object]:
    calibrator = _literal_calibrator()
    return {
        "artifact_format": "safe-json-v1",
        "model_family": "class_prior",
        "seed": 17,
        "manifest_ids": ["feature", "source", "label", "fold", "cost"],
        "training_selection_id": "sha256:selection",
        "probabilities_by_cell": {"XTAI:1": {"up": 0.5, "flat": 0.25, "down": 0.25}},
        "calibrator_ids": [calibrator["calibrator_id"]],
        "calibrators": [calibrator],
    }


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


def test_class_prior_fits_separate_empirical_priors_per_market_horizon_cell() -> None:
    rows = (
        FeatureRow("tw-up-1", "XTAI", 1, (0.0,), "up"),
        FeatureRow("tw-up-2", "XTAI", 1, (0.0,), "up"),
        FeatureRow("tw-flat", "XTAI", 1, (0.0,), "flat"),
        FeatureRow("tw-down", "XTAI", 1, (0.0,), "down"),
        FeatureRow("us-up", "XNAS", 20, (0.0,), "up"),
        FeatureRow("us-flat", "XNAS", 20, (0.0,), "flat"),
        FeatureRow("us-down-1", "XNAS", 20, (0.0,), "down"),
        FeatureRow("us-down-2", "XNAS", 20, (0.0,), "down"),
    )
    batch = FeatureBatch("feature", "source", "label", "fold", "cost", rows)
    artifact = ClassPriorTrendForecaster().train(
        TrainingRequest(batch, tuple(row.row_id for row in rows), (), 17)
    )

    predictions = ClassPriorTrendForecaster.load(artifact.serialized).predict(
        PredictionRequest(
            artifact,
            (
                FeatureRow("tw-unseen", "XTAI", 1, (9.0,), None),
                FeatureRow("us-unseen", "XNAS", 20, (9.0,), None),
            ),
        )
    )

    assert predictions.predictions[0].probabilities == {
        "up": 0.5,
        "flat": 0.25,
        "down": 0.25,
    }
    assert predictions.predictions[1].probabilities == {
        "up": 0.25,
        "flat": 0.25,
        "down": 0.5,
    }


@pytest.mark.parametrize(
    "probabilities",
    [
        {"up": -0.1, "flat": 0.5, "down": 0.6},
        {"up": 0.5, "flat": 0.25, "down": 0.20},
        {"up": float("nan"), "flat": 0.5, "down": 0.5},
    ],
)
def test_class_prior_offline_loader_rejects_invalid_probability_vectors(
    probabilities: dict[str, float],
) -> None:
    payload = _literal_class_prior_payload()
    payload["probabilities_by_cell"] = {"XTAI:1": probabilities}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(ValueError, match="artifact_schema_invalid"):
        ClassPriorTrendForecaster.load(serialized)


def test_class_prior_offline_loader_rejects_unknown_fields_and_calibrator_drift() -> None:
    unknown = _literal_class_prior_payload()
    unknown["unknown_field"] = "must-not-be-ignored"
    mismatched = _literal_class_prior_payload()
    mismatched["calibrator_ids"] = ["sha256:unrelated-calibrator"]

    for payload in (unknown, mismatched):
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(ValueError, match="artifact_schema_invalid"):
            ClassPriorTrendForecaster.load(serialized)


@pytest.mark.parametrize(
    ("payload_factory", "loader"),
    [
        (_literal_class_prior_payload, ClassPriorTrendForecaster.load),
        (_literal_logistic_payload, RegularizedMultinomialLogisticTrendForecaster.load),
    ],
)
def test_offline_loaders_reject_stale_calibrator_content_ids(
    payload_factory: Callable[[], dict[str, object]],
    loader: Callable[[bytes], object],
) -> None:
    payload = payload_factory()
    calibrators = payload["calibrators"]
    assert isinstance(calibrators, list)
    calibrator = calibrators[0]
    assert isinstance(calibrator, dict)
    calibrator["temperature"] = 1.5
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(ValueError, match="artifact_schema_invalid"):
        loader(serialized)


@pytest.mark.parametrize(
    ("payload_factory", "loader"),
    [
        (_literal_class_prior_payload, ClassPriorTrendForecaster.load),
        (_literal_logistic_payload, RegularizedMultinomialLogisticTrendForecaster.load),
    ],
)
def test_offline_loaders_require_the_complete_calibrator_bundle(
    payload_factory: Callable[[], dict[str, object]],
    loader: Callable[[bytes], object],
) -> None:
    payload = payload_factory()
    del payload["calibrator_ids"]
    del payload["calibrators"]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(ValueError, match="artifact_schema_invalid"):
        loader(serialized)


def test_logistic_offline_loader_rejects_missing_or_orphan_calibrator_cells() -> None:
    missing = _literal_logistic_payload()
    missing["calibrator_ids"] = []
    missing["calibrators"] = []

    orphan = _literal_logistic_payload()
    orphan_calibrator = _literal_calibrator()
    orphan_calibrator["market"] = "XNAS"
    orphan_payload = {
        key: value for key, value in orphan_calibrator.items() if key != "calibrator_id"
    }
    serialized_orphan = json.dumps(orphan_payload, sort_keys=True, separators=(",", ":")).encode()
    orphan_calibrator["calibrator_id"] = (
        f"sha256:{hashlib.sha256(b'temperature_calibrator' + serialized_orphan).hexdigest()}"
    )
    orphan["calibrator_ids"] = [orphan_calibrator["calibrator_id"]]
    orphan["calibrators"] = [orphan_calibrator]

    for payload in (missing, orphan):
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(ValueError, match="artifact_schema_invalid"):
            RegularizedMultinomialLogisticTrendForecaster.load(serialized)


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
    taiwan_rows = tuple(
        FeatureRow(f"up-{index}", "XTAI", 1, (float(index), 1.0), "up") for index in range(8)
    ) + (
        FeatureRow("flat-only", "XTAI", 1, (0.0, 2.0), "flat"),
        FeatureRow("down-only", "XTAI", 1, (-1.0, -1.0), "down"),
    )
    us_rows = (
        FeatureRow("us-up-only", "XNAS", 1, (100.0, 100.0), "up"),
        *(
            FeatureRow(f"us-flat-{index}", "XNAS", 1, (100.0 + index, 102.0), "flat")
            for index in range(8)
        ),
        FeatureRow("us-down-only", "XNAS", 1, (99.0, 99.0), "down"),
    )
    balanced_labels: tuple[TrendLabel, ...] = ("up", "flat", "down")
    balanced_rows = tuple(
        FeatureRow(
            f"us-balanced-{label}-{index}",
            "XNAS",
            5,
            (100.0 + index, 101.0),
            label,
        )
        for label in balanced_labels
        for index in range(3)
    )
    rows = (
        taiwan_rows
        + us_rows
        + balanced_rows
        + (FeatureRow("validation-flat", "XTAI", 1, (1000.0, 1000.0), "flat"),)
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
            training_row_ids=tuple(row.row_id for row in taiwan_rows + us_rows + balanced_rows),
            validation_row_ids=(),
            seed=17,
        )
    )

    payload = json.loads(artifact.serialized)

    assert payload["class_weights_by_cell"] == {
        "XNAS:1": {"up": 2.0, "flat": 0.5, "down": 2.0},
        "XNAS:5": {"up": 1.0, "flat": 1.0, "down": 1.0},
        "XTAI:1": {"up": 0.5, "flat": 2.0, "down": 2.0},
    }
    assert payload["cell_loss_normalizers"] == {
        "XNAS:1": 8.0,
        "XNAS:5": 9.0,
        "XTAI:1": 8.0,
    }
    taiwan_normalizer = payload["normalizers"]["XTAI"]
    assert taiwan_normalizer["method"] == "median_iqr_winsorized"
    assert taiwan_normalizer["lower_quantile"] == 0.01
    assert taiwan_normalizer["upper_quantile"] == 0.99
    assert taiwan_normalizer["medians"] == [2.5, 1.0]
    assert taiwan_normalizer["iqrs"] == [4.5, 1.0]
    assert taiwan_normalizer["lower_bounds"] == pytest.approx([-0.91, -0.82])
    assert taiwan_normalizer["upper_bounds"] == pytest.approx([6.91, 1.91])
    assert payload["normalizers"]["XTAI"]["medians"] != payload["normalizers"]["XNAS"]["medians"]


def test_logistic_offline_artifact_applies_bound_market_horizon_temperature() -> None:
    payload = _literal_logistic_payload()
    calibrator_ids = payload["calibrator_ids"]
    assert isinstance(calibrator_ids, list)
    assert isinstance(calibrator_ids[0], str)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    artifact = ModelArtifact(
        artifact_id=f"sha256:{hashlib.sha256(serialized).hexdigest()}",
        model_family="regularized_multinomial_logistic",
        seed=17,
        manifest_ids=("feature", "source", "label", "fold", "cost"),
        training_selection_id="sha256:selection",
        model_parameters_id="sha256:parameters",
        serialized=serialized,
        calibrator_ids=(calibrator_ids[0],),
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


@pytest.mark.parametrize(
    "normalizer_override",
    [
        {"method": "unknown"},
        {"iqrs": [0.0]},
        {"lower_bounds": [2.0], "upper_bounds": [1.0]},
    ],
)
def test_logistic_offline_loader_rejects_invalid_normalizer_schema(
    normalizer_override: dict[str, object],
) -> None:
    payload = _literal_logistic_payload()
    normalizers = payload["normalizers"]
    assert isinstance(normalizers, dict)
    normalizer = normalizers["XTAI"]
    assert isinstance(normalizer, dict)
    normalizer.update(normalizer_override)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(ValueError, match="artifact_schema_invalid"):
        RegularizedMultinomialLogisticTrendForecaster.load(serialized)


def test_logistic_offline_loader_rejects_unknown_artifact_fields() -> None:
    payload = _literal_logistic_payload()
    payload["unknown_field"] = "must-not-be-ignored"
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(ValueError, match="artifact_schema_invalid"):
        RegularizedMultinomialLogisticTrendForecaster.load(serialized)
