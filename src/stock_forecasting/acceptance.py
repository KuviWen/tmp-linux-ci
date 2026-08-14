from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from dagster import ResourceDefinition, materialize
from fastapi.testclient import TestClient
from httpx import Client

from stock_forecasting.adapters.dagster import (
    FixtureRunner,
    xnas_fixture_eod_asset,
    xtai_fixture_eod_asset,
)
from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import Application, build_application, build_test_application
from stock_forecasting.authorization import (
    EntitlementStatus,
    LocalApiKeyIdentity,
    PolicyDeniedOutcome,
)
from stock_forecasting.authorization_repository import (
    FIXTURE_ACTIVE_POLICY_SET,
    FIXTURE_EXPIRED_POLICY_SET,
    FIXTURE_GRANT_MISSING_POLICY_SET,
    FIXTURE_POLICY_UNKNOWN_SET,
    FIXTURE_PURPOSE_REMOVED_POLICY_SET,
    FIXTURE_REVOKED_POLICY_SET,
    FIXTURE_SUSPENDED_POLICY_SET,
)
from stock_forecasting.dagster_deployment import (
    inspect_dagster_deployment,
    materialize_deployed_asset,
)
from stock_forecasting.fixture_market import FixtureMarket, default_fixture_market_adapters
from stock_forecasting.fixture_scenarios import FixtureScenario
from stock_forecasting.outbox import RelayFault, RelayOutcome
from stock_forecasting.research_query import ResearchQuery
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand, FixtureEodOutcome
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


@dataclass(frozen=True)
class _AuthorizationMatrixCase:
    status: EntitlementStatus
    purposes: frozenset[str]
    expected_reason: str
    policy_set_id: str
    grant_actions: frozenset[str] | None = None
    policy_markets: frozenset[str] | None = None


class _CrashOperationsProjection(RelayFault):
    def before_consumers(self, event_id: str) -> None:
        pass

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None:
        if consumer_name == "operations_projection":
            raise RuntimeError("injected_consumer_transaction_crash")

    def before_ack(self, event_id: str) -> None:
        pass


def _fixture_success(
    application: Application,
    command: FixtureEodCommand,
) -> FixtureEodOutcome:
    outcome = application.run_fixture_eod(command)
    assert isinstance(outcome, FixtureEodOutcome)
    return outcome


def _listing_success(
    query: ResearchQuery,
    *,
    listing_id: str,
    information_cutoff: datetime,
    fixture_scenario: str = "normal",
    trace_id: str | None = None,
) -> dict[str, Any]:
    outcome = query.get_listing_research(
        listing_id=listing_id,
        information_cutoff=information_cutoff,
        fixture_scenario=fixture_scenario,
        trace_id=trace_id,
    )
    assert not isinstance(outcome, PolicyDeniedOutcome)
    return outcome


def _validate_deployment_endpoints(base_url: str | None, dagster_url: str | None) -> None:
    if (base_url is None) != (dagster_url is None):
        raise ValueError("deployment_endpoints_must_be_provided_together")


def _build_acceptance_application(
    *,
    database_url: str,
    object_root: Path,
    observed_at: datetime,
    deployed: bool,
    relay_fault: RelayFault | None = None,
) -> Application:
    if deployed:
        key_file = os.environ.get("LOCAL_API_KEY_FILE")
        if key_file is None:
            raise RuntimeError("LOCAL_API_KEY_FILE is required for deployed acceptance")
        return build_application(
            observed_at=observed_at,
            object_root=object_root,
            database_url=database_url,
            relay_fault=relay_fault,
            local_identity=LocalApiKeyIdentity.load(Path(key_file)),
            authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
        )
    return build_test_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        relay_fault=relay_fault,
        authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
    )


