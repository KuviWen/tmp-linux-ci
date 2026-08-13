from stock_forecasting.forecasting import FeatureSnapshot, FixtureTrendForecaster


def test_fixture_forecaster_consumes_an_immutable_feature_snapshot_contract() -> None:
    forecaster = FixtureTrendForecaster()
    snapshot = FeatureSnapshot(
        feature_snapshot_id="feature-snapshot-available",
        data_selection_id="data-selection-available",
        status="full",
        values={
            "adjusted_return_1": 0.001,
            "adjusted_return_5": 0.005,
            "adjusted_return_20": 0.02,
            "volume_ratio_20": 1.1,
        },
    )

    predictions = forecaster.predict(snapshot)

    assert [prediction["horizon_sessions"] for prediction in predictions] == [1, 5, 20]
    assert predictions[1] == {
        "horizon_sessions": 5,
        "probabilities": {"up": 0.55, "flat": 0.28, "down": 0.17},
        "confidence_score": 0.102073,
        "prediction_status": "full",
        "data_support": {"price_volume": "full"},
    }


def test_fixture_forecaster_propagates_unavailability_without_probabilities() -> None:
    forecaster = FixtureTrendForecaster()
    snapshot = FeatureSnapshot(
        feature_snapshot_id="feature-snapshot-unavailable",
        data_selection_id="data-selection-unavailable",
        status="unavailable",
        unavailable_reason="post_cutoff_evidence",
    )

    predictions = forecaster.predict(snapshot)

    assert predictions == [
        {
            "horizon_sessions": horizon,
            "prediction_status": "unavailable",
            "unavailable_reason": {"code": "post_cutoff_evidence"},
            "data_support": {"price_volume": "unavailable"},
        }
        for horizon in (1, 5, 20)
    ]
