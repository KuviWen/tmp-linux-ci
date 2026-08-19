from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization_repository import FIXTURE_ACTIVE_POLICY_SET
from stock_forecasting.outbox import EventCompatibility
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand
from tests.support import assert_success


class CrashOperationsConsumer:
    def before_consumers(self, event_id: str) -> None:
        pass

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None:
        if consumer_name == "operations_projection":
            raise RuntimeError("injected_consumer_transaction_crash")

    def before_ack(self, event_id: str) -> None:
        pass


class MutableRelayClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class ExpireResearchConsumerLease:
    def __init__(self, clock: MutableRelayClock) -> None:
        self._clock = clock

    def before_consumers(self, event_id: str) -> None:
        pass

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None:
        if consumer_name == "research_projection":
            self._clock.advance(timedelta(seconds=3))

    def before_ack(self, event_id: str) -> None:
        pass


def test_expired_fencing_token_cannot_commit_consumer_effects(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'expired-fence.db'}"
    object_root = tmp_path / "objects"
    clock = MutableRelayClock(cutoff)
    expired_worker = build_test_application(
        observed_at=cutoff,
        database_url=database_url,
        object_root=object_root,
        relay_clock=clock,
        relay_worker_id="expired-worker",
        relay_fault=ExpireResearchConsumerLease(clock),
    )
    outcome = assert_success(expired_worker).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-03-expired-fence",
            idempotency_key="ticket-03-expired-fence",
        )
    )

    lease_lost = expired_worker.relay_outbox(event_id=outcome.outbox_event_id)
    after_expiry = expired_worker.operations_control.get_outbox_recovery(outcome.outbox_event_id)
    replacement = build_test_application(
        observed_at=cutoff,
        database_url=database_url,
        object_root=object_root,
        relay_clock=clock,
        relay_worker_id="replacement-worker",
    )
    recovered = replacement.relay_outbox(event_id=outcome.outbox_event_id)
    after_recovery = replacement.operations_control.get_outbox_recovery(outcome.outbox_event_id)

    assert lease_lost.status == "busy"
    assert after_expiry["consumer_effect_counts"] == {
        "research_projection": 0,
        "operations_projection": 0,
    }
    assert recovered.status == "delivered"
    assert [attempt["fencing_token"] for attempt in after_recovery["delivery_attempts"]] == [1, 2]
    assert after_recovery["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 1,
    }


def test_incompatible_event_version_is_isolated_before_consumer_effects() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(
        observed_at=cutoff,
        event_compatibility=EventCompatibility(
            accepted_versions={
                "forecast_publication.completed": frozenset({"2.0.0"}),
            }
        ),
    )
    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-03-incompatible-event",
            idempotency_key="ticket-03-incompatible-event",
        )
    )
    isolated = application.relay_outbox(event_id=outcome.outbox_event_id)
    event = application.operations_control.get_outbox_event(outcome.outbox_event_id)
    recovery = application.operations_control.get_outbox_recovery(outcome.outbox_event_id)
    incidents = application.operations_control.list_outbox_incidents(
        aggregate_id=outcome.listing_id
    )

    assert isolated.status == "isolated"
    assert event["delivery_status"] == "isolated"
    assert recovery["consumer_effect_counts"] == {
        "research_projection": 0,
        "operations_projection": 0,
    }
    assert [attempt["status"] for attempt in recovery["delivery_attempts"]] == ["blocked"]
    assert incidents[0]["reason_code"] == "incompatible_event_contract"


def test_prediction_publication_is_stale_until_its_outbox_event_is_relayed() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    trace_id = "trace-ticket-03-pending"
    application = build_test_application(observed_at=cutoff)

    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key="ticket-03-pending",
        )
    )

    event = application.operations_control.get_outbox_event(outcome.outbox_event_id)
    research = assert_success(application).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )
    authoritative_predictions = application.operations_control.list_prediction_records(
        trace_id=trace_id
    )

    assert event == {
        "event_id": outcome.outbox_event_id,
        "event_type": "forecast_publication.completed",
        "schema_version": "1.0.0",
        "aggregate_id": outcome.listing_id,
        "aggregate_version": 1,
        "occurred_at": "2026-08-12T07:00:00Z",
        "producer": "forecast_execution",
        "trace_id": trace_id,
        "delivery_status": "pending",
    }
    assert research["projection"] == {
        "core_projection_version": 1,
        "evidence_projection_version": 0,
        "stale": True,
    }
    assert authoritative_predictions == research["predictions"]


