from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from dagster import ResourceDefinition, materialize
from fastapi.testclient import TestClient
from httpx import Client

from stock_forecasting.adapters.dagster import (
    FixtureRunner,
    xnas_fixture_eod_asset,
    xtai_fixture_eod_asset,
)
from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_application, build_test_application
from stock_forecasting.dagster_deployment import inspect_dagster_deployment
from stock_forecasting.fixture_market import FixtureMarket, default_fixture_market_adapters
from stock_forecasting.fixture_scenarios import FixtureScenario
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
    observed_at: datetime,
    base_url: str | None = None,
    dagster_url: str | None = None,
) -> dict[str, Any]:
    if (base_url is None) != (dagster_url is None):
        raise ValueError("deployment_endpoints_must_be_provided_together")
    if base_url is None:
        application = build_test_application(
            observed_at=observed_at,
            object_root=object_root,
            database_url=database_url,
        )
    else:
        application = build_application(
            object_root=object_root,
            database_url=database_url,
            observed_at=observed_at,
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
    scenario_observed_at: dict[FixtureScenario, datetime] = {
        "late": information_cutoff + timedelta(minutes=5),
        "duplicate": observed_at + timedelta(minutes=1),
        "correction": observed_at + timedelta(minutes=2),
        "missing": observed_at + timedelta(minutes=3),
        "withdrawal": observed_at + timedelta(minutes=4),
    }
    scenario_records: dict[str, dict[str, Any]] = {}
    scenario_operations: dict[str, dict[str, Any]] = {}
    for scenario, scenario_time in scenario_observed_at.items():
        if base_url is None:
            scenario_application = build_test_application(
                observed_at=scenario_time,
                object_root=object_root,
                database_url=database_url,
            )
        else:
            scenario_application = build_application(
                observed_at=scenario_time,
                object_root=object_root,
                database_url=database_url,
            )
        scenario_outcome = scenario_application.run_fixture_eod(
            FixtureEodCommand(
                information_cutoff=information_cutoff,
                trace_id=f"trace-p1-trace-tw-01-{scenario}",
                idempotency_key=f"p1-trace-tw-01-{scenario}",
                fixture_scenario=scenario,
            )
        )
        scenario_records[scenario] = scenario_application.research_query.get_listing_research(
            listing_id=scenario_outcome.listing_id,
            information_cutoff=information_cutoff,
            fixture_scenario=scenario,
        )
        scenario_operations[scenario] = {
            "work": scenario_application.operations_control.get_work(scenario_outcome.work_id),
            "health": scenario_application.operations_control.list_health(
                scope=f"xtai_fixture_source/{scenario}"
            )[0],
            "audit": scenario_application.security_audit.list_events(
                trace_id=f"trace-p1-trace-tw-01-{scenario}"
            )[0],
        }
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
        "adversarial_scenarios": (
            scenario_records["late"]["predictions"][0]["unavailable_reason"]["code"]
            == "post_cutoff_evidence"
            and scenario_records["duplicate"]["predictions"][0]["prediction_status"] == "full"
            and scenario_records["correction"]["lineage"]["feature_snapshot_id"]
            != research["lineage"]["feature_snapshot_id"]
            and scenario_records["missing"]["predictions"][0]["unavailable_reason"]["code"]
            == "missing_anchor_price"
            and scenario_records["withdrawal"]["predictions"][0]["unavailable_reason"]["code"]
            == "source_withdrawn"
            and scenario_operations["late"]["work"]["status"] == "blocked"
            and scenario_operations["missing"]["health"]["reason_code"] == "coverage_incomplete"
            and scenario_operations["withdrawal"]["health"]["status"] == "blocked"
            and scenario_operations["withdrawal"]["audit"]["reason_code"] == "fixture_policy_active"
        ),
        "immutable_identity": outcome.listing_id != outcome.display_ticker
        and len(research["identity"]["ticker_assertions"]) == 2
        and {assertion["listing_id"] for assertion in research["identity"]["ticker_assertions"]}
        == {outcome.listing_id},
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
    if dagster_url is not None:
        checks["deployed_dagster_ready"] = inspect_dagster_deployment(dagster_url).ready
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "trace_ids": ["P1-ENTRY-01", "P1-TRACE-TW-01"],
        "execution_purpose": "fixture",
        "formal_source_qualified": False,
        "formal_prediction": False,
        "listing_id": outcome.listing_id,
        "checks": checks,
    }


