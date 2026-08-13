from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dagster import ResourceDefinition, materialize

from stock_forecasting.adapters.dagster import (
    FixtureRunner,
    xnas_fixture_eod_asset,
    xtai_fixture_eod_asset,
)
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import PolicyDeniedOutcome
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand


def test_dagster_and_direct_workflow_publish_the_same_outcome(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    command = FixtureEodCommand(
        information_cutoff=cutoff,
        trace_id="trace-ticket-01-dagster",
        idempotency_key="ticket-01-dagster",
    )
    direct_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "direct-objects",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'direct.db'}",
    )
    dagster_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "dagster-objects",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'dagster.db'}",
    )

    direct_outcome = direct_application.require_fixture_eod_success(command)
    materialization = materialize(
        [xtai_fixture_eod_asset],
        resources={
            "fixture_runner": ResourceDefinition.hardcoded_resource(
                FixtureRunner(dagster_application, command)
            )
        },
    )

    assert materialization.success
    dagster_outcome = materialization.output_for_node("xtai_fixture_eod")
    assert dagster_outcome == {
        "status": direct_outcome.status,
        "execution_purpose": direct_outcome.execution_purpose,
        "market": "XTAI",
        "listing_id": direct_outcome.listing_id,
        "dataset_version_id": direct_outcome.dataset_version_id,
        "feature_snapshot_id": direct_outcome.feature_snapshot_id,
        "model_artifact_id": direct_outcome.model_artifact_id,
        "serving_assignment_id": direct_outcome.serving_assignment_id,
    }
    assert (
        dagster_application.research_query.require_listing_research(
            listing_id=direct_outcome.listing_id,
            information_cutoff=cutoff,
        )["predictions"]
        == direct_application.research_query.require_listing_research(
            listing_id=direct_outcome.listing_id,
            information_cutoff=cutoff,
        )["predictions"]
    )


def test_xnas_dagster_asset_calls_the_same_public_workflow(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    command = FixtureEodCommand(
        information_cutoff=cutoff,
        trace_id="trace-ticket-02-xnas-dagster",
        idempotency_key="ticket-02-xnas-dagster",
        market="XNAS",
    )
    direct_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "xnas-direct-objects",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'xnas-direct.db'}",
    )
    dagster_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "xnas-dagster-objects",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'xnas-dagster.db'}",
    )

    direct_outcome = direct_application.require_fixture_eod_success(command)
    materialization = materialize(
        [xnas_fixture_eod_asset],
        resources={
            "xnas_fixture_runner": ResourceDefinition.hardcoded_resource(
                FixtureRunner(dagster_application, command)
            )
        },
    )

    assert materialization.success
    assert materialization.output_for_node("xnas_fixture_eod") == {
        "status": direct_outcome.status,
        "execution_purpose": direct_outcome.execution_purpose,
        "market": "XNAS",
        "listing_id": direct_outcome.listing_id,
        "dataset_version_id": direct_outcome.dataset_version_id,
        "feature_snapshot_id": direct_outcome.feature_snapshot_id,
        "model_artifact_id": direct_outcome.model_artifact_id,
        "serving_assignment_id": direct_outcome.serving_assignment_id,
    }


def test_dagster_projects_policy_denial_as_the_same_stable_outcome(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    command = FixtureEodCommand(
        information_cutoff=cutoff,
        trace_id="trace-ticket-04-dagster-denied",
        idempotency_key="ticket-04-dagster-denied",
    )
    direct_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "direct-denied-objects",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'direct-denied.db'}",
        entitlement_states={"XTAI": "revoked"},
    )
    dagster_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "dagster-denied-objects",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'dagster-denied.db'}",
        entitlement_states={"XTAI": "revoked"},
    )

    direct_outcome = direct_application.run_fixture_eod(command)
    materialization = materialize(
        [xtai_fixture_eod_asset],
        resources={
            "fixture_runner": ResourceDefinition.hardcoded_resource(
                FixtureRunner(dagster_application, command)
            )
        },
    )

    assert isinstance(direct_outcome, PolicyDeniedOutcome)
    assert materialization.success
    assert materialization.output_for_node("xtai_fixture_eod") == {
        "status": "policy_denied",
        "code": "authorization_denied",
        "correlation_id": "trace-ticket-04-dagster-denied",
        "decision_id": direct_outcome.decision_id,
    }