def test_restarted_relay_delivers_the_original_event_once(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'relay.db'}"
    object_root = tmp_path / "objects"
    first_application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    outcome = assert_success(first_application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-03-restart",
            idempotency_key="ticket-03-restart",
        )
    )

    restarted = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    delivered = restarted.relay_outbox(event_id=outcome.outbox_event_id)

    restarted_again = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    duplicate = restarted_again.relay_outbox(event_id=outcome.outbox_event_id)
    research = assert_success(restarted_again).research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
    )
    recovery = restarted_again.operations_control.get_outbox_recovery(outcome.outbox_event_id)

    assert delivered.status == "delivered"
    assert delivered.event_id == outcome.outbox_event_id
    assert delivered.aggregate_version == outcome.outbox_aggregate_version
    assert duplicate.status == "already_delivered"
    assert research["projection"] == {
        "core_projection_version": 1,
        "evidence_projection_version": 1,
        "stale": False,
    }
    assert recovery["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 1,
    }
    assert [attempt["status"] for attempt in recovery["delivery_attempts"]] == ["delivered"]


def test_consumer_transaction_crash_retries_without_duplicate_effects(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'consumer-crash.db'}"
    object_root = tmp_path / "objects"
    crashing_application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
        relay_fault=CrashOperationsConsumer(),
    )
    outcome = assert_success(crashing_application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-03-consumer-crash",
            idempotency_key="ticket-03-consumer-crash",
        )
    )

    failed = crashing_application.relay_outbox(event_id=outcome.outbox_event_id)
    after_failure = crashing_application.operations_control.get_outbox_recovery(
        outcome.outbox_event_id
    )

    restarted = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    recovered = restarted.relay_outbox(event_id=outcome.outbox_event_id)
    after_recovery = restarted.operations_control.get_outbox_recovery(outcome.outbox_event_id)

    assert failed.status == "failed"
    assert after_failure["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 0,
    }
    assert recovered.status == "delivered"
    assert after_recovery["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 1,
    }
    assert [attempt["status"] for attempt in after_recovery["delivery_attempts"]] == [
        "failed",
        "delivered",
    ]
    assert [attempt["work_status"] for attempt in after_recovery["delivery_attempts"]] == [
        "failed",
        "succeeded",
    ]


def test_out_of_order_versions_defer_and_share_one_incident(tmp_path: Path) -> None:
    first_cutoff = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)
    second_cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(
        observed_at=second_cutoff,
        object_root=tmp_path / "objects",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ordering.db'}",
    )
    first = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=first_cutoff,
            trace_id="trace-ticket-03-ordering-1",
            idempotency_key="ticket-03-ordering-1",
        )
    )
    second = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=second_cutoff,
            trace_id="trace-ticket-03-ordering-2",
            idempotency_key="ticket-03-ordering-2",
        )
    )

    first_deferral = application.relay_outbox(event_id=second.outbox_event_id)
    second_deferral = application.relay_outbox(event_id=second.outbox_event_id)
    still_stale = assert_success(application).research_query.get_listing_research(
        listing_id=second.listing_id,
        information_cutoff=second_cutoff,
    )

    first_delivery = application.relay_outbox(event_id=first.outbox_event_id)
    second_delivery = application.relay_outbox(event_id=second.outbox_event_id)
    incidents = application.operations_control.list_outbox_incidents(aggregate_id=second.listing_id)
    second_recovery = application.operations_control.get_outbox_recovery(second.outbox_event_id)

    assert first.outbox_aggregate_version == 1
    assert second.outbox_aggregate_version == 2
    assert first_deferral.status == second_deferral.status == "deferred"
    assert still_stale["projection"]["stale"] is True
    assert first_delivery.status == second_delivery.status == "delivered"
    assert len(incidents) == 1
    assert incidents[0]["occurrence_count"] == 2
    assert incidents[0]["status"] == "monitoring"
    assert incidents[0]["impact_scope"] == (
        f"listing:{second.listing_id}:research_and_operations_projection"
    )
    assert incidents[0]["severity"] == "SEV3"
    assert incidents[0]["owner"] == "operations_control"
    assert second_recovery["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 1,
    }
    assert [attempt["status"] for attempt in second_recovery["delivery_attempts"]] == [
        "deferred",
        "deferred",
        "delivered",
    ]