def run_ticket_02(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    observed_at: datetime,
    base_url: str | None = None,
    dagster_url: str | None = None,
) -> dict[str, Any]:
    if (base_url is None) != (dagster_url is None):
        raise ValueError("deployment_endpoints_must_be_provided_together")
    if base_url is None:
        application = build_test_application(
            observed_at=observed_at,
            object_root=object_root,
            database_url=database_url,
        )
    else:
        application = build_application(
            observed_at=observed_at,
            object_root=object_root,
            database_url=database_url,
        )

    commands: dict[FixtureMarket, FixtureEodCommand] = {
        "XTAI": FixtureEodCommand(
            information_cutoff=information_cutoff,
            trace_id="trace-p1-trace-tw-01",
            idempotency_key="p1-trace-tw-01",
        ),
        "XNAS": FixtureEodCommand(
            information_cutoff=information_cutoff,
            trace_id="trace-p1-trace-us-01",
            idempotency_key="p1-trace-us-01",
            market="XNAS",
        ),
    }
    outcomes = {
        market: application.run_fixture_eod(command) for market, command in commands.items()
    }
    research = {
        market: application.research_query.get_listing_research(
            listing_id=outcome.listing_id,
            information_cutoff=information_cutoff,
        )
        for market, outcome in outcomes.items()
    }
    raw_payloads = {
        market: json.loads(application.object_repository.open(outcome.raw_object_ref).read())
        for market, outcome in outcomes.items()
    }
    materialization = materialize(
        [xtai_fixture_eod_asset, xnas_fixture_eod_asset],
        resources={
            "fixture_runner": ResourceDefinition.hardcoded_resource(
                FixtureRunner(application, commands["XTAI"])
            ),
            "xnas_fixture_runner": ResourceDefinition.hardcoded_resource(
                FixtureRunner(application, commands["XNAS"])
            ),
        },
    )

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
            "market": "all",
            "support": "full",
            "sort": "confidence_desc",
        },
    )
    xnas_detail = client.get(
        f"/research/listings/{outcomes['XNAS'].listing_id}",
        params={
            "information_cutoff": cutoff_text,
            "horizon": 5,
            "market": "all",
            "support": "full",
            "sort": "confidence_desc",
            "tab": "lineage",
        },
    )
    client.close()

    scenario_times: dict[FixtureScenario, datetime] = {
        "late": information_cutoff + timedelta(minutes=5),
        "duplicate": observed_at + timedelta(seconds=10),
        "correction": observed_at + timedelta(seconds=20),
        "missing": observed_at + timedelta(seconds=30),
        "missing_company_action": observed_at + timedelta(seconds=40),
        "missing_calendar": observed_at + timedelta(seconds=50),
        "withdrawal": observed_at + timedelta(minutes=1),
    }
    scenario_records: dict[FixtureScenario, dict[str, Any]] = {}
    scenario_operations: dict[FixtureScenario, dict[str, Any]] = {}
    for scenario, scenario_time in scenario_times.items():
        if base_url is None:
            scenario_application = build_test_application(
                observed_at=scenario_time,
                object_root=object_root,
                database_url=database_url,
            )
        else:
            scenario_application = build_application(
                observed_at=scenario_time,
                object_root=object_root,
                database_url=database_url,
            )
        trace_id = f"trace-p1-trace-us-01-{scenario}"
        scenario_outcome = scenario_application.run_fixture_eod(
            FixtureEodCommand(
                information_cutoff=information_cutoff,
                trace_id=trace_id,
                idempotency_key=f"p1-trace-us-01-{scenario}",
                market="XNAS",
                fixture_scenario=scenario,
            )
        )
        scenario_records[scenario] = scenario_application.research_query.get_listing_research(
            listing_id=scenario_outcome.listing_id,
            information_cutoff=information_cutoff,
            fixture_scenario=scenario,
        )
        scenario_operations[scenario] = {
            "work": scenario_application.operations_control.get_work(scenario_outcome.work_id),
            "health": scenario_application.operations_control.list_health(
                scope=f"xnas_fixture_source/{scenario}"
            )[0],
            "audit": scenario_application.security_audit.list_events(trace_id=trace_id)[0],
        }

    adapters = default_fixture_market_adapters()
    batches = {market: adapters[market].load(information_cutoff) for market in adapters}
    matrix_payload = matrix_response.json()
    matrix_items = matrix_payload["items"] if matrix_response.status_code == 200 else []
    prediction_shapes = {
        tuple(sorted(prediction))
        for market_research in research.values()
        for prediction in market_research["predictions"]
    }
    research_shapes = {tuple(sorted(market_research)) for market_research in research.values()}
    identity_shapes = {
        tuple(sorted(market_research["identity"])) for market_research in research.values()
    }
    xnas_source_version = research["XNAS"]["source_evidence"]["source_record_version_id"]
    expected_unavailable: dict[FixtureScenario, str] = {
        "late": "post_cutoff_evidence",
        "missing": "missing_anchor_price",
        "missing_company_action": "missing_company_action",
        "missing_calendar": "calendar_unresolved",
        "withdrawal": "source_withdrawn",
    }
    trace_evidence = {
        market: application.operations_control.get_trace_evidence(command.trace_id)
        for market, command in commands.items()
    }
    checks = {
        "shared_workflow_succeeded": all(
            outcome.status == "succeeded" and outcome.execution_purpose == "fixture"
            for outcome in outcomes.values()
        ),
        "dagster_parity": materialization.success
        and materialization.output_for_node("xtai_fixture_eod")["listing_id"]
        == outcomes["XTAI"].listing_id
        and materialization.output_for_node("xnas_fixture_eod")["listing_id"]
        == outcomes["XNAS"].listing_id,
        "shared_domain_contract": len(research_shapes) == 1
        and len(identity_shapes) == 1
        and all(outcome.listing_id != outcome.display_ticker for outcome in outcomes.values())
        and all(
            {
                assertion["listing_id"]
                for assertion in market_research["identity"]["ticker_assertions"]
            }
            == {market_research["identity"]["listing_id"]}
            for market_research in research.values()
        ),
        "market_specific_adapter": batches["XTAI"].timezone == "Asia/Taipei"
        and batches["XNAS"].timezone == "America/New_York"
        and batches["XTAI"].company_action_payload["kind"] == "cash_dividend"
        and batches["XNAS"].company_action_payload["kind"] == "split"
        and batches["XNAS"].session_time_examples[0]["open_at"] == "14:30:00Z"
        and batches["XNAS"].session_time_examples[1]["open_at"] == "13:30:00Z",
        "xnas_fixture_contract": research["XNAS"]["calendar"]["session_count"] == 253
        and research["XNAS"]["calendar"]["session_fact_count"] >= 300
        and bool(research["XNAS"]["calendar"]["closure_dates"])
        and bool(research["XNAS"]["calendar"]["revision_ids"])
        and raw_payloads["XNAS"]["price_kind"] == "unadjusted"
        and research["XNAS"]["source_evidence"]["source_policy"]["formal_source_qualified"]
        is False,
        "adversarial_scenarios": all(
            {
                prediction["unavailable_reason"]["code"]
                for prediction in scenario_records[scenario]["predictions"]
            }
            == {code}
            for scenario, code in expected_unavailable.items()
        )
        and scenario_records["duplicate"]["source_evidence"]["duplicate_of"] == xnas_source_version
        and scenario_records["correction"]["source_evidence"]["supersedes"] == xnas_source_version
        and scenario_records["correction"]["lineage"]["feature_snapshot_id"]
        != research["XNAS"]["lineage"]["feature_snapshot_id"],
        "one_market_failure_isolated": all(
            prediction["prediction_status"] == "full"
            for prediction in research["XTAI"]["predictions"]
        )
        and all(
            prediction["prediction_status"] == "unavailable"
            for prediction in scenario_records["missing_calendar"]["predictions"]
        ),
        "shared_prediction_shape": prediction_shapes
        == {
            (
                "confidence_score",
                "data_support",
                "horizon_sessions",
                "prediction_status",
                "probabilities",
            )
        }
        and all(
            {prediction["horizon_sessions"] for prediction in market_research["predictions"]}
            == {1, 5, 20}
            for market_research in research.values()
        ),
        "rest_matrix": matrix_response.status_code == 200
        and {item["market"] for item in matrix_items} == {"XTAI", "XNAS"}
        and all(
            set(item["lineage"]) == set(research[item["market"]]["lineage"])
            for item in matrix_items
        ),
        "ui_matrix_and_detail": matrix_html.status_code == 200
        and "2330 · XTAI" in matrix_html.text
        and "USF2 · XNAS" in matrix_html.text
        and xnas_detail.status_code == 200
        and "USF2 · XNAS" in xnas_detail.text,
        "operations_and_audit": all(
            scenario_operations[scenario]["audit"]["reason_code"] == "fixture_policy_active"
            for scenario in scenario_times
        )
        and scenario_operations["late"]["work"]["status"] == "blocked"
        and scenario_operations["missing_company_action"]["health"]["reason_code"]
        == "missing_company_action"
        and scenario_operations["missing_calendar"]["health"]["reason_code"]
        == "calendar_unresolved",
        "no_production_prediction_records": all(
            evidence["production_prediction_record_count"] == 0
            for evidence in trace_evidence.values()
        ),
    }
    if dagster_url is not None:
        checks["deployed_dagster_ready"] = inspect_dagster_deployment(dagster_url).ready
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "trace_ids": ["P1-TRACE-TW-01", "P1-TRACE-US-01"],
        "execution_purpose": "fixture",
        "formal_source_qualified": False,
        "formal_prediction": False,
        "listing_ids": {market: outcome.listing_id for market, outcome in outcomes.items()},
        "checks": checks,
    }
