from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