def test_relay_process_crash_before_consumers_recovers_from_postgresql_truth(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    cutoff_text = "2026-08-12T07:00:00Z"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'relay-process.db'}"
    object_root = tmp_path / "objects"
    ready_file = tmp_path / "relay-before-consumers.ready"
    application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
        authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
    )
    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-03-process-crash",
            idempotency_key="ticket-03-process-crash",
        )
    )
    event_before = application.operations_control.get_outbox_event(outcome.outbox_event_id)
    audit_before = application.security_audit.list_events(trace_id="trace-ticket-03-process-crash")
    lineage_before = application.operations_control.get_trace_evidence(
        "trace-ticket-03-process-crash"
    )
    prediction_evidence_before = application.operations_control.list_prediction_record_evidence(
        trace_id="trace-ticket-03-process-crash"
    )
    local_key_file = tmp_path / "relay-process-local-api-key.json"
    application.local_identity.save(local_key_file)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "stock_forecasting.acceptance_relay",
            "--event-id",
            outcome.outbox_event_id,
            "--pause-before-consumers",
            str(ready_file),
        ],
        cwd=Path(__file__).parents[2],
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "OBJECT_ROOT": str(object_root),
            "FIXTURE_INFORMATION_CUTOFF": cutoff_text,
            "FIXTURE_COLLECTION_OBSERVED_AT": cutoff_text,
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
        deadline = time.monotonic() + 10
        while not ready_file.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), process.stderr.read() if process.stderr else ""
        competing = application.relay_outbox(event_id=outcome.outbox_event_id)
        assert competing.status == "busy"
        process.terminate()
        process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

    crashed = application.operations_control.get_outbox_recovery(outcome.outbox_event_id)
    assert process.returncode != 0
    assert crashed["consumer_effect_counts"] == {
        "research_projection": 0,
        "operations_projection": 0,
    }
    assert [attempt["status"] for attempt in crashed["delivery_attempts"]] == ["running"]

    restarted = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    deadline = time.monotonic() + 5
    recovered = restarted.relay_outbox(event_id=outcome.outbox_event_id)
    while recovered.status == "busy" and time.monotonic() < deadline:
        time.sleep(0.05)
        recovered = restarted.relay_outbox(event_id=outcome.outbox_event_id)
    evidence = restarted.operations_control.get_outbox_recovery(outcome.outbox_event_id)
    event_after = restarted.operations_control.get_outbox_event(outcome.outbox_event_id)
    audit_after = restarted.security_audit.list_events(trace_id="trace-ticket-03-process-crash")
    lineage_after = restarted.operations_control.get_trace_evidence("trace-ticket-03-process-crash")
    prediction_evidence_after = restarted.operations_control.list_prediction_record_evidence(
        trace_id="trace-ticket-03-process-crash"
    )

    assert recovered.status == "delivered"
    assert event_after == {**event_before, "delivery_status": "delivered"}
    assert [attempt["status"] for attempt in evidence["delivery_attempts"]] == [
        "superseded",
        "delivered",
    ]
    assert evidence["delivery_attempts"][0]["reason_code"] == "relay_lease_superseded"
    assert [attempt["work_status"] for attempt in evidence["delivery_attempts"]] == [
        "failed",
        "succeeded",
    ]
    assert [attempt["fencing_token"] for attempt in evidence["delivery_attempts"]] == [1, 2]
    assert all(attempt["worker_id"] for attempt in evidence["delivery_attempts"])
    assert evidence["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 1,
    }
    assert audit_after[0] == audit_before[0]
    for lineage_field in ("feature_snapshot_id", "serving_assignment_id"):
        artifact_id = evidence_id = lineage_after["lineage_ids"][lineage_field]
        assert evidence_id == lineage_before["lineage_ids"][lineage_field]
        assert (
            lineage_after["artifact_content_digests"][artifact_id]
            == lineage_before["artifact_content_digests"][artifact_id]
        )
    assert lineage_after["audit_events"][0] == lineage_before["audit_events"][0]
    assert lineage_before["audit_events"][0]["event_id"]
    assert prediction_evidence_after == prediction_evidence_before
    assert len(prediction_evidence_before) == 3
    assert all(evidence["prediction_id"] for evidence in prediction_evidence_before)
    assert all(len(evidence["content_digest"]) == 64 for evidence in prediction_evidence_before)
    assert [event["action"] for event in audit_after] == [
        "fixture_pipeline.execute",
        "outbox_recovery",
        "outbox_delivery",
    ]


