from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal, Protocol, cast

from stock_forecasting.content_address import canonical_json_bytes, sha256_id
from stock_forecasting.content_address import content_id as _content_id
from stock_forecasting.contracts import HistoricalTrainingLineage
from stock_forecasting.evaluation_report import EvaluationReport, SeedArtifactEvaluation
from stock_forecasting.forecasting import (
    ArtifactProvenance,
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
from stock_forecasting.model_governance import BOOTSTRAP_MINIMUM_NONINFERIOR_QUARTERS

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
    provenance: ArtifactProvenance
    execution_purpose: Literal["formal_candidate", "engineering_acceptance"] = "formal_candidate"
    historical_claims: tuple[HistoricalClaimRef, ...] = ()

    def content_id(self) -> str:
        return _content_id(
            "training_intent",
            {
                "model_family_id": self.model_family_id,
                "initiated_by": self.initiated_by,
                "executed_by": self.executed_by,
                "created_at": self.created_at.isoformat(),
                "feature_batch_id": self.feature_batch.feature_batch_id,
                "preregistered_seeds": self.preregistered_seeds,
                "provenance": self.provenance.payload(),
                "execution_purpose": self.execution_purpose,
                "historical_claims": [
                    {"market": claim.market, "claim_id": claim.claim_id}
                    for claim in self.historical_claims
                ],
            },
        )

    def with_content_id(self) -> TrainingIntentRef:
        return replace(self, training_intent_id=self.content_id())

    def is_content_addressed(self) -> bool:
        return self.training_intent_id == self.content_id()


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
    serialized: bytes = b""

    @classmethod
    def create(
        cls,
        *,
        folds: tuple[WalkForwardFold, ...],
        actual_history_start: date,
        actual_history_end: date,
        purge_sessions: int = 20,
        embargo_sessions: int = 20,
    ) -> FoldManifest:
        if (
            not folds
            or isinstance(purge_sessions, bool)
            or not isinstance(purge_sessions, int)
            or purge_sessions < 0
            or isinstance(embargo_sessions, bool)
            or not isinstance(embargo_sessions, int)
            or embargo_sessions < 0
            or not isinstance(actual_history_start, date)
            or not isinstance(actual_history_end, date)
            or actual_history_start > actual_history_end
        ):
            raise ValueError("fold_manifest_schema_invalid")
        payload = {
            "artifact_kind": "walk_forward_fold_manifest",
            "schema_version": "walk-forward-fold-manifest/v1",
            "folds": [cls._fold_payload(fold) for fold in folds],
            "fold_count": len(folds),
            "actual_history_start": actual_history_start.isoformat(),
            "actual_history_end": actual_history_end.isoformat(),
            "purge_sessions": purge_sessions,
            "embargo_sessions": embargo_sessions,
        }
        serialized = canonical_json_bytes(payload)
        return cls(
            fold_manifest_id=sha256_id(serialized),
            folds=folds,
            fold_count=len(folds),
            actual_history_start=actual_history_start,
            actual_history_end=actual_history_end,
            purge_sessions=purge_sessions,
            embargo_sessions=embargo_sessions,
            serialized=serialized,
        )

    @classmethod
    def from_serialized(cls, fold_manifest_id: str, serialized: bytes) -> FoldManifest:
        try:
            payload = json.loads(serialized)
        except (TypeError, ValueError) as error:
            raise ValueError("fold_manifest_schema_invalid") from error
        expected = {
            "artifact_kind",
            "schema_version",
            "folds",
            "fold_count",
            "actual_history_start",
            "actual_history_end",
            "purge_sessions",
            "embargo_sessions",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or payload["artifact_kind"] != "walk_forward_fold_manifest"
            or payload["schema_version"] != "walk-forward-fold-manifest/v1"
            or not isinstance(payload["folds"], list)
            or isinstance(payload["fold_count"], bool)
            or not isinstance(payload["fold_count"], int)
            or payload["fold_count"] != len(payload["folds"])
            or not isinstance(payload["actual_history_start"], str)
            or not isinstance(payload["actual_history_end"], str)
            or isinstance(payload["purge_sessions"], bool)
            or not isinstance(payload["purge_sessions"], int)
            or isinstance(payload["embargo_sessions"], bool)
            or not isinstance(payload["embargo_sessions"], int)
        ):
            raise ValueError("fold_manifest_schema_invalid")
        try:
            folds = tuple(cls._fold_from_payload(item) for item in payload["folds"])
            manifest = cls.create(
                folds=folds,
                actual_history_start=date.fromisoformat(payload["actual_history_start"]),
                actual_history_end=date.fromisoformat(payload["actual_history_end"]),
                purge_sessions=payload["purge_sessions"],
                embargo_sessions=payload["embargo_sessions"],
            )
        except (TypeError, ValueError) as error:
            raise ValueError("fold_manifest_schema_invalid") from error
        if manifest.fold_manifest_id != fold_manifest_id or manifest.serialized != serialized:
            raise ValueError("fold_manifest_checksum_mismatch")
        return manifest

    def is_content_addressed(self) -> bool:
        try:
            return self.from_serialized(self.fold_manifest_id, self.serialized) == self
        except ValueError:
            return False

    @staticmethod
    def _fold_payload(fold: WalkForwardFold) -> dict[str, object]:
        if (
            fold.market not in {"XTAI", "XNAS"}
            or not isinstance(fold.test_quarter, str)
            or not fold.test_quarter
            or any(not isinstance(item, str) or not item for item in fold.training_row_ids)
            or any(not isinstance(item, str) or not item for item in fold.validation_row_ids)
            or any(not isinstance(item, str) or not item for item in fold.test_row_ids)
            or any(not isinstance(item, date) for item in fold.purge_session_dates)
            or any(not isinstance(item, date) for item in fold.embargo_session_dates)
        ):
            raise ValueError("fold_manifest_schema_invalid")
        return {
            "market": fold.market,
            "test_quarter": fold.test_quarter,
            "training_row_ids": fold.training_row_ids,
            "validation_row_ids": fold.validation_row_ids,
            "test_row_ids": fold.test_row_ids,
            "purge_session_dates": [item.isoformat() for item in fold.purge_session_dates],
            "embargo_session_dates": [item.isoformat() for item in fold.embargo_session_dates],
        }

    @staticmethod
    def _fold_from_payload(raw: object) -> WalkForwardFold:
        expected = {
            "market",
            "test_quarter",
            "training_row_ids",
            "validation_row_ids",
            "test_row_ids",
            "purge_session_dates",
            "embargo_session_dates",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != expected
            or raw["market"] not in {"XTAI", "XNAS"}
            or not isinstance(raw["test_quarter"], str)
            or any(
                not isinstance(raw[field], list)
                or any(not isinstance(item, str) or not item for item in raw[field])
                for field in (
                    "training_row_ids",
                    "validation_row_ids",
                    "test_row_ids",
                    "purge_session_dates",
                    "embargo_session_dates",
                )
            )
        ):
            raise ValueError("fold_manifest_schema_invalid")
        return WalkForwardFold(
            market=cast(Market, raw["market"]),
            test_quarter=raw["test_quarter"],
            training_row_ids=tuple(raw["training_row_ids"]),
            validation_row_ids=tuple(raw["validation_row_ids"]),
            test_row_ids=tuple(raw["test_row_ids"]),
            purge_session_dates=tuple(
                date.fromisoformat(item) for item in raw["purge_session_dates"]
            ),
            embargo_session_dates=tuple(
                date.fromisoformat(item) for item in raw["embargo_session_dates"]
            ),
        )


@dataclass(frozen=True)
class FormalQualificationEvidence:
    qualification_evidence_id: str
    training_intent_id: str
    feature_batch_id: str
    source_policy_manifest_id: str
    label_manifest_id: str
    fold_manifest_id: str
    cost_manifest_id: str
    historical_claims: tuple[HistoricalClaimRef, ...]
    feature_rows_digests: tuple[tuple[Market, str], ...]
    serialized: bytes

    @classmethod
    def create(
        cls,
        intent: TrainingIntentRef,
        fold_manifest: FoldManifest,
    ) -> FormalQualificationEvidence:
        claims = tuple(sorted(intent.historical_claims, key=lambda item: item.market))
        markets: tuple[Market, Market] = ("XTAI", "XNAS")
        digests = tuple(
            (market, intent.feature_batch.market_rows_digest(market)) for market in markets
        )
        payload = {
            "artifact_kind": "formal_candidate_qualification",
            "schema_version": "formal-candidate-qualification/v1",
            "training_intent_id": intent.training_intent_id,
            "feature_batch_id": intent.feature_batch.feature_batch_id,
            "source_policy_manifest_id": intent.feature_batch.source_policy_manifest_id,
            "label_manifest_id": intent.feature_batch.label_manifest_id,
            "fold_manifest_id": fold_manifest.fold_manifest_id,
            "cost_manifest_id": intent.feature_batch.cost_manifest_id,
            "historical_claims": [
                {"market": item.market, "claim_id": item.claim_id} for item in claims
            ],
            "feature_rows_digests": [
                {"market": market, "digest": digest} for market, digest in digests
            ],
        }
        serialized = canonical_json_bytes(payload)
        return cls(
            qualification_evidence_id=sha256_id(serialized),
            training_intent_id=intent.training_intent_id,
            feature_batch_id=intent.feature_batch.feature_batch_id,
            source_policy_manifest_id=intent.feature_batch.source_policy_manifest_id,
            label_manifest_id=intent.feature_batch.label_manifest_id,
            fold_manifest_id=fold_manifest.fold_manifest_id,
            cost_manifest_id=intent.feature_batch.cost_manifest_id,
            historical_claims=claims,
            feature_rows_digests=digests,
            serialized=serialized,
        )

    @classmethod
    def from_serialized(
        cls,
        qualification_evidence_id: str,
        serialized: bytes,
    ) -> FormalQualificationEvidence:
        try:
            payload = json.loads(serialized)
        except (TypeError, ValueError) as error:
            raise ValueError("formal_qualification_schema_invalid") from error
        expected = {
            "artifact_kind",
            "schema_version",
            "training_intent_id",
            "feature_batch_id",
            "source_policy_manifest_id",
            "label_manifest_id",
            "fold_manifest_id",
            "cost_manifest_id",
            "historical_claims",
            "feature_rows_digests",
        }
        string_fields = (
            "training_intent_id",
            "feature_batch_id",
            "source_policy_manifest_id",
            "label_manifest_id",
            "fold_manifest_id",
            "cost_manifest_id",
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or payload["artifact_kind"] != "formal_candidate_qualification"
            or payload["schema_version"] != "formal-candidate-qualification/v1"
            or any(
                not isinstance(payload[field], str) or not payload[field] for field in string_fields
            )
            or not isinstance(payload["historical_claims"], list)
            or not isinstance(payload["feature_rows_digests"], list)
        ):
            raise ValueError("formal_qualification_schema_invalid")
        try:
            claims = tuple(
                HistoricalClaimRef(market=cast(Market, item["market"]), claim_id=item["claim_id"])
                for item in payload["historical_claims"]
                if isinstance(item, dict)
                and set(item) == {"market", "claim_id"}
                and item["market"] in {"XTAI", "XNAS"}
                and isinstance(item["claim_id"], str)
                and item["claim_id"]
            )
            digests = tuple(
                (cast(Market, item["market"]), item["digest"])
                for item in payload["feature_rows_digests"]
                if isinstance(item, dict)
                and set(item) == {"market", "digest"}
                and item["market"] in {"XTAI", "XNAS"}
                and isinstance(item["digest"], str)
                and item["digest"]
            )
        except (KeyError, TypeError) as error:
            raise ValueError("formal_qualification_schema_invalid") from error
        if len(claims) != len(payload["historical_claims"]) or len(digests) != len(
            payload["feature_rows_digests"]
        ):
            raise ValueError("formal_qualification_schema_invalid")
        evidence = cls(
            qualification_evidence_id=qualification_evidence_id,
            training_intent_id=payload["training_intent_id"],
            feature_batch_id=payload["feature_batch_id"],
            source_policy_manifest_id=payload["source_policy_manifest_id"],
            label_manifest_id=payload["label_manifest_id"],
            fold_manifest_id=payload["fold_manifest_id"],
            cost_manifest_id=payload["cost_manifest_id"],
            historical_claims=claims,
            feature_rows_digests=digests,
            serialized=serialized,
        )
        if sha256_id(serialized) != qualification_evidence_id:
            raise ValueError("formal_qualification_checksum_mismatch")
        return evidence

    def is_content_addressed(self) -> bool:
        try:
            return self.from_serialized(self.qualification_evidence_id, self.serialized) == self
        except ValueError:
            return False

    def binds(self, intent: TrainingIntentRef, fold_manifest: FoldManifest) -> bool:
        expected = self.create(intent, fold_manifest)
        return expected == self


class FormalCandidateQualificationVerifier:
    def __init__(
        self,
        historical_claim_verifier: FormalHistoricalClaimVerifier,
        cost_scenario_verifier: FormalCostScenarioVerifier,
    ) -> None:
        self._historical_claim_verifier = historical_claim_verifier
        self._cost_scenario_verifier = cost_scenario_verifier

    def verify_source_basis(self, intent: TrainingIntentRef) -> bool:
        if intent.execution_purpose != "formal_candidate":
            return False
        if len(intent.historical_claims) != 2 or {
            item.market for item in intent.historical_claims
        } != {"XTAI", "XNAS"}:
            return False
        lineages = intent.feature_batch.historical_lineage
        if len(lineages) != 2 or {item.market for item in lineages} != {"XTAI", "XNAS"}:
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

    def verify_cost_scenario(self, intent: TrainingIntentRef) -> bool:
        if intent.execution_purpose != "formal_candidate":
            return False
        try:
            return self._cost_scenario_verifier.verify_cost_scenario(
                intent.feature_batch.cost_manifest_id
            )
        except (KeyError, RuntimeError, ValueError):
            return False

    def verify(
        self,
        evidence: FormalQualificationEvidence,
        intent: TrainingIntentRef,
        fold_manifest: FoldManifest,
    ) -> bool:
        return (
            evidence.is_content_addressed()
            and evidence.binds(intent, fold_manifest)
            and fold_manifest.is_content_addressed()
            and self.verify_source_basis(intent)
            and self.verify_cost_scenario(intent)
        )


@dataclass(frozen=True)
class CandidateEvidenceBundle:
    candidate_id: str
    training_intent: TrainingIntentRef
    primary_artifact: ModelArtifact
    logistic_artifacts: tuple[ModelArtifact, ...]
    class_prior_artifacts: tuple[ModelArtifact, ...]
    fold_manifest: FoldManifest
    calibrators: tuple[CalibrationEvidence, ...]
    evaluation_report: EvaluationReport
    qualification_evidence: FormalQualificationEvidence | None

    @property
    def formal_qualification(self) -> bool:
        return self.qualification_evidence is not None

    @property
    def model_family_id(self) -> str:
        return self.training_intent.model_family_id

    @property
    def training_intent_id(self) -> str:
        return self.training_intent.training_intent_id

    def content_id(self) -> str:
        return _content_id(
            "candidate",
            {
                "model_family_id": self.model_family_id,
                "training_intent_id": self.training_intent_id,
                "primary_artifact_id": self.primary_artifact.artifact_id,
                "logistic_artifact_ids": [
                    artifact.artifact_id for artifact in self.logistic_artifacts
                ],
                "class_prior_artifact_ids": [
                    artifact.artifact_id for artifact in self.class_prior_artifacts
                ],
                "fold_manifest_id": self.fold_manifest.fold_manifest_id,
                "fold_count": self.fold_manifest.fold_count,
                "calibrator_ids": [item.calibrator_id for item in self.calibrators],
                "calibrator_statuses": [item.status for item in self.calibrators],
                "evaluation_report_id": self.evaluation_report.evaluation_report_id,
                "qualification_evidence_id": (
                    self.qualification_evidence.qualification_evidence_id
                    if self.qualification_evidence is not None
                    else None
                ),
            },
        )

    def with_content_id(self) -> CandidateEvidenceBundle:
        return replace(self, candidate_id=self.content_id())

    def is_content_addressed(self) -> bool:
        return self.candidate_id == self.content_id()


@dataclass(frozen=True)
class _EvaluationScores:
    class_prior_equal_cell_macro_f1: float
    logistic_equal_cell_macro_f1: float
    seed_macro_f1: tuple[float, float, float]


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
        self.qualification_verifier = FormalCandidateQualificationVerifier(
            self._historical_claim_verifier,
            self._cost_scenario_verifier,
        )
        self._class_prior_forecaster = class_prior_forecaster or ClassPriorTrendForecaster()
        self._logistic_forecaster = (
            logistic_forecaster or RegularizedMultinomialLogisticTrendForecaster()
        )

    def preregister(self, draft: TrainingIntentRef) -> TrainingIntentRef:
        fold_manifest = self._build_fold_manifest(draft.feature_batch.rows)
        if fold_manifest is None:
            raise ValueError("insufficient_fold_history")
        final_lineages = tuple(
            replace(lineage, fold_manifest_id=fold_manifest.fold_manifest_id)
            for lineage in draft.feature_batch.historical_lineage
        )
        feature_batch = replace(
            draft.feature_batch,
            fold_manifest_id=fold_manifest.fold_manifest_id,
            historical_lineage=final_lineages,
        ).with_content_id()
        return replace(draft, feature_batch=feature_batch).with_content_id()

    def develop(self, intent: TrainingIntentRef) -> ForecastLabOutcome:
        seeds = intent.preregistered_seeds
        if (
            not isinstance(seeds, tuple)
            or len(seeds) != 3
            or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
            or len(set(seeds)) != 3
        ):
            return self._blocked("invalid_preregistered_seeds")
        if not isinstance(intent.provenance, ArtifactProvenance):
            return self._blocked("training_intent_provenance_invalid")
        if not self._has_class_support(intent.feature_batch.rows):
            return self._blocked("insufficient_class_support")
        fold_manifest = self._build_fold_manifest(intent.feature_batch.rows)
        if fold_manifest is None:
            return self._blocked("insufficient_fold_history")
        if (
            intent.execution_purpose == "formal_candidate"
            and not self._has_statistical_fold_support(fold_manifest)
        ):
            return self._blocked("insufficient_statistical_support")
        if (
            fold_manifest.fold_manifest_id != intent.feature_batch.fold_manifest_id
            or not intent.feature_batch.is_content_addressed()
            or not intent.is_content_addressed()
        ):
            return self._blocked("training_intent_mismatch")
        feature_batch = intent.feature_batch
        formal_source_basis = self._has_formal_source_basis(intent)
        if intent.execution_purpose == "formal_candidate" and not formal_source_basis:
            return self._blocked("unverified_source_basis")
        formal_cost_scenario = self._has_formal_cost_scenario(intent)
        if intent.execution_purpose == "formal_candidate" and not formal_cost_scenario:
            return self._blocked("unverified_cost_scenario")
        qualification_evidence = (
            FormalQualificationEvidence.create(intent, fold_manifest)
            if formal_source_basis and formal_cost_scenario
            else None
        )
        try:
            evaluation_scores = self._evaluate(
                feature_batch,
                fold_manifest,
                intent.preregistered_seeds,
                provenance=intent.provenance,
            )
            training_ids, validation_ids = self._latest_joint_split(fold_manifest)
            logistic_artifacts = tuple(
                self._logistic_forecaster.train(
                    TrainingRequest(
                        feature_batch=feature_batch,
                        training_row_ids=training_ids,
                        validation_row_ids=validation_ids,
                        seed=seed,
                        provenance=intent.provenance,
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
                        provenance=intent.provenance,
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
        evaluation = EvaluationReport.create(
            class_prior_equal_cell_macro_f1=(evaluation_scores.class_prior_equal_cell_macro_f1),
            logistic_equal_cell_macro_f1=evaluation_scores.logistic_equal_cell_macro_f1,
            seed_results=tuple(
                SeedArtifactEvaluation(
                    seed=seed,
                    logistic_artifact_id=logistic.artifact_id,
                    class_prior_artifact_id=prior.artifact_id,
                    logistic_macro_f1=score,
                )
                for seed, logistic, prior, score in zip(
                    intent.preregistered_seeds,
                    logistic_artifacts,
                    prior_artifacts,
                    evaluation_scores.seed_macro_f1,
                    strict=True,
                )
            ),
            feature_batch_id=feature_batch.feature_batch_id,
            source_policy_manifest_id=feature_batch.source_policy_manifest_id,
            label_manifest_id=feature_batch.label_manifest_id,
            cost_manifest_id=feature_batch.cost_manifest_id,
            fold_manifest_id=fold_manifest.fold_manifest_id,
        )
        candidate_bundle = CandidateEvidenceBundle(
            candidate_id="",
            training_intent=intent,
            primary_artifact=logistic_artifacts[0],
            logistic_artifacts=logistic_artifacts,
            class_prior_artifacts=prior_artifacts,
            fold_manifest=fold_manifest,
            calibrators=calibrators,
            evaluation_report=evaluation,
            qualification_evidence=qualification_evidence,
        ).with_content_id()
        return ForecastLabOutcome(
            status="developed",
            blocked_reasons=(),
            candidate_bundle=candidate_bundle,
        )

    def _has_formal_source_basis(self, intent: TrainingIntentRef) -> bool:
        return self.qualification_verifier.verify_source_basis(intent)

    def _has_formal_cost_scenario(self, intent: TrainingIntentRef) -> bool:
        return self.qualification_verifier.verify_cost_scenario(intent)

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

    def _has_statistical_fold_support(self, fold_manifest: FoldManifest) -> bool:
        quarters_by_market = {
            market: {fold.test_quarter for fold in fold_manifest.folds if fold.market == market}
            for market in self._markets
        }
        joint_quarters = set.intersection(*(quarters_by_market[market] for market in self._markets))
        return len(joint_quarters) >= BOOTSTRAP_MINIMUM_NONINFERIOR_QUARTERS

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
        return FoldManifest.create(
            folds=tuple(folds),
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
        *,
        provenance: ArtifactProvenance,
    ) -> _EvaluationScores:
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
                TrainingRequest(
                    batch,
                    training_ids,
                    validation_ids,
                    seeds[0],
                    provenance,
                )
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
                    TrainingRequest(
                        batch,
                        training_ids,
                        validation_ids,
                        seed,
                        provenance,
                    )
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
        return _EvaluationScores(
            class_prior_equal_cell_macro_f1=prior_score,
            logistic_equal_cell_macro_f1=logistic_score,
            seed_macro_f1=cast(tuple[float, float, float], seed_scores),
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
