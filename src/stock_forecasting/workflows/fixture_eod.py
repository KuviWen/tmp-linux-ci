from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from stock_forecasting.fixture_dataset import (
    CALENDAR_CLOSURES,
    CALENDAR_REVISION_ID,
    XtaiFixtureDataset,
)
from stock_forecasting.forecasting import FeatureSnapshot, FixtureTrendForecaster, TrendForecaster
from stock_forecasting.platform.object_repository import FilesystemObjectRepository, ObjectRef
from stock_forecasting.platform.state_store import StateStore

FixtureScenario = Literal[
    "normal",
    "late",
    "duplicate",
    "correction",
    "missing",
    "withdrawal",
]


def _fixture_id(kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"stock-forecasting/fixture/xtai/{kind}"))


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _version_id(kind: str, payload: object) -> str:
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return _fixture_id(f"{kind}/{digest}")


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FixtureEodCommand:
    information_cutoff: datetime
    trace_id: str
    idempotency_key: str
    fixture_scenario: FixtureScenario = "normal"


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
        observed_at: datetime | None,
        object_repository: FilesystemObjectRepository,
        fixture_dataset: XtaiFixtureDataset | None = None,
        forecaster: TrendForecaster | None = None,
    ) -> None:
        self._state_store = state_store
        self._observed_at = observed_at
        self._object_repository = object_repository
        self._fixture_dataset = fixture_dataset or XtaiFixtureDataset.load()
        self._forecaster = forecaster or FixtureTrendForecaster()

    def execute(self, command: FixtureEodCommand) -> FixtureEodOutcome:
        observed_at = self._observed_at or datetime.now(UTC)
        issuer_id = _fixture_id("issuer")
        security_id = _fixture_id("security")
        listing_id = _fixture_id("listing")
        identity_assertion_id = _fixture_id("identity-assertion-ticker-2330-v1")
        cutoff_text = _instant(command.information_cutoff)

        selection = self._fixture_dataset.select(command.information_cutoff)
        base_records: list[dict[str, object]] = [dict(record) for record in selection.records]
        raw_records = [dict(record) for record in base_records]
        if command.fixture_scenario == "correction":
            raw_records[-1]["close"] = "607.50"
        elif command.fixture_scenario == "missing":
            raw_records[-1].pop("close")
        raw_payload = {
            "exchange": "XTAI",
            "listing_id": listing_id,
            "session_count": len(selection.sessions),
            "records": raw_records,
            "price_kind": "unadjusted",
        }
        raw_content = _canonical_bytes(raw_payload)
        raw_checksum = hashlib.sha256(raw_content).hexdigest()
        raw_object_ref = self._object_repository.put_verified(
            BytesIO(raw_content),
            expected_checksum=raw_checksum,
            metadata={
                "media_type": "application/json",
                "source": "xtai-fixture",
            },
        )

        identity_payload = {
            "issuer_id": issuer_id,
            "security_id": security_id,
            "listing_id": listing_id,
            "display_ticker": "2330",
            "ticker_valid_from": "2025-08-13",
            "ticker_valid_to": None,
        }
        source_policy_payload = {
            "execution_purpose": "fixture",
            "content_origin": "synthetic",
            "formal_source_qualified": False,
        }
        source_policy_version_id = _version_id("source-policy-version", source_policy_payload)
        raw_artifact_payload = {
            "checksum": raw_object_ref.checksum,
            "object_id": raw_object_ref.object_id,
            "price_kind": "unadjusted",
        }
        raw_artifact_id = _version_id("raw-artifact", raw_artifact_payload)
        base_raw_payload = {
            "exchange": "XTAI",
            "listing_id": listing_id,
            "session_count": len(selection.sessions),
            "records": base_records,
            "price_kind": "unadjusted",
        }
        base_raw_checksum = hashlib.sha256(_canonical_bytes(base_raw_payload)).hexdigest()
        base_raw_artifact_id = _version_id(
            "raw-artifact",
            {
                "checksum": base_raw_checksum,
                "object_id": f"sha256:{base_raw_checksum}",
                "price_kind": "unadjusted",
            },
        )
        base_source_record_payload = {
            "raw_artifact_id": base_raw_artifact_id,
            "source_policy_version_id": source_policy_version_id,
            "record_count": len(selection.sessions),
        }
        base_source_record_version_id = _version_id(
            "source-record-version", base_source_record_payload
        )
        source_record_payload = {
            "raw_artifact_id": raw_artifact_id,
            "source_policy_version_id": source_policy_version_id,
            "record_count": len(selection.sessions),
        }
        if command.fixture_scenario == "correction":
            source_record_payload.update(
                {
                    "supersedes": base_source_record_version_id,
                    "revision_number": 2,
                }
            )
        elif command.fixture_scenario == "withdrawal":
            source_record_payload.update(
                {
                    "withdraws": base_source_record_version_id,
                    "availability": "withdrawn",
                }
            )
        source_record_version_id = _version_id("source-record-version", source_record_payload)
        if command.fixture_scenario == "duplicate":
            source_record_version_id = base_source_record_version_id
            source_record_payload = base_source_record_payload
        normalized_record_payload = {
            "source_record_version_id": source_record_version_id,
            "schema_version": "xtai-eod-normalized-v1",
            "record_count": len(selection.sessions),
        }
        normalized_record_version_id = _version_id(
            "normalized-record-version", normalized_record_payload
        )
        retrieval_receipt_payload = {
            "source_record_version_id": source_record_version_id,
            "first_observed_at": _instant(observed_at),
            "idempotency_key": command.idempotency_key,
            "fixture_scenario": command.fixture_scenario,
        }
        retrieval_receipt_id = _version_id("retrieval-receipt", retrieval_receipt_payload)
        coverage_status = (
            "withdrawn"
            if command.fixture_scenario == "withdrawal"
            else "incomplete"
            if command.fixture_scenario == "missing"
            else "completed"
        )
        coverage_payload = {
            "status": coverage_status,
            "expected_partitions": 1,
            "received_partitions": 1,
            "missing_partitions": (
                ["anchor_close"] if command.fixture_scenario == "missing" else []
            ),
            "session_count": len(selection.sessions),
            "first_session_id": selection.session_ids[0],
            "last_session_id": selection.session_ids[-1],
        }
        coverage_report_id = _version_id("coverage-report", coverage_payload)

        calendar_payload = self._fixture_dataset.calendar_payload()
        calendar_version_id = _version_id("calendar-version", calendar_payload)
        company_action_payload = {
            "kind": "cash_dividend",
            "effective_session_id": "XTAI:2026-06-15",
            "cash_amount": "5.00",
            "currency": "TWD",
        }
        company_action_version_id = _version_id("company-action-version", company_action_payload)
        adjusted_records = [
            {
                "session_id": record["session_id"],
                "adjusted_close": (
                    str(
                        (
                            Decimal(str(record["close"])) - Decimal("5.00")
                            if str(record["session_id"])
                            < str(company_action_payload["effective_session_id"])
                            else Decimal(str(record["close"]))
                        ).quantize(Decimal("0.01"))
                    )
                ),
            }
            for record in raw_records
            if "close" in record
        ]
        unavailable_reason: (
            Literal[
                "missing_anchor_price",
                "post_cutoff_evidence",
                "source_withdrawn",
            ]
            | None
        ) = None
        if observed_at > command.information_cutoff:
            unavailable_reason = "post_cutoff_evidence"
        elif command.fixture_scenario == "withdrawal":
            unavailable_reason = "source_withdrawn"
        elif command.fixture_scenario == "missing":
            unavailable_reason = "missing_anchor_price"
        adjustment_payload = {
            "input_raw_artifact_id": raw_artifact_id,
            "company_action_version_id": company_action_version_id,
            "method": "fixture_cash_dividend_back_adjustment_v1",
            "input_price_kind": "unadjusted",
            "output_price_kind": "adjusted",
            "adjusted_records": adjusted_records,
            "status": "unavailable" if unavailable_reason else "completed",
        }
        adjustment_version_id = _version_id("adjustment-version", adjustment_payload)

        dataset_payload = {
            "normalized_record_version_id": normalized_record_version_id,
            "coverage_report_id": coverage_report_id,
            "calendar_version_id": calendar_version_id,
            "adjustment_version_id": adjustment_version_id,
        }
        dataset_version_id = _version_id("dataset-version", dataset_payload)
        data_selection_payload = {
            "dataset_version_id": dataset_version_id,
            "information_cutoff": cutoff_text,
            "selected_session_ids": selection.session_ids if unavailable_reason is None else [],
            "excluded_source_record_version_ids": (
                [] if unavailable_reason is None else [source_record_version_id]
            ),
            "exclusion_reason": unavailable_reason,
            "fixture_scenario": command.fixture_scenario,
        }
        data_selection_id = _version_id("data-selection", data_selection_payload)

        if unavailable_reason is None:
            adjusted_closes = [
                Decimal(str(record["adjusted_close"])) for record in adjusted_records
            ]
            volumes = [Decimal(int(str(record["volume"]))) for record in raw_records]
            anchor = adjusted_closes[-1]
            feature_values = {
                "adjusted_return_1": round(float(anchor / adjusted_closes[-2] - 1), 6),
                "adjusted_return_5": round(float(anchor / adjusted_closes[-6] - 1), 6),
                "adjusted_return_20": round(float(anchor / adjusted_closes[-21] - 1), 6),
                "volume_ratio_20": round(
                    float(volumes[-1] / (sum(volumes[-20:]) / Decimal(20))),
                    6,
                ),
            }
            feature_snapshot_payload: dict[str, object] = {
                "data_selection_id": data_selection_id,
                "feature_schema_version": "fixture-price-volume-features-v1",
                "anchor_session_id": selection.session_ids[-1],
                "status": "full",
                "values": feature_values,
            }
        else:
            feature_snapshot_payload = {
                "data_selection_id": data_selection_id,
                "feature_schema_version": "fixture-price-volume-features-v1",
                "status": "unavailable",
                "unavailable_reason": unavailable_reason,
            }
        feature_snapshot_id = _version_id("feature-snapshot", feature_snapshot_payload)
        feature_snapshot = FeatureSnapshot(
            feature_snapshot_id=feature_snapshot_id,
            data_selection_id=data_selection_id,
            status="unavailable" if unavailable_reason else "full",
            values=None if unavailable_reason else feature_values,
            unavailable_reason=unavailable_reason,
        )
        model_artifact_payload = {
            "kind": "FixtureTrendForecaster",
            "artifact_version": "fixture-trend-forecaster-v1",
            "promotable": False,
        }
        model_artifact_id = _version_id("model-artifact", model_artifact_payload)
        serving_assignment_payload = {
            "model_artifact_id": model_artifact_id,
            "execution_purpose": "fixture",
            "assignment_version": "fixture-serving-assignment-v1",
        }
        serving_assignment_id = _version_id("serving-assignment", serving_assignment_payload)

        predictions = self._forecaster.predict(feature_snapshot)
        calendar_projection = {
            "exchange": "XTAI",
            "timezone": "Asia/Taipei",
            "version_id": calendar_version_id,
            "session_count": len(selection.sessions),
            "session_fact_count": self._fixture_dataset.session_fact_count,
            "closure_dates": list(CALENDAR_CLOSURES),
            "half_day_session_ids": [
                session.session_id
                for session in selection.sessions
                if session.session_kind == "half_day"
            ],
            "revision_ids": [CALENDAR_REVISION_ID],
        }
        adjustment_projection = {
            "version_id": adjustment_version_id,
            "input_price_kind": "unadjusted",
            "output_price_kind": "adjusted",
            "session_count": len(adjusted_records),
            "company_action_count": 1,
        }
        research_record = {
            "identity": identity_payload,
            "calendar": calendar_projection,
            "company_actions": [company_action_payload],
            "adjustment_version_id": adjustment_version_id,
            "adjustment": adjustment_projection,
            "execution_purpose": "fixture",
            "fixture_badge": "Fixture／非正式預測",
            "information_cutoff": cutoff_text,
            "source_evidence": {
                "fixture_scenario": command.fixture_scenario,
                "raw_artifact_id": raw_artifact_id,
                "raw_object_checksum": raw_object_ref.checksum,
                "source_record_version_id": source_record_version_id,
                "normalized_record_version_id": normalized_record_version_id,
                "retrieval_receipt_id": retrieval_receipt_id,
                "coverage_report_id": coverage_report_id,
                "first_observed_at": _instant(observed_at),
                "source_policy": {
                    "version_id": source_policy_version_id,
                    **source_policy_payload,
                },
                "coverage": coverage_payload,
                "committed_checkpoint": "xtai-fixture-page:1",
                **(
                    {
                        "duplicate_of": base_source_record_version_id,
                        "deduplicated": True,
                    }
                    if command.fixture_scenario == "duplicate"
                    else {}
                ),
                **(
                    {
                        "supersedes": base_source_record_version_id,
                        "revision_number": 2,
                    }
                    if command.fixture_scenario == "correction"
                    else {}
                ),
                **(
                    {"withdraws": base_source_record_version_id}
                    if command.fixture_scenario == "withdrawal"
                    else {}
                ),
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
            "observed_at": _instant(observed_at),
        }
        artifacts: list[dict[str, Any]] = [
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
                "payload": source_policy_payload,
            },
            {
                "artifact_id": raw_artifact_id,
                "artifact_kind": "raw_artifact",
                "payload": raw_artifact_payload,
            },
            {
                "artifact_id": source_record_version_id,
                "artifact_kind": "source_record_version",
                "payload": source_record_payload,
            },
            {
                "artifact_id": normalized_record_version_id,
                "artifact_kind": "normalized_record_version",
                "payload": normalized_record_payload,
            },
            {
                "artifact_id": retrieval_receipt_id,
                "artifact_kind": "retrieval_receipt",
                "payload": retrieval_receipt_payload,
            },
            {
                "artifact_id": coverage_report_id,
                "artifact_kind": "coverage_report",
                "payload": coverage_payload,
            },
            {
                "artifact_id": calendar_version_id,
                "artifact_kind": "calendar_version",
                "payload": calendar_payload,
            },
            {
                "artifact_id": company_action_version_id,
                "artifact_kind": "company_action_version",
                "payload": company_action_payload,
            },
            {
                "artifact_id": adjustment_version_id,
                "artifact_kind": "adjustment_version",
                "payload": adjustment_payload,
            },
            {
                "artifact_id": dataset_version_id,
                "artifact_kind": "dataset_version",
                "payload": dataset_payload,
            },
            {
                "artifact_id": data_selection_id,
                "artifact_kind": "data_selection",
                "payload": data_selection_payload,
            },
            {
                "artifact_id": feature_snapshot_id,
                "artifact_kind": "feature_snapshot",
                "payload": feature_snapshot_payload,
            },
            {
                "artifact_id": model_artifact_id,
                "artifact_kind": "model_artifact",
                "payload": model_artifact_payload,
            },
            {
                "artifact_id": serving_assignment_id,
                "artifact_kind": "serving_assignment",
                "payload": serving_assignment_payload,
            },
        ]
        work_id = _fixture_id(f"work/{command.idempotency_key}")
        self._state_store.publish_fixture_trace(
            record_id=_fixture_id(f"research-record/{command.idempotency_key}"),
            payload=research_record,
            work_id=work_id,
            trace_id=command.trace_id,
            idempotency_key=command.idempotency_key,
            health_assessment_id=_fixture_id(f"source-health/{command.idempotency_key}"),
            audit_event_id=_fixture_id(f"audit/{command.idempotency_key}"),
            artifacts=artifacts,
            fixture_predictions=[
                {
                    "prediction_id": _version_id(
                        "fixture-prediction",
                        {
                            "idempotency_key": command.idempotency_key,
                            "payload": prediction,
                        },
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
