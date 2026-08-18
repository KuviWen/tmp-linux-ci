from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol
from uuid import uuid4

from dagster import ResourceDefinition, materialize
from fastapi.testclient import TestClient
from httpx import Client
from sqlalchemy import text

from stock_forecasting.acceptance_bundle import (
    P1_FAILURE_EVIDENCE_CATALOG,
    P1_HARD_GATE_OWNERS,
    P1_REPRODUCTION_COMMAND,
    P1_SCENARIO_OWNERS,
    P1_TRACE_IDS,
    P1AcceptanceBundlePublisher,
    P1AcceptanceEvaluation,
    P1GateResult,
    digest_required_paths,
    is_sha256_reference,
    p1_acceptance_bundle_envelope_is_valid,
)
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
from stock_forecasting.platform.object_repository import (
    FilesystemObjectRepository,
    ObjectIntegrityError,
)
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


class _PreviousAcceptanceBundleInvalid(RuntimeError):
    def __init__(self, reference: str | None) -> None:
        super().__init__("previous_acceptance_bundle_invalid")
        self.reference = reference


def _validated_previous_bundle_reference(path: Path) -> str:
    content = path.read_bytes()
    reference = f"sha256:{hashlib.sha256(content).hexdigest()}"
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _PreviousAcceptanceBundleInvalid(reference) from error
    if not p1_acceptance_bundle_envelope_is_valid(payload):
        raise _PreviousAcceptanceBundleInvalid(reference)
    return reference


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


