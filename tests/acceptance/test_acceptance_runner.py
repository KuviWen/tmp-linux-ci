from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from stock_forecasting.acceptance import (
    run_ticket_01,
    run_ticket_02,
    run_ticket_04,
    run_ticket_05,
)
from stock_forecasting.dagster_deployment import (
    inspect_dagster_deployment,
    materialize_deployed_asset,
)


@contextmanager
def _dagster_graphql(payload: Any) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            json.loads(self.rfile.read(content_length))
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/graphql"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@contextmanager
def _dagster_graphql_sequence(
    payloads: list[Any], *, requests: list[dict[str, Any]] | None = None
) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(content_length))
            if requests is not None:
                requests.append(request)
            body = json.dumps(payloads.pop(0)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/graphql"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_deployed_dagster_health_fails_closed_on_malformed_payload() -> None:
    with _dagster_graphql([]) as dagster_url:
        status = inspect_dagster_deployment(dagster_url)

    assert status.ready is False


def test_deployed_dagster_asset_is_launched_and_observed_to_success() -> None:
    payloads = [
        {
            "data": {
                "launchPipelineExecution": {
                    "__typename": "LaunchRunSuccess",
                    "run": {"runId": "run-ticket-04-denied"},
                }
            }
        },
        {
            "data": {
                "pipelineRunOrError": {
                    "__typename": "Run",
                    "status": "STARTED",
                }
            }
        },
        {
            "data": {
                "pipelineRunOrError": {
                    "__typename": "Run",
                    "status": "SUCCESS",
                }
            }
        },
    ]

    requests: list[dict[str, Any]] = []
    with _dagster_graphql_sequence(payloads, requests=requests) as dagster_url:
        succeeded = materialize_deployed_asset(
            dagster_url,
            location_name="stock_forecasting_denied",
            asset_name="xtai_fixture_eod",
            poll_interval_seconds=0.0,
        )

    assert succeeded is True
    assert payloads == []
    assert 'pipelineName: "__ASSET_JOB"' in requests[0]["query"]
    assert 'pipelineName: "__ASSET_JOB__"' not in requests[0]["query"]


def test_acceptance_rejects_partial_deployment_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="deployment_endpoints_must_be_provided_together"):
        run_ticket_01(
            database_url="sqlite+pysqlite:///:memory:",
            object_root=tmp_path / "objects",
            information_cutoff=datetime(2026, 8, 12, 7, tzinfo=UTC),
            observed_at=datetime(2026, 8, 12, 6, 55, tzinfo=UTC),
            base_url="http://api:8000",
        )


