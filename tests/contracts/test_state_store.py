from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from stock_forecasting.application import build_test_application
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand


def test_fixture_research_state_survives_application_restart(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'application.db'}"
    object_root = tmp_path / "objects"
    first_application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )

    outcome = first_application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-persistence",
            idempotency_key="ticket-01-persistence",
        )
    )
    before_restart = first_application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )

    restarted_application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    after_restart = restarted_application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )

    assert after_restart == before_restart
    assert after_restart["lineage"]["serving_assignment_id"] == outcome.serving_assignment_id
    assert len(after_restart["predictions"]) == 3


def test_work_health_and_audit_evidence_survive_application_restart(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    trace_id = "trace-ticket-01-operations"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'operations.db'}"
    object_root = tmp_path / "objects"
    application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key="ticket-01-operations",
        )
    )
    UUID(outcome.work_id)

    restarted = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    assert restarted.operations_control.get_work(outcome.work_id) == {
        "work_id": outcome.work_id,
        "operation": "fixture_eod",
        "status": "succeeded",
        "execution_purpose": "fixture",
        "trace_id": trace_id,
        "attempt_count": 1,
    }
    assert restarted.operations_control.list_health(scope="xtai_fixture_source") == [
        {
            "scope": "xtai_fixture_source",
            "status": "ready",
            "reason_code": "coverage_complete",
            "affected_attempts": 1,
        }
    ]
    assert restarted.security_audit.list_events(trace_id=trace_id) == [
        {
            "action": "fixture_eod_publication",
            "outcome": "allowed",
            "reason_code": "fixture_policy_active",
            "trace_id": trace_id,
        }
    ]
