from dataclasses import dataclass
from typing import Literal, TypedDict

UnavailableCode = Literal[
    "missing_anchor_price",
    "post_cutoff_evidence",
    "source_withdrawn",
]


@dataclass(frozen=True)
class PublicationDisposition:
    work_status: Literal["succeeded", "blocked"]
    health_scope: str
    health_status: Literal["ready", "degraded", "blocked"]
    health_reason_code: str
    audit_reason_code: str = "fixture_policy_active"


class ProbabilityVector(TypedDict):
    up: float
    flat: float
    down: float


class DataSupport(TypedDict):
    price_volume: Literal["full", "unavailable"]


class UnavailableReason(TypedDict):
    code: UnavailableCode


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
