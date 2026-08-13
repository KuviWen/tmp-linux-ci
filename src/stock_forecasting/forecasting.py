from __future__ import annotations

from dataclasses import dataclass
from math import log
from typing import Literal, Protocol

from stock_forecasting.contracts import PredictionPayload, ProbabilityVector

UnavailableCode = Literal[
    "missing_anchor_price",
    "post_cutoff_evidence",
    "source_withdrawn",
]


@dataclass(frozen=True)
class FeatureSnapshot:
    feature_snapshot_id: str
    data_selection_id: str
    status: Literal["full", "unavailable"]
    values: dict[str, float] | None = None
    unavailable_reason: UnavailableCode | None = None

    def __post_init__(self) -> None:
        if self.status == "full" and (self.values is None or self.unavailable_reason is not None):
            raise ValueError("full_feature_snapshot_requires_values")
        if self.status == "unavailable" and (
            self.values is not None or self.unavailable_reason is None
        ):
            raise ValueError("unavailable_feature_snapshot_requires_reason")


class TrendForecaster(Protocol):
    def predict(self, feature_snapshot: FeatureSnapshot) -> list[PredictionPayload]: ...


def _confidence(probabilities: ProbabilityVector) -> float:
    values = (probabilities["up"], probabilities["flat"], probabilities["down"])
    entropy = -sum(value * log(value) for value in values)
    return round(1 - (entropy / log(3)), 6)


class FixtureTrendForecaster:
    _probabilities: dict[int, ProbabilityVector] = {
        1: {"up": 0.62, "flat": 0.23, "down": 0.15},
        5: {"up": 0.55, "flat": 0.28, "down": 0.17},
        20: {"up": 0.43, "flat": 0.35, "down": 0.22},
    }

    def predict(self, feature_snapshot: FeatureSnapshot) -> list[PredictionPayload]:
        if feature_snapshot.status == "unavailable":
            reason = feature_snapshot.unavailable_reason
            if reason is None:
                raise ValueError("unavailable_feature_snapshot_requires_reason")
            return [
                {
                    "horizon_sessions": horizon,
                    "prediction_status": "unavailable",
                    "unavailable_reason": {"code": reason},
                    "data_support": {"price_volume": "unavailable"},
                }
                for horizon in self._probabilities
            ]
        return [
            {
                "horizon_sessions": horizon,
                "probabilities": probabilities,
                "confidence_score": _confidence(probabilities),
                "prediction_status": "full",
                "data_support": {"price_volume": "full"},
            }
            for horizon, probabilities in self._probabilities.items()
        ]
