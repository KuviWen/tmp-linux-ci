from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from stock_forecasting.application import build_test_application
from stock_forecasting.fixture_scenarios import FixtureScenario
from stock_forecasting.identity import ListingIdentity
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand
from stock_forecasting.workflows.fixture_use import FixtureUseCommand
from tests.support import assert_success


def test_xtai_fixture_identity_calendar_and_adjustment_are_visible_after_eod() -> None:
    observed_at = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=observed_at)

    outcome = assert_success(application).run_fixture_eod(
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

    research = assert_success(application).research_query.get_listing_research(
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
        "ticker_assertions": [
            {
                "listing_id": outcome.listing_id,
                "ticker": "1234",
                "valid_from": "2024-01-01",
                "valid_to": "2025-08-12",
            },
            {
                "listing_id": outcome.listing_id,
                "ticker": "2330",
                "valid_from": "2025-08-13",
                "valid_to": None,
            },
        ],
        "external_identifier_assertions": [
            {
                "subject_kind": "security",
                "subject_id": outcome.security_id,
                "identifier_type": "fixture_source_security_id",
                "identifier_value": "XTAI-FIXTURE-SECURITY-001",
                "source": "synthetic_fixture_registry",
                "evidence": "xtai-fixture-identity-manifest-v1",
                "trust_level": "fixture_only",
                "valid_from": "2024-01-01",
                "valid_to": None,
                "source_policy_version_id": outcome.source_policy_version_id,
            }
        ],
    }
    identity = ListingIdentity.from_payload(research["identity"])
    assert identity.ticker_at(datetime(2025, 8, 12, tzinfo=UTC).date()) == "1234"
    assert identity.ticker_at(datetime(2025, 8, 13, tzinfo=UTC).date()) == "2330"
    assert {assertion.listing_id for assertion in identity.ticker_assertions} == {
        outcome.listing_id
    }
    calendar = research["calendar"]
    assert calendar["exchange"] == "XTAI"
    assert calendar["timezone"] == "Asia/Taipei"
    assert calendar["version_id"] == outcome.calendar_version_id
    assert calendar["session_count"] == 253
    assert calendar["session_fact_count"] == 300
    assert calendar["closure_dates"] == [
        "2025-10-06",
        "2025-10-10",
        "2026-01-01",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-02-19",
        "2026-02-20",
        "2026-04-06",
        "2026-05-01",
        "2026-06-19",
    ]
    assert calendar["half_day_session_ids"] == ["XTAI:2025-12-31"]
    assert calendar["revision_ids"] == ["xtai-fixture-calendar-revision-1"]
    assert research["company_actions"] == [
        {
            "kind": "cash_dividend",
            "effective_session_id": "XTAI:2026-06-15",
            "cash_amount": "5.00",
            "currency": "TWD",
        }
    ]
    assert research["adjustment_version_id"] == outcome.adjustment_version_id
    assert research["adjustment"] == {
        "version_id": outcome.adjustment_version_id,
        "input_price_kind": "unadjusted",
        "output_price_kind": "adjusted",
        "session_count": 253,
        "company_action_count": 1,
    }


def test_xtai_collection_publishes_point_in_time_evidence_and_checkpoint() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    observed_at = datetime(2026, 8, 12, 7, 3, tzinfo=UTC)
    application = build_test_application(observed_at=observed_at)

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-evidence",
            idempotency_key="ticket-01-evidence",
        )
    )

    research = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
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

    assert evidence["first_observed_at"] == "2026-08-12T07:03:00Z"
    assert evidence["source_policy"] == {
        "version_id": outcome.source_policy_version_id,
        "dataset_id": "xtai-fixture-eod",
        "execution_purpose": "fixture",
        "content_origin": "synthetic",
        "formal_source_qualified": False,
        "allowed_actions": ["fixture_pipeline.execute", "research_prediction.read"],
        "purposes": ["fixture_research"],
        "environments": ["development"],
        "data_protection_class": "internal",
        "resource_states": ["active"],
    }
    assert evidence["coverage"] == {
        "status": "completed",
        "expected_partitions": 1,
        "received_partitions": 1,
        "missing_partitions": [],
        "session_count": 253,
        "first_session_id": "XTAI:2025-08-08",
        "last_session_id": "XTAI:2026-08-12",
    }
    assert evidence["committed_checkpoint"] == "xtai-fixture-page:1"
    assert evidence["fixture_scenario"] == "normal"


def test_default_collection_clock_owns_first_observation_instead_of_copying_cutoff() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application()
    before = datetime.now(UTC)

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-platform-clock",
            idempotency_key="ticket-01-platform-clock",
        )
    )
    after = datetime.now(UTC)

    research = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )
    first_observed_at = datetime.fromisoformat(
        research["source_evidence"]["first_observed_at"].replace("Z", "+00:00")
    )
    assert before <= first_observed_at <= after
    assert first_observed_at != cutoff
    assert {prediction["unavailable_reason"]["code"] for prediction in research["predictions"]} == {
        "post_cutoff_evidence"
    }


