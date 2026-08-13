from datetime import UTC, datetime

import pytest

from stock_forecasting.runtime import RuntimeSettings


def test_runtime_requires_a_platform_owned_fixture_observation_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runtime.db")
    monkeypatch.setenv("OBJECT_ROOT", ".runtime-objects")
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.delenv("FIXTURE_COLLECTION_OBSERVED_AT", raising=False)

    with pytest.raises(RuntimeError, match="FIXTURE_COLLECTION_OBSERVED_AT is required"):
        RuntimeSettings.from_environment()


def test_runtime_keeps_observation_time_distinct_from_information_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runtime.db")
    monkeypatch.setenv("OBJECT_ROOT", ".runtime-objects")
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")

    settings = RuntimeSettings.from_environment()

    assert settings.fixture_information_cutoff == datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    assert settings.fixture_collection_observed_at == datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
