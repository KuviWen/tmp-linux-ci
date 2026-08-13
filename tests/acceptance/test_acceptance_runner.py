from __future__ import annotations

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

from stock_forecasting.acceptance import run_ticket_01, run_ticket_02
from stock_forecasting.dagster_deployment import inspect_dagster_deployment


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


def test_deployed_dagster_health_fails_closed_on_malformed_payload() -> None:
    with _dagster_graphql([]) as dagster_url:
        status = inspect_dagster_deployment(dagster_url)

    assert status.ready is False


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