def test_adversarial_collection_versions_traverse_the_same_vertical_path(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'scenarios.db'}"
    observed_times: dict[FixtureScenario, datetime] = {
        "normal": datetime(2026, 8, 12, 6, 50, tzinfo=UTC),
        "late": datetime(2026, 8, 12, 7, 5, tzinfo=UTC),
        "duplicate": datetime(2026, 8, 12, 6, 51, tzinfo=UTC),
        "correction": datetime(2026, 8, 12, 6, 52, tzinfo=UTC),
        "missing": datetime(2026, 8, 12, 6, 53, tzinfo=UTC),
        "withdrawal": datetime(2026, 8, 12, 6, 54, tzinfo=UTC),
    }
    records = {}
    operations: dict[FixtureScenario, dict[str, Any]] = {}
    local_identity = None
    for scenario, observed_at in observed_times.items():
        application = build_test_application(
            observed_at=observed_at,
            object_root=tmp_path / "objects",
            database_url=database_url,
            local_identity=local_identity,
        )
        local_identity = application.local_identity
        outcome = assert_success(application).run_fixture_eod(
            FixtureEodCommand(
                information_cutoff=cutoff,
                trace_id=f"trace-ticket-01-{scenario}",
                idempotency_key=f"ticket-01-{scenario}",
                fixture_scenario=scenario,
            )
        )
        records[scenario] = assert_success(application).research_query.get_listing_research(
            listing_id=outcome.listing_id,
            information_cutoff=cutoff,
            fixture_scenario=scenario,
        )
        operations[scenario] = {
            "work": application.operations_control.get_work(outcome.work_id),
            "health": application.operations_control.list_health(
                scope=(
                    "xtai_fixture_source"
                    if scenario == "normal"
                    else f"xtai_fixture_source/{scenario}"
                )
            ),
            "audit": application.security_audit.list_events(trace_id=f"trace-ticket-01-{scenario}"),
        }

    normal_source = records["normal"]["source_evidence"]["source_record_version_id"]
    assert records["normal"]["predictions"][0]["prediction_status"] == "full"
    assert records["late"]["predictions"][0]["unavailable_reason"]["code"] == (
        "post_cutoff_evidence"
    )
    assert records["duplicate"]["source_evidence"]["duplicate_of"] == normal_source
    assert records["duplicate"]["source_evidence"]["deduplicated"] is True
    assert records["duplicate"]["predictions"][0]["prediction_status"] == "full"
    assert records["correction"]["source_evidence"]["supersedes"] == normal_source
    assert records["correction"]["source_evidence"]["revision_number"] == 2
    assert (
        records["correction"]["lineage"]["feature_snapshot_id"]
        != records["normal"]["lineage"]["feature_snapshot_id"]
    )
    assert records["missing"]["predictions"][0]["unavailable_reason"]["code"] == (
        "missing_anchor_price"
    )
    assert records["withdrawal"]["predictions"][0]["unavailable_reason"]["code"] == (
        "source_withdrawn"
    )
    assert records["withdrawal"]["source_evidence"]["withdraws"] == normal_source
    assert operations["normal"]["work"]["status"] == "succeeded"
    assert operations["duplicate"]["health"][0]["reason_code"] == "duplicate_deduplicated"
    assert operations["correction"]["health"][0]["reason_code"] == "correction_applied"
    assert operations["late"]["work"]["status"] == "blocked"
    assert operations["late"]["health"][0]["reason_code"] == "post_cutoff_evidence"
    assert operations["missing"]["health"][0]["reason_code"] == "coverage_incomplete"
    assert operations["withdrawal"]["health"][0] == {
        "scope": "xtai_fixture_source/withdrawal",
        "status": "blocked",
        "reason_code": "source_withdrawn",
        "affected_attempts": 1,
    }
    assert operations["withdrawal"]["audit"][0]["reason_code"] == "authorized"


@pytest.mark.parametrize(
    ("scenario", "reference_field"),
    [("correction", "supersedes"), ("withdrawal", "withdraws")],
)
def test_standalone_revision_publishes_the_version_it_references(
    scenario: FixtureScenario,
    reference_field: str,
) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    trace_id = f"trace-ticket-01-standalone-{scenario}"
    application = build_test_application(observed_at=cutoff)

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key=f"ticket-01-standalone-{scenario}",
            fixture_scenario=scenario,
        )
    )

    record = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
        fixture_scenario=scenario,
    )
    trace_evidence = application.operations_control.get_trace_evidence(trace_id)
    assert record["source_evidence"][reference_field] in trace_evidence["artifact_ids"]
    assert trace_evidence["artifact_kinds"].count("source_record_version") == 2


