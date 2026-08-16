from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from math import exp, log, sqrt
from typing import Literal, Protocol, cast

from stock_forecasting.contracts import PredictionPayload, ProbabilityVector, UnavailableCode


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


class FixturePredictionForecaster(Protocol):
    def predict(self, feature_snapshot: FeatureSnapshot) -> list[PredictionPayload]: ...


TrendLabel = Literal["up", "flat", "down"]


@dataclass(frozen=True)
class FeatureRow:
    row_id: str
    market: Literal["XTAI", "XNAS"]
    horizon_sessions: Literal[1, 5, 20]
    values: tuple[float, ...]
    label: TrendLabel | None
    session_date: date | None = None


@dataclass(frozen=True)
class HistoricalTrainingLineage:
    market: Literal["XTAI", "XNAS"]
    claim_id: str
    dataset_version_id: str
    adjustment_version_id: str
    mature_labels_id: str
    feature_snapshot_id: str
    qualification_fold_manifest_id: str
    source_policy_id: str
    feature_batch_id: str
    source_policy_manifest_id: str
    label_manifest_id: str
    fold_manifest_id: str

    def is_bound_to(self, feature_batch: FeatureBatch) -> bool:
        return (
            self.feature_batch_id == feature_batch.feature_batch_id
            and self.source_policy_manifest_id == feature_batch.source_policy_manifest_id
            and self.label_manifest_id == feature_batch.label_manifest_id
            and self.fold_manifest_id == feature_batch.fold_manifest_id
        )


@dataclass(frozen=True)
class FeatureBatch:
    feature_batch_id: str
    source_policy_manifest_id: str
    label_manifest_id: str
    fold_manifest_id: str
    cost_manifest_id: str
    rows: tuple[FeatureRow, ...]
    historical_lineage: tuple[HistoricalTrainingLineage, ...] = ()


@dataclass(frozen=True)
class TrainingRequest:
    feature_batch: FeatureBatch
    training_row_ids: tuple[str, ...]
    validation_row_ids: tuple[str, ...]
    seed: int


@dataclass(frozen=True)
class CalibrationEvidence:
    calibrator_id: str
    market: Literal["XTAI", "XNAS"]
    horizon_sessions: Literal[1, 5, 20]
    status: Literal["sufficient_data", "insufficient_data"]
    sample_count: int
    class_counts: tuple[int, ...]
    temperature: float
    fit_method: Literal["temperature_scaling"]
    pre_nll: float
    post_nll: float


@dataclass(frozen=True)
class ModelArtifact:
    artifact_id: str
    model_family: str
    seed: int
    manifest_ids: tuple[str, str, str, str, str]
    training_selection_id: str
    model_parameters_id: str
    serialized: bytes
    calibrator_ids: tuple[str, ...] = ()
    calibrators: tuple[CalibrationEvidence, ...] = ()
    evaluation_report_id: str | None = None

    def bind_evaluation_report(self, evaluation_report_id: str) -> ModelArtifact:
        payload = cast(dict[str, object], json.loads(self.serialized))
        payload["evaluation_report_id"] = evaluation_report_id
        serialized = _serialize_payload(payload)
        return ModelArtifact(
            artifact_id=_artifact_id(serialized),
            model_family=self.model_family,
            seed=self.seed,
            manifest_ids=self.manifest_ids,
            training_selection_id=self.training_selection_id,
            model_parameters_id=self.model_parameters_id,
            serialized=serialized,
            calibrator_ids=self.calibrator_ids,
            calibrators=self.calibrators,
            evaluation_report_id=evaluation_report_id,
        )


@dataclass(frozen=True)
class PredictionRequest:
    artifact: ModelArtifact
    rows: tuple[FeatureRow, ...]


@dataclass(frozen=True)
class ForecastPrediction:
    row_id: str
    probabilities: ProbabilityVector


@dataclass(frozen=True)
class ForecastBatch:
    artifact_id: str
    predictions: tuple[ForecastPrediction, ...]