@pytest.mark.parametrize(
    "endpoint_args",
    [
        ["--base-url", "http://api:8000"],
        ["--dagster-url", "http://dagster-webserver:3000/graphql"],
    ],
)
def test_acceptance_cli_reports_partial_deployment_mode(
    tmp_path: Path,
    endpoint_args: list[str],
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_forecasting.cli",
            "acceptance",
            "ticket-01",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
            "--object-root",
            str(tmp_path / "objects"),
            "--information-cutoff",
            "2026-08-12T07:00:00Z",
            "--observed-at",
            "2026-08-12T06:55:00Z",
            *endpoint_args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 2
    assert "--base-url and --dagster-url must be provided together" in completed.stderr


@pytest.mark.parametrize(
    ("asset_names", "expected_ready"),
    [
        (("xtai_fixture_eod", "xnas_fixture_eod"), True),
        (("xtai_fixture_eod",), False),
    ],
)
def test_deployed_dagster_health_requires_both_market_assets_and_heartbeats(
    asset_names: tuple[str, ...], expected_ready: bool
) -> None:
    dagster_payload = {
        "data": {
            "workspaceOrError": {
                "__typename": "Workspace",
                "locationEntries": [
                    {
                        "name": "stock_forecasting",
                        "loadStatus": "LOADED",
                        "locationOrLoadError": {
                            "__typename": "RepositoryLocation",
                            "name": "stock_forecasting",
                            "repositories": [
                                {
                                    "name": "__repository__",
                                    "assetNodes": [
                                        {"assetKey": {"path": [asset_name]}}
                                        for asset_name in asset_names
                                    ],
                                }
                            ],
                        },
                    }
                ],
            },
            "instance": {
                "daemonHealth": {
                    "allDaemonStatuses": [
                        {
                            "daemonType": "SENSOR",
                            "required": True,
                            "healthy": True,
                            "lastHeartbeatTime": 1_786_622_400.0,
                        }
                    ]
                }
            },
        }
    }
    with _dagster_graphql(dagster_payload) as dagster_url:
        status = inspect_dagster_deployment(dagster_url)

    assert status.ready is expected_ready


def test_ticket_01_acceptance_runner_verifies_the_public_seams(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_forecasting.cli",
            "acceptance",
            "ticket-01",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
            "--object-root",
            str(tmp_path / "objects"),
            "--information-cutoff",
            "2026-08-12T07:00:00Z",
            "--observed-at",
            "2026-08-12T06:55:00Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "passed"
    assert report["trace_ids"] == ["P1-ENTRY-01", "P1-TRACE-TW-01"]
    assert report["execution_purpose"] == "fixture"
    assert report["formal_source_qualified"] is False
    assert report["formal_prediction"] is False
    assert all(report["checks"].values())
    assert set(report["checks"]) == {
        "workflow_succeeded",
        "dagster_parity",
        "adversarial_scenarios",
        "immutable_identity",
        "xtai_253_sessions",
        "raw_evidence_durable",
        "checkpoint_committed",
        "three_horizon_result_or_reason",
        "lineage_complete",
        "rest_matrix",
        "ui_matrix",
        "ui_detail_reload",
        "fixture_use_denied",
        "canonical_health",
        "audit_evidence",
        "no_production_prediction_records",
    }


def test_ticket_02_acceptance_runner_verifies_the_shared_us_seams(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_forecasting.cli",
            "acceptance",
            "ticket-02",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
            "--object-root",
            str(tmp_path / "objects"),
            "--information-cutoff",
            "2026-08-12T22:00:00Z",
            "--observed-at",
            "2026-08-12T21:55:00Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "passed"
    assert report["trace_ids"] == ["P1-TRACE-TW-01", "P1-TRACE-US-01"]
    assert set(report["listing_ids"]) == {"XTAI", "XNAS"}
    assert report["execution_purpose"] == "fixture"
    assert report["formal_source_qualified"] is False
    assert report["formal_prediction"] is False
    assert all(report["checks"].values())
    assert set(report["checks"]) == {
        "shared_workflow_succeeded",
        "dagster_parity",
        "shared_domain_contract",
        "market_specific_adapter",
        "xnas_fixture_contract",
        "adversarial_scenarios",
        "one_market_failure_isolated",
        "shared_prediction_shape",
        "rest_matrix",
        "ui_matrix_and_detail",
        "operations_and_audit",
        "no_production_prediction_records",
    }

    direct_report = run_ticket_02(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'direct.db'}",
        object_root=tmp_path / "direct-objects",
        information_cutoff=datetime(2026, 8, 12, 22, tzinfo=UTC),
        observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC),
    )
    assert direct_report["status"] == "passed"


def test_ticket_03_acceptance_runner_verifies_outbox_crash_recovery(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_forecasting.cli",
            "acceptance",
            "ticket-03",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
            "--object-root",
            str(tmp_path / "objects"),
            "--information-cutoff",
            "2026-08-12T07:00:00Z",
            "--observed-at",
            "2026-08-12T06:55:00Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "passed"
    assert report["trace_ids"] == ["P1-TRACE-OUTBOX-01"]
    assert report["execution_purpose"] == "fixture"
    assert report["formal_prediction"] is False
    assert all(report["checks"].values())
    assert set(report["checks"]) == {
        "canonical_commit_before_consumers",
        "original_event_identity_recovered",
        "consumer_transaction_recovered",
        "duplicate_delivery_idempotent",
        "out_of_order_deferred",
        "rest_projection_stale_then_fresh",
        "ui_projection_stale_then_fresh",
        "canonical_state_immutable",
        "operations_recovery_evidence",
        "single_correlated_incident",
        "zero_lost_or_duplicate_effects",
    }


def test_ticket_04_acceptance_runner_verifies_authorization_denial_path(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_forecasting.cli",
            "acceptance",
            "ticket-04",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
            "--object-root",
            str(tmp_path / "objects"),
            "--information-cutoff",
            "2026-08-12T22:00:00Z",
            "--observed-at",
            "2026-08-12T21:55:00Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "passed"
    assert report["trace_ids"] == ["P1-TRACE-AUTH-01"]
    assert report["execution_purpose"] == "fixture"
    assert report["formal_prediction"] is False
    assert all(report["checks"].values())
    assert set(report["checks"]) == {
        "shared_security_context",
        "active_entitlements_allow",
        "same_grant_denial",
        "decision_matrix_fail_closed",
        "administrative_identity_denied",
        "denial_before_persistence",
        "existing_projection_blocked_not_deleted",
        "rest_problem_redacted",
        "ui_problem_redacted",
        "dagster_denial",
        "audit_decision_evidence",
    }

    direct_report = run_ticket_04(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'direct.db'}",
        object_root=tmp_path / "direct-objects",
        information_cutoff=datetime(2026, 8, 12, 22, tzinfo=UTC),
        observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC),
    )
    assert direct_report["status"] == "passed"


def test_ticket_05_runner_publishes_blocked_evidence_when_not_deployed(
    tmp_path: Path,
) -> None:
    report = run_ticket_05(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
        object_root=tmp_path / "objects",
        information_cutoff=datetime(2026, 8, 12, 22, tzinfo=UTC),
        observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC),
        project_root=Path.cwd(),
        git_dir=Path.cwd() / ".git",
    )

    assert report["status"] == "blocked"
    assert report["trace_ids"] == [
        "P1-ENTRY-01",
        "P1-TRACE-TW-01",
        "P1-TRACE-US-01",
        "P1-TRACE-OUTBOX-01",
        "P1-TRACE-AUTH-01",
        "P1-EXIT-01",
        "GATE-POLICY-01",
        "GATE-PIT-01",
        "GATE-DATA-01",
        "GATE-MODEL-01",
        "GATE-SEC-01",
        "GATE-OPS-01",
        "GATE-DEPLOY-01",
        "GATE-UX-01",
    ]
    assert report["hard_gates"]["GATE-DEPLOY-01"] == {
        "owner": "platform_owner",
        "reason": "deployed_endpoints_required",
        "status": "blocked",
    }
    assert set(report["scenario_results"]) == {
        "checksum_failure",
        "correction",
        "duplicate_collection",
        "fixture_promotion_attempt",
        "late_data",
        "missing_calendar",
        "missing_company_action",
        "necessary_modality_missing",
        "one_market_failure",
        "optional_modalities_missing",
        "outbox_redelivery",
        "outbox_restart",
        "stale_fencing",
        "withdrawal",
    }
    assert all(result["status"] == "passed" for result in report["scenario_results"].values())
    assert report["bundle"]["object_id"].startswith("sha256:")
    bundle = json.loads(Path(report["bundle"]["uri"]).read_text(encoding="utf-8"))
    assert bundle["status"] == "blocked"
    assert bundle["previous_bundle_reference"] is None
    assert bundle["claims"]["scope"] == "engineering_spine_only"
    assert all(
        result["evidence_digest"].startswith("sha256:") for result in bundle["contracts"].values()
    )
    failure_evidence = {result["scenario"]: result for result in bundle["failure_evidence"]}
    assert failure_evidence["withdrawal"]["reason"] == "source_withdrawn"
    assert failure_evidence["withdrawal"]["owner"] == "data_owner"
    assert failure_evidence["withdrawal"]["evidence_ids"]
    assert all(
        result["reason"] != "evidence_capture_failed" for result in failure_evidence.values()
    )
    object_root = Path(report["bundle"]["uri"]).parents[2]
    for scenario in ("checksum_failure", "stale_fencing"):
        evidence_reference = failure_evidence[scenario]["evidence_ids"][0]
        checksum = evidence_reference.removeprefix("sha256:")
        probe = json.loads(
            (object_root / "sha256" / checksum[:2] / checksum).read_text(encoding="utf-8")
        )
        assert probe["scenario"] == scenario
        assert probe["verified"] is True
    assert (
        bundle["provenance"]["git_commit"]
        == subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
    )


