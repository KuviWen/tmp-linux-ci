from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from stock_forecasting.fixture_market import (
    FixtureMarket,
    FixtureMarketAdapter,
    default_fixture_market_adapters,
)
from stock_forecasting.fixture_scenarios import FixtureScenario, scenario_policy
from stock_forecasting.forecasting import FeatureSnapshot, FixtureTrendForecaster, TrendForecaster
from stock_forecasting.identity import ListingIdentity, TickerAssertion
from stock_forecasting.platform.object_repository import FilesystemObjectRepository, ObjectRef
from stock_forecasting.platform.state_store import StateStore


def _fixture_id(namespace: str, kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"stock-forecasting/fixture/{namespace}/{kind}"))


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _version_id(namespace: str, kind: str, payload: object) -> str:
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return _fixture_id(namespace, f"{kind}/{digest}")


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FixtureEodCommand:
    information_cutoff: datetime
    trace_id: str
    idempotency_key: str
    market: FixtureMarket = "XTAI"
    fixture_scenario: FixtureScenario = "normal"


@dataclass(frozen=True)
class FixtureEodOutcome:
    status: str
    execution_purpose: str
    market: FixtureMarket
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
        forecaster: TrendForecaster | None = None,
        market_adapters: Mapping[FixtureMarket, FixtureMarketAdapter] | None = None,
    ) -> None:
        self._state_store = state_store
        self._observed_at = observed_at
        self._object_repository = object_repository
        self._market_adapters = dict(market_adapters or default_fixture_market_adapters())
        self._forecaster = forecaster or FixtureTrendForecaster()

    def execute(self, command: FixtureEodCommand) -> FixtureEodOutcome:
        observed_at = self._observed_at or datetime.now(UTC)
        policy = scenario_policy(command.fixture_scenario)
        market_batch = self._market_adapters[command.market].load(command.information_cutoff)
        namespace = market_batch.namespace

        def fixture_id(kind: str) -> str:
            return _fixture_id(namespace, kind)

        def version_id(kind: str, payload: object) -> str:
            return _version_id(namespace, kind, payload)

        issuer_id = fixture_id("issuer")
        security_id = fixture_id("security")
        listing_id = fixture_id("listing")
        cutoff_text = _instant(command.information_cutoff)
        identity_timeline = ListingIdentity(
            listing_id=listing_id,
            ticker_assertions=tuple(
                TickerAssertion(
                    listing_id=listing_id,
                    ticker=assertion.ticker,
                    valid_from=assertion.valid_from,
                    valid_to=assertion.valid_to,
                )
                for assertion in market_batch.ticker_assertions
            ),
        )
        display_ticker = identity_timeline.ticker_at(command.information_cutoff.date())

        selection = market_batch.selection
        base_records: list[dict[str, object]] = [dict(record) for record in selection.records]
        raw_records = policy.mutate_records(base_records)
        raw_payload = {
            "exchange": market_batch.market,
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
                "source": market_batch.source_name,
            },
        )

        identity_payload = {
            "issuer_id": issuer_id,
            "security_id": security_id,
            "listing_id": listing_id,
            "display_ticker": display_ticker,
            "ticker_valid_from": next(
                assertion.valid_from.isoformat()
                for assertion in market_batch.ticker_assertions
                if assertion.ticker == display_ticker
            ),
            "ticker_valid_to": next(
                assertion.valid_to.isoformat() if assertion.valid_to else None
                for assertion in market_batch.ticker_assertions
                if assertion.ticker == display_ticker
            ),
            "ticker_assertions": identity_timeline.assertions_payload(),
        }
        source_policy_payload = {
            "execution_purpose": "fixture",
            "content_origin": "synthetic",
            "formal_source_qualified": False,
        }
        source_policy_version_id = version_id("source-policy-version", source_policy_payload)
        raw_artifact_payload = {
            "checksum": raw_object_ref.checksum,
            "object_id": raw_object_ref.object_id,
            "price_kind": "unadjusted",
        }
        raw_artifact_id = version_id("raw-artifact", raw_artifact_payload)
        base_raw_payload = {
            "exchange": market_batch.market,
            "listing_id": listing_id,
            "session_count": len(selection.sessions),
            "records": base_records,
            "price_kind": "unadjusted",
        }
        base_raw_content = _canonical_bytes(base_raw_payload)
        base_raw_checksum = hashlib.sha256(base_raw_content).hexdigest()
        if policy.related_base_required:
            self._object_repository.put_verified(
                BytesIO(base_raw_content),
                expected_checksum=base_raw_checksum,
                metadata={
                    "media_type": "application/json",
                    "source": market_batch.source_name,
                },
            )
        base_raw_artifact_payload = {
            "checksum": base_raw_checksum,
            "object_id": f"sha256:{base_raw_checksum}",
            "price_kind": "unadjusted",
        }
        base_raw_artifact_id = version_id(
            "raw-artifact",
            base_raw_artifact_payload,
        )
        base_source_record_payload = {
            "raw_artifact_id": base_raw_artifact_id,
            "source_policy_version_id": source_policy_version_id,
            "record_count": len(selection.sessions),
        }
        base_source_record_version_id = version_id(
            "source-record-version", base_source_record_payload
        )
        current_source_record_payload = {
            "raw_artifact_id": raw_artifact_id,
            "source_policy_version_id": source_policy_version_id,
            "record_count": len(selection.sessions),
        }
        source_record_payload = policy.source_record_payload(
            current_payload=current_source_record_payload,
            base_payload=base_source_record_payload,
            base_version_id=base_source_record_version_id,
        )
        source_record_version_id = version_id("source-record-version", source_record_payload)
        normalized_record_payload = {
            "source_record_version_id": source_record_version_id,
            "schema_version": market_batch.normalized_schema_version,
            "record_count": len(selection.sessions),
        }
        normalized_record_version_id = version_id(
            "normalized-record-version", normalized_record_payload
        )
        retrieval_receipt_payload = {
            "source_record_version_id": source_record_version_id,
            "first_observed_at": _instant(observed_at),
            "idempotency_key": command.idempotency_key,
            "fixture_scenario": command.fixture_scenario,
        }
        retrieval_receipt_id = version_id("retrieval-receipt", retrieval_receipt_payload)
        coverage_payload = {
            "status": policy.coverage_status,
            "expected_partitions": 1,
            "received_partitions": 1,
            "missing_partitions": list(policy.missing_partitions),
            "session_count": len(selection.sessions),
            "first_session_id": selection.session_ids[0],
            "last_session_id": selection.session_ids[-1],
        }
        coverage_report_id = version_id("coverage-report", coverage_payload)

        calendar_payload = market_batch.calendar_payload
        calendar_version_id = version_id("calendar-version", calendar_payload)
        company_action_payload = market_batch.company_action_payload
        company_action_available = command.fixture_scenario != "missing_company_action"
        company_action_version_id = (
            version_id("company-action-version", company_action_payload)
            if company_action_available
            else None
        )
        adjusted_records = (
            market_batch.adjustment_rule.apply(raw_records) if company_action_available else []
        )
        unavailable_reason = policy.unavailable_reason(
            observed_at=observed_at,
            information_cutoff=command.information_cutoff,
        )
        adjustment_payload = {
            "input_raw_artifact_id": raw_artifact_id,
            "company_action_version_id": company_action_version_id,
            "method": market_batch.adjustment_rule.method,
            "input_price_kind": "unadjusted",
            "output_price_kind": "adjusted",
            "adjusted_records": adjusted_records,
            "status": "unavailable" if unavailable_reason else "completed",
        }
        adjustment_version_id = version_id("adjustment-version", adjustment_payload)

        dataset_payload = {
            "normalized_record_version_id": normalized_record_version_id,
            "coverage_report_id": coverage_report_id,
            "calendar_version_id": calendar_version_id,
            "adjustment_version_id": adjustment_version_id,
        }
        dataset_version_id = version_id("dataset-version", dataset_payload)
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
        data_selection_id = version_id("data-selection", data_selection_payload)

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
        feature_snapshot_id = version_id("feature-snapshot", feature_snapshot_payload)
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
        model_artifact_id = version_id("model-artifact", model_artifact_payload)
        serving_assignment_payload = {
            "model_artifact_id": model_artifact_id,
            "execution_purpose": "fixture",
            "assignment_version": "fixture-serving-assignment-v1",
        }
        serving_assignment_id = version_id("serving-assignment", serving_assignment_payload)

        predictions = self._forecaster.predict(feature_snapshot)
        calendar_projection = {
            "exchange": market_batch.market,
            "timezone": market_batch.timezone,
            "version_id": calendar_version_id,
            "session_count": len(selection.sessions),
            "session_fact_count": market_batch.session_fact_count,
            "closure_dates": list(market_batch.closure_dates),
            "half_day_session_ids": [
                session.session_id
                for session in selection.sessions
                if session.session_kind == "half_day"
            ],
            "revision_ids": list(market_batch.calendar_revision_ids),
            "session_time_examples": list(market_batch.session_time_examples),
            "resolution_status": (
                "unavailable" if unavailable_reason == "calendar_unresolved" else "available"
            ),
        }
        adjustment_projection = {
            "version_id": adjustment_version_id,
            "input_price_kind": "unadjusted",
            "output_price_kind": "adjusted",
            "session_count": len(adjusted_records),
            "company_action_count": 1 if company_action_available else 0,
        }
        research_record = {
            "identity": identity_payload,
            "calendar": calendar_projection,
            "company_actions": [company_action_payload] if company_action_available else [],
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
                "committed_checkpoint": market_batch.committed_checkpoint,
                **policy.source_evidence(base_source_record_version_id),
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
        related_artifacts: list[dict[str, Any]] = []
        if policy.related_base_required:
            if base_raw_artifact_id != raw_artifact_id:
                related_artifacts.append(
                    {
                        "artifact_id": base_raw_artifact_id,
                        "artifact_kind": "raw_artifact",
                        "payload": base_raw_artifact_payload,
                    }
                )
            related_artifacts.append(
                {
                    "artifact_id": base_source_record_version_id,
                    "artifact_kind": "source_record_version",
                    "payload": base_source_record_payload,
                }
            )
        disposition = policy.publication_disposition(
            unavailable_reason,
            health_scope=market_batch.health_scope,
        )
        company_action_artifacts: list[dict[str, Any]] = (
            [
                {
                    "artifact_id": company_action_version_id,
                    "artifact_kind": "company_action_version",
                    "payload": company_action_payload,
                }
            ]
            if company_action_version_id is not None
            else []
        )
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
                "payload": {"security_id": security_id, "exchange": market_batch.market},
            },
            *[
                {
                    "artifact_id": version_id("identity-assertion", assertion),
                    "artifact_kind": "identity_assertion",
                    "payload": assertion,
                }
                for assertion in identity_timeline.assertions_payload()
            ],
            {
                "artifact_id": source_policy_version_id,
                "artifact_kind": "source_policy_version",
                "payload": source_policy_payload,
            },
            *related_artifacts,
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
            *company_action_artifacts,
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
        work_id = fixture_id(f"work/{command.idempotency_key}")
        self._state_store.publish_fixture_trace(
            record_id=fixture_id(f"research-record/{command.idempotency_key}"),
            payload=research_record,
            work_id=work_id,
            trace_id=command.trace_id,
            idempotency_key=command.idempotency_key,
            health_assessment_id=fixture_id(f"source-health/{command.idempotency_key}"),
            audit_event_id=fixture_id(f"audit/{command.idempotency_key}"),
            operations=disposition,
            artifacts=artifacts,
            fixture_predictions=[
                {
                    "prediction_id": version_id(
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
            status=disposition.work_status,
            execution_purpose="fixture",
            market=market_batch.market,
            issuer_id=issuer_id,
            security_id=security_id,
            listing_id=listing_id,
            display_ticker=display_ticker,
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