class TrendForecaster(Protocol):
    def train(self, request: TrainingRequest) -> ModelArtifact: ...

    def predict(self, request: PredictionRequest) -> ForecastBatch: ...


class ClassPriorTrendForecaster:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self._payload = payload

    def train(self, request: TrainingRequest) -> ModelArtifact:
        rows_by_id = {row.row_id: row for row in request.feature_batch.rows}
        labels = [rows_by_id[row_id].label for row_id in request.training_row_ids]
        if not labels or any(label is None for label in labels):
            raise ValueError("training_labels_required")
        typed_labels = cast(list[TrendLabel], labels)
        class_labels: tuple[TrendLabel, ...] = ("up", "flat", "down")
        counts = {label: typed_labels.count(label) for label in class_labels}
        total = len(labels)
        probabilities = {label: counts[label] / total for label in counts}
        batch = request.feature_batch
        manifest_ids = (
            batch.feature_batch_id,
            batch.source_policy_manifest_id,
            batch.label_manifest_id,
            batch.fold_manifest_id,
            batch.cost_manifest_id,
        )
        training_selection_id = _training_selection_id(request)
        payload: dict[str, object] = {
            "model_family": "class_prior",
            "seed": request.seed,
            "manifest_ids": manifest_ids,
            "training_selection_id": training_selection_id,
            "probabilities": probabilities,
        }
        model_parameters_id = _artifact_id(_serialize_payload(payload))
        calibrators = _fit_temperature_calibrators(
            request,
            lambda row: {
                "up": probabilities["up"],
                "flat": probabilities["flat"],
                "down": probabilities["down"],
            },
        )
        payload = _bind_calibrators(payload, calibrators)
        serialized = _serialize_payload(payload)
        artifact_id = _artifact_id(serialized)
        return ModelArtifact(
            artifact_id=artifact_id,
            model_family="class_prior",
            seed=request.seed,
            manifest_ids=manifest_ids,
            training_selection_id=training_selection_id,
            model_parameters_id=model_parameters_id,
            serialized=serialized,
            calibrator_ids=tuple(item.calibrator_id for item in calibrators),
            calibrators=calibrators,
        )

    @classmethod
    def load(cls, serialized: bytes) -> ClassPriorTrendForecaster:
        payload = cast(dict[str, object], json.loads(serialized))
        if payload.get("model_family") != "class_prior":
            raise ValueError("wrong_model_family")
        return cls(payload)

    def predict(self, request: PredictionRequest) -> ForecastBatch:
        if self._payload is None:
            loaded = self.load(request.artifact.serialized)
            return loaded.predict(request)
        expected_id = f"sha256:{hashlib.sha256(request.artifact.serialized).hexdigest()}"
        if request.artifact.artifact_id != expected_id:
            raise ValueError("artifact_checksum_mismatch")
        raw_probabilities = cast(dict[str, float], self._payload["probabilities"])
        base_probabilities = (
            raw_probabilities["up"],
            raw_probabilities["flat"],
            raw_probabilities["down"],
        )
        return ForecastBatch(
            artifact_id=request.artifact.artifact_id,
            predictions=tuple(
                ForecastPrediction(
                    row_id=row.row_id,
                    probabilities=self._calibrated_prior(row, base_probabilities),
                )
                for row in request.rows
            ),
        )

    def _calibrated_prior(
        self,
        row: FeatureRow,
        probabilities: tuple[float, float, float],
    ) -> ProbabilityVector:
        temperature = _artifact_temperature(self._payload, row)
        scaled = [max(value, 1e-15) ** (1.0 / temperature) for value in probabilities]
        total = sum(scaled)
        return {
            "up": scaled[0] / total,
            "flat": scaled[1] / total,
            "down": scaled[2] / total,
        }


