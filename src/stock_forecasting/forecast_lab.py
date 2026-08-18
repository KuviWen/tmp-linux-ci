from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal, Protocol, cast

from stock_forecasting.content_address import content_id as _content_id
from stock_forecasting.contracts import HistoricalTrainingLineage
from stock_forecasting.forecasting import (
    CalibrationEvidence,
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


@dataclass(frozen=True)
class HistoricalClaimRef:
    market: Market
    claim_id: str


class FormalHistoricalClaimVerifier(Protocol):
    def verify_training_lineage(
        self,
        *,
        lineage: HistoricalTrainingLineage,
        feature_batch_id: str,
        source_policy_manifest_id: str,
        label_manifest_id: str,
        fold_manifest_id: str,
        feature_rows_digest: str,
    ) -> bool: ...


class FormalCostScenarioVerifier(Protocol):
    def verify_cost_scenario(self, cost_manifest_id: str) -> bool: ...


class _UnavailableHistoricalClaimVerifier:
    def verify_training_lineage(
        self,
        *,
        lineage: HistoricalTrainingLineage,
        feature_batch_id: str,
        source_policy_manifest_id: str,
        label_manifest_id: str,
        fold_manifest_id: str,
        feature_rows_digest: str,
    ) -> bool:
        return False


class _UnavailableCostScenarioVerifier:
    def verify_cost_scenario(self, cost_manifest_id: str) -> bool:
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
        cost_scenario_verifier: FormalCostScenarioVerifier | None = None,
        class_prior_forecaster: TrendForecaster | None = None,
        logistic_forecaster: TrendForecaster | None = None,
    ) -> None:
        self._historical_claim_verifier = (
            historical_claim_verifier or _UnavailableHistoricalClaimVerifier()
        )
        self._cost_scenario_verifier = cost_scenario_verifier or _UnavailableCostScenarioVerifier()
        self._class_prior_forecaster = class_prior_forecaster or ClassPriorTrendForecaster()
        self._logistic_forecaster = (
            logistic_forecaster or RegularizedMultinomialLogisticTrendForecaster()
        )

    def develop(self, intent: TrainingIntentRef) -> ForecastLabOutcome:
        if not self._has_class_support(intent.feature_batch.rows):
            return self._blocked("insufficient_class_support")
        fold_manifest = self._build_fold_manifest(intent.feature_batch.rows)
        if fold_manifest is None:
            return self._blocked("insufficient_fold_history")
        final_lineages = tuple(
            replace(lineage, fold_manifest_id=fold_manifest.fold_manifest_id)
            for lineage in intent.feature_batch.historical_lineage
        )
        feature_batch = replace(
            intent.feature_batch,
            fold_manifest_id=fold_manifest.fold_manifest_id,
            historical_lineage=final_lineages,
        ).with_content_id()
        final_intent = replace(intent, feature_batch=feature_batch)
        formal_source_basis = self._has_formal_source_basis(final_intent)
        if intent.execution_purpose == "formal_candidate" and not formal_source_basis:
            return self._blocked("unverified_source_basis")
        formal_cost_scenario = self._has_formal_cost_scenario(final_intent)
        if intent.execution_purpose == "formal_candidate" and not formal_cost_scenario:
            return self._blocked("unverified_cost_scenario")
        formal_qualification = formal_source_basis and formal_cost_scenario
        try:
            evaluation = self._evaluate(
                feature_batch,
                fold_manifest,
                intent.preregistered_seeds,
            )
            training_ids, validation_ids = self._latest_joint_split(fold_manifest)
            logistic_artifacts = tuple(
                self._logistic_forecaster.train(
                    TrainingRequest(
                        feature_batch=feature_batch,
                        training_row_ids=training_ids,
                        validation_row_ids=validation_ids,
                        seed=seed,
                    )
                )
                for seed in intent.preregistered_seeds
            )
            calibrators = logistic_artifacts[0].calibrators
            prior_artifacts = tuple(
                self._class_prior_forecaster.train(
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
            artifact.bind_evaluation_report(evaluation.evaluation_report_id)
            for artifact in logistic_artifacts
        )
        prior_artifacts = tuple(
            artifact.bind_evaluation_report(evaluation.evaluation_report_id)
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
        lineages = intent.feature_batch.historical_lineage
        if len(lineages) != len(self._markets):
            return False
        if {item.market for item in lineages} != set(self._markets):
            return False
        if not intent.feature_batch.is_content_addressed():
            return False
        lineage_by_market = {item.market: item for item in lineages}
        try:
            return all(
                (lineage := lineage_by_market[item.market]).claim_id == item.claim_id
                and lineage.source_policy_manifest_id
                == intent.feature_batch.source_policy_manifest_id
                and lineage.label_manifest_id == intent.feature_batch.label_manifest_id
                and lineage.fold_manifest_id == intent.feature_batch.fold_manifest_id
                and lineage.feature_rows_digest
                == intent.feature_batch.market_rows_digest(item.market)
                and self._historical_claim_verifier.verify_training_lineage(
                    lineage=lineage,
                    feature_batch_id=intent.feature_batch.feature_batch_id,
                    source_policy_manifest_id=intent.feature_batch.source_policy_manifest_id,
                    label_manifest_id=intent.feature_batch.label_manifest_id,
                    fold_manifest_id=intent.feature_batch.fold_manifest_id,
                    feature_rows_digest=intent.feature_batch.market_rows_digest(item.market),
                )
                for item in intent.historical_claims
            )
        except (KeyError, RuntimeError, ValueError):
            return False

    def _has_formal_cost_scenario(self, intent: TrainingIntentRef) -> bool:
        if intent.execution_purpose != "formal_candidate":
            return False
        try:
            return self._cost_scenario_verifier.verify_cost_scenario(
                intent.feature_batch.cost_manifest_id
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
            prior_artifact = self._class_prior_forecaster.train(
                TrainingRequest(batch, training_ids, validation_ids, seeds[0])
            )
            prior_forecast = self._class_prior_forecaster.predict(
                PredictionRequest(prior_artifact, test_rows)
            )
            self._collect_predictions(
                test_rows,
                prior_forecast.predictions,
                prior_truth,
                prior_predictions,
            )
            for seed_index, seed in enumerate(seeds):
                artifact = self._logistic_forecaster.train(
                    TrainingRequest(batch, training_ids, validation_ids, seed)
                )
                forecast = self._logistic_forecaster.predict(PredictionRequest(artifact, test_rows))
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