class _MutableRelayClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class _ExpireResearchConsumerLease(RelayFault):
    def __init__(self, clock: _MutableRelayClock) -> None:
        self._clock = clock

    def before_consumers(self, event_id: str) -> None:
        pass

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None:
        if consumer_name == "research_projection":
            self._clock.advance(timedelta(seconds=3))

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
    scenario_evidence: dict[str, dict[str, object]] = {}
    for scenario, record in scenario_records.items():
        source_evidence = record["source_evidence"]
        evidence_ids = [
            value
            for key in (
                "raw_artifact_id",
                "source_record_version_id",
                "supersedes",
                "duplicate_of",
            )
            if isinstance((value := source_evidence.get(key)), str)
        ]
        observed_reason: str | None
        if scenario == "duplicate":
            verified = source_evidence["duplicate_of"] == xnas_source_version
            observed_reason = "duplicate_source_record"
        elif scenario == "correction":
            verified = (
                source_evidence["supersedes"] == xnas_source_version
                and record["lineage"]["feature_snapshot_id"]
                != research["XNAS"]["lineage"]["feature_snapshot_id"]
            )
            observed_reason = "correction_superseded"
        else:
            observed_reasons = {
                prediction["unavailable_reason"]["code"] for prediction in record["predictions"]
            }
            observed_reason = next(iter(observed_reasons)) if len(observed_reasons) == 1 else None
            verified = observed_reason == expected_unavailable[scenario]
        scenario_evidence[scenario] = {
            "evidence_ids": evidence_ids,
            "observed_reason": observed_reason,
            "verified": verified,
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
        "scenario_evidence": scenario_evidence,
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
        state_store = active_application.state_store
        checks["application_database_role_least_privilege"] = (
            state_store.authorization_policy_sets_are_read_only_for_current_role()
            and state_store.model_lifecycle_events_are_append_only_for_current_role()
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


def _git_commit(git_dir: Path) -> str:
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    reference = head.removeprefix("ref: ")
    loose_reference = git_dir / reference
    if loose_reference.exists():
        return loose_reference.read_text(encoding="utf-8").strip()
    packed_refs = git_dir / "packed-refs"
    if packed_refs.exists():
        for line in packed_refs.read_text(encoding="utf-8").splitlines():
            if line.endswith(f" {reference}"):
                return line.split(" ", 1)[0]
    raise RuntimeError("git_commit_unavailable")


def _contract_result(checks: dict[str, bool]) -> dict[str, object]:
    evidence = json.dumps(checks, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return {
        "checks": checks,
        "evidence_digest": f"sha256:{hashlib.sha256(evidence).hexdigest()}",
        "status": "passed" if checks and all(checks.values()) else "failed",
    }


def _load_counterpart_platform_results(path: Path) -> dict[str, dict[str, object]]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    platform_runs = bundle.get("platform_runs")
    if not isinstance(platform_runs, dict):
        raise ValueError("counterpart_platform_evidence_missing")
    results: dict[str, dict[str, object]] = {}
    for platform, run in platform_runs.items():
        if not isinstance(platform, str) or not isinstance(run, dict):
            raise ValueError("counterpart_platform_evidence_invalid")
        evidence = run.get("evidence")
        reference = run.get("evidence_reference")
        if not isinstance(evidence, dict) or not isinstance(reference, str):
            raise ValueError("counterpart_platform_evidence_invalid")
        evidence_content = json.dumps(
            {
                "evidence": evidence,
                "platform": platform,
                "schema_version": "p1-platform-run-v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if reference != f"sha256:{hashlib.sha256(evidence_content).hexdigest()}":
            raise ValueError("counterpart_platform_evidence_checksum_mismatch")
        results[platform] = evidence
    return results


def _p1_research_goldens(
    application: Application,
    *,
    base_url: str | None,
    information_cutoff: datetime,
    listing_id: str,
) -> dict[str, object]:
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
    rest_response = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff_text},
    )
    matrix_response = client.get(
        "/research",
        params={
            "information_cutoff": cutoff_text,
            "horizon": 5,
            "market": "all",
            "support": "full",
            "sort": "confidence_desc",
        },
    )
    detail_params = {
        "information_cutoff": cutoff_text,
        "horizon": 5,
        "market": "all",
        "support": "full",
        "sort": "confidence_desc",
        "tab": "lineage",
    }
    detail_response = client.get(
        f"/research/listings/{listing_id}",
        params=detail_params,
    )
    detail_reload = client.get(
        f"/research/listings/{listing_id}",
        params=detail_params,
    )
    client.close()
    rest_payload = rest_response.json() if rest_response.status_code == 200 else {"items": []}
    items = rest_payload["items"]
    visible_text = matrix_response.text + detail_response.text
    observable = (
        rest_response.status_code == 200
        and matrix_response.status_code == 200
        and detail_response.status_code == 200
        and detail_response.text == detail_reload.text
        and {item["market"] for item in items} == {"XTAI", "XNAS"}
        and all(
            {prediction["horizon_sessions"] for prediction in item["predictions"]} == {1, 5, 20}
            for item in items
        )
        and '<html lang="zh-Hant">' in visible_text
        and "Fixture／非正式預測" in visible_text
        and "資訊截止點" in visible_text
        and "上漲" in visible_text
        and "盤整" in visible_text
        and "下跌" in visible_text
        and "信心" in visible_text
        and "資料支援" in visible_text
        and "FeatureSnapshot" in visible_text
        and "ModelArtifact" in visible_text
        and "服務指派" in visible_text
        and "資料集版本" in visible_text
        and "原始資料物件" in visible_text
        and "<a href=" in visible_text
        and "a:focus-visible" in visible_text
    )
    return {
        "observable": observable,
        "phase_boundaries": rest_payload.get("phase_boundaries", {}),
        "rest_digest": f"sha256:{hashlib.sha256(rest_response.content).hexdigest()}",
        "ui_digest": f"sha256:{hashlib.sha256(visible_text.encode('utf-8')).hexdigest()}",
    }


def _p1_stale_fencing_probe(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    deployed: bool,
) -> dict[str, object]:
    clock = _MutableRelayClock(information_cutoff)
    if deployed:
        key_file = os.environ.get("LOCAL_API_KEY_FILE")
        if key_file is None:
            raise RuntimeError("LOCAL_API_KEY_FILE is required for deployed acceptance")
        identity = LocalApiKeyIdentity.load(Path(key_file))
        expired_worker = build_application(
            observed_at=information_cutoff,
            object_root=object_root,
            database_url=database_url,
            relay_clock=clock,
            relay_worker_id="p1-expired-worker",
            relay_fault=_ExpireResearchConsumerLease(clock),
            local_identity=identity,
            authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
        )
    else:
        expired_worker = build_test_application(
            observed_at=information_cutoff,
            object_root=object_root,
            database_url=database_url,
            relay_clock=clock,
            relay_worker_id="p1-expired-worker",
            relay_fault=_ExpireResearchConsumerLease(clock),
            authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
        )
        identity = expired_worker.local_identity
    outcome = _fixture_success(
        expired_worker,
        FixtureEodCommand(
            information_cutoff=information_cutoff,
            trace_id="trace-p1-exit-01-stale-fencing",
            idempotency_key="p1-exit-01-stale-fencing",
        ),
    )
    lease_lost = expired_worker.relay_outbox(event_id=outcome.outbox_event_id)
    after_expiry = expired_worker.operations_control.get_outbox_recovery(outcome.outbox_event_id)
    replacement = (
        build_application(
            observed_at=information_cutoff,
            object_root=object_root,
            database_url=database_url,
            relay_clock=clock,
            relay_worker_id="p1-replacement-worker",
            local_identity=identity,
            authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
        )
        if deployed
        else build_test_application(
            observed_at=information_cutoff,
            object_root=object_root,
            database_url=database_url,
            relay_clock=clock,
            relay_worker_id="p1-replacement-worker",
            local_identity=identity,
            authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
        )
    )
    recovered = replacement.relay_outbox(event_id=outcome.outbox_event_id)
    after_recovery = replacement.operations_control.get_outbox_recovery(outcome.outbox_event_id)
    verified = (
        lease_lost.status == "busy"
        and after_expiry["consumer_effect_counts"]
        == {"research_projection": 0, "operations_projection": 0}
        and recovered.status == "delivered"
        and [attempt["fencing_token"] for attempt in after_recovery["delivery_attempts"]] == [1, 2]
        and after_recovery["consumer_effect_counts"]
        == {"research_projection": 1, "operations_projection": 1}
    )
    probe_content = json.dumps(
        {
            "consumer_effect_counts": after_recovery["consumer_effect_counts"],
            "event_id": outcome.outbox_event_id,
            "fencing_tokens": [
                attempt["fencing_token"] for attempt in after_recovery["delivery_attempts"]
            ],
            "scenario": "stale_fencing",
            "verified": verified,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    probe_reference = replacement.object_repository.put_verified(
        BytesIO(probe_content),
        expected_checksum=hashlib.sha256(probe_content).hexdigest(),
        metadata={
            "event_id": outcome.outbox_event_id,
            "media_type": "application/vnd.stock-forecasting.acceptance-probe+json",
            "scenario": "stale_fencing",
        },
    )
    return {
        "evidence_reference": probe_reference.object_id,
        "verified": verified,
    }


def _run_ticket_05(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    observed_at: datetime,
    project_root: Path,
    git_dir: Path,
    base_url: str | None = None,
    dagster_url: str | None = None,
    denied_base_url: str | None = None,
    previous_bundle_reference: str | None = None,
    platform_name: str | None = None,
    container_image_digest: str | None = None,
    counterpart_bundle: Path | None = None,
) -> dict[str, Any]:
    _validate_deployment_endpoints(base_url, dagster_url)
    deployed = base_url is not None
    if deployed != (denied_base_url is not None):
        raise ValueError("denied_deployment_endpoint_must_match_deployment_mode")

    ticket_03 = run_ticket_03(
        database_url=database_url,
        object_root=object_root,
        information_cutoff=information_cutoff + timedelta(days=10),
        observed_at=observed_at + timedelta(days=10),
        base_url=base_url,
        dagster_url=dagster_url,
    )
    stale_fencing_probe = _p1_stale_fencing_probe(
        database_url=database_url,
        object_root=object_root,
        information_cutoff=information_cutoff + timedelta(days=30),
        deployed=deployed,
    )
    ticket_02 = run_ticket_02(
        database_url=database_url,
        object_root=object_root,
        information_cutoff=information_cutoff,
        observed_at=observed_at,
        base_url=base_url,
        dagster_url=dagster_url,
    )
    ticket_04 = run_ticket_04(
        database_url=database_url,
        object_root=object_root,
        information_cutoff=information_cutoff + timedelta(days=20),
        observed_at=observed_at + timedelta(days=20),
        base_url=base_url,
        dagster_url=dagster_url,
        denied_base_url=denied_base_url,
    )
    application = _build_acceptance_application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        deployed=deployed,
    )

    trace_evidence = {
        "XTAI": application.operations_control.get_trace_evidence("trace-p1-trace-tw-01"),
        "XNAS": application.operations_control.get_trace_evidence("trace-p1-trace-us-01"),
    }
    research = {
        market: _listing_success(
            application.research_query,
            listing_id=listing_id,
            information_cutoff=information_cutoff,
        )
        for market, listing_id in ticket_02["listing_ids"].items()
    }
    promotion_decision = application.attempt_fixture_use(
        FixtureUseCommand(
            model_artifact_id=trace_evidence["XTAI"]["lineage_ids"]["model_artifact_id"],
            target="model_promotion",
            trace_id="trace-p1-exit-01-fixture-promotion",
        )
    )

    checksum_rejected = False
    try:
        application.object_repository.put_verified(
            BytesIO(b"p1-checksum-failure-probe"),
            expected_checksum="0" * 64,
            metadata={"media_type": "application/octet-stream", "source": "acceptance"},
        )
    except ObjectIntegrityError as error:
        checksum_rejected = str(error) == "checksum_mismatch"
    checksum_probe_content = json.dumps(
        {
            "actual_checksum": hashlib.sha256(b"p1-checksum-failure-probe").hexdigest(),
            "expected_checksum": "0" * 64,
            "reason": "checksum_mismatch",
            "scenario": "checksum_failure",
            "verified": checksum_rejected,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    checksum_probe_reference = application.object_repository.put_verified(
        BytesIO(checksum_probe_content),
        expected_checksum=hashlib.sha256(checksum_probe_content).hexdigest(),
        metadata={
            "media_type": "application/vnd.stock-forecasting.acceptance-probe+json",
            "scenario": "checksum_failure",
        },
    )
    smoke_content = b'{"probe":"p1-resource-smoke"}'
    smoke_checksum = hashlib.sha256(smoke_content).hexdigest()
    smoke_reference = application.object_repository.put_verified(
        BytesIO(smoke_content),
        expected_checksum=smoke_checksum,
        metadata={"media_type": "application/json", "source": "acceptance"},
    )
    object_round_trip = application.object_repository.open(smoke_reference).read() == smoke_content
    duplicate_smoke_reference = application.object_repository.put_verified(
        BytesIO(smoke_content),
        expected_checksum=smoke_checksum,
        metadata={"media_type": "application/json", "source": "acceptance"},
    )
    object_content_addressing = duplicate_smoke_reference == smoke_reference
    with application.state_store.engine.connect() as connection:
        database_ready = connection.execute(text("SELECT 1")).scalar_one() == 1

    goldens = _p1_research_goldens(
        application,
        base_url=base_url,
        information_cutoff=information_cutoff,
        listing_id=ticket_02["listing_ids"]["XNAS"],
    )

    observed_phase_boundaries = goldens["phase_boundaries"]
    if not isinstance(observed_phase_boundaries, dict):
        raise ValueError("phase_boundary_contract_invalid")
    optional_modalities: dict[str, Any] = observed_phase_boundaries
    optional_absence_explicit = all(
        prediction["prediction_status"] == "full"
        and "probabilities" in prediction
        and prediction["data_support"] == {"price_volume": "full"}
        for market_research in research.values()
        for prediction in market_research["predictions"]
    ) and all(
        support
        == {
            "status": "unavailable",
            "reason": "phase_1_optional_modality_out_of_scope",
        }
        for support in optional_modalities.values()
    )

    scenario_checks = {
        "checksum_failure": checksum_rejected,
        "correction": ticket_02["scenario_evidence"]["correction"]["verified"],
        "duplicate_collection": ticket_02["scenario_evidence"]["duplicate"]["verified"],
        "fixture_promotion_attempt": promotion_decision["code"] == "fixture_use_forbidden",
        "late_data": ticket_02["scenario_evidence"]["late"]["verified"],
        "missing_calendar": ticket_02["scenario_evidence"]["missing_calendar"]["verified"],
        "missing_company_action": ticket_02["scenario_evidence"]["missing_company_action"][
            "verified"
        ],
        "necessary_modality_missing": ticket_02["scenario_evidence"]["missing"]["verified"],
        "one_market_failure": ticket_02["checks"]["one_market_failure_isolated"],
        "optional_modalities_missing": optional_absence_explicit,
        "outbox_redelivery": ticket_03["checks"]["duplicate_delivery_idempotent"],
        "outbox_restart": ticket_03["checks"]["original_event_identity_recovered"],
        "stale_fencing": stale_fencing_probe["verified"],
        "withdrawal": ticket_02["scenario_evidence"]["withdrawal"]["verified"],
    }
    scenario_results = {
        scenario: {
            "status": "passed" if passed else "failed",
            "reason": f"{scenario}_{'verified' if passed else 'failed'}",
            "owner": P1_SCENARIO_OWNERS[scenario],
        }
        for scenario, passed in scenario_checks.items()
    }

    actual_postgresql = database_url.startswith("postgresql+") and database_ready
    dagster_ready = dagster_url is not None and inspect_dagster_deployment(dagster_url).ready
    deploy_passed = (
        deployed
        and actual_postgresql
        and object_round_trip
        and dagster_ready
        and ticket_04["checks"].get("application_database_role_least_privilege", False)
    )
    gate_conditions = {
        "GATE-POLICY-01": ticket_04["status"] == "passed",
        "GATE-PIT-01": ticket_02["checks"]["adversarial_scenarios"],
        "GATE-DATA-01": ticket_02["status"] == "passed" and checksum_rejected and object_round_trip,
        "GATE-MODEL-01": scenario_checks["fixture_promotion_attempt"]
        and ticket_02["checks"]["no_production_prediction_records"],
        "GATE-SEC-01": ticket_04["status"] == "passed",
        "GATE-OPS-01": ticket_03["status"] == "passed" and stale_fencing_probe["verified"] is True,
        "GATE-DEPLOY-01": deploy_passed,
        "GATE-UX-01": bool(goldens["observable"]) and optional_absence_explicit,
    }
    gate_results = tuple(
        P1GateResult(
            trace_id=trace_id,
            status=(
                "passed" if passed else "blocked" if trace_id == "GATE-DEPLOY-01" else "failed"
            ),
            reason=(
                f"{trace_id.lower()}_passed"
                if passed
                else "deployed_endpoints_required"
                if trace_id == "GATE-DEPLOY-01" and not deployed
                else f"{trace_id.lower()}_failed"
            ),
            owner=P1_HARD_GATE_OWNERS[trace_id],
        )
        for trace_id, passed in gate_conditions.items()
    )

    source_policy_ids: list[str] = []
    manifest_ids: list[str] = []
    fixture_digests: dict[str, str] = {}
    for market, evidence in trace_evidence.items():
        artifacts = dict(zip(evidence["artifact_kinds"], evidence["artifact_ids"], strict=False))
        source_policy_ids.append(artifacts["source_policy_version"])
        manifest_id = evidence["lineage_ids"]["dataset_version_id"]
        manifest_ids.append(manifest_id)
        raw_artifact_id = artifacts["raw_artifact"]
        fixture_digests[market] = f"sha256:{evidence['artifact_content_digests'][raw_artifact_id]}"

    failure_outcomes: dict[str, tuple[bool, str, str, str, list[str]]] = {
        "late_data": (
            bool(scenario_checks["late_data"]),
            *P1_FAILURE_EVIDENCE_CATALOG["late_data"],
            list(ticket_02["scenario_evidence"]["late"]["evidence_ids"]),
        ),
        "necessary_modality_missing": (
            bool(scenario_checks["necessary_modality_missing"]),
            *P1_FAILURE_EVIDENCE_CATALOG["necessary_modality_missing"],
            list(ticket_02["scenario_evidence"]["missing"]["evidence_ids"]),
        ),
        "optional_modalities_missing": (
            bool(scenario_checks["optional_modalities_missing"]),
            *P1_FAILURE_EVIDENCE_CATALOG["optional_modalities_missing"],
            [str(goldens["rest_digest"])],
        ),
        "missing_calendar": (
            bool(scenario_checks["missing_calendar"]),
            *P1_FAILURE_EVIDENCE_CATALOG["missing_calendar"],
            list(ticket_02["scenario_evidence"]["missing_calendar"]["evidence_ids"]),
        ),
        "missing_company_action": (
            bool(scenario_checks["missing_company_action"]),
            *P1_FAILURE_EVIDENCE_CATALOG["missing_company_action"],
            list(ticket_02["scenario_evidence"]["missing_company_action"]["evidence_ids"]),
        ),
        "withdrawal": (
            bool(scenario_checks["withdrawal"]),
            *P1_FAILURE_EVIDENCE_CATALOG["withdrawal"],
            list(ticket_02["scenario_evidence"]["withdrawal"]["evidence_ids"]),
        ),
        "checksum_failure": (
            bool(scenario_checks["checksum_failure"]),
            *P1_FAILURE_EVIDENCE_CATALOG["checksum_failure"],
            [checksum_probe_reference.object_id],
        ),
        "stale_fencing": (
            bool(scenario_checks["stale_fencing"]),
            *P1_FAILURE_EVIDENCE_CATALOG["stale_fencing"],
            [str(stale_fencing_probe["evidence_reference"])],
        ),
        "one_market_failure": (
            bool(scenario_checks["one_market_failure"]),
            *P1_FAILURE_EVIDENCE_CATALOG["one_market_failure"],
            list(ticket_02["scenario_evidence"]["missing_calendar"]["evidence_ids"]),
        ),
        "fixture_promotion_attempt": (
            bool(scenario_checks["fixture_promotion_attempt"]),
            *P1_FAILURE_EVIDENCE_CATALOG["fixture_promotion_attempt"],
            [trace_evidence["XTAI"]["lineage_ids"]["model_artifact_id"]],
        ),
        "source_entitlement": (
            bool(ticket_04["checks"]["same_grant_denial"]),
            *P1_FAILURE_EVIDENCE_CATALOG["source_entitlement"],
            source_policy_ids,
        ),
        "outbox_restart": (
            bool(scenario_checks["outbox_restart"]),
            *P1_FAILURE_EVIDENCE_CATALOG["outbox_restart"],
            [ticket_03["event_id"]],
        ),
    }
    failure_evidence = tuple(
        {
            "evidence_ids": evidence_ids,
            "scenario": scenario,
            "status": expected_status if observed else "failed",
            "reason": expected_reason if observed else "evidence_capture_failed",
            "owner": owner,
        }
        for scenario, (
            observed,
            expected_status,
            expected_reason,
            owner,
            evidence_ids,
        ) in failure_outcomes.items()
    )
    reproduction_command = P1_REPRODUCTION_COMMAND
    contract_results = {
        "dagster_wrapper": _contract_result(
            {
                "direct_and_dagster_parity": bool(ticket_02["checks"]["dagster_parity"]),
                "deployed_wrapper_ready": dagster_ready,
            }
        ),
        "event": _contract_result(
            {
                "consumer_transaction_rollback": bool(
                    ticket_03["checks"]["consumer_transaction_recovered"]
                ),
                "relay_crash_recovery": bool(
                    ticket_03["checks"]["original_event_identity_recovered"]
                ),
                "single_consumer_effect": bool(
                    ticket_03["checks"]["zero_lost_or_duplicate_effects"]
                ),
            }
        ),
        "filesystem_object_repository": _contract_result(
            {
                "checksum_rejected": checksum_rejected,
                "content_addressed_duplicate": object_content_addressing,
                "round_trip": object_round_trip,
            }
        ),
        "fixture_market_provider": _contract_result(
            {
                "market_specific_adapter": bool(ticket_02["checks"]["market_specific_adapter"]),
                "shared_contract": bool(ticket_02["checks"]["shared_domain_contract"]),
            }
        ),
        "postgresql": _contract_result(
            {
                "authoritative_store": actual_postgresql,
                "connection_ready": database_ready,
                "lease_fencing": stale_fencing_probe["verified"] is True,
                "transaction_rollback": bool(ticket_03["checks"]["consumer_transaction_recovered"]),
            }
        ),
        "rest": _contract_result(
            {
                "phase_boundary_observed": optional_absence_explicit,
                "schema_and_surfaces_observed": bool(goldens["observable"]),
            }
        ),
    }
    git_commit = _git_commit(git_dir)
    application_payload_digest = digest_required_paths(
        project_root,
        ("src", "pyproject.toml", "requirements.lock", "openapi"),
    )
    deployment_digest = digest_required_paths(
        project_root,
        ("Dockerfile", "compose.yaml", ".dockerignore", "docker", ".github/workflows"),
    )
    migration_digest = digest_required_paths(project_root, ("alembic.ini", "migrations"))
    restart_results = {
        "outbox_recovered": ticket_03["checks"]["original_event_identity_recovered"],
        "same_event_identity": ticket_03["checks"]["original_event_identity_recovered"],
        "single_consumer_effect": ticket_03["checks"]["zero_lost_or_duplicate_effects"],
    }
    resource_smoke = {
        "api_ready": bool(goldens["observable"]),
        "dagster_ready": dagster_ready,
        "filesystem_object_round_trip": object_round_trip,
        "postgresql_ready": actual_postgresql,
        "formal_capacity_claim": False,
    }
    platform_results = (
        _load_counterpart_platform_results(counterpart_bundle)
        if counterpart_bundle is not None
        else {}
    )
    current_platform_passed = (
        platform_name in {"windows_docker_desktop", "linux_ci"}
        and deploy_passed
        and all(result["status"] == "passed" for result in contract_results.values())
        and all(result["status"] == "passed" for result in scenario_results.values())
        and all(restart_results.values())
        and all(
            result is True
            for name, result in resource_smoke.items()
            if name != "formal_capacity_claim"
        )
        and is_sha256_reference(container_image_digest)
    )
    if platform_name is not None:
        platform_results[platform_name] = {
            "application_payload_digest": application_payload_digest,
            "container_image_digest": container_image_digest,
            "contract_results": contract_results,
            "deployment_digest": deployment_digest,
            "git_commit": git_commit,
            "migration_digest": migration_digest,
            "reproduction_command": reproduction_command,
            "resource_smoke": resource_smoke,
            "restart_results": restart_results,
            "scenario_results": scenario_results,
            "status": "passed" if current_platform_passed else "blocked",
        }
    evaluation = P1AcceptanceEvaluation(
        attempt_id=f"p1-{uuid4()}",
        created_at=datetime.now(UTC),
        git_commit=git_commit,
        application_payload_digest=application_payload_digest,
        deployment_digest=deployment_digest,
        migration_digest=migration_digest,
        fixture_digests=fixture_digests,
        source_policy_ids=tuple(source_policy_ids),
        manifest_ids=tuple(manifest_ids),
        contract_results=contract_results,
        end_to_end_ids=(
            "trace-p1-trace-tw-01",
            "trace-p1-trace-us-01",
            ticket_03["event_id"],
            ticket_02["listing_ids"]["XTAI"],
            ticket_02["listing_ids"]["XNAS"],
        ),
        scenario_results=scenario_results,
        failure_evidence=failure_evidence,
        rest_golden_digest=str(goldens["rest_digest"]),
        ui_golden_digest=str(goldens["ui_digest"]),
        restart_results=restart_results,
        resource_smoke=resource_smoke,
        gate_results=gate_results,
        previous_bundle_reference=previous_bundle_reference,
        reproduction_command=reproduction_command,
        platform_results=platform_results,
    )
    bundle_reference = P1AcceptanceBundlePublisher(application.object_repository).publish(
        evaluation
    )
    bundle = json.loads(application.object_repository.open(bundle_reference).read())
    return {
        "status": bundle["status"],
        "platform_run_status": "passed" if current_platform_passed else "blocked",
        "trace_ids": list(P1_TRACE_IDS),
        "hard_gates": bundle["hard_gates"],
        "scenario_results": scenario_results,
        "optional_modalities": optional_modalities,
        "bundle": {
            "object_id": bundle_reference.object_id,
            "checksum": bundle_reference.checksum,
            "uri": bundle_reference.uri,
        },
    }


def run_ticket_05(
    *,
    database_url: str,
    object_root: Path,
    information_cutoff: datetime,
    observed_at: datetime,
    project_root: Path,
    git_dir: Path,
    base_url: str | None = None,
    dagster_url: str | None = None,
    denied_base_url: str | None = None,
    previous_bundle_reference: str | None = None,
    previous_bundle_path: Path | None = None,
    platform_name: str | None = None,
    container_image_digest: str | None = None,
    counterpart_bundle: Path | None = None,
) -> dict[str, Any]:
    resolved_previous_bundle_reference = previous_bundle_reference
    try:
        if resolved_previous_bundle_reference is not None and not is_sha256_reference(
            resolved_previous_bundle_reference
        ):
            raise _PreviousAcceptanceBundleInvalid(None)
        if resolved_previous_bundle_reference is None and previous_bundle_path is not None:
            resolved_previous_bundle_reference = _validated_previous_bundle_reference(
                previous_bundle_path
            )
        return _run_ticket_05(
            database_url=database_url,
            object_root=object_root,
            information_cutoff=information_cutoff,
            observed_at=observed_at,
            project_root=project_root,
            git_dir=git_dir,
            base_url=base_url,
            dagster_url=dagster_url,
            denied_base_url=denied_base_url,
            previous_bundle_reference=resolved_previous_bundle_reference,
            platform_name=platform_name,
            container_image_digest=container_image_digest,
            counterpart_bundle=counterpart_bundle,
        )
    except Exception as error:
        previous_bundle_invalid = isinstance(error, _PreviousAcceptanceBundleInvalid)
        failure_reason = (
            "previous_acceptance_bundle_invalid"
            if previous_bundle_invalid
            else "evidence_capture_failed"
        )
        if isinstance(error, _PreviousAcceptanceBundleInvalid):
            resolved_previous_bundle_reference = error.reference
        repository = FilesystemObjectRepository(object_root)
        reproduction_command = P1_REPRODUCTION_COMMAND
        evaluation = P1AcceptanceEvaluation(
            attempt_id=f"p1-{uuid4()}",
            created_at=datetime.now(UTC),
            git_commit="unavailable:evidence_capture_failed",
            application_payload_digest="unavailable:evidence_capture_failed",
            deployment_digest="unavailable:evidence_capture_failed",
            migration_digest="unavailable:evidence_capture_failed",
            fixture_digests={},
            source_policy_ids=(),
            manifest_ids=(),
            contract_results={"acceptance_runner": {"status": "blocked"}},
            end_to_end_ids=(),
            scenario_results={"acceptance_runner": {"status": "blocked"}},
            failure_evidence=(
                {
                    **(
                        {"evidence_ids": [resolved_previous_bundle_reference]}
                        if resolved_previous_bundle_reference is not None
                        and previous_bundle_invalid
                        else {}
                    ),
                    "owner": "platform_owner",
                    "reason": failure_reason,
                    "scenario": "acceptance_runner",
                    "stage": (
                        "previous_bundle_validation" if previous_bundle_invalid else "orchestration"
                    ),
                    "status": "exception",
                },
            ),
            rest_golden_digest="unavailable:evidence_capture_failed",
            ui_golden_digest="unavailable:evidence_capture_failed",
            restart_results={},
            resource_smoke={},
            gate_results=tuple(
                P1GateResult(
                    trace_id=trace_id,
                    status="blocked",
                    reason=failure_reason,
                    owner=owner,
                )
                for trace_id, owner in P1_HARD_GATE_OWNERS.items()
            ),
            previous_bundle_reference=resolved_previous_bundle_reference,
            reproduction_command=reproduction_command,
        )
        bundle_reference = P1AcceptanceBundlePublisher(repository).publish(evaluation)
        bundle = json.loads(repository.open(bundle_reference).read())
        return {
            "status": bundle["status"],
            "platform_run_status": "blocked",
            "trace_ids": list(P1_TRACE_IDS),
            "hard_gates": bundle["hard_gates"],
            "scenario_results": evaluation.scenario_results,
            "optional_modalities": {},
            "bundle": {
                "object_id": bundle_reference.object_id,
                "checksum": bundle_reference.checksum,
                "uri": bundle_reference.uri,
            },
        }
