from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from math import exp, isfinite, log
from typing import Literal, Protocol, TypeGuard, cast

from stock_forecasting.content_address import canonical_json_bytes, sha256_id
from stock_forecasting.content_address import content_id as _content_id
from stock_forecasting.contracts import (
    HistoricalTrainingLineage,
    PredictionPayload,
    ProbabilityVector,
    UnavailableCode,
)


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
class FeatureBatch:
    feature_batch_id: str
    source_policy_manifest_id: str
    label_manifest_id: str
    fold_manifest_id: str
    cost_manifest_id: str
    rows: tuple[FeatureRow, ...]
    historical_lineage: tuple[HistoricalTrainingLineage, ...] = ()

    def market_rows_digest(self, market: Literal["XTAI", "XNAS"]) -> str:
        return _content_id(
            "feature_rows",
            [
                _feature_row_payload(row)
                for row in sorted(self.rows, key=lambda item: item.row_id)
                if row.market == market
            ],
        )

    def content_id(self) -> str:
        return _content_id(
            "feature_batch",
            {
                "source_policy_manifest_id": self.source_policy_manifest_id,
                "label_manifest_id": self.label_manifest_id,
                "fold_manifest_id": self.fold_manifest_id,
                "cost_manifest_id": self.cost_manifest_id,
                "rows": [
                    _feature_row_payload(row)
                    for row in sorted(self.rows, key=lambda item: item.row_id)
                ],
                "historical_lineage": [
                    asdict(lineage)
                    for lineage in sorted(self.historical_lineage, key=lambda item: item.market)
                ],
            },
        )

    def with_content_id(self) -> FeatureBatch:
        return replace(self, feature_batch_id=self.content_id())

    def is_content_addressed(self) -> bool:
        return self.feature_batch_id == self.content_id()


