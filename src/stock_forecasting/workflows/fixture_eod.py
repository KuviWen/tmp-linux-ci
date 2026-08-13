from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from math import log
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from stock_forecasting.contracts import PredictionPayload, ProbabilityVector
from stock_forecasting.platform.object_repository import FilesystemObjectRepository, ObjectRef
from stock_forecasting.platform.state_store import StateStore


def _fixture_id(kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"stock-forecasting/fixture/xtai/{kind}"))


def _sessions_ending_on(end: date, count: int) -> list[str]:
    sessions: list[str] = []
    candidate = end
    while len(sessions) < count:
        if candidate.weekday() < 5:
            sessions.append(f"XTAI:{candidate.isoformat()}")
        candidate -= timedelta(days=1)
    sessions.reverse()
    return sessions


def _confidence(probabilities: ProbabilityVector) -> float:
    values = (probabilities["up"], probabilities["flat"], probabilities["down"])
    entropy = -sum(value * log(value) for value in values)
    return round(1 - (entropy / log(3)), 6)


@dataclass(frozen=True)
class FixtureEodCommand:
    information_cutoff: datetime
    trace_id: str
    idempotency_key: str
    fixture_scenario: Literal["normal", "missing_anchor_price"] = "normal"


@dataclass(frozen=True)
class FixtureEodOutcome:
    status: str
    execution_purpose: str
    issuer_id: str
    security_id: str
    listing_id: str
    display_ticker: str
    calendar_version_id: str
    adjustment_version_id: str
    source_policy_version_id: str
    data_selection_id: str
    dataset_version_id: str
    feature_snapshot_id: str
    model_artifact_id: str
    serving_assignment_id: str
    raw_object_ref: ObjectRef
    work_id: str