def _capture_fixture_scenarios(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    deployed: bool,
    market: FixtureMarket,
    scenario_times: dict[FixtureScenario, datetime],
    local_identity: LocalApiKeyIdentity | None,
) -> tuple[
    dict[FixtureScenario, dict[str, Any]],
    dict[FixtureScenario, dict[str, Any]],
]:
    trace_market = "tw" if market == "XTAI" else "us"
    health_scope = "xtai_fixture_source" if market == "XTAI" else "xnas_fixture_source"
    trace_prefix = f"trace-p1-trace-{trace_market}-01"
    idempotency_prefix = f"p1-trace-{trace_market}-01"
    records: dict[FixtureScenario, dict[str, Any]] = {}
    operations: dict[FixtureScenario, dict[str, Any]] = {}
    for scenario, scenario_time in scenario_times.items():
        application = (
            _build_acceptance_application(
                observed_at=scenario_time,
                object_root=object_root,
                database_url=database_url,
                deployed=True,
            )
            if deployed
            else build_test_application(
                observed_at=scenario_time,
                object_root=object_root,
                database_url=database_url,
                local_identity=local_identity,
                authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
            )
        )
        local_identity = application.local_identity
        trace_id = f"{trace_prefix}-{scenario}"
        outcome = _fixture_success(
            application,
            FixtureEodCommand(
                information_cutoff=information_cutoff,
                trace_id=trace_id,
                idempotency_key=f"{idempotency_prefix}-{scenario}",
                market=market,
                fixture_scenario=scenario,
            ),
        )
        records[scenario] = _listing_success(
            application.research_query,
            listing_id=outcome.listing_id,
            information_cutoff=information_cutoff,
            fixture_scenario=scenario,
        )
        operations[scenario] = {
            "work": application.operations_control.get_work(outcome.work_id),
            "health": application.operations_control.list_health(
                scope=f"{health_scope}/{scenario}"
            )[0],
            "audit": application.security_audit.list_events(trace_id=trace_id)[0],
        }
    return records, operations


def _probe_research_surfaces(
    application: Application,
    *,
    base_url: str | None,
    information_cutoff: datetime,
    outcome: FixtureEodOutcome,
    market_filter: str,
) -> dict[str, Any]:
    authorization_headers = {
        "Authorization": application.local_identity.credential.authorization_header()
    }
    client: HttpClient = (
        TestClient(
            create_web_app(application),
            headers=authorization_headers,
            client=("127.0.0.1", 50000),
        )
        if base_url is None
        else Client(base_url=base_url, timeout=10.0, headers=authorization_headers)
    )
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
            "market": market_filter,
            "support": "full",
            "sort": "confidence_desc",
        },
    )
    detail_params = {
        "information_cutoff": cutoff_text,
        "horizon": 5,
        "market": market_filter,
        "support": "full",
        "sort": "confidence_desc",
        "tab": "lineage",
    }
    detail_path = f"/research/listings/{outcome.listing_id}"
    detail_first = client.get(detail_path, params=detail_params)
    detail_reload = client.get(detail_path, params=detail_params)
    client.close()
    return {
        "matrix_response": matrix_response,
        "matrix_html": matrix_html,
        "detail_first": detail_first,
        "detail_reload": detail_reload,
    }