@dataclass(frozen=True)
class ArtifactProvenance:
    feature_schema_id: str
    runtime_id: str
    code_provenance: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.feature_schema_id, self.runtime_id, self.code_provenance)
        ):
            raise ValueError("artifact_provenance_invalid")

    def payload(self) -> dict[str, str]:
        return {
            "feature_schema_id": self.feature_schema_id,
            "runtime_id": self.runtime_id,
            "code_provenance": self.code_provenance,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> ArtifactProvenance:
        try:
            return cls(
                cast(str, payload["feature_schema_id"]),
                cast(str, payload["runtime_id"]),
                cast(str, payload["code_provenance"]),
            )
        except KeyError as error:
            raise ValueError("artifact_provenance_invalid") from error


@dataclass(frozen=True)
class TrainingRequest:
    feature_batch: FeatureBatch
    training_row_ids: tuple[str, ...]
    validation_row_ids: tuple[str, ...]
    seed: int
    provenance: ArtifactProvenance


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
    provenance: ArtifactProvenance
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
            provenance=self.provenance,
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
        rows = [rows_by_id[row_id] for row_id in request.training_row_ids]
        if not rows or any(row.label is None for row in rows):
            raise ValueError("training_labels_required")
        class_labels: tuple[TrendLabel, ...] = ("up", "flat", "down")
        cell_rows: dict[str, list[FeatureRow]] = {}
        for row in rows:
            cell_rows.setdefault(_cell_key(row), []).append(row)
        probabilities_by_cell: dict[str, dict[TrendLabel, float]] = {}
        for cell, members in cell_rows.items():
            counts = {label: sum(row.label == label for row in members) for label in class_labels}
            probabilities_by_cell[cell] = {
                label: counts[label] / len(members) for label in class_labels
            }
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
            **_artifact_provenance(request),
            "probabilities_by_cell": probabilities_by_cell,
        }
        model_parameters_id = _artifact_id(_serialize_payload(payload))
        calibrators = _fit_temperature_calibrators(
            request,
            lambda row: cast(ProbabilityVector, probabilities_by_cell[_cell_key(row)]),
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
            provenance=request.provenance,
            serialized=serialized,
            calibrator_ids=tuple(item.calibrator_id for item in calibrators),
            calibrators=calibrators,
        )

    @classmethod
    def load(cls, serialized: bytes) -> ClassPriorTrendForecaster:
        return cls(_load_class_prior_artifact(serialized))

    def predict(self, request: PredictionRequest) -> ForecastBatch:
        if self._payload is None:
            loaded = self.load(request.artifact.serialized)
            return loaded.predict(request)
        expected_id = sha256_id(request.artifact.serialized)
        if request.artifact.artifact_id != expected_id:
            raise ValueError("artifact_checksum_mismatch")
        probabilities_by_cell = cast(
            dict[str, dict[str, float]], self._payload["probabilities_by_cell"]
        )
        for row in request.rows:
            if _cell_key(row) not in probabilities_by_cell:
                raise ValueError("class_prior_cell_missing")
        return ForecastBatch(
            artifact_id=request.artifact.artifact_id,
            predictions=tuple(
                ForecastPrediction(
                    row_id=row.row_id,
                    probabilities=self._calibrated_prior(
                        row,
                        (
                            probabilities_by_cell[_cell_key(row)]["up"],
                            probabilities_by_cell[_cell_key(row)]["flat"],
                            probabilities_by_cell[_cell_key(row)]["down"],
                        ),
                    ),
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
        rows_by_market: dict[str, list[FeatureRow]] = {}
        for row in rows:
            rows_by_market.setdefault(row.market, []).append(row)
        normalizers: dict[str, dict[str, object]] = {}
        for market, market_rows in rows_by_market.items():
            normalizers[market] = _fit_robust_normalizer(market_rows, feature_count)
        normalized = [
            _apply_robust_normalizer(normalizers[row.market], row.values) + [1.0] for row in rows
        ]
        rows_by_cell: dict[str, list[FeatureRow]] = {}
        for row in rows:
            rows_by_cell.setdefault(_cell_key(row), []).append(row)
        class_weights_by_cell: dict[str, dict[TrendLabel, float]] = {}
        for cell, cell_rows in rows_by_cell.items():
            label_counts = {
                label: sum(row.label == label for row in cell_rows) for label in self._labels
            }
            if any(count == 0 for count in label_counts.values()):
                raise ValueError("all_training_classes_required")
            class_weights_by_cell[cell] = {
                label: min(
                    2.0,
                    max(
                        0.5,
                        len(cell_rows) / (len(self._labels) * label_counts[label]),
                    ),
                )
                for label in self._labels
            }
        cell_loss_normalizers: dict[str, float] = {}
        for cell, cell_rows in rows_by_cell.items():
            weighted_total = 0.0
            for row in cell_rows:
                assert row.label is not None
                weighted_total += class_weights_by_cell[cell][row.label]
            cell_loss_normalizers[cell] = weighted_total
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
                cell = _cell_key(row)
                sample_weight = class_weights_by_cell[cell][row.label] / (
                    cell_loss_normalizers[cell] * len(rows_by_cell)
                )
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
                        gradients[label_index][feature_index] + penalty
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
            **_artifact_provenance(request),
            "normalizers": normalizers,
            "class_weights_by_cell": class_weights_by_cell,
            "cell_loss_normalizers": cell_loss_normalizers,
            "loss_weighting": "equal_market_horizon_cells",
            "regularization": regularization,
            "weights": weights,
        }
        model_parameters_id = _artifact_id(_serialize_payload(payload))

        def raw_probabilities(row: FeatureRow) -> ProbabilityVector:
            try:
                normalizer = normalizers[row.market]
            except KeyError as error:
                raise ValueError("market_normalizer_missing") from error
            vector = _apply_robust_normalizer(normalizer, row.values) + [1.0]
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
            provenance=request.provenance,
            serialized=serialized,
            calibrator_ids=tuple(item.calibrator_id for item in calibrators),
            calibrators=calibrators,
        )

    @classmethod
    def load(cls, serialized: bytes) -> RegularizedMultinomialLogisticTrendForecaster:
        return cls(_load_logistic_artifact(serialized))

    def predict(self, request: PredictionRequest) -> ForecastBatch:
        if self._payload is None:
            loaded = self.load(request.artifact.serialized)
            return loaded.predict(request)
        expected_id = sha256_id(request.artifact.serialized)
        if request.artifact.artifact_id != expected_id:
            raise ValueError("artifact_checksum_mismatch")
        normalizers = cast(dict[str, dict[str, object]], self._payload["normalizers"])
        weights = cast(list[list[float]], self._payload["weights"])
        predictions: list[ForecastPrediction] = []
        for row in request.rows:
            try:
                normalizer = normalizers[row.market]
            except KeyError as error:
                raise ValueError("market_normalizer_missing") from error
            medians = cast(list[float], normalizer["medians"])
            if len(row.values) != len(medians):
                raise ValueError("feature_count_mismatch")
            vector = _apply_robust_normalizer(normalizer, row.values) + [1.0]
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
        training_ids = set(request.training_row_ids)
        cells = sorted(
            {
                (row.market, row.horizon_sessions)
                for row in request.feature_batch.rows
                if row.row_id in training_ids
            }
        )
        insufficient_evidence: list[CalibrationEvidence] = []
        for market, horizon in cells:
            insufficient_payload: dict[str, object] = {
                "market": market,
                "horizon_sessions": horizon,
                "sample_count": 0,
                "class_counts": (0, 0, 0),
                "temperature": 1.0,
                "fit_method": "temperature_scaling",
                "pre_nll": 0.0,
                "post_nll": 0.0,
                "status": "insufficient_data",
            }
            insufficient_evidence.append(
                CalibrationEvidence(
                    calibrator_id=_content_id("temperature_calibrator", insufficient_payload),
                    market=market,
                    horizon_sessions=horizon,
                    status="insufficient_data",
                    sample_count=0,
                    class_counts=(0, 0, 0),
                    temperature=1.0,
                    fit_method="temperature_scaling",
                    pre_nll=0.0,
                    post_nll=0.0,
                )
            )
        return tuple(insufficient_evidence)
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
    base_payload = {**payload, "artifact_format": "safe-json-v1"}
    return {
        **base_payload,
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


def _load_logistic_artifact(serialized: bytes) -> dict[str, object]:
    try:
        raw_payload = json.loads(serialized)
    except (TypeError, ValueError) as error:
        raise ValueError("artifact_schema_invalid") from error
    if not isinstance(raw_payload, dict):
        raise ValueError("artifact_schema_invalid")
    payload = cast(dict[str, object], raw_payload)
    required = {
        "artifact_format",
        "model_family",
        "seed",
        "manifest_ids",
        "training_selection_id",
        "feature_schema_id",
        "runtime_id",
        "code_provenance",
        "normalizers",
        "class_weights_by_cell",
        "cell_loss_normalizers",
        "loss_weighting",
        "regularization",
        "weights",
        "calibrator_ids",
        "calibrators",
    }
    optional = {"evaluation_report_id"}
    if (
        not required.issubset(payload)
        or not set(payload).issubset(required | optional)
        or payload["artifact_format"] != "safe-json-v1"
        or payload["model_family"] != "regularized_multinomial_logistic"
        or isinstance(payload["seed"], bool)
        or not isinstance(payload["seed"], int)
        or not isinstance(payload["training_selection_id"], str)
        or not _valid_artifact_provenance(payload)
        or payload["loss_weighting"] != "equal_market_horizon_cells"
        or not _is_finite_number(payload["regularization"])
        or payload["regularization"] < 0
    ):
        raise ValueError("artifact_schema_invalid")
    manifest_ids = payload["manifest_ids"]
    if (
        not isinstance(manifest_ids, list)
        or len(manifest_ids) != 5
        or not all(isinstance(item, str) for item in manifest_ids)
    ):
        raise ValueError("artifact_schema_invalid")

    normalizers = payload["normalizers"]
    if not isinstance(normalizers, dict) or not normalizers:
        raise ValueError("artifact_schema_invalid")
    feature_count: int | None = None
    for market, raw_normalizer in normalizers.items():
        if market not in {"XTAI", "XNAS"} or not isinstance(raw_normalizer, dict):
            raise ValueError("artifact_schema_invalid")
        if set(raw_normalizer) != {
            "method",
            "lower_quantile",
            "upper_quantile",
            "medians",
            "iqrs",
            "lower_bounds",
            "upper_bounds",
        }:
            raise ValueError("artifact_schema_invalid")
        if (
            raw_normalizer["method"] != "median_iqr_winsorized"
            or raw_normalizer["lower_quantile"] != 0.01
            or raw_normalizer["upper_quantile"] != 0.99
        ):
            raise ValueError("artifact_schema_invalid")
        arrays = (
            raw_normalizer["medians"],
            raw_normalizer["iqrs"],
            raw_normalizer["lower_bounds"],
            raw_normalizer["upper_bounds"],
        )
        if any(not isinstance(values, list) or not values for values in arrays):
            raise ValueError("artifact_schema_invalid")
        medians, iqrs, lower_bounds, upper_bounds = cast(
            tuple[list[object], list[object], list[object], list[object]], arrays
        )
        if not (
            len(medians) == len(iqrs) == len(lower_bounds) == len(upper_bounds)
            and all(_is_finite_number(value) for values in arrays for value in values)
        ):
            raise ValueError("artifact_schema_invalid")
        if feature_count is None:
            feature_count = len(medians)
        if feature_count != len(medians):
            raise ValueError("artifact_schema_invalid")
        for iqr, lower, upper in zip(iqrs, lower_bounds, upper_bounds, strict=True):
            if (
                not _is_finite_number(iqr)
                or not _is_finite_number(lower)
                or not _is_finite_number(upper)
                or iqr <= 0
                or lower > upper
            ):
                raise ValueError("artifact_schema_invalid")

    if feature_count is None:
        raise ValueError("artifact_schema_invalid")
    raw_weights = payload["weights"]
    if (
        not isinstance(raw_weights, list)
        or len(raw_weights) != 3
        or any(
            not isinstance(label_weights, list)
            or len(label_weights) != feature_count + 1
            or not all(_is_finite_number(value) for value in label_weights)
            for label_weights in raw_weights
        )
    ):
        raise ValueError("artifact_schema_invalid")

    class_weights = payload["class_weights_by_cell"]
    loss_normalizers = payload["cell_loss_normalizers"]
    if (
        not isinstance(class_weights, dict)
        or not class_weights
        or not isinstance(loss_normalizers, dict)
        or set(class_weights) != set(loss_normalizers)
    ):
        raise ValueError("artifact_schema_invalid")
    for cell, raw_class_weights in class_weights.items():
        if (
            not isinstance(cell, str)
            or not _valid_cell_key(cell, set(normalizers))
            or not isinstance(raw_class_weights, dict)
            or set(raw_class_weights) != {"up", "flat", "down"}
            or any(
                not _is_finite_number(value) or not 0.5 <= value <= 2.0
                for value in raw_class_weights.values()
            )
            or not _is_finite_number(loss_normalizers[cell])
            or loss_normalizers[cell] <= 0
        ):
            raise ValueError("artifact_schema_invalid")

    _validate_artifact_calibrators(
        payload,
        expected_cells={cast(str, cell) for cell in class_weights},
    )
    if "evaluation_report_id" in payload and not isinstance(payload["evaluation_report_id"], str):
        raise ValueError("artifact_schema_invalid")
    return payload


def _load_class_prior_artifact(serialized: bytes) -> dict[str, object]:
    try:
        raw_payload = json.loads(serialized)
    except (TypeError, ValueError) as error:
        raise ValueError("artifact_schema_invalid") from error
    if not isinstance(raw_payload, dict):
        raise ValueError("artifact_schema_invalid")
    payload = cast(dict[str, object], raw_payload)
    required = {
        "artifact_format",
        "model_family",
        "seed",
        "manifest_ids",
        "training_selection_id",
        "feature_schema_id",
        "runtime_id",
        "code_provenance",
        "probabilities_by_cell",
        "calibrator_ids",
        "calibrators",
    }
    optional = {"evaluation_report_id"}
    if (
        not required.issubset(payload)
        or not set(payload).issubset(required | optional)
        or payload["artifact_format"] != "safe-json-v1"
        or payload["model_family"] != "class_prior"
        or isinstance(payload["seed"], bool)
        or not isinstance(payload["seed"], int)
        or not isinstance(payload["training_selection_id"], str)
        or not _valid_artifact_provenance(payload)
    ):
        raise ValueError("artifact_schema_invalid")
    manifest_ids = payload["manifest_ids"]
    if (
        not isinstance(manifest_ids, list)
        or len(manifest_ids) != 5
        or not all(isinstance(item, str) for item in manifest_ids)
    ):
        raise ValueError("artifact_schema_invalid")
    probabilities_by_cell = payload["probabilities_by_cell"]
    if not isinstance(probabilities_by_cell, dict) or not probabilities_by_cell:
        raise ValueError("artifact_schema_invalid")
    for cell, probabilities in probabilities_by_cell.items():
        if (
            not isinstance(cell, str)
            or not _valid_cell_key(cell, {"XTAI", "XNAS"})
            or not isinstance(probabilities, dict)
            or set(probabilities) != {"up", "flat", "down"}
            or any(
                not _is_finite_number(value) or not 0.0 <= value <= 1.0
                for value in probabilities.values()
            )
        ):
            raise ValueError("artifact_schema_invalid")
        probability_sum = sum(cast(float, value) for value in probabilities.values())
        if abs(probability_sum - 1.0) > 1e-12:
            raise ValueError("artifact_schema_invalid")
    _validate_artifact_calibrators(
        payload,
        expected_cells={cast(str, cell) for cell in probabilities_by_cell},
    )
    if "evaluation_report_id" in payload and not isinstance(payload["evaluation_report_id"], str):
        raise ValueError("artifact_schema_invalid")
    return payload


def _validate_artifact_calibrators(
    payload: dict[str, object],
    *,
    expected_cells: set[str],
) -> None:
    calibrator_ids = payload.get("calibrator_ids")
    calibrators = payload.get("calibrators")
    if (calibrator_ids is None) != (calibrators is None):
        raise ValueError("artifact_schema_invalid")
    if calibrator_ids is not None:
        if (
            not isinstance(calibrator_ids, list)
            or not isinstance(calibrators, list)
            or not all(isinstance(item, str) for item in calibrator_ids)
            or len(calibrator_ids) != len(calibrators)
        ):
            raise ValueError("artifact_schema_invalid")
        parsed_ids: list[str] = []
        parsed_bindings: set[tuple[object, object]] = set()
        for calibrator in calibrators:
            if (
                not isinstance(calibrator, dict)
                or set(calibrator)
                != {
                    "calibrator_id",
                    "market",
                    "horizon_sessions",
                    "temperature",
                    "fit_method",
                    "sample_count",
                    "class_counts",
                    "pre_nll",
                    "post_nll",
                    "status",
                }
                or not isinstance(calibrator.get("calibrator_id"), str)
                or calibrator.get("market") not in {"XTAI", "XNAS"}
                or calibrator.get("horizon_sessions") not in {1, 5, 20}
                or calibrator.get("fit_method") != "temperature_scaling"
                or calibrator.get("status") not in {"sufficient_data", "insufficient_data"}
                or not _is_finite_number(calibrator.get("temperature"))
                or cast(float, calibrator["temperature"]) <= 0
                or isinstance(calibrator.get("sample_count"), bool)
                or not isinstance(calibrator.get("sample_count"), int)
                or cast(int, calibrator["sample_count"]) < 0
                or not isinstance(calibrator.get("class_counts"), list)
                or len(cast(list[object], calibrator["class_counts"])) != 3
                or any(
                    isinstance(count, bool) or not isinstance(count, int) or count < 0
                    for count in cast(list[object], calibrator["class_counts"])
                )
                or sum(cast(list[int], calibrator["class_counts"])) != calibrator["sample_count"]
                or not _is_finite_number(calibrator.get("pre_nll"))
                or not _is_finite_number(calibrator.get("post_nll"))
                or cast(float, calibrator["post_nll"]) > cast(float, calibrator["pre_nll"])
            ):
                raise ValueError("artifact_schema_invalid")
            if calibrator["status"] == "sufficient_data" and calibrator["sample_count"] == 0:
                raise ValueError("artifact_schema_invalid")
            if calibrator["status"] == "insufficient_data" and (
                calibrator["sample_count"] != 0
                or calibrator["class_counts"] != [0, 0, 0]
                or calibrator["temperature"] != 1.0
                or calibrator["pre_nll"] != 0.0
                or calibrator["post_nll"] != 0.0
            ):
                raise ValueError("artifact_schema_invalid")
            calibrator_id = cast(str, calibrator["calibrator_id"])
            content = {key: value for key, value in calibrator.items() if key != "calibrator_id"}
            if calibrator_id != _content_id("temperature_calibrator", content):
                raise ValueError("artifact_schema_invalid")
            parsed_ids.append(calibrator_id)
            parsed_bindings.add((calibrator["market"], calibrator["horizon_sessions"]))
        expected_bindings = {
            (market, int(raw_horizon))
            for cell in expected_cells
            for market, raw_horizon in (cell.split(":", maxsplit=1),)
        }
        if (
            calibrator_ids != parsed_ids
            or len(set(parsed_ids)) != len(parsed_ids)
            or len(parsed_bindings) != len(parsed_ids)
            or parsed_bindings != expected_bindings
        ):
            raise ValueError("artifact_schema_invalid")


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)


def _valid_cell_key(cell: str, markets: set[object]) -> bool:
    try:
        market, raw_horizon = cell.split(":", maxsplit=1)
        horizon = int(raw_horizon)
    except ValueError:
        return False
    return market in markets and horizon in {1, 5, 20} and raw_horizon == str(horizon)


def _serialize_payload(payload: object) -> bytes:
    return canonical_json_bytes(payload)


def _artifact_provenance(request: TrainingRequest) -> dict[str, str]:
    if not isinstance(request.provenance, ArtifactProvenance):
        raise ValueError("artifact_provenance_required")
    return request.provenance.payload()


def _valid_artifact_provenance(payload: dict[str, object]) -> bool:
    try:
        ArtifactProvenance.from_payload(payload)
    except ValueError:
        return False
    return True


def _artifact_id(serialized: bytes) -> str:
    return sha256_id(serialized)


def _feature_row_payload(row: FeatureRow) -> dict[str, object]:
    return {
        "row_id": row.row_id,
        "market": row.market,
        "horizon_sessions": row.horizon_sessions,
        "values": row.values,
        "label": row.label,
        "session_date": row.session_date.isoformat() if row.session_date is not None else None,
    }


def _cell_key(row: FeatureRow) -> str:
    return f"{row.market}:{row.horizon_sessions}"


def _fit_robust_normalizer(
    rows: list[FeatureRow],
    feature_count: int,
) -> dict[str, object]:
    medians: list[float] = []
    iqrs: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    for index in range(feature_count):
        values = sorted(row.values[index] for row in rows)
        median = _quantile(values, 0.5)
        iqr = _quantile(values, 0.75) - _quantile(values, 0.25)
        medians.append(median)
        iqrs.append(iqr if iqr > 1e-12 else 1.0)
        lower_bounds.append(_quantile(values, 0.01))
        upper_bounds.append(_quantile(values, 0.99))
    return {
        "method": "median_iqr_winsorized",
        "lower_quantile": 0.01,
        "upper_quantile": 0.99,
        "medians": medians,
        "iqrs": iqrs,
        "lower_bounds": lower_bounds,
        "upper_bounds": upper_bounds,
    }


def _apply_robust_normalizer(
    normalizer: dict[str, object],
    values: tuple[float, ...],
) -> list[float]:
    medians = cast(list[float], normalizer["medians"])
    iqrs = cast(list[float], normalizer["iqrs"])
    lower_bounds = cast(list[float], normalizer["lower_bounds"])
    upper_bounds = cast(list[float], normalizer["upper_bounds"])
    if not (len(values) == len(medians) == len(iqrs) == len(lower_bounds) == len(upper_bounds)):
        raise ValueError("feature_count_mismatch")
    return [
        (min(max(value, lower_bounds[index]), upper_bounds[index]) - medians[index]) / iqrs[index]
        for index, value in enumerate(values)
    ]


def _quantile(values: list[float], probability: float) -> float:
    position = (len(values) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - lower_index
    return values[lower_index] + (values[upper_index] - values[lower_index]) * fraction


def _training_selection_id(request: TrainingRequest) -> str:
    return _content_id(
        "training-selection",
        {
            "feature_batch_id": request.feature_batch.feature_batch_id,
            "fold_manifest_id": request.feature_batch.fold_manifest_id,
            "training_row_ids": request.training_row_ids,
            "validation_row_ids": request.validation_row_ids,
        },
    )


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