class FixtureEodWorkflow:
    def __init__(
        self,
        state_store: StateStore,
        *,
        observed_at: datetime,
        object_repository: FilesystemObjectRepository,
    ) -> None:
        self._state_store = state_store
        self._observed_at = observed_at
        self._object_repository = object_repository

    def execute(self, command: FixtureEodCommand) -> FixtureEodOutcome:
        issuer_id = _fixture_id("issuer")
        security_id = _fixture_id("security")
        listing_id = _fixture_id("listing")
        calendar_version_id = _fixture_id("calendar-version-1")
        adjustment_version_id = _fixture_id("adjustment-version-1")
        source_policy_version_id = _fixture_id("source-policy-version-1")
        identity_assertion_id = _fixture_id("identity-assertion-ticker-2330-v1")
        raw_artifact_id = _fixture_id("raw-artifact-v1")
        source_record_version_id = _fixture_id("source-record-version-v1")
        normalized_record_version_id = _fixture_id("normalized-record-version-v1")
        retrieval_receipt_id = _fixture_id(f"retrieval-receipt/{command.idempotency_key}")
        coverage_report_id = _fixture_id(f"coverage-report/{command.idempotency_key}")
        company_action_version_id = _fixture_id("company-action-version-v1")
        cutoff_key = command.information_cutoff.isoformat()
        data_selection_id = _fixture_id(f"data-selection/{cutoff_key}")
        dataset_version_id = _fixture_id("dataset-version-1")
        feature_snapshot_id = _fixture_id(f"feature-snapshot/{cutoff_key}")
        model_artifact_id = _fixture_id("fixture-trend-forecaster-artifact-v1")
        serving_assignment_id = _fixture_id("fixture-serving-assignment-v1")
        work_id = _fixture_id(f"work/{command.idempotency_key}")
        sessions = _sessions_ending_on(command.information_cutoff.date(), 253)
        raw_content = json.dumps(
            {
                "exchange": "XTAI",
                "listing_id": listing_id,
                "session_count": len(sessions),
                "sessions": sessions,
                "price_kind": "unadjusted",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        raw_checksum = hashlib.sha256(raw_content).hexdigest()
        raw_object_ref = self._object_repository.put_verified(
            BytesIO(raw_content),
            expected_checksum=raw_checksum,
            metadata={
                "media_type": "application/json",
                "source": "xtai-fixture",
            },
        )
        probability_rows: dict[int, ProbabilityVector] = {
            1: {"up": 0.62, "flat": 0.23, "down": 0.15},
            5: {"up": 0.55, "flat": 0.28, "down": 0.17},
            20: {"up": 0.43, "flat": 0.35, "down": 0.22},
        }
        predictions: list[PredictionPayload]
        if command.fixture_scenario == "missing_anchor_price":
            predictions = [
                {
                    "horizon_sessions": horizon,
                    "prediction_status": "unavailable",
                    "unavailable_reason": {"code": "missing_anchor_price"},
                    "data_support": {"price_volume": "unavailable"},
                }
                for horizon in probability_rows
            ]
        else:
            predictions = [
                {
                    "horizon_sessions": horizon,
                    "probabilities": probabilities,
                    "confidence_score": _confidence(probabilities),
                    "prediction_status": "full",
                    "data_support": {"price_volume": "full"},
                }
                for horizon, probabilities in probability_rows.items()
            ]

        information_cutoff = command.information_cutoff.isoformat().replace("+00:00", "Z")
        calendar = {
            "exchange": "XTAI",
            "timezone": "Asia/Taipei",
            "version_id": calendar_version_id,
            "session_count": len(sessions),
        }
        company_action = {
            "kind": "cash_dividend",
            "effective_session_id": "XTAI:2026-06-15",
            "cash_amount": "5.00",
            "currency": "TWD",
        }
        coverage = {
            "status": "completed",
            "expected_partitions": 1,
            "received_partitions": 1,
            "missing_partitions": [],
            "session_count": len(sessions),
        }
        research_record = {
            "identity": {
                "issuer_id": issuer_id,
                "security_id": security_id,
                "listing_id": listing_id,
                "display_ticker": "2330",
                "ticker_valid_from": "2025-08-13",
                "ticker_valid_to": None,
            },
            "calendar": calendar,
            "company_actions": [company_action],
            "adjustment_version_id": adjustment_version_id,
            "execution_purpose": "fixture",
            "fixture_badge": "Fixture／非正式預測",
            "information_cutoff": information_cutoff,
            "source_evidence": {
                "raw_artifact_id": raw_artifact_id,
                "raw_object_checksum": raw_object_ref.checksum,
                "source_record_version_id": source_record_version_id,
                "normalized_record_version_id": normalized_record_version_id,
                "retrieval_receipt_id": retrieval_receipt_id,
                "coverage_report_id": coverage_report_id,
                "first_observed_at": self._observed_at.isoformat().replace("+00:00", "Z"),
                "source_policy": {
                    "version_id": source_policy_version_id,
                    "execution_purpose": "fixture",
                    "content_origin": "synthetic",
                    "formal_source_qualified": False,
                },
                "coverage": coverage,
                "committed_checkpoint": "xtai-fixture-page:1",
                "scenario_kinds": [
                    "normal",
                    "late",
                    "duplicate",
                    "correction",
                    "missing",
                    "withdrawal",
                ],
            },
            "lineage": {
                "data_selection_id": data_selection_id,
                "dataset_version_id": dataset_version_id,
                "feature_snapshot_id": feature_snapshot_id,
                "model_artifact_id": model_artifact_id,
                "serving_assignment_id": serving_assignment_id,
                "raw_artifact_id": raw_artifact_id,
            },
            "predictions": predictions,
            "observed_at": self._observed_at.isoformat().replace("+00:00", "Z"),
        }
        self._state_store.publish_fixture_trace(
            record_id=_fixture_id(f"research-record/{command.idempotency_key}"),
            payload=research_record,
            work_id=work_id,
            trace_id=command.trace_id,
            idempotency_key=command.idempotency_key,
            health_assessment_id=_fixture_id(f"source-health/{command.idempotency_key}"),
            audit_event_id=_fixture_id(f"audit/{command.idempotency_key}"),
            artifacts=[
                {"artifact_id": issuer_id, "artifact_kind": "issuer", "payload": {}},
                {
                    "artifact_id": security_id,
                    "artifact_kind": "security",
                    "payload": {"issuer_id": issuer_id},
                },
                {
                    "artifact_id": listing_id,
                    "artifact_kind": "listing",
                    "payload": {"security_id": security_id, "exchange": "XTAI"},
                },
                {
                    "artifact_id": identity_assertion_id,
                    "artifact_kind": "identity_assertion",
                    "payload": {"listing_id": listing_id, "ticker": "2330"},
                },
                {
                    "artifact_id": source_policy_version_id,
                    "artifact_kind": "source_policy_version",
                    "payload": {"content_origin": "synthetic"},
                },
                {
                    "artifact_id": raw_artifact_id,
                    "artifact_kind": "raw_artifact",
                    "payload": {"checksum": raw_object_ref.checksum},
                },
                {
                    "artifact_id": source_record_version_id,
                    "artifact_kind": "source_record_version",
                    "payload": {"raw_artifact_id": raw_artifact_id},
                },
                {
                    "artifact_id": normalized_record_version_id,
                    "artifact_kind": "normalized_record_version",
                    "payload": {"source_record_version_id": source_record_version_id},
                },
                {
                    "artifact_id": retrieval_receipt_id,
                    "artifact_kind": "retrieval_receipt",
                    "payload": {
                        "first_observed_at": self._observed_at.isoformat().replace("+00:00", "Z")
                    },
                },
                {
                    "artifact_id": coverage_report_id,
                    "artifact_kind": "coverage_report",
                    "payload": coverage,
                },
                {
                    "artifact_id": calendar_version_id,
                    "artifact_kind": "calendar_version",
                    "payload": calendar,
                },
                {
                    "artifact_id": company_action_version_id,
                    "artifact_kind": "company_action_version",
                    "payload": company_action,
                },
                {
                    "artifact_id": adjustment_version_id,
                    "artifact_kind": "adjustment_version",
                    "payload": {"company_action_version_id": company_action_version_id},
                },
                {
                    "artifact_id": dataset_version_id,
                    "artifact_kind": "dataset_version",
                    "payload": {"coverage_report_id": coverage_report_id},
                },
                {
                    "artifact_id": data_selection_id,
                    "artifact_kind": "data_selection",
                    "payload": {
                        "dataset_version_id": dataset_version_id,
                        "information_cutoff": information_cutoff,
                    },
                },
                {
                    "artifact_id": feature_snapshot_id,
                    "artifact_kind": "feature_snapshot",
                    "payload": {"data_selection_id": data_selection_id},
                },
                {
                    "artifact_id": model_artifact_id,
                    "artifact_kind": "model_artifact",
                    "payload": {"kind": "FixtureTrendForecaster", "promotable": False},
                },
                {
                    "artifact_id": serving_assignment_id,
                    "artifact_kind": "serving_assignment",
                    "payload": {
                        "model_artifact_id": model_artifact_id,
                        "execution_purpose": "fixture",
                    },
                },
            ],
            fixture_predictions=[
                {
                    "prediction_id": _fixture_id(
                        f"fixture-prediction/{command.idempotency_key}/{prediction['horizon_sessions']}"
                    ),
                    "horizon_sessions": prediction["horizon_sessions"],
                    "payload": prediction,
                }
                for prediction in predictions
            ],
        )

        return FixtureEodOutcome(
            status="succeeded",
            execution_purpose="fixture",
            issuer_id=issuer_id,
            security_id=security_id,
            listing_id=listing_id,
            display_ticker="2330",
            calendar_version_id=calendar_version_id,
            adjustment_version_id=adjustment_version_id,
            source_policy_version_id=source_policy_version_id,
            data_selection_id=data_selection_id,
            dataset_version_id=dataset_version_id,
            feature_snapshot_id=feature_snapshot_id,
            model_artifact_id=model_artifact_id,
            serving_assignment_id=serving_assignment_id,
            raw_object_ref=raw_object_ref,
            work_id=work_id,
        )
