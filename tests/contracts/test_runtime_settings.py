from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_forecasting.authorization import LocalApiKeyIdentity
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


def test_runtime_rejects_local_key_mode_in_a_formal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runtime.db")
    monkeypatch.setenv("OBJECT_ROOT", ".runtime-objects")
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")

    with pytest.raises(RuntimeError, match="local_api_key_environment_forbidden"):
        RuntimeSettings.from_environment()


def test_runtime_without_local_key_mode_fails_closed_when_no_trusted_provider_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runtime.db")
    monkeypatch.setenv("OBJECT_ROOT", ".runtime-objects")
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "disabled")

    with pytest.raises(RuntimeError, match="trusted_identity_provider_required"):
        RuntimeSettings.from_environment().build_application()


def test_runtime_rejects_local_key_mode_on_a_non_loopback_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runtime.db")
    monkeypatch.setenv("OBJECT_ROOT", ".runtime-objects")
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")

    with pytest.raises(RuntimeError, match="local_api_key_loopback_required"):
        RuntimeSettings.from_environment()


def test_runtime_processes_load_the_same_ephemeral_local_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    key_file = tmp_path / "run" / "local-api-key.json"
    LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=24),
    ).save(key_file)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}")
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))

    settings = RuntimeSettings.from_environment()
    first = settings.build_application()
    second = settings.build_application()

    assert first.security_context.principal_id == second.security_context.principal_id
    assert first.local_identity.credential.authorization_header() == (
        second.local_identity.credential.authorization_header()
    )


def test_runtime_loads_versioned_entitlement_state_for_denied_adapter_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    key_file = tmp_path / "run" / "local-api-key.json"
    LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=24),
    ).save(key_file)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}")
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("XTAI_SOURCE_ENTITLEMENT_STATUS", "revoked")

    application = RuntimeSettings.from_environment().build_application()

    states = {
        entitlement.dataset_id: entitlement.status
        for entitlement in application.authorization_policy.source_entitlements
    }
    assert states == {
        "xtai-fixture-eod": "revoked",
        "xnas-fixture-eod": "active",
    }
