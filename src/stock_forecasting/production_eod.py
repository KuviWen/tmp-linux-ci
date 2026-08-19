from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite, log
from typing import Any, Literal, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from stock_forecasting.content_address import content_id
from stock_forecasting.contracts import ProbabilityVector
from stock_forecasting.forecasting import (
    FeatureRow,
    ModelArtifact,
    PredictionRequest,
    RegularizedMultinomialLogisticTrendForecaster,
)
from stock_forecasting.model_governance import (
    PinServingAssignment,
    ServingAssignmentResolver,
)

Market = Literal["XTAI", "XNAS"]
Horizon = Literal[1, 5, 20]


def _stable_id(kind: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"stock-forecasting/{kind}/{value}"))


@dataclass(frozen=True)
class ProductionListingInput:
    listing_id: str
    display_ticker: str
    market: Market
    dataset_version_id: str
    calendar_version_id: str
    anchor_session_id: str
    target_session_ids: tuple[tuple[Horizon, str], ...]
    evidence_level: str
    first_observed_at: datetime
    processed_at: datetime
    feature_values: tuple[float, ...]
    support_status: Literal["full", "degraded", "unavailable"]
    unavailable_reason: str | None


@dataclass(frozen=True)
class ResolvedProductionDataSelection:
    data_selection_id: str
    market: Market
    information_cutoff: datetime
    stock_pool_version_id: str
    source_policy_manifest_id: str
    source_policy_status: str
    listings: tuple[ProductionListingInput, ...]

    @classmethod
    def create(
        cls,
        *,
        market: Market,
        information_cutoff: datetime,
        stock_pool_version_id: str,
        source_policy_manifest_id: str,
        source_policy_status: str,
        listings: tuple[ProductionListingInput, ...],
    ) -> ResolvedProductionDataSelection:
        if information_cutoff.tzinfo is None:
            raise ValueError("information_cutoff_timezone_required")
        payload = {
            "market": market,
            "information_cutoff": information_cutoff.isoformat(),
            "stock_pool_version_id": stock_pool_version_id,
            "source_policy_manifest_id": source_policy_manifest_id,
            "source_policy_status": source_policy_status,
            "listings": [
                {
                    "listing_id": item.listing_id,
                    "display_ticker": item.display_ticker,
                    "market": item.market,
                    "dataset_version_id": item.dataset_version_id,
                    "calendar_version_id": item.calendar_version_id,
                    "anchor_session_id": item.anchor_session_id,
                    "target_session_ids": item.target_session_ids,
                    "evidence_level": item.evidence_level,
                    "first_observed_at": item.first_observed_at.isoformat(),
                    "processed_at": item.processed_at.isoformat(),
                    "feature_values": item.feature_values,
                    "support_status": item.support_status,
                    "unavailable_reason": item.unavailable_reason,
                }
                for item in listings
            ],
        }
        return cls(
            data_selection_id=content_id("production_data_selection", payload),
            market=market,
            information_cutoff=information_cutoff,
            stock_pool_version_id=stock_pool_version_id,
            source_policy_manifest_id=source_policy_manifest_id,
            source_policy_status=source_policy_status,
            listings=listings,
        )


@dataclass(frozen=True)
class ProductionDataSelectionRequest:
    market: Market
    information_cutoff: datetime
    stock_pool_version_id: str


class ProductionDataSelectionResolver(Protocol):
    def resolve(
        self,
        request: ProductionDataSelectionRequest,
    ) -> ResolvedProductionDataSelection: ...


class UnavailableProductionDataSelectionResolver:
    def resolve(
        self,
        request: ProductionDataSelectionRequest,
    ) -> ResolvedProductionDataSelection:
        del request
        raise ValueError("production_data_selection_unavailable")


class ProductionArtifactRepository(Protocol):
    def resolve(self, artifact_id: str) -> bytes | None: ...


@dataclass(frozen=True)
class ForecastRunCommand:
    market: Market
    information_cutoff: datetime
    stock_pool_version_id: str
    model_family_id: str
    execution_purpose: str
    idempotency_key: str
    trace_id: str


