from typing import Literal, TypedDict


class ProbabilityVector(TypedDict):
    up: float
    flat: float
    down: float


class DataSupport(TypedDict):
    price_volume: Literal["full", "unavailable"]


class UnavailableReason(TypedDict):
    code: Literal["missing_anchor_price"]


class AvailablePrediction(TypedDict):
    horizon_sessions: int
    prediction_status: Literal["full"]
    probabilities: ProbabilityVector
    confidence_score: float
    data_support: DataSupport


class UnavailablePrediction(TypedDict):
    horizon_sessions: int
    prediction_status: Literal["unavailable"]
    unavailable_reason: UnavailableReason
    data_support: DataSupport


PredictionPayload = AvailablePrediction | UnavailablePrediction