def run_ticket_01(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    observed_at: datetime,
    base_url: str | None = None,
    dagster_url: str | None = None,
) -> dict[str, Any]:
    _validate_deployment_endpoints(base_url, dagster_url)
    application = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=base_url is not None,
    )
    command = FixtureEodCommand(
        information_cutoff=information_cutoff,
        trace_id="trace-p1-trace-tw-01",
        idempotency_key="p1-trace-tw-01",
    )
    outcome = _fixture_success(application, command)
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
    scenario_records, scenario_operations = _capture_fixture_scenarios(
        database_url=database_url,
        object_root=object_root,
        information_cutoff=information_cutoff,
        deployed=base_url is not None,
        market="XTAI",
        scenario_times=scenario_observed_at,
        local_identity=application.local_identity,
    )
    research = _listing_success(
        application.research_query,
        listing_id=outcome.listing_id,
        information_cutoff=information_cutoff,
    )
    trace_evidence = application.operations_control.get_trace_evidence(command.trace_id)
    surfaces = _probe_research_surfaces(
        application,
        base_url=base_url,
        information_cutoff=information_cutoff,
        outcome=outcome,
        market_filter="XTAI",
    )
    matrix_response = surfaces["matrix_response"]
    matrix_html = surfaces["matrix_html"]
    detail_first = surfaces["detail_first"]
    detail_reload = surfaces["detail_reload"]

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
            and scenario_operations["withdrawal"]["audit"]["reason_code"] == "authorized"
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
        "audit_evidence": len(publication_audit) == 2
        and publication_audit[0]["decision_id"] == publication_audit[1]["decision_id"]
        and publication_audit[0]["evaluation_id"] != publication_audit[1]["evaluation_id"]
        and len(denial_audit) == 4,
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
    _validate_deployment_endpoints(base_url, dagster_url)
    application = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=base_url is not None,
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
        market: _fixture_success(application, command) for market, command in commands.items()
    }
    research = {
        market: _listing_success(
            application.research_query,
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

    surfaces = _probe_research_surfaces(
        application,
        base_url=base_url,
        information_cutoff=information_cutoff,
        outcome=outcomes["XNAS"],
        market_filter="all",
    )
    matrix_response = surfaces["matrix_response"]
    matrix_html = surfaces["matrix_html"]
    xnas_detail = surfaces["detail_first"]

    scenario_times: dict[FixtureScenario, datetime] = {
        "late": information_cutoff + timedelta(minutes=5),
        "duplicate": observed_at + timedelta(seconds=10),
        "correction": observed_at + timedelta(seconds=20),
        "missing": observed_at + timedelta(seconds=30),
        "missing_company_action": observed_at + timedelta(seconds=40),
        "missing_calendar": observed_at + timedelta(seconds=50),
        "withdrawal": observed_at + timedelta(minutes=1),
    }
    scenario_records, scenario_operations = _capture_fixture_scenarios(
        database_url=database_url,
        object_root=object_root,
        information_cutoff=information_cutoff,
        deployed=base_url is not None,
        market="XNAS",
        scenario_times=scenario_times,
        local_identity=application.local_identity,
    )

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
            scenario_operations[scenario]["audit"]["reason_code"] == "authorized"
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


def _terminate_relay_before_consumers(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    observed_at: datetime,
    event_id: str,
    competing_application: Application,
) -> tuple[int, str]:
    with TemporaryDirectory(prefix="stock-forecasting-relay-") as directory:
        ready_file = Path(directory) / "before-consumers.ready"
        local_key_file = Path(directory) / "local-api-key.json"
        competing_application.local_identity.save(local_key_file)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "stock_forecasting.acceptance_relay",
                "--event-id",
                event_id,
                "--pause-before-consumers",
                str(ready_file),
            ],
            env={
                **os.environ,
                "DATABASE_URL": database_url,
                "OBJECT_ROOT": str(object_root),
                "FIXTURE_INFORMATION_CUTOFF": information_cutoff.isoformat(),
                "FIXTURE_COLLECTION_OBSERVED_AT": observed_at.isoformat(),
                "RUNTIME_ENVIRONMENT": "development",
                "PUBLIC_BIND_HOST": "127.0.0.1",
                "LOCAL_API_KEY_MODE": "enabled",
                "LOCAL_API_KEY_FILE": str(local_key_file),
                "AUTHORIZATION_POLICY_SET_ID": FIXTURE_ACTIVE_POLICY_SET,
                "PYTHONUTF8": "1",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            deadline = time.monotonic() + 15
            while (
                not ready_file.exists() and process.poll() is None and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            if not ready_file.exists():
                _, error = process.communicate(timeout=5)
                raise RuntimeError(f"relay_process_did_not_reach_failpoint: {error}")
            competing = competing_application.relay_outbox(event_id=event_id)
            process.terminate()
            process.communicate(timeout=5)
            return int(process.returncode or 0), competing.status
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def _relay_after_lease_expiry(application: Application, *, event_id: str) -> RelayOutcome:
    deadline = time.monotonic() + 5
    outcome = application.relay_outbox(event_id=event_id)
    while outcome.status == "busy" and time.monotonic() < deadline:
        time.sleep(0.05)
        outcome = application.relay_outbox(event_id=event_id)
    return outcome


def run_ticket_03(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    observed_at: datetime,
    base_url: str | None = None,
    dagster_url: str | None = None,
) -> dict[str, Any]:
    _validate_deployment_endpoints(base_url, dagster_url)
    deployed = base_url is not None

    application = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=deployed,
    )
    first_command = FixtureEodCommand(
        information_cutoff=information_cutoff,
        trace_id="trace-p1-trace-outbox-01-relay-crash",
        idempotency_key="p1-trace-outbox-01-relay-crash",
    )
    first = _fixture_success(application, first_command)
    event_before = application.operations_control.get_outbox_event(first.outbox_event_id)
    predictions_before = application.operations_control.list_prediction_records(
        trace_id=first_command.trace_id
    )
    prediction_evidence_before = application.operations_control.list_prediction_record_evidence(
        trace_id=first_command.trace_id
    )
    lineage_before = application.operations_control.get_trace_evidence(first_command.trace_id)
    audit_before = application.security_audit.list_events(trace_id=first_command.trace_id)

    relay_returncode, competing_status = _terminate_relay_before_consumers(
        database_url=database_url,
        object_root=object_root,
        information_cutoff=information_cutoff,
        observed_at=observed_at,
        event_id=first.outbox_event_id,
        competing_application=application,
    )
    interrupted_evidence = application.operations_control.get_outbox_recovery(first.outbox_event_id)

    authorization_headers = {
        "Authorization": application.local_identity.credential.authorization_header()
    }
    client: HttpClient = (
        TestClient(
            create_web_app(application),
            headers=authorization_headers,
            client=("127.0.0.1", 50000),
        )
        if base_url is None
        else Client(base_url=base_url, timeout=10.0, headers=authorization_headers)
    )
    cutoff_text = information_cutoff.isoformat().replace("+00:00", "Z")
    pending_rest = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff_text},
    )
    pending_ui = client.get(
        "/research",
        params={"information_cutoff": cutoff_text, "support": "full"},
    )

    restarted = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=deployed,
    )
    recovered = _relay_after_lease_expiry(restarted, event_id=first.outbox_event_id)
    duplicate = restarted.relay_outbox(event_id=first.outbox_event_id)
    event_after = restarted.operations_control.get_outbox_event(first.outbox_event_id)
    predictions_after = restarted.operations_control.list_prediction_records(
        trace_id=first_command.trace_id
    )
    prediction_evidence_after = restarted.operations_control.list_prediction_record_evidence(
        trace_id=first_command.trace_id
    )
    lineage_after = restarted.operations_control.get_trace_evidence(first_command.trace_id)
    audit_after = restarted.security_audit.list_events(trace_id=first_command.trace_id)
    first_recovery = restarted.operations_control.get_outbox_recovery(first.outbox_event_id)
    recovered_rest = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff_text},
    )
    recovered_ui = client.get(
        "/research",
        params={"information_cutoff": cutoff_text, "support": "full"},
    )
    client.close()

    second_cutoff = information_cutoff + timedelta(days=1)
    crashing_consumer = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=deployed,
        relay_fault=_CrashOperationsProjection(),
    )
    second = _fixture_success(
        crashing_consumer,
        FixtureEodCommand(
            information_cutoff=second_cutoff,
            trace_id="trace-p1-trace-outbox-01-consumer-crash",
            idempotency_key="p1-trace-outbox-01-consumer-crash",
        ),
    )
    consumer_failed = crashing_consumer.relay_outbox(event_id=second.outbox_event_id)
    consumer_restarted = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=deployed,
    )
    consumer_recovered = consumer_restarted.relay_outbox(event_id=second.outbox_event_id)
    second_recovery = consumer_restarted.operations_control.get_outbox_recovery(
        second.outbox_event_id
    )

    third = _fixture_success(
        consumer_restarted,
        FixtureEodCommand(
            information_cutoff=information_cutoff + timedelta(days=2),
            trace_id="trace-p1-trace-outbox-01-ordering-3",
            idempotency_key="p1-trace-outbox-01-ordering-3",
        ),
    )
    fourth = _fixture_success(
        consumer_restarted,
        FixtureEodCommand(
            information_cutoff=information_cutoff + timedelta(days=3),
            trace_id="trace-p1-trace-outbox-01-ordering-4",
            idempotency_key="p1-trace-outbox-01-ordering-4",
        ),
    )
    first_deferral = consumer_restarted.relay_outbox(event_id=fourth.outbox_event_id)
    second_deferral = consumer_restarted.relay_outbox(event_id=fourth.outbox_event_id)
    third_delivery = consumer_restarted.relay_outbox(event_id=third.outbox_event_id)
    fourth_delivery = consumer_restarted.relay_outbox(event_id=fourth.outbox_event_id)
    all_recoveries = [
        consumer_restarted.operations_control.get_outbox_recovery(event_id)
        for event_id in (
            first.outbox_event_id,
            second.outbox_event_id,
            third.outbox_event_id,
            fourth.outbox_event_id,
        )
    ]
    incidents = consumer_restarted.operations_control.list_outbox_incidents(
        aggregate_id=first.listing_id
    )

    pending_projection = pending_rest.json()["items"][0]["projection"]
    recovered_projection = recovered_rest.json()["items"][0]["projection"]
    identity_fields = (
        "event_id",
        "event_type",
        "schema_version",
        "aggregate_id",
        "aggregate_version",
        "producer",
        "trace_id",
    )
    checks = {
        "canonical_commit_before_consumers": relay_returncode != 0
        and competing_status == "busy"
        and event_before["delivery_status"] == "pending"
        and interrupted_evidence["consumer_effect_counts"]
        == {"research_projection": 0, "operations_projection": 0}
        and len(predictions_before) == 3,
        "original_event_identity_recovered": recovered.status == "delivered"
        and all(event_before[field] == event_after[field] for field in identity_fields),
        "consumer_transaction_recovered": consumer_failed.status == "failed"
        and consumer_recovered.status == "delivered"
        and [attempt["status"] for attempt in second_recovery["delivery_attempts"]]
        == ["failed", "delivered"],
        "duplicate_delivery_idempotent": duplicate.status == "already_delivered",
        "out_of_order_deferred": first_deferral.status == "deferred"
        and second_deferral.status == "deferred"
        and third_delivery.status == "delivered"
        and fourth_delivery.status == "delivered",
        "rest_projection_stale_then_fresh": pending_projection["stale"] is True
        and pending_projection["evidence_projection_version"] == 0
        and recovered_projection["stale"] is False
        and recovered_projection["evidence_projection_version"]
        == recovered_projection["core_projection_version"]
        and pending_rest.json()["items"][0]["predictions"] == predictions_before
        and recovered_rest.json()["items"][0]["predictions"] == predictions_after,
        "ui_projection_stale_then_fresh": "投影狀態：等待恢復" in pending_ui.text
        and "投影狀態：已同步" in recovered_ui.text,
        "canonical_state_immutable": predictions_after == predictions_before
        and prediction_evidence_after == prediction_evidence_before
        and len(prediction_evidence_before) == 3
        and all(evidence["prediction_id"] for evidence in prediction_evidence_before)
        and all(len(evidence["content_digest"]) == 64 for evidence in prediction_evidence_before)
        and lineage_after["lineage_ids"] == lineage_before["lineage_ids"]
        and lineage_after["artifact_content_digests"] == lineage_before["artifact_content_digests"]
        and all(
            lineage_after["lineage_ids"][field] == lineage_before["lineage_ids"][field]
            for field in ("feature_snapshot_id", "serving_assignment_id")
        )
        and lineage_after["audit_events"][0] == lineage_before["audit_events"][0]
        and bool(lineage_before["audit_events"][0]["event_id"])
        and audit_after[0] == audit_before[0],
        "operations_recovery_evidence": [
            attempt["status"] for attempt in first_recovery["delivery_attempts"]
        ]
        == ["superseded", "delivered"]
        and first_recovery["delivery_attempts"][0]["reason_code"] == "relay_lease_superseded"
        and [event["action"] for event in audit_after]
        == ["fixture_pipeline.execute", "outbox_recovery", "outbox_delivery"]
        and [attempt["fencing_token"] for attempt in first_recovery["delivery_attempts"]] == [1, 2]
        and all(attempt["worker_id"] for attempt in first_recovery["delivery_attempts"]),
        "single_correlated_incident": len(incidents) == 3
        and all(incident["status"] == "monitoring" for incident in incidents)
        and all(incident["severity"] == "SEV3" for incident in incidents)
        and all(incident["owner"] == "operations_control" for incident in incidents)
        and all(
            incident["impact_scope"]
            == f"listing:{first.listing_id}:research_and_operations_projection"
            for incident in incidents
        )
        and len(
            [
                incident
                for incident in incidents
                if incident["reason_code"] == "out_of_order_aggregate_version"
                and incident["occurrence_count"] == 2
            ]
        )
        == 1,
        "zero_lost_or_duplicate_effects": all(
            recovery["consumer_effect_counts"]
            == {"research_projection": 1, "operations_projection": 1}
            for recovery in all_recoveries
        ),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "trace_ids": ["P1-TRACE-OUTBOX-01"],
        "execution_purpose": "fixture",
        "formal_prediction": False,
        "event_id": first.outbox_event_id,
        "checks": checks,
    }


