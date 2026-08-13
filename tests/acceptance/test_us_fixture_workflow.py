from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from stock_forecasting.application import build_test_application
from stock_forecasting.contracts import UnavailableCode
from stock_forecasting.fixture_scenarios import FixtureScenario
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand
from tests.support import assert_success


def test_xnas_fixture_uses_the_shared_eod_research_contract() -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    application = build_test_application(observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC))

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-p1-trace-us-01",
            idempotency_key="p1-trace-us-01",
            market="XNAS",
        )
    )
    research = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )

    assert outcome.status == "succeeded"
    assert outcome.execution_purpose == "fixture"
    UUID(outcome.issuer_id)
    UUID(outcome.security_id)
    UUID(outcome.listing_id)
    assert outcome.listing_id != outcome.display_ticker
    assert research["identity"] == {
        "issuer_id": outcome.issuer_id,
        "security_id": outcome.security_id,
        "listing_id": outcome.listing_id,
        "display_ticker": "USF2",
        "ticker_valid_from": "2026-01-01",
        "ticker_valid_to": None,
        "ticker_assertions": [
            {
                "listing_id": outcome.listing_id,
                "ticker": "USF1",
                "valid_from": "2024-01-01",
                "valid_to": "2025-12-31",
            },
            {
                "listing_id": outcome.listing_id,
                "ticker": "USF2",
                "valid_from": "2026-01-01",
                "valid_to": None,
            },
        ],
        "external_identifier_assertions": [
            {
                "subject_kind": "security",
                "subject_id": outcome.security_id,
                "identifier_type": "fixture_source_security_id",
                "identifier_value": "XNAS-FIXTURE-SECURITY-001",
                "source": "synthetic_fixture_registry",
                "evidence": "xnas-fixture-identity-manifest-v1",
                "trust_level": "fixture_only",
                "valid_from": "2024-01-01",
                "valid_to": None,
                "source_policy_version_id": outcome.source_policy_version_id,
            }
        ],
    }
    assert research["calendar"] == {
        "exchange": "XNAS",
        "timezone": "America/New_York",
        "version_id": outcome.calendar_version_id,
        "session_count": 253,
        "session_fact_count": 300,
        "closure_dates": [
            "2025-06-19",
            "2025-07-04",
            "2025-09-01",
            "2025-11-27",
            "2025-12-25",
            "2026-01-01",
            "2026-01-19",
            "2026-02-16",
            "2026-04-03",
            "2026-05-25",
            "2026-06-19",
            "2026-07-03",
        ],
        "half_day_session_ids": [
            "XNAS:2025-11-28",
            "XNAS:2025-12-24",
            "XNAS:2026-07-02",
        ],
        "revision_ids": ["xnas-fixture-calendar-revision-1"],
        "session_time_examples": [
            {
                "session_id": "XNAS:2026-03-06",
                "open_at": "14:30:00Z",
                "close_at": "21:00:00Z",
            },
            {
                "session_id": "XNAS:2026-03-09",
                "open_at": "13:30:00Z",
                "close_at": "20:00:00Z",
            },
        ],
        "resolution_status": "available",
    }
    assert research["company_actions"] == [
        {
            "kind": "split",
            "effective_session_id": "XNAS:2026-02-02",
            "split_ratio": "2.00",
        }
    ]
    assert research["adjustment"] == {
        "version_id": outcome.adjustment_version_id,
        "input_price_kind": "unadjusted",
        "output_price_kind": "adjusted",
        "session_count": 253,
        "company_action_count": 1,
    }
    assert research["source_evidence"]["source_policy"] == {
        "version_id": outcome.source_policy_version_id,
        "dataset_id": "xnas-fixture-eod",
        "execution_purpose": "fixture",
        "content_origin": "synthetic",
        "formal_source_qualified": False,
        "allowed_actions": ["fixture_pipeline.execute", "research_prediction.read"],
        "purposes": ["fixture_research"],
        "environments": ["development"],
        "data_protection_class": "internal",
        "resource_states": ["active"],
    }
    assert research["source_evidence"]["coverage"]["last_session_id"] == "XNAS:2026-08-12"
    assert research["source_evidence"]["committed_checkpoint"] == "xnas-fixture-page:1"
    assert research["predictions"][1] == {
        "horizon_sessions": 5,
        "probabilities": {"up": 0.55, "flat": 0.28, "down": 0.17},
        "confidence_score": 0.102073,
        "prediction_status": "full",
        "data_support": {"price_volume": "full"},
    }
    raw_payload = json.loads(application.object_repository.open(outcome.raw_object_ref).read())
    assert raw_payload["exchange"] == "XNAS"
    assert raw_payload["price_kind"] == "unadjusted"
    assert raw_payload["session_count"] == 253


