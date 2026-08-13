from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from stock_forecasting.platform.state_store import metadata, work_attempts

REPOSITORY_ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.postgresql


def _test_database_url() -> str:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to run the disposable PostgreSQL integration test")

    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL")
    if parsed.database is None or not parsed.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a database whose name ends with '_test'")
    return database_url


def test_migrations_and_transaction_rollback_on_real_postgresql() -> None:
    database_url = _test_database_url()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "-x",
            f"database_url={database_url}",
            "upgrade",
            "head",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert completed.returncode == 0, completed.stderr

    engine = create_engine(database_url)
    parsed = make_url(database_url)
    try:
        with engine.connect() as connection:
            database_name, database_user = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
        assert database_name == parsed.database
        assert database_user == parsed.username
        assert set(inspect(engine).get_table_names()) == {
            table.name for table in metadata.tables.values()
        } | {"alembic_version"}

        work_id = str(uuid4())
        with (
            pytest.raises(RuntimeError, match="force rollback"),
            engine.begin() as connection,
        ):
            connection.execute(
                work_attempts.insert().values(
                    work_id=work_id,
                    operation="postgres_contract_probe",
                    status="running",
                    execution_purpose="fixture",
                    trace_id=f"trace-{work_id}",
                    idempotency_key=f"probe-{work_id}",
                    attempt_count=1,
                )
            )
            raise RuntimeError("force rollback")

        with engine.connect() as connection:
            assert (
                connection.execute(
                    select(work_attempts.c.work_id).where(work_attempts.c.work_id == work_id)
                ).scalar_one_or_none()
                is None
            )
    finally:
        engine.dispose()
