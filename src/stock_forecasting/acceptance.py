from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from dagster import ResourceDefinition, materialize
from fastapi.testclient import TestClient
from httpx import Client

from stock_forecasting.adapters.dagster import FixtureRunner, xtai_fixture_eod_asset
from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_application, build_test_application
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand
from stock_forecasting.workflows.fixture_use import FixtureUseCommand, FixtureUseTarget


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...

    def close(self) -> None: ...


def run_ticket_01(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    base_url: str | None = None,
) -> dict[str, Any]:
    if base_url is None:
        application = build_test_application(
            observed_at=information_cutoff,
            object_root=object_root,
            database_url=database_url,
        )
    else:
        application = build_application(
            object_root=object_root,
            database_url=database_url,
        )
    command = FixtureEodCommand(
        information_cutoff=information_cutoff,
        trace_id="trace-p1-trace-tw-01",
        idempotency_key="p1-trace-tw-01",
    )
    outcome = application.run_fixture_eod(command)
    materialization = materialize(
        [xtai_fixture_eod_asset],
        resources={
            "fixture_runner": ResourceDefinition.hardcoded_resource(
                FixtureRunner(application, command)
            )
        },
    )
    dagster_outcome = materialization.output_for_node("xtai_fixture_eod")
    research = application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=information_cutoff,
    )
    trace_evidence = application.operations_control.get_trace_evidence(command.trace_id)
    client: HttpClient
    if base_url is None:
        client = TestClient(create_web_app(application))
    else:
        client = Client(base_url=base_url, timeout=10.0)
    cutoff_text = information_cutoff.isoformat().replace("+00:00", "Z")
    matrix_response = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff_text},
    )
    matrix_html = client.get(
        "/research",
        params={
            "information_cutoff": cutoff_text,
            "horizon": 5,
            "market": "XTAI",
            "support": "full",
            "sort": "confidence_desc",
        },
    )
    detail_query = {
        "information_cutoff": cutoff_text,
        "horizon": 5,
        "market": "XTAI",
        "support": "full",
        "sort": "confidence_desc",
        "tab": "lineage",
    }
    detail_url = f"/research/listings/{outcome.listing_id}?{urlencode(detail_query)}"
    detail_first = client.get(detail_url)
    detail_reload = client.get(detail_url)
    client.close()

    denial_trace_id = "trace-p1-fixture-isolation"
    targets: tuple[FixtureUseTarget, ...] = (
        "production_route",
        "production_prediction_record",
        "formal_export",
        "model_promotion",
    )
    decisions = [
        application.attempt_fixture_use(
            FixtureUseCommand(
                model_artifact_id=outcome.model_artifact_id,
                target=target,
                trace_id=denial_trace_id,
            )
        )
        for target in targets
    ]
    object_stat = application.object_repository.stat(outcome.raw_object_ref)
    horizons = {prediction["horizon_sessions"] for prediction in research["predictions"]}
    result_or_reason = all(
        ("probabilities" in prediction) != ("unavailable_reason" in prediction)
        for prediction in research["predictions"]
    )
    expected_lineage = {
        "data_selection_id",
        "dataset_version_id",
        "feature_snapshot_id",
        "model_artifact_id",
        "serving_assignment_id",
        "raw_artifact_id",
    }
    publication_audit = application.security_audit.list_events(trace_id=command.trace_id)
    denial_audit = application.security_audit.list_events(trace_id=denial_trace_id)
    checks = {
        "workflow_succeeded": outcome.status == "succeeded",
        "dagster_parity": materialization.success
        and dagster_outcome["listing_id"] == outcome.listing_id
        and dagster_outcome["feature_snapshot_id"] == outcome.feature_snapshot_id,
        "immutable_identity": outcome.listing_id != outcome.display_ticker,
        "xtai_253_sessions": research["calendar"]["session_count"] == 253,
        "raw_evidence_durable": object_stat["checksum"] == outcome.raw_object_ref.checksum,
        "checkpoint_committed": research["source_evidence"]["committed_checkpoint"]
        == "xtai-fixture-page:1",
        "three_horizon_result_or_reason": horizons == {1, 5, 20} and result_or_reason,
        "lineage_complete": set(research["lineage"]) == expected_lineage,
        "rest_matrix": matrix_response.status_code == 200
        and matrix_response.json()["items"][0]["listing_id"] == outcome.listing_id,
        "ui_matrix": matrix_html.status_code == 200
        and "比較矩陣" in matrix_html.text
        and "Fixture／非正式預測" in matrix_html.text,
        "ui_detail_reload": detail_first.status_code == 200
        and detail_first.text == detail_reload.text
        and "FeatureSnapshot" in detail_first.text,
        "fixture_use_denied": all(
            decision["code"] == "fixture_use_forbidden" for decision in decisions
        ),
        "canonical_health": application.operations_control.list_health(scope="xtai_fixture_source")[
            0
        ]["status"]
        == "ready"
        and application.operations_control.list_health(scope="fixture_isolation")[0][
            "affected_attempts"
        ]
        == 4,
        "audit_evidence": len(publication_audit) == 1 and len(denial_audit) == 4,
        "no_production_prediction_records": trace_evidence["production_prediction_record_count"]
        == 0,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "trace_ids": ["P1-ENTRY-01", "P1-TRACE-TW-01"],
        "execution_purpose": "fixture",
        "formal_source_qualified": False,
        "formal_prediction": False,
        "listing_id": outcome.listing_id,
        "checks": checks,
    }