def test_relay_process_crash_after_consumer_commits_redelivers_without_duplicates(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    cutoff_text = "2026-08-12T07:00:00Z"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'relay-before-ack.db'}"
    object_root = tmp_path / "objects"
    ready_file = tmp_path / "relay-before-ack.ready"
    application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
        authorization_policy_set_id=FIXTURE_ACTIVE_POLICY_SET,
    )
    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-03-before-ack",
            idempotency_key="ticket-03-before-ack",
        )
    )
    local_key_file = tmp_path / "relay-before-ack-local-api-key.json"
    application.local_identity.save(local_key_file)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "stock_forecasting.acceptance_relay",
            "--event-id",
            outcome.outbox_event_id,
            "--pause-before-ack",
            str(ready_file),
        ],
        cwd=Path(__file__).parents[2],
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "OBJECT_ROOT": str(object_root),
            "FIXTURE_INFORMATION_CUTOFF": cutoff_text,
            "FIXTURE_COLLECTION_OBSERVED_AT": cutoff_text,
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
        deadline = time.monotonic() + 10
        while not ready_file.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), process.stderr.read() if process.stderr else ""
        competing = application.relay_outbox(event_id=outcome.outbox_event_id)
        assert competing.status == "busy"
        process.terminate()
        process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

    committed_before_ack = application.operations_control.get_outbox_recovery(
        outcome.outbox_event_id
    )
    assert process.returncode != 0
    assert committed_before_ack["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 1,
    }
    assert (
        application.operations_control.get_outbox_event(outcome.outbox_event_id)["delivery_status"]
        == "pending"
    )

    restarted = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        database_url=database_url,
    )
    deadline = time.monotonic() + 5
    recovered = restarted.relay_outbox(event_id=outcome.outbox_event_id)
    while recovered.status == "busy" and time.monotonic() < deadline:
        time.sleep(0.05)
        recovered = restarted.relay_outbox(event_id=outcome.outbox_event_id)
    evidence = restarted.operations_control.get_outbox_recovery(outcome.outbox_event_id)

    assert recovered.status == "delivered"
    assert [attempt["status"] for attempt in evidence["delivery_attempts"]] == [
        "superseded",
        "delivered",
    ]
    assert evidence["delivery_attempts"][0]["reason_code"] == "relay_lease_superseded"
    assert evidence["consumer_effect_counts"] == {
        "research_projection": 1,
        "operations_projection": 1,
    }


def test_rest_and_ui_show_projection_staleness_until_recovery() -> None:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    cutoff_text = "2026-08-12T07:00:00Z"
    trace_id = "trace-ticket-03-rest-ui"
    application = build_test_application(observed_at=cutoff)
    outcome = assert_success(application).run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key="ticket-03-rest-ui",
        )
    )
    authoritative_predictions = application.operations_control.list_prediction_records(
        trace_id=trace_id
    )
    client = TestClient(
        create_web_app(application),
        headers={"Authorization": application.local_identity.credential.authorization_header()},
        client=("127.0.0.1", 50000),
    )

    pending_rest = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff_text},
    )
    pending_ui = client.get(
        "/research",
        params={"information_cutoff": cutoff_text, "support": "full"},
    )

    assert pending_rest.json()["items"][0]["projection"] == {
        "core_projection_version": 1,
        "evidence_projection_version": 0,
        "stale": True,
    }
    assert pending_rest.json()["items"][0]["predictions"] == authoritative_predictions
    assert "投影狀態：等待恢復" in pending_ui.text
    assert "核心版本 1／證據版本 0" in pending_ui.text

    application.relay_outbox(event_id=outcome.outbox_event_id)
    recovered_rest = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff_text},
    )
    recovered_ui = client.get(
        "/research",
        params={"information_cutoff": cutoff_text, "support": "full"},
    )

    assert recovered_rest.json()["items"][0]["projection"] == {
        "core_projection_version": 1,
        "evidence_projection_version": 1,
        "stale": False,
    }
    assert recovered_rest.json()["items"][0]["predictions"] == authoritative_predictions
    assert "投影狀態：已同步" in recovered_ui.text
    assert "核心版本 1／證據版本 1" in recovered_ui.text