class RegularizedMultinomialLogisticTrendForecaster:
    _labels: tuple[TrendLabel, ...] = ("up", "flat", "down")

    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self._payload = payload

    def train(self, request: TrainingRequest) -> ModelArtifact:
        rows_by_id = {row.row_id: row for row in request.feature_batch.rows}
        rows = [rows_by_id[row_id] for row_id in request.training_row_ids]
        if not rows or any(row.label is None for row in rows):
            raise ValueError("training_labels_required")
        feature_count = len(rows[0].values)
        if feature_count == 0 or any(len(row.values) != feature_count for row in rows):
            raise ValueError("consistent_features_required")
        means = [
            sum(row.values[index] for row in rows) / len(rows) for index in range(feature_count)
        ]
        scales = [
            sqrt(sum((row.values[index] - means[index]) ** 2 for row in rows) / len(rows))
            for index in range(feature_count)
        ]
        scales = [scale if scale > 1e-12 else 1.0 for scale in scales]
        normalized = [
            [(row.values[index] - means[index]) / scales[index] for index in range(feature_count)]
            + [1.0]
            for row in rows
        ]
        label_counts = {label: sum(row.label == label for row in rows) for label in self._labels}
        if any(count == 0 for count in label_counts.values()):
            raise ValueError("all_training_classes_required")
        class_weights = {
            label: min(
                2.0,
                max(
                    0.5,
                    len(rows) / (len(self._labels) * label_counts[label]),
                ),
            )
            for label in self._labels
        }
        generator = random.Random(request.seed)
        weights = [
            [generator.uniform(-0.01, 0.01) for _ in range(feature_count + 1)] for _ in self._labels
        ]
        regularization = 0.05
        learning_rate = 0.08
        for _ in range(60):
            gradients = [[0.0 for _ in range(feature_count + 1)] for _ in self._labels]
            for vector, row in zip(normalized, rows, strict=True):
                probabilities = self._softmax(
                    [
                        sum(
                            weight * value
                            for weight, value in zip(label_weights, vector, strict=True)
                        )
                        for label_weights in weights
                    ]
                )
                assert row.label is not None
                sample_weight = class_weights[row.label]
                for label_index, label in enumerate(self._labels):
                    error = (
                        probabilities[label_index] - (1.0 if row.label == label else 0.0)
                    ) * sample_weight
                    for feature_index, value in enumerate(vector):
                        gradients[label_index][feature_index] += error * value
            for label_index, label_weights in enumerate(weights):
                for feature_index in range(feature_count + 1):
                    penalty = (
                        regularization * label_weights[feature_index]
                        if feature_index < feature_count
                        else 0.0
                    )
                    label_weights[feature_index] -= learning_rate * (
                        gradients[label_index][feature_index] / len(rows) + penalty
                    )
        batch = request.feature_batch
        manifest_ids = (
            batch.feature_batch_id,
            batch.source_policy_manifest_id,
            batch.label_manifest_id,
            batch.fold_manifest_id,
            batch.cost_manifest_id,
        )
        training_selection_id = _training_selection_id(request)
        payload: dict[str, object] = {
            "model_family": "regularized_multinomial_logistic",
            "seed": request.seed,
            "manifest_ids": manifest_ids,
            "training_selection_id": training_selection_id,
            "normalizer": {"means": means, "scales": scales},
            "class_weights": class_weights,
            "regularization": regularization,
            "weights": weights,
        }
        model_parameters_id = _artifact_id(_serialize_payload(payload))

        def raw_probabilities(row: FeatureRow) -> ProbabilityVector:
            vector = [
                (value - means[index]) / scales[index] for index, value in enumerate(row.values)
            ] + [1.0]
            values = self._softmax(
                [
                    sum(weight * value for weight, value in zip(label_weights, vector, strict=True))
                    for label_weights in weights
                ]
            )
            return {"up": values[0], "flat": values[1], "down": values[2]}

        calibrators = _fit_temperature_calibrators(request, raw_probabilities)
        payload = _bind_calibrators(payload, calibrators)
        serialized = _serialize_payload(payload)
        artifact_id = _artifact_id(serialized)
        return ModelArtifact(
            artifact_id=artifact_id,
            model_family="regularized_multinomial_logistic",
            seed=request.seed,
            manifest_ids=manifest_ids,
            training_selection_id=training_selection_id,
            model_parameters_id=model_parameters_id,
            serialized=serialized,
            calibrator_ids=tuple(item.calibrator_id for item in calibrators),
            calibrators=calibrators,
        )

    @classmethod
    def load(cls, serialized: bytes) -> RegularizedMultinomialLogisticTrendForecaster:
        payload = cast(dict[str, object], json.loads(serialized))
        if payload.get("model_family") != "regularized_multinomial_logistic":
            raise ValueError("wrong_model_family")
        return cls(payload)

    def predict(self, request: PredictionRequest) -> ForecastBatch:
        if self._payload is None:
            loaded = self.load(request.artifact.serialized)
            return loaded.predict(request)
        expected_id = f"sha256:{hashlib.sha256(request.artifact.serialized).hexdigest()}"
        if request.artifact.artifact_id != expected_id:
            raise ValueError("artifact_checksum_mismatch")
        normalizer = cast(dict[str, list[float]], self._payload["normalizer"])
        means = normalizer["means"]
        scales = normalizer["scales"]
        weights = cast(list[list[float]], self._payload["weights"])
        predictions: list[ForecastPrediction] = []
        for row in request.rows:
            if len(row.values) != len(means):
                raise ValueError("feature_count_mismatch")
            vector = [
                (value - means[index]) / scales[index] for index, value in enumerate(row.values)
            ] + [1.0]
            raw_probabilities = self._softmax(
                [
                    sum(weight * value for weight, value in zip(label_weights, vector, strict=True))
                    for label_weights in weights
                ]
            )
            temperature = _artifact_temperature(self._payload, row)
            if temperature != 1.0:
                raw_probabilities = self._softmax(
                    [
                        log(max(probability, 1e-15)) / temperature
                        for probability in raw_probabilities
                    ]
                )
            probabilities: ProbabilityVector = {
                "up": raw_probabilities[0],
                "flat": raw_probabilities[1],
                "down": raw_probabilities[2],
            }
            predictions.append(ForecastPrediction(row_id=row.row_id, probabilities=probabilities))
        return ForecastBatch(
            artifact_id=request.artifact.artifact_id,
            predictions=tuple(predictions),
        )

    @staticmethod
    def _softmax(scores: list[float]) -> list[float]:
        maximum = max(scores)
        exponentials = [exp(score - maximum) for score in scores]
        total = sum(exponentials)
        return [value / total for value in exponentials]