def test_ticket_05_cli_invokes_the_bundle_runner(tmp_path: Path) -> None:
    export_directory = tmp_path / "exports"
    export_directory.mkdir()
    previous_content = b'{"schema_version":"p1-acceptance-bundle-v1"}'
    previous_reference = f"sha256:{hashlib.sha256(previous_content).hexdigest()}"
    (export_directory / "p1-acceptance-bundle.json").write_bytes(previous_content)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_forecasting.cli",
            "acceptance",
            "ticket-05",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
            "--object-root",
            str(tmp_path / "objects"),
            "--information-cutoff",
            "2026-08-12T22:00:00Z",
            "--observed-at",
            "2026-08-12T21:55:00Z",
            "--project-root",
            str(Path.cwd()),
            "--git-dir",
            str(Path.cwd() / ".git"),
            "--evidence-export-dir",
            str(export_directory),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 1, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "blocked"
    assert report["bundle"]["object_id"].startswith("sha256:")
    bundle = json.loads(Path(report["bundle"]["uri"]).read_text(encoding="utf-8"))
    assert bundle["previous_bundle_reference"] == previous_reference
    exported_bundle = export_directory / "p1-acceptance-bundle.json"
    assert hashlib.sha256(exported_bundle.read_bytes()).hexdigest() == report["bundle"]["checksum"]
    assert (export_directory / "p1-acceptance-bundle.json.sha256").read_text(
        encoding="utf-8"
    ) == f"{report['bundle']['checksum']}  p1-acceptance-bundle.json\n"
    failure_evidence = {result["scenario"]: result for result in bundle["failure_evidence"]}
    preserved_lines = (export_directory / "p1-evidence-objects.sha256").read_text(encoding="utf-8")
    for scenario in ("checksum_failure", "stale_fencing"):
        reference = failure_evidence[scenario]["evidence_ids"][0]
        checksum = reference.removeprefix("sha256:")
        relative_path = Path("objects") / "sha256" / checksum[:2] / checksum
        preserved = export_directory / relative_path
        assert hashlib.sha256(preserved.read_bytes()).hexdigest() == checksum
        assert f"{checksum}  {relative_path.as_posix()}\n" in preserved_lines


