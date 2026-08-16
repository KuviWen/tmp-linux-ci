from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from math import log
from typing import Literal, Protocol, cast

from stock_forecasting.contracts import ProbabilityVector
from stock_forecasting.data_supply import HistoricalAvailabilityClaim
from stock_forecasting.forecasting import (
    ClassPriorTrendForecaster,
    FeatureBatch,
    FeatureRow,
    ForecastPrediction,
    ModelArtifact,
    PredictionRequest,
    RegularizedMultinomialLogisticTrendForecaster,
    TrainingRequest,
    TrendForecaster,
    TrendLabel,
)

Market = Literal["XTAI", "XNAS"]
Horizon = Literal[1, 5, 20]


def _content_id(kind: str, payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(kind.encode() + serialized).hexdigest()}"


@dataclass(frozen=True)
class HistoricalClaimRef:
    market: Market
    claim_id: str
    claim: HistoricalAvailabilityClaim


class FormalHistoricalClaimVerifier(Protocol):
    def is_formally_reconstructable(
        self,
        *,
        claim_id: str,
        claim: HistoricalAvailabilityClaim,
    ) -> bool: ...


class _UnavailableHistoricalClaimVerifier:
    def is_formally_reconstructable(
        self,
        *,
        claim_id: str,
        claim: HistoricalAvailabilityClaim,
    ) -> bool:
        return False


@dataclass(frozen=True)
class TrainingIntentRef:
    training_intent_id: str
    model_family_id: str
    initiated_by: str
    executed_by: str
    created_at: datetime
    feature_batch: FeatureBatch
    preregistered_seeds: tuple[int, int, int]
    execution_purpose: Literal["formal_candidate", "engineering_acceptance"] = "formal_candidate"
    historical_claims: tuple[HistoricalClaimRef, ...] = ()


@dataclass(frozen=True)
class WalkForwardFold:
    market: Market
    test_quarter: str
    training_row_ids: tuple[str, ...]
    validation_row_ids: tuple[str, ...]
    test_row_ids: tuple[str, ...]
    purge_session_dates: tuple[date, ...]
    embargo_session_dates: tuple[date, ...]


@dataclass(frozen=True)
class FoldManifest:
    fold_manifest_id: str
    folds: tuple[WalkForwardFold, ...]
    fold_count: int
    actual_history_start: date
    actual_history_end: date
    purge_sessions: int = 20
    embargo_sessions: int = 20


@dataclass(frozen=True)
class CalibrationEvidence:
    calibrator_id: str
    market: Market
    horizon_sessions: Horizon
    status: Literal["sufficient_data", "insufficient_data"]
    sample_count: int
    class_counts: tuple[int, ...]
    temperature: float
    fit_method: Literal["temperature_scaling"]
    pre_nll: float
    post_nll: float


@dataclass(frozen=True)
class EvaluationReport:
    evaluation_report_id: str
    class_prior_equal_cell_macro_f1: float
    logistic_equal_cell_macro_f1: float
    improvement_percentage_points: float
    seed_macro_f1: tuple[float, ...]
    cost_manifest_id: str
    fold_manifest_id: str


@dataclass(frozen=True)
class CandidateEvidenceBundle:
    candidate_id: str
    model_family_id: str
    training_intent_id: str
    primary_artifact: ModelArtifact
    logistic_artifacts: tuple[ModelArtifact, ...]
    class_prior_artifacts: tuple[ModelArtifact, ...]
    fold_manifest: FoldManifest
    calibrators: tuple[CalibrationEvidence, ...]
    evaluation_report: EvaluationReport
    formal_qualification: bool


@dataclass(frozen=True)
class ForecastLabOutcome:
    status: Literal["developed", "blocked"]
    blocked_reasons: tuple[str, ...]
    candidate_bundle: CandidateEvidenceBundle | None