def _artifact_temperature(payload: dict[str, object] | None, row: FeatureRow) -> float:
    if payload is None:
        return 1.0
    calibrators = payload.get("calibrators", [])
    if not isinstance(calibrators, list):
        raise ValueError("artifact_calibrators_invalid")
    matches = [
        item
        for item in calibrators
        if isinstance(item, dict)
        and item.get("market") == row.market
        and item.get("horizon_sessions") == row.horizon_sessions
    ]
    if not matches:
        return 1.0
    if len(matches) != 1:
        raise ValueError("artifact_calibrators_ambiguous")
    temperature = matches[0].get("temperature")
    if not isinstance(temperature, (int, float)) or temperature <= 0:
        raise ValueError("artifact_calibrator_temperature_invalid")
    return float(temperature)


def _fit_temperature_calibrators(
    request: TrainingRequest,
    predict_raw: Callable[[FeatureRow], ProbabilityVector],
) -> tuple[CalibrationEvidence, ...]:
    if not request.validation_row_ids:
        return ()
    rows_by_id = {row.row_id: row for row in request.feature_batch.rows}
    validation_rows = tuple(rows_by_id[row_id] for row_id in request.validation_row_ids)
    labels: tuple[TrendLabel, ...] = ("up", "flat", "down")
    evidence: list[CalibrationEvidence] = []
    for market in ("XTAI", "XNAS"):
        for horizon in (1, 5, 20):
            cell_rows = tuple(
                row
                for row in validation_rows
                if row.market == market
                and row.horizon_sessions == horizon
                and row.label is not None
            )
            class_counts = tuple(sum(row.label == label for row in cell_rows) for label in labels)
            if len(cell_rows) < 9 or any(count < 2 for count in class_counts):
                raise ValueError("insufficient_calibration_support")
            probabilities = {row.row_id: predict_raw(row) for row in cell_rows}

            def nll(
                temperature: float,
                *,
                rows: tuple[FeatureRow, ...] = cell_rows,
                cell_probabilities: dict[str, ProbabilityVector] = probabilities,
            ) -> float:
                total = 0.0
                for row in rows:
                    assert row.label is not None
                    calibrated = _apply_temperature(cell_probabilities[row.row_id], temperature)
                    total -= log(max(calibrated[row.label], 1e-15))
                return total / len(rows)

            pre_nll = nll(1.0)
            candidates = tuple(0.5 + index * 0.05 for index in range(51))
            temperature = min(candidates, key=lambda value: (nll(value), abs(value - 1.0)))
            post_nll = nll(temperature)
            payload = {
                "market": market,
                "horizon_sessions": horizon,
                "sample_count": len(cell_rows),
                "class_counts": class_counts,
                "temperature": temperature,
                "fit_method": "temperature_scaling",
                "pre_nll": pre_nll,
                "post_nll": post_nll,
                "status": "sufficient_data",
            }
            evidence.append(
                CalibrationEvidence(
                    calibrator_id=_content_id("temperature_calibrator", payload),
                    market=market,
                    horizon_sessions=horizon,
                    status="sufficient_data",
                    sample_count=len(cell_rows),
                    class_counts=class_counts,
                    temperature=temperature,
                    fit_method="temperature_scaling",
                    pre_nll=pre_nll,
                    post_nll=post_nll,
                )
            )
    return tuple(evidence)


