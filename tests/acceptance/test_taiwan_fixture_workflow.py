from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from stock_forecasting.application import build_test_application
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand
from stock_forecasting.workflows.fixture_use import FixtureUseCommand


def test_xtai_fixture_identity_calendar_and_adjustment_are_visible_after_eod() -> None:
    observed_at = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=observed_at)

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=observed_at,
            trace_id="trace-ticket-01-identity",
            idempotency_key="ticket-01-identity",
        )
    )

    assert outcome.status == "succeeded"
    assert outcome.execution_purpose == "fixture"
    UUID(outcome.listing_id)
    assert outcome.listing_id != outcome.display_ticker

    research = application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=observed_at,
    )

    assert research["identity"] == {
        "issuer_id": outcome.issuer_id,
        "security_id": outcome.security_id,
        "listing_id": outcome.listing_id,
        "display_ticker": "2330",
        "ticker_valid_from": "2025-08-13",
        "ticker_valid_to": None,
    }
    assert research["calendar"] == {
        "exchange": "XTAI",
        "timezone": "Asia/Taipei",
        "version_id": outcome.calendar_version_id,
        "session_count": 253,
    }
    assert research["company_actions"] == [
        {
            "kind": "cash_dividend",
            "effective_session_id": "XTAI:2026-06-15",
            "cash_amount": "5.00",
            "currency": "TWD",
        }
    ]
    assert research["adjustment_version_id"] == outcome.adjustment_version_id


def test_xtai_collection_publishes_point_in_time_evidence_and_checkpoint() -> None:
    observed_at = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=observed_at)

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=observed_at,
            trace_id="trace-ticket-01-evidence",
            idempotency_key="ticket-01-evidence",
        )
    )

    research = application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=observed_at,
    )
    evidence = research["source_evidence"]

    for field in (
        "raw_artifact_id",
        "source_record_version_id",
        "normalized_record_version_id",
        "retrieval_receipt_id",
        "coverage_report_id",
    ):
        UUID(evidence[field])

    assert evidence["first_observed_at"] == "2026-08-12T07:00:00Z"
    assert evidence["source_policy"] == {
        "version_id": outcome.source_policy_version_id,
        "execution_purpose": "fixture",
        "content_origin": "synthetic",
        "formal_source_qualified": False,
    }
    assert evidence["coverage"] == {
        "status": "completed",
        "expected_partitions": 1,
        "received_partitions": 1,
        "missing_partitions": [],
        "session_count": 253,
    }
    assert evidence["committed_checkpoint"] == "xtai-fixture-page:1"
    assert evidence["scenario_kinds"] == [
        "normal",
        "late",
        "duplicate",
        "correction",
        "missing",
        "withdrawal",
    ]


def test_fixture_eod_pins_lineage_and_publishes_three_horizon_probabilities() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=cutoff)

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-forecast",
            idempotency_key="ticket-01-forecast",
        )
    )
    research = application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )

    assert research["execution_purpose"] == "fixture"
    assert research["fixture_badge"] == "Fixture／非正式預測"
    assert research["information_cutoff"] == "2026-08-12T07:00:00Z"
    assert research["lineage"] == {
        "data_selection_id": outcome.data_selection_id,
        "dataset_version_id": outcome.dataset_version_id,
        "feature_snapshot_id": outcome.feature_snapshot_id,
        "model_artifact_id": outcome.model_artifact_id,
        "serving_assignment_id": outcome.serving_assignment_id,
        "raw_artifact_id": research["source_evidence"]["raw_artifact_id"],
    }
    assert research["predictions"] == [
        {
            "horizon_sessions": 1,
            "probabilities": {"up": 0.62, "flat": 0.23, "down": 0.15},
            "confidence_score": 0.163512,
            "prediction_status": "full",
            "data_support": {"price_volume": "full"},
        },
        {
            "horizon_sessions": 5,
            "probabilities": {"up": 0.55, "flat": 0.28, "down": 0.17},
            "confidence_score": 0.102073,
            "prediction_status": "full",
            "data_support": {"price_volume": "full"},
        },
        {
            "horizon_sessions": 20,
            "probabilities": {"up": 0.43, "flat": 0.35, "down": 0.22},
            "confidence_score": 0.032003,
            "prediction_status": "full",
            "data_support": {"price_volume": "full"},
        },
    ]