class ForecastLab:
    _markets: tuple[Market, ...] = ("XTAI", "XNAS")
    _horizons: tuple[Horizon, ...] = (1, 5, 20)
    _labels: tuple[TrendLabel, ...] = ("up", "flat", "down")

    def __init__(
        self,
        *,
        historical_claim_verifier: FormalHistoricalClaimVerifier | None = None,
    ) -> None:
        self._historical_claim_verifier = (
            historical_claim_verifier or _UnavailableHistoricalClaimVerifier()
        )

    def develop(self, intent: TrainingIntentRef) -> ForecastLabOutcome:
        if not self._has_class_support(intent.feature_batch.rows):
            return self._blocked("insufficient_class_support")
        formal_qualification = self._has_formal_source_basis(intent)
        if intent.execution_purpose == "formal_candidate" and not formal_qualification:
            return self._blocked("unverified_source_basis")
        fold_manifest = self._build_fold_manifest(intent.feature_batch.rows)
        if fold_manifest is None:
            return self._blocked("insufficient_fold_history")
        feature_batch = replace(
            intent.feature_batch,
            fold_manifest_id=fold_manifest.fold_manifest_id,
        )
        try:
            evaluation = self._evaluate(
                feature_batch,
                fold_manifest,
                intent.preregistered_seeds,
            )
            training_ids, validation_ids = self._latest_joint_split(fold_manifest)
            logistic_forecaster: TrendForecaster = RegularizedMultinomialLogisticTrendForecaster()
            logistic_artifacts = tuple(
                logistic_forecaster.train(
                    TrainingRequest(
                        feature_batch=feature_batch,
                        training_row_ids=training_ids,
                        validation_row_ids=validation_ids,
                        seed=seed,
                    )
                )
                for seed in intent.preregistered_seeds
            )
            calibrators = self._build_calibrators(
                feature_batch,
                fold_manifest,
                logistic_artifacts[0],
            )
            if any(item.status == "insufficient_data" for item in calibrators):
                return self._blocked("insufficient_calibration_support")
            prior_forecaster: TrendForecaster = ClassPriorTrendForecaster()
            prior_artifacts = tuple(
                prior_forecaster.train(
                    TrainingRequest(
                        feature_batch=feature_batch,
                        training_row_ids=training_ids,
                        validation_row_ids=validation_ids,
                        seed=seed,
                    )
                )
                for seed in intent.preregistered_seeds
            )
        except ValueError as error:
            if str(error) == "insufficient_calibration_support":
                return self._blocked("insufficient_calibration_support")
            return self._blocked("logistic_training_failed")
        except ArithmeticError:
            return self._blocked("logistic_training_failed")
        logistic_artifacts = tuple(
            self._bind_candidate_evidence(
                artifact,
                calibrators=calibrators,
                evaluation_report_id=evaluation.evaluation_report_id,
            )
            for artifact in logistic_artifacts
        )
        prior_artifacts = tuple(
            self._bind_candidate_evidence(
                artifact,
                calibrators=calibrators,
                evaluation_report_id=evaluation.evaluation_report_id,
            )
            for artifact in prior_artifacts
        )
        candidate_id = _content_id(
            "candidate",
            {
                "training_intent_id": intent.training_intent_id,
                "artifact_ids": [artifact.artifact_id for artifact in logistic_artifacts],
                "calibrator_ids": [item.calibrator_id for item in calibrators],
                "evaluation_report_id": evaluation.evaluation_report_id,
            },
        )
        return ForecastLabOutcome(
            status="developed",
            blocked_reasons=(),
            candidate_bundle=CandidateEvidenceBundle(
                candidate_id=candidate_id,
                model_family_id=intent.model_family_id,
                training_intent_id=intent.training_intent_id,
                primary_artifact=logistic_artifacts[0],
                logistic_artifacts=logistic_artifacts,
                class_prior_artifacts=prior_artifacts,
                fold_manifest=fold_manifest,
                calibrators=calibrators,
                evaluation_report=evaluation,
                formal_qualification=formal_qualification,
            ),
        )

    def _has_formal_source_basis(self, intent: TrainingIntentRef) -> bool:
        if intent.execution_purpose != "formal_candidate":
            return False
        if len(intent.historical_claims) != len(self._markets):
            return False
        if {item.market for item in intent.historical_claims} != set(self._markets):
            return False
        try:
            return all(
                self._historical_claim_verifier.is_formally_reconstructable(
                    claim_id=item.claim_id,
                    claim=item.claim,
                )
                for item in intent.historical_claims
            )
        except (KeyError, RuntimeError, ValueError):
            return False

    @staticmethod
    def _blocked(reason: str) -> ForecastLabOutcome:
        return ForecastLabOutcome(
            status="blocked",
            blocked_reasons=(reason,),
            candidate_bundle=None,
        )

    @staticmethod
    def _bind_candidate_evidence(
        artifact: ModelArtifact,
        *,
        calibrators: tuple[CalibrationEvidence, ...],
        evaluation_report_id: str | None,
    ) -> ModelArtifact:
        payload = json.loads(artifact.serialized)
        calibrator_ids = tuple(item.calibrator_id for item in calibrators)
        payload["calibrator_ids"] = calibrator_ids
        payload["calibrators"] = [
            {
                "calibrator_id": item.calibrator_id,
                "market": item.market,
                "horizon_sessions": item.horizon_sessions,
                "temperature": item.temperature,
                "fit_method": item.fit_method,
            }
            for item in calibrators
        ]
        if evaluation_report_id is not None:
            payload["evaluation_report_id"] = evaluation_report_id
        payload["artifact_format"] = "safe-json-v1"
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return replace(
            artifact,
            artifact_id=f"sha256:{hashlib.sha256(serialized).hexdigest()}",
            serialized=serialized,
            calibrator_ids=calibrator_ids,
            evaluation_report_id=evaluation_report_id,
        )

    def _has_class_support(self, rows: tuple[FeatureRow, ...]) -> bool:
        for market in self._markets:
            for horizon in self._horizons:
                labels = {
                    row.label
                    for row in rows
                    if row.market == market
                    and row.horizon_sessions == horizon
                    and row.label is not None
                }
                if labels != set(self._labels):
                    return False
        return True

    def _build_fold_manifest(self, rows: tuple[FeatureRow, ...]) -> FoldManifest | None:
        all_dates = sorted({row.session_date for row in rows if row.session_date is not None})
        if not all_dates:
            return None
        folds: list[WalkForwardFold] = []
        for market in self._markets:
            market_rows = tuple(
                row for row in rows if row.market == market and row.session_date is not None
            )
            dates_by_quarter: dict[str, list[date]] = defaultdict(list)
            market_dates = sorted({cast(date, row.session_date) for row in market_rows})
            for session_date in market_dates:
                dates_by_quarter[self._quarter(session_date)].append(session_date)
            quarters = sorted(dates_by_quarter)
            for test_quarter in quarters[4:]:
                test_dates = tuple(dates_by_quarter[test_quarter])
                first_test_date = test_dates[0]
                prior_dates = sorted(
                    session_date
                    for quarter in quarters
                    for session_date in dates_by_quarter[quarter]
                    if session_date < first_test_date
                )
                if len(prior_dates) < 62:
                    continue
                training_dates = set(prior_dates[:-61])
                purge_dates = tuple(prior_dates[-61:-41])
                validation_dates = set(prior_dates[-41:-20])
                embargo_dates = tuple(prior_dates[-20:])
                folds.append(
                    WalkForwardFold(
                        market=market,
                        test_quarter=test_quarter,
                        training_row_ids=self._row_ids(market_rows, training_dates),
                        validation_row_ids=self._row_ids(market_rows, validation_dates),
                        test_row_ids=self._row_ids(market_rows, set(test_dates)),
                        purge_session_dates=purge_dates,
                        embargo_session_dates=embargo_dates,
                    )
                )
        if not folds:
            return None
        payload = [
            {
                "market": fold.market,
                "test_quarter": fold.test_quarter,
                "training_row_ids": fold.training_row_ids,
                "validation_row_ids": fold.validation_row_ids,
                "test_row_ids": fold.test_row_ids,
                "purge_session_dates": [item.isoformat() for item in fold.purge_session_dates],
                "embargo_session_dates": [item.isoformat() for item in fold.embargo_session_dates],
            }
            for fold in folds
        ]
        return FoldManifest(
            fold_manifest_id=_content_id("walk_forward_fold_manifest", payload),
            folds=tuple(folds),
            fold_count=len(folds),
            actual_history_start=all_dates[0],
            actual_history_end=all_dates[-1],
        )

    @staticmethod
    def _quarter(session_date: date) -> str:
        return f"{session_date.year}-Q{((session_date.month - 1) // 3) + 1}"

    @staticmethod
    def _row_ids(rows: tuple[FeatureRow, ...], dates: set[date]) -> tuple[str, ...]:
        return tuple(
            row.row_id for row in rows if row.session_date is not None and row.session_date in dates
        )

    def _build_calibrators(
        self,
        batch: FeatureBatch,
        fold_manifest: FoldManifest,
        artifact: ModelArtifact,
    ) -> tuple[CalibrationEvidence, ...]:
        _, validation_ids = self._latest_joint_split(fold_manifest)
        return self._build_calibrators_for_ids(batch, validation_ids, artifact)

    def _build_calibrators_for_ids(
        self,
        batch: FeatureBatch,
        validation_ids: tuple[str, ...],
        artifact: ModelArtifact,
    ) -> tuple[CalibrationEvidence, ...]:
        validation_id_set = set(validation_ids)
        validation_rows = tuple(row for row in batch.rows if row.row_id in validation_id_set)
        forecaster: TrendForecaster
        if artifact.model_family == "regularized_multinomial_logistic":
            forecaster = RegularizedMultinomialLogisticTrendForecaster.load(artifact.serialized)
        elif artifact.model_family == "class_prior":
            forecaster = ClassPriorTrendForecaster.load(artifact.serialized)
        else:
            raise ValueError("unsupported_calibration_model_family")
        forecast = forecaster.predict(PredictionRequest(artifact, validation_rows))
        probabilities_by_id = {
            prediction.row_id: prediction.probabilities for prediction in forecast.predictions
        }
        evidence: list[CalibrationEvidence] = []
        for market in self._markets:
            for horizon in self._horizons:
                labels = [
                    row.label
                    for row in batch.rows
                    if row.row_id in validation_id_set
                    and row.market == market
                    and row.horizon_sessions == horizon
                    and row.label is not None
                ]
                counts = tuple(labels.count(label) for label in self._labels)
                sufficient = len(labels) >= 9 and all(count >= 2 for count in counts)
                status: Literal["sufficient_data", "insufficient_data"] = (
                    "sufficient_data" if sufficient else "insufficient_data"
                )
                cell_rows = tuple(
                    row
                    for row in validation_rows
                    if row.market == market
                    and row.horizon_sessions == horizon
                    and row.label is not None
                )
                temperature, pre_nll, post_nll = self._fit_temperature(
                    cell_rows,
                    probabilities_by_id,
                )
                payload = {
                    "market": market,
                    "horizon_sessions": horizon,
                    "sample_count": len(labels),
                    "class_counts": counts,
                    "temperature": temperature,
                    "fit_method": "temperature_scaling",
                    "pre_nll": pre_nll,
                    "post_nll": post_nll,
                    "status": status,
                }
                evidence.append(
                    CalibrationEvidence(
                        calibrator_id=_content_id("temperature_calibrator", payload),
                        market=market,
                        horizon_sessions=horizon,
                        status=status,
                        sample_count=len(labels),
                        class_counts=counts,
                        temperature=temperature,
                        fit_method="temperature_scaling",
                        pre_nll=pre_nll,
                        post_nll=post_nll,
                    )
                )
        return tuple(evidence)

    def _fit_temperature(
        self,
        rows: tuple[FeatureRow, ...],
        probabilities_by_id: dict[str, ProbabilityVector],
    ) -> tuple[float, float, float]:
        def nll(temperature: float) -> float:
            if not rows:
                return float("inf")
            total = 0.0
            for row in rows:
                assert row.label is not None
                calibrated = self._apply_temperature(probabilities_by_id[row.row_id], temperature)
                total -= log(max(calibrated[row.label], 1e-15))
            return total / len(rows)

        pre_nll = nll(1.0)
        candidates = tuple(0.5 + index * 0.05 for index in range(51))
        temperature = min(candidates, key=lambda value: (nll(value), abs(value - 1.0)))
        return temperature, pre_nll, nll(temperature)

    @staticmethod
    def _apply_temperature(
        probabilities: ProbabilityVector, temperature: float
    ) -> ProbabilityVector:
        up = max(probabilities["up"], 1e-15) ** (1.0 / temperature)
        flat = max(probabilities["flat"], 1e-15) ** (1.0 / temperature)
        down = max(probabilities["down"], 1e-15) ** (1.0 / temperature)
        total = up + flat + down
        return {"up": up / total, "flat": flat / total, "down": down / total}

    def _evaluate(
        self,
        batch: FeatureBatch,
        fold_manifest: FoldManifest,
        seeds: tuple[int, int, int],
    ) -> EvaluationReport:
        rows_by_id = {row.row_id: row for row in batch.rows}
        folds_by_quarter: dict[str, list[WalkForwardFold]] = defaultdict(list)
        for fold in fold_manifest.folds:
            folds_by_quarter[fold.test_quarter].append(fold)
        prior_truth = self._cell_lists()
        prior_predictions = self._cell_lists()
        seed_truth = [self._cell_lists() for _ in seeds]
        seed_predictions = [self._cell_lists() for _ in seeds]
        for folds in folds_by_quarter.values():
            training_ids = tuple(row_id for fold in folds for row_id in fold.training_row_ids)
            validation_ids = tuple(row_id for fold in folds for row_id in fold.validation_row_ids)
            test_ids = tuple(row_id for fold in folds for row_id in fold.test_row_ids)
            test_rows = tuple(rows_by_id[row_id] for row_id in test_ids)
            prior_artifact = ClassPriorTrendForecaster().train(
                TrainingRequest(batch, training_ids, validation_ids, seeds[0])
            )
            prior_calibrators = self._build_calibrators_for_ids(
                batch, validation_ids, prior_artifact
            )
            if any(item.status == "insufficient_data" for item in prior_calibrators):
                raise ValueError("insufficient_calibration_support")
            calibrated_prior = self._bind_candidate_evidence(
                prior_artifact,
                calibrators=prior_calibrators,
                evaluation_report_id=None,
            )
            prior_forecast = ClassPriorTrendForecaster.load(calibrated_prior.serialized).predict(
                PredictionRequest(calibrated_prior, test_rows)
            )
            self._collect_predictions(
                test_rows,
                prior_forecast.predictions,
                prior_truth,
                prior_predictions,
            )
            for seed_index, seed in enumerate(seeds):
                artifact = RegularizedMultinomialLogisticTrendForecaster().train(
                    TrainingRequest(batch, training_ids, validation_ids, seed)
                )
                calibrators = self._build_calibrators_for_ids(batch, validation_ids, artifact)
                if any(item.status == "insufficient_data" for item in calibrators):
                    raise ValueError("insufficient_calibration_support")
                calibrated_artifact = self._bind_candidate_evidence(
                    artifact,
                    calibrators=calibrators,
                    evaluation_report_id=None,
                )
                forecast = RegularizedMultinomialLogisticTrendForecaster.load(
                    calibrated_artifact.serialized
                ).predict(PredictionRequest(calibrated_artifact, test_rows))
                self._collect_predictions(
                    test_rows,
                    forecast.predictions,
                    seed_truth[seed_index],
                    seed_predictions[seed_index],
                )
        prior_score = self._equal_cell_macro_f1(prior_truth, prior_predictions)
        seed_scores = tuple(
            self._equal_cell_macro_f1(truth, predictions)
            for truth, predictions in zip(seed_truth, seed_predictions, strict=True)
        )
        logistic_score = sum(seed_scores) / len(seed_scores)
        report_payload = {
            "prior": prior_score,
            "logistic": logistic_score,
            "seed_scores": seed_scores,
            "cost_manifest_id": batch.cost_manifest_id,
            "fold_manifest_id": fold_manifest.fold_manifest_id,
        }
        return EvaluationReport(
            evaluation_report_id=_content_id("evaluation_report", report_payload),
            class_prior_equal_cell_macro_f1=prior_score,
            logistic_equal_cell_macro_f1=logistic_score,
            improvement_percentage_points=(logistic_score - prior_score) * 100,
            seed_macro_f1=seed_scores,
            cost_manifest_id=batch.cost_manifest_id,
            fold_manifest_id=fold_manifest.fold_manifest_id,
        )

    @staticmethod
    def _latest_joint_split(
        fold_manifest: FoldManifest,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        latest_quarter = max(fold.test_quarter for fold in fold_manifest.folds)
        latest_folds = tuple(
            fold for fold in fold_manifest.folds if fold.test_quarter == latest_quarter
        )
        return (
            tuple(row_id for fold in latest_folds for row_id in fold.training_row_ids),
            tuple(row_id for fold in latest_folds for row_id in fold.validation_row_ids),
        )

    def _cell_lists(self) -> dict[tuple[Market, Horizon], list[TrendLabel]]:
        return {(market, horizon): [] for market in self._markets for horizon in self._horizons}

    def _collect_predictions(
        self,
        rows: tuple[FeatureRow, ...],
        predictions: tuple[ForecastPrediction, ...],
        truth: dict[tuple[Market, Horizon], list[TrendLabel]],
        predicted: dict[tuple[Market, Horizon], list[TrendLabel]],
    ) -> None:
        predictions_by_id = {
            prediction.row_id: prediction.probabilities for prediction in predictions
        }
        for row in rows:
            assert row.label is not None
            key = (row.market, row.horizon_sessions)
            probabilities = predictions_by_id[row.row_id]
            winning_label = cast(
                TrendLabel,
                max(
                    (
                        ("up", probabilities["up"]),
                        ("flat", probabilities["flat"]),
                        ("down", probabilities["down"]),
                    ),
                    key=lambda item: item[1],
                )[0],
            )
            truth[key].append(row.label)
            predicted[key].append(winning_label)

    def _equal_cell_macro_f1(
        self,
        truth: dict[tuple[Market, Horizon], list[TrendLabel]],
        predicted: dict[tuple[Market, Horizon], list[TrendLabel]],
    ) -> float:
        cell_scores: list[float] = []
        for market in self._markets:
            for horizon in self._horizons:
                actual = truth[(market, horizon)]
                guesses = predicted[(market, horizon)]
                label_scores: list[float] = []
                for label in self._labels:
                    true_positive = sum(
                        actual_label == label and guess == label
                        for actual_label, guess in zip(actual, guesses, strict=True)
                    )
                    false_positive = sum(
                        actual_label != label and guess == label
                        for actual_label, guess in zip(actual, guesses, strict=True)
                    )
                    false_negative = sum(
                        actual_label == label and guess != label
                        for actual_label, guess in zip(actual, guesses, strict=True)
                    )
                    denominator = (2 * true_positive) + false_positive + false_negative
                    label_scores.append((2 * true_positive / denominator) if denominator else 0.0)
                cell_scores.append(sum(label_scores) / len(label_scores))
        return sum(cell_scores) / len(cell_scores)
