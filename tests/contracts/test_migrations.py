from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from stock_forecasting.platform.state_store import metadata


def test_alembic_upgrade_builds_the_canonical_schema(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"

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
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    migrated_tables = set(inspect(create_engine(database_url)).get_table_names())
    assert migrated_tables == {table.name for table in metadata.tables.values()} | {
        "alembic_version"
    }