def test_missing_necessary_price_evidence_omits_probabilities() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=cutoff)

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-unavailable",
            idempotency_key="ticket-01-unavailable",
            fixture_scenario="missing_anchor_price",
        )
    )
    research = application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )

    assert research["predictions"] == [
        {
            "horizon_sessions": horizon,
            "prediction_status": "unavailable",
            "unavailable_reason": {"code": "missing_anchor_price"},
            "data_support": {"price_volume": "unavailable"},
        }
        for horizon in (1, 5, 20)
    ]
    assert all("probabilities" not in result for result in research["predictions"])
    assert all("confidence_score" not in result for result in research["predictions"])


def test_fixture_cannot_enter_formal_routes_and_denials_are_observable() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    trace_id = "trace-ticket-01-fixture-isolation"
    application = build_test_application(observed_at=cutoff)
    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-fixture-source",
            idempotency_key="ticket-01-fixture-isolation",
        )
    )

    for target in (
        "production_route",
        "production_prediction_record",
        "formal_export",
        "model_promotion",
    ):
        decision = application.attempt_fixture_use(
            FixtureUseCommand(
                model_artifact_id=outcome.model_artifact_id,
                target=target,
                trace_id=trace_id,
            )
        )
        assert decision == {
            "status": "blocked",
            "code": "fixture_use_forbidden",
            "target": target,
        }

    assert application.research_query.list_predictions(execution_purpose="production") == []
    assert application.security_audit.list_events(trace_id=trace_id) == [
        {
            "action": target,
            "outcome": "denied",
            "reason_code": "fixture_use_forbidden",
            "trace_id": trace_id,
        }
        for target in (
            "production_route",
            "production_prediction_record",
            "formal_export",
            "model_promotion",
        )
    ]
    assert application.operations_control.list_health(scope="fixture_isolation") == [
        {
            "scope": "fixture_isolation",
            "status": "blocked",
            "reason_code": "fixture_use_forbidden",
            "affected_attempts": 4,
        }
    ]


def test_collection_persists_raw_evidence_before_exposing_checkpoint(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=cutoff, object_root=tmp_path)

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-object",
            idempotency_key="ticket-01-object",
        )
    )

    raw_content = application.object_repository.open(outcome.raw_object_ref).read()
    assert b'"exchange":"XTAI"' in raw_content
    assert b'"session_count":253' in raw_content
    assert application.object_repository.stat(outcome.raw_object_ref)["size"] == len(raw_content)
    research = application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )
    assert research["source_evidence"]["raw_object_checksum"] == outcome.raw_object_ref.checksum
    assert research["source_evidence"]["committed_checkpoint"] == "xtai-fixture-page:1"


def test_canonical_trace_keeps_fixture_results_separate_from_production_records() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    trace_id = "trace-ticket-01-canonical"
    application = build_test_application(observed_at=cutoff)

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key="ticket-01-canonical",
        )
    )

    evidence = application.operations_control.get_trace_evidence(trace_id)
    assert evidence["execution_purpose"] == "fixture"
    assert evidence["artifact_kinds"] == [
        "issuer",
        "security",
        "listing",
        "identity_assertion",
        "source_policy_version",
        "raw_artifact",
        "source_record_version",
        "normalized_record_version",
        "retrieval_receipt",
        "coverage_report",
        "calendar_version",
        "company_action_version",
        "adjustment_version",
        "dataset_version",
        "data_selection",
        "feature_snapshot",
        "model_artifact",
        "serving_assignment",
    ]
    assert evidence["lineage_ids"] == {
        "dataset_version_id": outcome.dataset_version_id,
        "data_selection_id": outcome.data_selection_id,
        "feature_snapshot_id": outcome.feature_snapshot_id,
        "model_artifact_id": outcome.model_artifact_id,
        "serving_assignment_id": outcome.serving_assignment_id,
    }
    assert evidence["fixture_prediction_result_count"] == 3
    assert evidence["production_prediction_record_count"] == 0