def _bind_calibrators(
    payload: dict[str, object],
    calibrators: tuple[CalibrationEvidence, ...],
) -> dict[str, object]:
    if not calibrators:
        return payload
    return {
        **payload,
        "artifact_format": "safe-json-v1",
        "calibrator_ids": [item.calibrator_id for item in calibrators],
        "calibrators": [
            {
                "calibrator_id": item.calibrator_id,
                "market": item.market,
                "horizon_sessions": item.horizon_sessions,
                "temperature": item.temperature,
                "fit_method": item.fit_method,
                "sample_count": item.sample_count,
                "class_counts": item.class_counts,
                "pre_nll": item.pre_nll,
                "post_nll": item.post_nll,
                "status": item.status,
            }
            for item in calibrators
        ],
    }


def _apply_temperature(probabilities: ProbabilityVector, temperature: float) -> ProbabilityVector:
    values = [
        max(probabilities["up"], 1e-15) ** (1.0 / temperature),
        max(probabilities["flat"], 1e-15) ** (1.0 / temperature),
        max(probabilities["down"], 1e-15) ** (1.0 / temperature),
    ]
    total = sum(values)
    return {"up": values[0] / total, "flat": values[1] / total, "down": values[2] / total}


def _serialize_payload(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _artifact_id(serialized: bytes) -> str:
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


def _content_id(kind: str, payload: object) -> str:
    return _artifact_id(kind.encode() + _serialize_payload(payload))


def _training_selection_id(request: TrainingRequest) -> str:
    serialized = json.dumps(
        {
            "feature_batch_id": request.feature_batch.feature_batch_id,
            "fold_manifest_id": request.feature_batch.fold_manifest_id,
            "training_row_ids": request.training_row_ids,
            "validation_row_ids": request.validation_row_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(b'training-selection' + serialized).hexdigest()}"


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