def run_ticket_04(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    observed_at: datetime,
    base_url: str | None = None,
    dagster_url: str | None = None,
    denied_base_url: str | None = None,
) -> dict[str, Any]:
    _validate_deployment_endpoints(base_url, dagster_url)
    deployed = base_url is not None
    if deployed != (denied_base_url is not None):
        raise ValueError("denied_deployment_endpoint_must_match_deployment_mode")
    active_application = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=deployed,
    )

    markets: tuple[FixtureMarket, ...] = ("XTAI", "XNAS")
    active_commands: dict[FixtureMarket, FixtureEodCommand] = {
        market: FixtureEodCommand(
            information_cutoff=information_cutoff,
            trace_id=f"trace-p1-trace-auth-01-active-{market.lower()}",
            idempotency_key=f"p1-trace-auth-01-active-{market.lower()}",
            market=market,
        )
        for market in markets
    }
    active_outcomes = {
        market: _fixture_success(active_application, command)
        for market, command in active_commands.items()
    }
    object_files_after_allow = {path for path in object_root.rglob("*") if path.is_file()}

    def policy_application(
        *,
        policy_set_id: str,
        status: EntitlementStatus,
        purposes: frozenset[str],
        grant_actions: frozenset[str] | None = None,
        policy_markets: frozenset[str] | None = None,
    ) -> Application:
        if deployed:
            return build_application(
                observed_at=observed_at,
                object_root=object_root,
                database_url=database_url,
                local_identity=active_application.local_identity,
                authorization_policy_set_id=policy_set_id,
            )
        return build_test_application(
            observed_at=observed_at,
            object_root=object_root,
            database_url=database_url,
            local_identity=active_application.local_identity,
            entitlement_states={"XTAI": status},
            entitlement_purposes={"XTAI": purposes},
            grant_actions=grant_actions,
            policy_markets=policy_markets,
        )

    matrix_cases = (
        _AuthorizationMatrixCase(
            status="suspended",
            purposes=frozenset({"fixture_research"}),
            expected_reason="source_entitlement_suspended",
            policy_set_id=FIXTURE_SUSPENDED_POLICY_SET,
        ),
        _AuthorizationMatrixCase(
            status="expired",
            purposes=frozenset({"fixture_research"}),
            expected_reason="source_entitlement_expired",
            policy_set_id=FIXTURE_EXPIRED_POLICY_SET,
        ),
        _AuthorizationMatrixCase(
            status="revoked",
            purposes=frozenset({"fixture_research"}),
            expected_reason="source_entitlement_revoked",
            policy_set_id=FIXTURE_REVOKED_POLICY_SET,
        ),
        _AuthorizationMatrixCase(
            status="active",
            purposes=frozenset(),
            expected_reason="source_entitlement_purpose_denied",
            policy_set_id=FIXTURE_PURPOSE_REMOVED_POLICY_SET,
        ),
        _AuthorizationMatrixCase(
            status="active",
            purposes=frozenset({"fixture_research"}),
            expected_reason="action_grant_missing",
            policy_set_id=FIXTURE_GRANT_MISSING_POLICY_SET,
            grant_actions=frozenset(),
        ),
        _AuthorizationMatrixCase(
            status="active",
            purposes=frozenset({"fixture_research"}),
            expected_reason="source_policy_unknown",
            policy_set_id=FIXTURE_POLICY_UNKNOWN_SET,
            policy_markets=frozenset({"XNAS"}),
        ),
    )
    matrix_results: dict[str, bool] = {}
    matrix_audits: list[dict[str, Any]] = []
    denied_traces: list[str] = []
    revoked_application: Application | None = None
    for case in matrix_cases:
        candidate = policy_application(
            policy_set_id=case.policy_set_id,
            status=case.status,
            purposes=case.purposes,
            grant_actions=case.grant_actions,
            policy_markets=case.policy_markets,
        )
        if case.status == "revoked":
            revoked_application = candidate
        trace_id = f"trace-p1-trace-auth-01-{case.expected_reason}"
        denied_traces.append(trace_id)
        outcome = candidate.run_fixture_eod(
            FixtureEodCommand(
                information_cutoff=information_cutoff,
                trace_id=trace_id,
                idempotency_key=trace_id,
                market="XTAI",
            )
        )
        if isinstance(outcome, PolicyDeniedOutcome):
            event = candidate.security_audit.list_events(trace_id=trace_id)[0]
            matrix_audits.append(event)
            matrix_results[case.expected_reason] = (
                outcome.code == "authorization_denied"
                and outcome.correlation_id == trace_id
                and event["reason_code"] == case.expected_reason
            )
        else:
            matrix_results[case.expected_reason] = False
    assert revoked_application is not None

    if deployed:
        platform_admin_key_file = os.environ.get("PLATFORM_ADMIN_API_KEY_FILE")
        if platform_admin_key_file is None:
            raise RuntimeError("PLATFORM_ADMIN_API_KEY_FILE is required for deployed acceptance")
        administrative_identity = LocalApiKeyIdentity.load(Path(platform_admin_key_file))
    else:
        administrative_identity = LocalApiKeyIdentity.issue(
            owner="platform-admin",
            environment=active_application.security_context.environment,
            scopes={"fixture_pipeline.execute", "research_prediction.read"},
            issued_at=active_application.security_context.issued_at,
            expires_at=active_application.security_context.expires_at,
        )
    administrative_application = (
        build_application(
            observed_at=observed_at,
            object_root=object_root,
            database_url=database_url,
            local_identity=administrative_identity,
            authorization_policy_set_id=FIXTURE_REVOKED_POLICY_SET,
        )
        if deployed
        else build_test_application(
            observed_at=observed_at,
            object_root=object_root,
            database_url=database_url,
            local_identity=administrative_identity,
            entitlement_states={"XTAI": "revoked"},
        )
    )
    administrative_trace = "trace-p1-trace-auth-01-platform-admin-denied"
    administrative_outcome = administrative_application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=information_cutoff,
            trace_id=administrative_trace,
            idempotency_key=administrative_trace,
            market="XTAI",
        )
    )
    administrative_audit = administrative_application.security_audit.list_events(
        trace_id=administrative_trace
    )

    query_trace = "trace-p1-trace-auth-01-existing-projection-denied"
    query_outcome = revoked_application.research_query.get_listing_research(
        listing_id=active_outcomes["XTAI"].listing_id,
        information_cutoff=information_cutoff,
        trace_id=query_trace,
    )
    existing_projection_denied = isinstance(query_outcome, PolicyDeniedOutcome)
    restored_record = _listing_success(
        active_application.research_query,
        listing_id=active_outcomes["XTAI"].listing_id,
        information_cutoff=information_cutoff,
        trace_id="trace-p1-trace-auth-01-existing-projection-restored",
    )

    authorization_headers = {
        "Authorization": active_application.local_identity.credential.authorization_header()
    }
    client: HttpClient = (
        TestClient(
            create_web_app(revoked_application),
            headers=authorization_headers,
            client=("127.0.0.1", 50000),
        )
        if denied_base_url is None
        else Client(base_url=denied_base_url, timeout=10.0, headers=authorization_headers)
    )
    cutoff_text = information_cutoff.isoformat().replace("+00:00", "Z")
    rest_trace = "trace-p1-trace-auth-01-rest-denied"
    rest_response = client.get(
        f"/api/v1/research/listings/{active_outcomes['XTAI'].listing_id}",
        params={"information_cutoff": cutoff_text},
        headers={**authorization_headers, "X-Trace-Id": rest_trace},
    )
    ui_trace = "trace-p1-trace-auth-01-ui-denied"
    ui_response = client.get(
        "/research",
        params={"information_cutoff": cutoff_text},
        headers={**authorization_headers, "X-Trace-Id": ui_trace},
    )
    client.close()

    dagster_trace = "trace-p1-trace-auth-01-dagster-denied"
    dagster_materialization = materialize(
        [xtai_fixture_eod_asset],
        resources={
            "fixture_runner": ResourceDefinition.hardcoded_resource(
                FixtureRunner(
                    revoked_application,
                    FixtureEodCommand(
                        information_cutoff=information_cutoff,
                        trace_id=dagster_trace,
                        idempotency_key=dagster_trace,
                        market="XTAI",
                    ),
                )
            )
        },
        raise_on_error=False,
    )
    dagster_audit = revoked_application.security_audit.list_events(trace_id=dagster_trace)
    active_audits = [
        active_application.security_audit.list_events(trace_id=command.trace_id)[0]
        for command in active_commands.values()
    ]
    object_files_after_denial = {path for path in object_root.rglob("*") if path.is_file()}
    denial_prediction_count = sum(
        len(revoked_application.state_store.list_prediction_records(trace_id=trace_id))
        for trace_id in denied_traces + [dagster_trace]
    )
    rest_payload = rest_response.json()
    ui_payload = ui_response.json()
    checks = {
        "shared_security_context": all(
            audit["principal_id"] == active_application.security_context.principal_id
            for audit in active_audits + matrix_audits
        ),
        "active_entitlements_allow": all(
            outcome.status == "succeeded" for outcome in active_outcomes.values()
        )
        and all(audit["outcome"] == "allowed" for audit in active_audits),
        "same_grant_denial": (
            active_application.authorization_policy.action_grants[0].version_id
            == revoked_application.authorization_policy.action_grants[0].version_id
            and all(
                audit["grant_version_id"]
                == active_application.authorization_policy.action_grants[0].version_id
                for audit in matrix_audits
                if str(audit["reason_code"]).startswith("source_entitlement_")
            )
        ),
        "decision_matrix_fail_closed": all(matrix_results.values())
        and set(matrix_results) == {case.expected_reason for case in matrix_cases},
        "administrative_identity_denied": isinstance(administrative_outcome, PolicyDeniedOutcome)
        and len(administrative_audit) == 1
        and administrative_audit[0]["outcome"] == "denied"
        and administrative_audit[0]["reason_code"] == "source_entitlement_revoked",
        "denial_before_persistence": object_files_after_denial == object_files_after_allow
        and denial_prediction_count == 0,
        "existing_projection_blocked_not_deleted": existing_projection_denied
        and restored_record["identity"]["listing_id"] == active_outcomes["XTAI"].listing_id,
        "rest_problem_redacted": rest_response.status_code == 403
        and rest_payload["code"] == "authorization_denied"
        and rest_payload["trace_id"] == rest_trace
        and "source_entitlement" not in rest_response.text,
        "ui_problem_redacted": ui_response.status_code == 403
        and ui_payload["code"] == "authorization_denied"
        and active_outcomes["XTAI"].listing_id not in ui_response.text
        and "source_entitlement" not in ui_response.text,
        "dagster_denial": dagster_materialization.success
        and dagster_materialization.output_for_node("xtai_fixture_eod")["status"] == "policy_denied"
        and len(dagster_audit) == 1
        and dagster_audit[0]["reason_code"] == "source_entitlement_revoked",
        "audit_decision_evidence": all(
            event.get("evaluation_id")
            and event.get("decision_id")
            and event.get("correlation_id")
            and event.get("credential_id")
            and event.get("authentication_method") == "local_api_key"
            and event.get("dataset_id") in {"xtai-fixture-eod", "xnas-fixture-eod"}
            and event.get("evaluated_at")
            and event.get("valid_until")
            and event.get("grant_version_id")
            and event.get("source_entitlement_version_id")
            and (
                (
                    event.get("source_policy_version_id") is None
                    and event.get("data_protection_class") is None
                    and event.get("reason_code") == "source_policy_unknown"
                )
                or (
                    event.get("source_policy_version_id")
                    and event.get("data_protection_class") == "internal"
                )
            )
            for event in active_audits + matrix_audits + dagster_audit
        ),
    }
    if deployed:
        checks["application_database_role_least_privilege"] = (
            active_application.state_store.authorization_policy_sets_are_read_only_for_current_role()
        )
    if dagster_url is not None:
        deployed_dagster_trace = "trace-p1-trace-auth-01-deployed-dagster-denied"
        deployed_materialization = materialize_deployed_asset(
            dagster_url,
            location_name="stock_forecasting_denied",
            asset_name="xtai_fixture_eod",
        )
        deployed_dagster_audit = revoked_application.security_audit.list_events(
            trace_id=deployed_dagster_trace
        )
        checks["dagster_denial"] = (
            checks["dagster_denial"]
            and inspect_dagster_deployment(dagster_url).ready
            and deployed_materialization
            and len(deployed_dagster_audit) == 1
            and deployed_dagster_audit[0]["outcome"] == "denied"
            and deployed_dagster_audit[0]["reason_code"] == "source_entitlement_revoked"
        )
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "trace_ids": ["P1-TRACE-AUTH-01"],
        "execution_purpose": "fixture",
        "formal_source_qualified": False,
        "formal_prediction": False,
        "checks": checks,
    }