def test_fixture_eod_pins_lineage_and_publishes_three_horizon_probabilities() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=cutoff)

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-forecast",
            idempotency_key="ticket-01-forecast",
        )
    )
    research = assert_success(application).research_query.get_listing_research(
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

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-unavailable",
            idempotency_key="ticket-01-unavailable",
            fixture_scenario="missing",
        )
    )
    research = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
        fixture_scenario="missing",
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
    outcome = assert_success(application).run_fixture_eod(
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

    assert (
        assert_success(application).research_query.list_predictions(execution_purpose="production")
        == []
    )
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

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-object",
            idempotency_key="ticket-01-object",
        )
    )

    raw_content = application.object_repository.open(outcome.raw_object_ref).read()
    raw_payload = json.loads(raw_content)
    assert raw_payload["exchange"] == "XTAI"
    assert raw_payload["price_kind"] == "unadjusted"
    assert raw_payload["session_count"] == 253
    assert len(raw_payload["records"]) == 253
    assert raw_payload["records"][0] == {
        "session_id": "XTAI:2025-08-08",
        "open": "480.00",
        "high": "482.00",
        "low": "478.00",
        "close": "481.00",
        "volume": 1000000,
    }
    assert set(raw_payload["records"][-1]) == {
        "session_id",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    assert application.object_repository.stat(outcome.raw_object_ref)["size"] == len(raw_content)
    research = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )
    assert research["source_evidence"]["raw_object_checksum"] == outcome.raw_object_ref.checksum
    assert research["source_evidence"]["committed_checkpoint"] == "xtai-fixture-page:1"


def test_canonical_trace_keeps_fixture_results_separate_from_production_records() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    trace_id = "trace-ticket-01-canonical"
    application = build_test_application(observed_at=cutoff)

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key="ticket-01-canonical",
        )
    )

    evidence = application.operations_control.get_trace_evidence(trace_id)
    assert evidence["execution_purpose"] == "fixture"
    assert set(evidence["artifact_kinds"]) == {
        "issuer",
        "security",
        "listing",
        "identity_assertion",
        "source_policy_version",
        "source_entitlement",
        "action_grant",
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
    }
    assert evidence["lineage_ids"] == {
        "dataset_version_id": outcome.dataset_version_id,
        "data_selection_id": outcome.data_selection_id,
        "feature_snapshot_id": outcome.feature_snapshot_id,
        "model_artifact_id": outcome.model_artifact_id,
        "serving_assignment_id": outcome.serving_assignment_id,
    }
    assert evidence["fixture_prediction_result_count"] == 3
    assert evidence["production_prediction_record_count"] == 0


def test_distinct_eod_cutoffs_append_content_bound_versions_without_conflicts() -> None:
    first_cutoff = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)
    second_cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=second_cutoff)

    first = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=first_cutoff,
            trace_id="trace-ticket-01-first-cutoff",
            idempotency_key="ticket-01-first-cutoff",
        )
    )
    second = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=second_cutoff,
            trace_id="trace-ticket-01-second-cutoff",
            idempotency_key="ticket-01-second-cutoff",
        )
    )

    first_record = assert_success(application).research_query.get_listing_research(
        listing_id=first.listing_id,
        information_cutoff=first_cutoff,
    )
    second_record = assert_success(application).research_query.get_listing_research(
        listing_id=second.listing_id,
        information_cutoff=second_cutoff,
    )
    assert first.listing_id == second.listing_id
    assert (
        first_record["source_evidence"]["raw_artifact_id"]
        != second_record["source_evidence"]["raw_artifact_id"]
    )
    assert first.dataset_version_id != second.dataset_version_id
    assert first.data_selection_id != second.data_selection_id
    assert first.feature_snapshot_id != second.feature_snapshot_id
    assert first_record["source_evidence"]["coverage"]["last_session_id"] == ("XTAI:2026-08-11")
    assert second_record["source_evidence"]["coverage"]["last_session_id"] == ("XTAI:2026-08-12")


def test_same_eod_command_is_idempotent_at_the_public_workflow_seam() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    trace_id = "trace-ticket-01-retry"
    application = build_test_application(observed_at=cutoff)
    command = FixtureEodCommand(
        information_cutoff=cutoff,
        trace_id=trace_id,
        idempotency_key="ticket-01-retry",
    )

    first = assert_success(application).run_fixture_eod(command)
    second = assert_success(application).run_fixture_eod(command)

    assert first == second
    evidence = application.operations_control.get_trace_evidence(trace_id)
    assert evidence["fixture_prediction_result_count"] == 3
    audit = application.security_audit.list_events(trace_id=trace_id)
    assert len(audit) == 2
    assert audit[0]["decision_id"] == audit[1]["decision_id"]
    assert audit[0]["evaluation_id"] != audit[1]["evaluation_id"]
    for event in audit:
        assert event["action"] == "fixture_pipeline.execute"
        assert event["outcome"] == "allowed"
        assert event["reason_code"] == "authorized"
        assert event["trace_id"] == trace_id
        assert event["source_policy_version_id"] == first.source_policy_version_id