@dataclass(frozen=True)
class ProductionPrediction:
    prediction_id: str
    listing_id: str
    display_ticker: str
    market: Market
    horizon_sessions: Horizon
    anchor_session_id: str
    target_session_id: str
    status: Literal["full", "degraded", "unavailable"]
    probabilities: ProbabilityVector | None
    confidence_score: float | None
    unavailable_reason: str | None
    data_selection_id: str
    dataset_version_id: str
    stock_pool_version_id: str
    feature_snapshot_id: str
    model_artifact_id: str
    calibrator_ids: tuple[str, ...]
    serving_assignment_id: str
    calendar_version_id: str
    source_policy_manifest_id: str
    execution_purpose: Literal["production"]


@dataclass(frozen=True)
class ProjectionState:
    core_projection_version: int
    evidence_projection_version: int
    stale: bool


@dataclass(frozen=True)
class WorkflowMilestone:
    event_kind: str
    due_at: datetime
    observed_at: datetime
    status: Literal["met", "missed"]


@dataclass(frozen=True)
class ForecastPublication:
    forecast_batch_id: str
    status: Literal["completed"]
    execution_purpose: Literal["production"]
    information_cutoff: datetime
    data_selection_id: str
    feature_snapshot_id: str
    feature_snapshot_listing_ids: tuple[str, ...]
    serving_assignment_id: str
    predictions: tuple[ProductionPrediction, ...]
    projection: ProjectionState
    outbox_event_id: str
    outbox_delivery_status: Literal["pending", "delivered"]
    completed_at: datetime
    slo_breached: bool
    milestones: tuple[WorkflowMilestone, ...]


class ProductionPublicationStore(Protocol):
    def publish(
        self,
        publication: ForecastPublication,
        *,
        trace_id: str,
        idempotency_key: str,
    ) -> ForecastPublication: ...


class ProductionStateStore(Protocol):
    def publish_production_trace(
        self,
        *,
        publication: dict[str, Any],
        research_record_payloads: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        predictions: list[dict[str, Any]],
        trace_id: str,
        idempotency_key: str,
    ) -> int: ...


class InMemoryProductionPublicationStore:
    def __init__(self) -> None:
        self._batches: dict[str, ForecastPublication] = {}

    def publish(
        self,
        publication: ForecastPublication,
        *,
        trace_id: str,
        idempotency_key: str,
    ) -> ForecastPublication:
        del trace_id, idempotency_key
        self._validate(publication)
        existing = self._batches.get(publication.forecast_batch_id)
        if existing is not None:
            if existing.predictions != publication.predictions:
                raise ValueError("immutable_production_batch_conflict")
            return existing
        committed = replace(
            publication,
            projection=ProjectionState(
                core_projection_version=1,
                evidence_projection_version=0,
                stale=True,
            ),
            outbox_delivery_status="pending",
        )
        self._batches[publication.forecast_batch_id] = committed
        return committed

    def get_batch(self, forecast_batch_id: str) -> ForecastPublication | None:
        return self._batches.get(forecast_batch_id)

    @staticmethod
    def _validate(publication: ForecastPublication) -> None:
        listing_ids = {item.listing_id for item in publication.predictions}
        if len(listing_ids) != 10 or len(publication.predictions) != 30:
            raise ValueError("production_result_or_reason_incomplete")
        for listing_id in listing_ids:
            horizons = {
                item.horizon_sessions
                for item in publication.predictions
                if item.listing_id == listing_id
            }
            if horizons != {1, 5, 20}:
                raise ValueError("production_result_or_reason_incomplete")
        for item in publication.predictions:
            if (
                item.execution_purpose != "production"
                or item.data_selection_id != publication.data_selection_id
                or item.feature_snapshot_id != publication.feature_snapshot_id
                or item.serving_assignment_id != publication.serving_assignment_id
            ):
                raise ValueError("production_prediction_lineage_invalid")
            if item.status == "unavailable":
                if (
                    item.probabilities is not None
                    or item.confidence_score is not None
                    or not item.unavailable_reason
                ):
                    raise ValueError("production_unavailable_contract_invalid")
                continue
            if (
                item.probabilities is None
                or item.confidence_score is None
                or item.unavailable_reason is not None
                or set(item.probabilities) != {"up", "flat", "down"}
            ):
                raise ValueError("production_probability_contract_invalid")
            values = (
                item.probabilities["up"],
                item.probabilities["flat"],
                item.probabilities["down"],
            )
            if (
                any(not isfinite(value) or value < 0.0 or value > 1.0 for value in values)
                or abs(sum(values) - 1.0) > 1e-9
                or not isfinite(item.confidence_score)
                or not 0.0 <= item.confidence_score <= 1.0
            ):
                raise ValueError("production_probability_invalid")