def test_xnas_necessary_data_failures_do_not_hide_the_successful_xtai_result() -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    application = build_test_application(observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC))
    xtai_outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-p1-trace-tw-01-shared-cutoff",
            idempotency_key="p1-trace-tw-01-shared-cutoff",
        )
    )

    expected_failures: dict[FixtureScenario, tuple[UnavailableCode, str]] = {
        "missing": ("missing_anchor_price", "coverage_incomplete"),
        "missing_company_action": ("missing_company_action", "missing_company_action"),
        "missing_calendar": ("calendar_unresolved", "calendar_unresolved"),
    }
    for scenario, (unavailable_code, health_reason) in expected_failures.items():
        trace_id = f"trace-p1-trace-us-01-{scenario}"
        outcome = assert_success(application).run_fixture_eod(
            FixtureEodCommand(
                information_cutoff=cutoff,
                trace_id=trace_id,
                idempotency_key=f"p1-trace-us-01-{scenario}",
                market="XNAS",
                fixture_scenario=scenario,
            )
        )
        research = assert_success(application).research_query.get_listing_research(
            listing_id=outcome.listing_id,
            information_cutoff=cutoff,
            fixture_scenario=scenario,
        )

        assert outcome.status == "blocked"
        assert {
            prediction["unavailable_reason"]["code"] for prediction in research["predictions"]
        } == {unavailable_code}
        assert all("probabilities" not in prediction for prediction in research["predictions"])
        work = application.operations_control.get_work(outcome.work_id)
        assert work is not None
        assert work["status"] == "blocked"
        assert application.operations_control.list_health(
            scope=f"xnas_fixture_source/{scenario}"
        ) == [
            {
                "scope": f"xnas_fixture_source/{scenario}",
                "status": "degraded",
                "reason_code": health_reason,
                "affected_attempts": 1,
            }
        ]
        audit = application.security_audit.list_events(trace_id=trace_id)
        assert len(audit) == 1
        assert audit[0]["action"] == "fixture_pipeline.execute"
        assert audit[0]["outcome"] == "allowed"
        assert audit[0]["reason_code"] == "authorized"
        assert audit[0]["trace_id"] == trace_id

    xtai_research = assert_success(application).research_query.get_listing_research(
        listing_id=xtai_outcome.listing_id,
        information_cutoff=cutoff,
    )
    assert xtai_outcome.status == "succeeded"
    assert {prediction["prediction_status"] for prediction in xtai_research["predictions"]} == {
        "full"
    }


def test_xnas_correction_revises_its_own_source_version() -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    application = build_test_application(observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC))
    normal = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-p1-trace-us-01-normal",
            idempotency_key="p1-trace-us-01-normal",
            market="XNAS",
        )
    )
    correction = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-p1-trace-us-01-correction",
            idempotency_key="p1-trace-us-01-correction",
            market="XNAS",
            fixture_scenario="correction",
        )
    )

    normal_raw = json.loads(application.object_repository.open(normal.raw_object_ref).read())
    correction_raw = json.loads(
        application.object_repository.open(correction.raw_object_ref).read()
    )
    normal_research = assert_success(application).research_query.get_listing_research(
        listing_id=normal.listing_id,
        information_cutoff=cutoff,
    )
    research = assert_success(application).research_query.get_listing_research(
        listing_id=correction.listing_id,
        information_cutoff=cutoff,
        fixture_scenario="correction",
    )

    assert Decimal(correction_raw["records"][-1]["close"]) == (
        Decimal(normal_raw["records"][-1]["close"]) - Decimal("0.50")
    )
    assert (
        research["source_evidence"]["supersedes"]
        == (normal_research["source_evidence"]["source_record_version_id"])
    )
    assert correction.status == "succeeded"


def test_xnas_unresolved_calendar_does_not_publish_a_valid_calendar_artifact() -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    trace_id = "trace-p1-trace-us-01-calendar-unresolved"
    application = build_test_application(observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC))

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key="p1-trace-us-01-calendar-unresolved",
            market="XNAS",
            fixture_scenario="missing_calendar",
        )
    )
    research = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
        fixture_scenario="missing_calendar",
    )
    trace_evidence = application.operations_control.get_trace_evidence(trace_id)

    assert outcome.calendar_version_id is None
    assert research["calendar"] == {
        "exchange": "XNAS",
        "timezone": "America/New_York",
        "version_id": None,
        "session_count": 0,
        "session_fact_count": 0,
        "closure_dates": [],
        "half_day_session_ids": [],
        "revision_ids": [],
        "session_time_examples": [],
        "resolution_status": "unavailable",
    }
    assert "calendar_version" not in trace_evidence["artifact_kinds"]
    assert {prediction["unavailable_reason"]["code"] for prediction in research["predictions"]} == {
        "calendar_unresolved"
    }
