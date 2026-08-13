from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import create_engine, inspect, select

from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import LocalApiKeyIdentity
from stock_forecasting.platform.database_schema import metadata, research_records
from tests.support import assert_success

REPOSITORY_ROOT = Path(__file__).parents[2]


def _upgrade(database_url: str, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "-x",
            f"database_url={database_url}",
            "upgrade",
            revision,
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )


def test_alembic_upgrade_builds_the_canonical_schema(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"

    completed = _upgrade(database_url, "head")

    assert completed.returncode == 0, completed.stderr
    migrated_tables = set(inspect(create_engine(database_url)).get_table_names())
    assert migrated_tables == {table.name for table in metadata.tables.values()} | {
        "alembic_version"
    }


def test_ticket_03_upgrade_backfills_existing_research_projection_status(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'upgrade.db'}"
    first_upgrade = _upgrade(database_url, "20260813_01")
    assert first_upgrade.returncode == 0, first_upgrade.stderr

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            research_records.insert().values(
                record_id="record-before-ticket-03",
                listing_id="listing-before-ticket-03",
                information_cutoff="2026-08-11T07:00:00Z",
                execution_purpose="fixture",
                fixture_scenario="normal",
                payload={
                    "identity": {"listing_id": "listing-before-ticket-03"},
                    "calendar": {"exchange": "XTAI"},
                },
            )
        )

    final_upgrade = _upgrade(database_url, "head")
    assert final_upgrade.returncode == 0, final_upgrade.stderr

    projection_status = metadata.tables["research_projection_status"]
    with engine.connect() as connection:
        row = connection.execute(select(projection_status)).mappings().one()
    assert dict(row) == {
        "record_id": "record-before-ticket-03",
        "core_projection_version": 0,
        "evidence_projection_version": 0,
        "stale": False,
    }
    identity_time = datetime(2026, 8, 13, tzinfo=UTC)
    application = build_test_application(
        database_url=database_url,
        object_root=tmp_path / "objects",
        observed_at=identity_time,
        local_identity=LocalApiKeyIdentity.issue(
            owner="migration-contract",
            environment="development",
            scopes={"fixture_pipeline.execute", "research_prediction.read"},
            issued_at=identity_time - timedelta(minutes=1),
            expires_at=identity_time + timedelta(hours=24),
        ),
    )
    records = assert_success(application).research_query.list_predictions(
        execution_purpose="fixture"
    )
    migrated_projection = records[0]["projection"]
    assert migrated_projection == {
        "core_projection_version": 0,
        "evidence_projection_version": 0,
        "stale": False,
    }
    projection_contract = yaml.safe_load(
        (REPOSITORY_ROOT / "openapi" / "openapi.yaml").read_text(encoding="utf-8")
    )["components"]["schemas"]["ProjectionStatus"]
    for version_field in ("core_projection_version", "evidence_projection_version"):
        assert (
            migrated_projection[version_field]
            >= projection_contract["properties"][version_field]["minimum"]
        )


def test_ticket_04_upgrade_adds_structured_authorization_audit_evidence(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'authorization-upgrade.db'}"
    prior_upgrade = _upgrade(database_url, "20260813_02")
    assert prior_upgrade.returncode == 0, prior_upgrade.stderr
    engine = create_engine(database_url)
    assert "authorization" not in {
        column["name"] for column in inspect(engine).get_columns("security_audit_events")
    }

    final_upgrade = _upgrade(database_url, "head")

    assert final_upgrade.returncode == 0, final_upgrade.stderr
    columns = {
        column["name"]: column for column in inspect(engine).get_columns("security_audit_events")
    }
    assert columns["authorization"]["nullable"] is True
    assert {
        column["name"] for column in inspect(engine).get_columns("authorization_policy_sets")
    } == {"policy_set_id", "principal_id", "content_digest", "payload"}