class SqlAlchemyProductionPublicationStore:
    def __init__(self, state_store: ProductionStateStore) -> None:
        self._state_store = state_store

    def publish(
        self,
        publication: ForecastPublication,
        *,
        trace_id: str,
        idempotency_key: str,
    ) -> ForecastPublication:
        InMemoryProductionPublicationStore._validate(publication)
        predictions = [self._prediction_payload(item) for item in publication.predictions]
        records = [
            self._research_record(publication, listing_id, predictions)
            for listing_id in sorted({item.listing_id for item in publication.predictions})
        ]
        first_prediction = publication.predictions[0]
        dataset_ids = sorted({item.dataset_version_id for item in publication.predictions})
        artifacts: list[dict[str, Any]] = [
            {
                "artifact_id": publication.data_selection_id,
                "artifact_kind": "data_selection",
                "payload": {
                    "data_selection_id": publication.data_selection_id,
                    "information_cutoff": publication.information_cutoff.isoformat(),
                    "stock_pool_version_id": first_prediction.stock_pool_version_id,
                    "source_policy_manifest_id": first_prediction.source_policy_manifest_id,
                },
            },
            {
                "artifact_id": publication.feature_snapshot_id,
                "artifact_kind": "feature_snapshot",
                "payload": {
                    "feature_snapshot_id": publication.feature_snapshot_id,
                    "data_selection_id": publication.data_selection_id,
                    "listing_ids": list(publication.feature_snapshot_listing_ids),
                },
            },
            {
                "artifact_id": first_prediction.model_artifact_id,
                "artifact_kind": "model_artifact",
                "payload": {"model_artifact_id": first_prediction.model_artifact_id},
            },
            {
                "artifact_id": publication.serving_assignment_id,
                "artifact_kind": "serving_assignment",
                "payload": {
                    "serving_assignment_id": publication.serving_assignment_id,
                    "model_artifact_id": first_prediction.model_artifact_id,
                },
            },
            *[
                {
                    "artifact_id": dataset_id,
                    "artifact_kind": "dataset_version",
                    "payload": {"dataset_version_id": dataset_id},
                }
                for dataset_id in dataset_ids
            ],
        ]
        core_version = self._state_store.publish_production_trace(
            publication={
                "forecast_batch_id": publication.forecast_batch_id,
                "information_cutoff": publication.information_cutoff.isoformat(),
                "outbox_event_id": publication.outbox_event_id,
                "market": first_prediction.market,
                "serving_assignment_id": publication.serving_assignment_id,
                "completed_at": publication.completed_at.isoformat(),
                "slo_breached": publication.slo_breached,
                "milestones": [
                    {
                        "event_kind": item.event_kind,
                        "due_at": item.due_at.isoformat(),
                        "observed_at": item.observed_at.isoformat(),
                        "status": item.status,
                    }
                    for item in publication.milestones
                ],
            },
            research_record_payloads=records,
            artifacts=artifacts,
            predictions=predictions,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
        return replace(
            publication,
            projection=ProjectionState(core_version, 0, True),
            outbox_delivery_status="pending",
        )

    @staticmethod
    def _prediction_payload(item: ProductionPrediction) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prediction_id": item.prediction_id,
            "listing_id": item.listing_id,
            "display_ticker": item.display_ticker,
            "market": item.market,
            "horizon_sessions": item.horizon_sessions,
            "anchor_session_id": item.anchor_session_id,
            "target_session_id": item.target_session_id,
            "prediction_status": item.status,
            "data_support": {"price_volume": item.status},
            "lineage": {
                "data_selection_id": item.data_selection_id,
                "dataset_version_id": item.dataset_version_id,
                "stock_pool_version_id": item.stock_pool_version_id,
                "feature_snapshot_id": item.feature_snapshot_id,
                "model_artifact_id": item.model_artifact_id,
                "serving_assignment_id": item.serving_assignment_id,
                "calendar_version_id": item.calendar_version_id,
                "source_policy_manifest_id": item.source_policy_manifest_id,
            },
            "calibration": {
                "model_artifact_id": item.model_artifact_id,
                "calibrator_ids": list(item.calibrator_ids),
            },
        }
        if item.status == "unavailable":
            payload["unavailable_reason"] = {"code": item.unavailable_reason}
        else:
            payload["probabilities"] = item.probabilities
            payload["confidence_score"] = item.confidence_score
        return payload

    @staticmethod
    def _research_record(
        publication: ForecastPublication,
        listing_id: str,
        predictions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        listing_predictions = [item for item in predictions if item["listing_id"] == listing_id]
        first = listing_predictions[0]
        lineage = cast(dict[str, Any], first["lineage"])
        formal_cutoff = publication.information_cutoff.isoformat().replace("+00:00", "Z")
        return {
            "record_id": _stable_id(
                "production-research-record",
                f"{publication.forecast_batch_id}:{listing_id}",
            ),
            "listing_id": listing_id,
            "information_cutoff": formal_cutoff,
            "execution_purpose": "production",
            "identity": {
                "listing_id": listing_id,
                "display_ticker": first["display_ticker"],
            },
            "calendar": {
                "exchange": first["market"],
                "calendar_version_id": lineage["calendar_version_id"],
                "anchor_session_id": first["anchor_session_id"],
            },
            "formal_cutoff": formal_cutoff,
            "predictions": listing_predictions,
            "lineage": lineage,
            "calibration": first["calibration"],
            "support": {"price_volume": first["data_support"]["price_volume"]},
            "allowed_evidence": [],
        }


class ForecastExecution:
    def __init__(
        self,
        *,
        assignment_resolver: ServingAssignmentResolver,
        data_selection_resolver: ProductionDataSelectionResolver,
        artifact_repository: ProductionArtifactRepository,
        publication_store: ProductionPublicationStore | None = None,
        clock: Callable[[], datetime],
    ) -> None:
        self._assignment_resolver = assignment_resolver
        self._data_selection_resolver = data_selection_resolver
        self._artifact_repository = artifact_repository
        self._publication_store = publication_store or InMemoryProductionPublicationStore()
        self._clock = clock

    def run(self, command: ForecastRunCommand) -> ForecastPublication:
        if command.execution_purpose != "production":
            raise ValueError("production_execution_purpose_required")
        started_at = self._observe_clock(not_before=command.information_cutoff)
        forecast_batch_id = _stable_id(
            "production-forecast-batch",
            (
                f"{command.market}:{command.information_cutoff.isoformat()}:"
                f"{command.stock_pool_version_id}:{command.model_family_id}:"
                f"{command.idempotency_key}"
            ),
        )
        selection = self._data_selection_resolver.resolve(
            ProductionDataSelectionRequest(
                market=command.market,
                information_cutoff=command.information_cutoff,
                stock_pool_version_id=command.stock_pool_version_id,
            )
        )
        self._validate_selection(command, selection)
        pin = self._assignment_resolver.pin(
            PinServingAssignment(
                model_family_id=command.model_family_id,
                forecast_batch_id=forecast_batch_id,
                market=command.market,
                information_cutoff=command.information_cutoff,
                started_at=started_at,
            )
        )
        serialized = self._artifact_repository.resolve(pin.assignment.artifact_id)
        if serialized is None:
            raise ValueError("production_artifact_unavailable")
        artifact = ModelArtifact.from_serialized(pin.assignment.artifact_id, serialized)
        forecaster = RegularizedMultinomialLogisticTrendForecaster.load(serialized)
        try:
            artifact_payload = cast(dict[str, Any], json.loads(serialized))
            normalizers = cast(dict[str, dict[str, Any]], artifact_payload["normalizers"])
            feature_counts = {
                market: len(cast(list[object], normalizer["medians"]))
                for market, normalizer in normalizers.items()
            }
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("production_artifact_schema_invalid") from error
        reasons = {
            item.listing_id: (
                "source_policy_withdrawn"
                if selection.source_policy_status != "active"
                else "source_policy_assignment_mismatch"
                if selection.source_policy_manifest_id != artifact.manifest_ids[1]
                else "feature_schema_mismatch"
                if len(item.feature_values) != feature_counts.get(item.market)
                else self._unavailable_reason(item, command.information_cutoff)
            )
            for item in selection.listings
        }
        included_listings = tuple(
            item for item in selection.listings if reasons[item.listing_id] is None
        )
        feature_snapshot_id = content_id(
            "production_feature_snapshot",
            {
                "data_selection_id": selection.data_selection_id,
                "rows": [
                    {"listing_id": item.listing_id, "values": item.feature_values}
                    for item in included_listings
                ],
            },
        )
        feature_completed_at = self._observe_clock(not_before=started_at)
        rows: list[FeatureRow] = []
        for listing in included_listings:
            for horizon, _target_session_id in listing.target_session_ids:
                row_id = f"{listing.listing_id}:{horizon}"
                rows.append(
                    FeatureRow(
                        row_id=row_id,
                        market=listing.market,
                        horizon_sessions=horizon,
                        values=listing.feature_values,
                        label=None,
                    )
                )
        batch = forecaster.predict(PredictionRequest(artifact=artifact, rows=tuple(rows)))
        validation_completed_at = self._observe_clock(not_before=feature_completed_at)
        available_by_row = {item.row_id: item.probabilities for item in batch.predictions}
        predictions: list[ProductionPrediction] = []
        for listing in selection.listings:
            for horizon, target_session_id in listing.target_session_ids:
                lineage = (listing, horizon, target_session_id)
                reason = reasons[listing.listing_id]
                if reason is not None:
                    predictions.append(
                        self._unavailable_prediction(
                            forecast_batch_id=forecast_batch_id,
                            row_lineage=lineage,
                            unavailable_reason=reason,
                            selection=selection,
                            feature_snapshot_id=feature_snapshot_id,
                            artifact=artifact,
                            serving_assignment_id=pin.assignment.assignment_id,
                        )
                    )
                    continue
                predictions.append(
                    self._publication_prediction(
                        forecast_batch_id=forecast_batch_id,
                        row_lineage=lineage,
                        probabilities=available_by_row[f"{listing.listing_id}:{horizon}"],
                        selection=selection,
                        feature_snapshot_id=feature_snapshot_id,
                        artifact=artifact,
                        serving_assignment_id=pin.assignment.assignment_id,
                    )
                )
        completed_at = self._observe_clock(not_before=validation_completed_at)
        milestones = self._milestones(
            information_cutoff=command.information_cutoff,
            readiness_at=started_at,
            feature_at=feature_completed_at,
            validation_at=validation_completed_at,
            publication_at=completed_at,
        )
        publication = ForecastPublication(
            forecast_batch_id=forecast_batch_id,
            status="completed",
            execution_purpose="production",
            information_cutoff=command.information_cutoff,
            data_selection_id=selection.data_selection_id,
            feature_snapshot_id=feature_snapshot_id,
            feature_snapshot_listing_ids=tuple(item.listing_id for item in included_listings),
            serving_assignment_id=pin.assignment.assignment_id,
            predictions=tuple(predictions),
            projection=ProjectionState(0, 0, True),
            outbox_event_id=_stable_id("production-outbox", forecast_batch_id),
            outbox_delivery_status="pending",
            completed_at=completed_at,
            slo_breached=milestones[-1].status == "missed",
            milestones=milestones,
        )
        return self._publication_store.publish(
            publication,
            trace_id=command.trace_id,
            idempotency_key=command.idempotency_key,
        )

    def _observe_clock(self, *, not_before: datetime) -> datetime:
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at < not_before:
            raise ValueError("production_clock_skew_detected")
        return observed_at

    @staticmethod
    def _validate_selection(
        command: ForecastRunCommand,
        selection: ResolvedProductionDataSelection,
    ) -> None:
        listing_ids = {item.listing_id for item in selection.listings}
        if (
            selection.market != command.market
            or selection.information_cutoff != command.information_cutoff
            or selection.stock_pool_version_id != command.stock_pool_version_id
            or len(selection.listings) != 10
            or len(listing_ids) != 10
            or any(item.market != command.market for item in selection.listings)
            or any(
                [horizon for horizon, _target in item.target_session_ids] != [1, 5, 20]
                for item in selection.listings
            )
        ):
            raise ValueError("production_data_selection_invalid")

    @staticmethod
    def _milestones(
        *,
        information_cutoff: datetime,
        readiness_at: datetime,
        feature_at: datetime,
        validation_at: datetime,
        publication_at: datetime,
    ) -> tuple[WorkflowMilestone, ...]:
        schedule = (
            ("t_plus_90_readiness", information_cutoff, readiness_at),
            (
                "t_plus_105_feature_freeze",
                information_cutoff + timedelta(minutes=15),
                feature_at,
            ),
            (
                "t_plus_115_forecast_validation",
                information_cutoff + timedelta(minutes=25),
                validation_at,
            ),
            (
                "t_plus_120_publication",
                information_cutoff + timedelta(minutes=30),
                publication_at,
            ),
        )
        return tuple(
            WorkflowMilestone(
                event_kind=event_kind,
                due_at=due_at,
                observed_at=observed_at,
                status="met" if observed_at <= due_at else "missed",
            )
            for event_kind, due_at, observed_at in schedule
        )

    @staticmethod
    def _unavailable_reason(
        listing: ProductionListingInput,
        information_cutoff: datetime,
    ) -> str | None:
        if listing.first_observed_at > information_cutoff:
            return "late_after_information_cutoff"
        if listing.processed_at > information_cutoff + timedelta(minutes=15):
            return "late_after_feature_freeze"
        if listing.evidence_level != "platform_observed":
            return "evidence_not_platform_observed"
        if listing.support_status == "unavailable":
            return listing.unavailable_reason or "data_support_unavailable"
        return None

    @staticmethod
    def _unavailable_prediction(
        *,
        forecast_batch_id: str,
        row_lineage: tuple[ProductionListingInput, Horizon, str],
        unavailable_reason: str,
        selection: ResolvedProductionDataSelection,
        feature_snapshot_id: str,
        artifact: ModelArtifact,
        serving_assignment_id: str,
    ) -> ProductionPrediction:
        listing, horizon, target_session_id = row_lineage
        prediction_id = _stable_id(
            "production-prediction",
            f"{forecast_batch_id}:{listing.listing_id}:{horizon}",
        )
        return ProductionPrediction(
            prediction_id=prediction_id,
            listing_id=listing.listing_id,
            display_ticker=listing.display_ticker,
            market=listing.market,
            horizon_sessions=horizon,
            anchor_session_id=listing.anchor_session_id,
            target_session_id=target_session_id,
            status="unavailable",
            probabilities=None,
            confidence_score=None,
            unavailable_reason=unavailable_reason,
            data_selection_id=selection.data_selection_id,
            dataset_version_id=listing.dataset_version_id,
            stock_pool_version_id=selection.stock_pool_version_id,
            feature_snapshot_id=feature_snapshot_id,
            model_artifact_id=artifact.artifact_id,
            calibrator_ids=artifact.calibrator_ids,
            serving_assignment_id=serving_assignment_id,
            calendar_version_id=listing.calendar_version_id,
            source_policy_manifest_id=selection.source_policy_manifest_id,
            execution_purpose="production",
        )

    @staticmethod
    def _publication_prediction(
        *,
        forecast_batch_id: str,
        row_lineage: tuple[ProductionListingInput, Horizon, str],
        probabilities: ProbabilityVector,
        selection: ResolvedProductionDataSelection,
        feature_snapshot_id: str,
        artifact: ModelArtifact,
        serving_assignment_id: str,
    ) -> ProductionPrediction:
        listing, horizon, target_session_id = row_lineage
        probability_values = cast(tuple[float, float, float], tuple(probabilities.values()))
        entropy = -sum(value * log(value) for value in probability_values if value > 0.0)
        prediction_id = _stable_id(
            "production-prediction",
            f"{forecast_batch_id}:{listing.listing_id}:{horizon}",
        )
        return ProductionPrediction(
            prediction_id=prediction_id,
            listing_id=listing.listing_id,
            display_ticker=listing.display_ticker,
            market=listing.market,
            horizon_sessions=horizon,
            anchor_session_id=listing.anchor_session_id,
            target_session_id=target_session_id,
            status=listing.support_status,
            probabilities=probabilities,
            confidence_score=1.0 - entropy / log(3.0),
            unavailable_reason=None,
            data_selection_id=selection.data_selection_id,
            dataset_version_id=listing.dataset_version_id,
            stock_pool_version_id=selection.stock_pool_version_id,
            feature_snapshot_id=feature_snapshot_id,
            model_artifact_id=artifact.artifact_id,
            calibrator_ids=artifact.calibrator_ids,
            serving_assignment_id=serving_assignment_id,
            calendar_version_id=listing.calendar_version_id,
            source_policy_manifest_id=selection.source_policy_manifest_id,
            execution_purpose="production",
        )