def test_ticket_05_cli_preserves_and_reports_an_invalid_previous_bundle(
    tmp_path: Path,
) -> None:
    export_directory = tmp_path / "exports"
    export_directory.mkdir()
    invalid_content = b"not-a-p1-bundle"
    invalid_checksum = hashlib.sha256(invalid_content).hexdigest()
    (export_directory / "p1-acceptance-bundle.json").write_bytes(invalid_content)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "stock_forecasting.cli",
            "acceptance",
            "ticket-05",
            "--database-url",
            f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
            "--object-root",
            str(tmp_path / "objects"),
            "--information-cutoff",
            "2026-08-12T22:00:00Z",
            "--observed-at",
            "2026-08-12T21:55:00Z",
            "--project-root",
            str(Path.cwd()),
            "--git-dir",
            str(Path.cwd() / ".git"),
            "--evidence-export-dir",
            str(export_directory),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 1, completed.stderr
    report = json.loads(completed.stdout)
    bundle = json.loads(Path(report["bundle"]["uri"]).read_text(encoding="utf-8"))
    invalid_reference = f"sha256:{invalid_checksum}"
    assert bundle["previous_bundle_reference"] == invalid_reference
    assert bundle["failure_evidence"] == [
        {
            "evidence_ids": [invalid_reference],
            "owner": "platform_owner",
            "reason": "previous_acceptance_bundle_invalid",
            "scenario": "acceptance_runner",
            "stage": "previous_bundle_validation",
            "status": "exception",
        }
    ]
    preserved_relative_path = Path("previous") / "sha256" / invalid_checksum[:2] / invalid_checksum
    assert (export_directory / preserved_relative_path).read_bytes() == invalid_content
    assert f"{invalid_checksum}  {preserved_relative_path.as_posix()}\n" in (
        export_directory / "p1-evidence-objects.sha256"
    ).read_text(encoding="utf-8")


def test_ticket_05_runner_publishes_fail_closed_bundle_when_a_stage_raises(
    tmp_path: Path,
) -> None:
    report = run_ticket_05(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
        object_root=tmp_path / "objects",
        information_cutoff=datetime(2026, 8, 12, 22, tzinfo=UTC),
        observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC),
        project_root=Path.cwd(),
        git_dir=Path.cwd() / ".git",
        base_url="http://api.invalid",
        dagster_url="http://dagster.invalid/graphql",
    )

    assert report["status"] == "blocked"
    bundle = json.loads(Path(report["bundle"]["uri"]).read_text(encoding="utf-8"))
    assert bundle["failure_evidence"] == [
        {
            "owner": "platform_owner",
            "reason": "evidence_capture_failed",
            "scenario": "acceptance_runner",
            "stage": "orchestration",
            "status": "exception",
        }
    ]
    assert bundle["previous_bundle_reference"] is None
    assert bundle["reproduction_command"] == (
        "docker compose --profile acceptance run --build --rm acceptance"
    )


def test_ticket_05_runner_rejects_an_arbitrary_previous_bundle_reference(
    tmp_path: Path,
) -> None:
    report = run_ticket_05(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'acceptance.db'}",
        object_root=tmp_path / "objects",
        information_cutoff=datetime(2026, 8, 12, 22, tzinfo=UTC),
        observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC),
        project_root=Path.cwd(),
        git_dir=Path.cwd() / ".git",
        previous_bundle_reference="arbitrary-reference",
    )

    bundle = json.loads(Path(report["bundle"]["uri"]).read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert bundle["previous_bundle_reference"] is None
    assert bundle["failure_evidence"][0]["reason"] == ("previous_acceptance_bundle_invalid")
