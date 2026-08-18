from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from stock_forecasting.authorization import (
    LocalApiKeyIdentity,
    build_pending_rights_operator_authorization_policy,
)
from stock_forecasting.authorization_repository import (
    FIXTURE_ACTIVE_POLICY_SET,
    FIXTURE_REVOKED_POLICY_SET,
    TICKET_09_OWNER_OPERATOR_POLICY_SET,
    AuthorizationPolicyRepository,
    fixture_authorization_policy_catalog,
)
from stock_forecasting.model_governance import (
    BOOTSTRAP_GATE_POLICY_V1,
    DecideApproval,
    EvaluateBootstrapCandidate,
    ModelApprovalPolicyVersion,
    RecordCandidate,
)
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.runtime import RuntimeSettings
from stock_forecasting.source_credentials import SecretUseContext
from tests.modeling_support import passing_hard_gate_evidence, passing_hard_gate_report


def _install_fixture_policy_catalog(
    database_url: str,
    identity: LocalApiKeyIdentity,
) -> None:
    repository = AuthorizationPolicyRepository(StateStore(database_url, create_schema=True))
    for policy_set_id, policy in fixture_authorization_policy_catalog(identity.context).items():
        repository.install(policy_set_id, policy)


def test_runtime_requires_a_platform_owned_fixture_observation_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runtime.db")
    monkeypatch.setenv("OBJECT_ROOT", ".runtime-objects")
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.delenv("FIXTURE_COLLECTION_OBSERVED_AT", raising=False)

    with pytest.raises(RuntimeError, match="FIXTURE_COLLECTION_OBSERVED_AT is required"):
        RuntimeSettings.from_environment()


def test_operator_runtime_does_not_require_fixture_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
    owner = LocalApiKeyIdentity.issue(
        owner="owner-local",
        environment="local",
        scopes={
            "price_research_eligibility.read",
            "source_credential.read",
            "source_credential.manage",
            "model_governance.read",
            "model_governance.approve",
        },
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"internal", "licensed", "restricted", "secret"},
        principal_classification="individual_non_commercial",
    )
    owner_key_file = tmp_path / "run" / "owner-api-key.json"
    owner.save(owner_key_file)
    source_adapter = LocalApiKeyIdentity.issue(
        owner="owner-local-source-adapter",
        environment="local",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    source_adapter_key_file = tmp_path / "run" / "source-adapter-api-key.json"
    source_adapter.save(source_adapter_key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'operator-runtime.db'}"
    repository = AuthorizationPolicyRepository(StateStore(database_url, create_schema=True))
    repository.install(
        TICKET_09_OWNER_OPERATOR_POLICY_SET,
        build_pending_rights_operator_authorization_policy(owner.context),
    )
    repository.install(
        TICKET_09_OWNER_OPERATOR_POLICY_SET,
        build_pending_rights_operator_authorization_policy(source_adapter.context),
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("SOURCE_SECRET_ROOT", str(tmp_path / "source-secrets"))
    monkeypatch.delenv("FIXTURE_INFORMATION_CUTOFF", raising=False)
    monkeypatch.delenv("FIXTURE_COLLECTION_OBSERVED_AT", raising=False)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(owner_key_file))
    monkeypatch.setenv("SOURCE_ADAPTER_API_KEY_FILE", str(source_adapter_key_file))
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", TICKET_09_OWNER_OPERATOR_POLICY_SET)

    settings = RuntimeSettings.from_environment()
    application = settings.build_application()

    assert settings.fixture_information_cutoff is None
    assert settings.fixture_collection_observed_at is None
    assert application.security_context.principal_id == owner.context.principal_id
    assert application.source_adapter_security_context == source_adapter.context


def test_runtime_keeps_observation_time_distinct_from_information_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runtime.db")
    monkeypatch.setenv("OBJECT_ROOT", ".runtime-objects")
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)

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
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)

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
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)

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
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)

    with pytest.raises(RuntimeError, match="local_api_key_loopback_required"):
        RuntimeSettings.from_environment()


def test_runtime_processes_load_the_same_ephemeral_local_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    key_file = tmp_path / "run" / "local-api-key.json"
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=24),
    )
    identity.save(key_file)
    adapter_key_file = tmp_path / "run" / "source-adapter-api-key.json"
    adapter_identity = LocalApiKeyIdentity.issue(
        owner="alpaca-source-adapter",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=1),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    adapter_identity.save(adapter_key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}"
    _install_fixture_policy_catalog(database_url, identity)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("SOURCE_ADAPTER_API_KEY_FILE", str(adapter_key_file))
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)
    source_secret_root = tmp_path / "source-secrets"
    monkeypatch.setenv("SOURCE_SECRET_ROOT", str(source_secret_root))

    settings = RuntimeSettings.from_environment()
    first = settings.build_application()
    second = settings.build_application()

    assert first.security_context.principal_id == second.security_context.principal_id
    assert first.local_identity.credential.authorization_header() == (
        second.local_identity.credential.authorization_header()
    )
    assert first.source_adapter_security_context is not None
    assert second.source_adapter_security_context is not None
    assert (
        first.source_adapter_security_context.principal_id == adapter_identity.context.principal_id
    )
    assert first.source_adapter_security_context.principal_id != first.security_context.principal_id
    assert first.finmind_price_adapter is not None
    secret_ref = first.secret_provider.put(
        provider_id="alpaca-market-data-basic",
        credential_fields={
            "api_key_id": "PK-RUNTIME-PERSISTENCE",
            "api_secret_key": "runtime-persistence-secret",
        },
    )
    third = settings.build_application()
    accessed_at = datetime.now(UTC)
    use_context = SecretUseContext(
        workload_principal_id=third.security_context.principal_id,
        environment=third.security_context.environment,
        source_id="alpaca-us-stock-bars",
        destination="alpaca-market-data-basic",
        purpose="runtime_persistence_test",
        request_id="request-runtime-persistence",
        work_id="work-runtime-persistence",
        credential_version=1,
        lease_duration=timedelta(minutes=5),
        lease_not_before=accessed_at,
        lease_expires_at=accessed_at + timedelta(minutes=5),
    )
    assert third.secret_provider.checkout(secret_ref, use_context).credential_fields() == {
        "api_key_id": "PK-RUNTIME-PERSISTENCE",
        "api_secret_key": "runtime-persistence-secret",
    }
    persisted = b"".join(path.read_bytes() for path in source_secret_root.iterdir())
    assert b"PK-RUNTIME-PERSISTENCE" not in persisted
    assert b"runtime-persistence-secret" not in persisted


def test_local_runtime_designates_its_single_owner_for_model_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    key_file = tmp_path / "run" / "owner-api-key.json"
    identity = LocalApiKeyIdentity.issue(
        owner="single-owner",
        environment="development",
        scopes={"model_governance.read", "model_governance.approve"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=24),
    )
    identity.save(key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'owner-runtime.db'}"
    _install_fixture_policy_catalog(database_url, identity)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("SOURCE_SECRET_ROOT", str(tmp_path / "source-secrets"))
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-17T02:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-17T02:00:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)
    application = RuntimeSettings.from_environment().build_application()
    owner_id = identity.context.principal_id
    evaluation_id = "sha256:owner-runtime-evaluation"
    report = passing_hard_gate_report(evaluation_id)
    application.governance_object_repository.put_verified(
        BytesIO(report.serialized),
        expected_checksum=report.artifact_id.removeprefix("sha256:"),
        metadata={"content_type": "application/json", "object_kind": "gate_report"},
    )
    application.model_lifecycle.execute(
        RecordCandidate(
            command_id="record-owner-runtime-candidate",
            model_family_id="owner-runtime-family",
            candidate_id="owner-runtime-candidate",
            model_family="regularized_multinomial_logistic",
            artifact_id="sha256:owner-runtime-artifact",
            evaluation_report_id=evaluation_id,
            training_intent_id="owner-runtime-intent",
            intent_initiator=owner_id,
            training_executor=owner_id,
            improvement_percentage_points=12.0,
            calibrator_statuses=("sufficient_data",) * 6,
            expected_version=0,
            occurred_at=now,
            formal_qualification=True,
        )
    )
    application.model_lifecycle.execute(
        EvaluateBootstrapCandidate(
            command_id="gate-owner-runtime-candidate",
            model_family_id="owner-runtime-family",
            candidate_id="owner-runtime-candidate",
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            hard_gates=passing_hard_gate_evidence(evaluation_id),
            expected_version=1,
            occurred_at=now,
        )
    )

    result = application.model_lifecycle.execute(
        DecideApproval(
            command_id="approve-owner-runtime-candidate",
            model_family_id="owner-runtime-family",
            candidate_id="owner-runtime-candidate",
            artifact_id="sha256:owner-runtime-artifact",
            evaluation_report_id=evaluation_id,
            policy_version_id=BOOTSTRAP_GATE_POLICY_V1.policy_version_id,
            approver_id=owner_id,
            decision="approved",
            reason="Owner accepts exact evidence without independent review.",
            expected_assignment="unassigned",
            expected_version=2,
            occurred_at=now,
        )
    )

    expected_policy = ModelApprovalPolicyVersion.create(
        policy_name="owner-operated-model-approval-v1",
        approval_mode="owner_operated",
        owner_principal_id=owner_id,
    )
    assert result.status == "approved"
    assert result.approval_decision is not None
    assert result.approval_decision.approval_policy_version_id == (
        expected_policy.policy_version_id
    )


def test_runtime_without_a_source_adapter_key_keeps_the_adapter_path_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    key_file = tmp_path / "run" / "local-api-key.json"
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=24),
    )
    identity.save(key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'runtime-disabled-adapter.db'}"
    _install_fixture_policy_catalog(database_url, identity)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("SOURCE_SECRET_ROOT", str(tmp_path / "source-secrets"))
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("SOURCE_ADAPTER_API_KEY_FILE", raising=False)
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)

    application = RuntimeSettings.from_environment().build_application()

    assert application.source_adapter_security_context is None
    assert application.alpaca_price_adapter is None
    assert application.finmind_price_adapter is None


def test_runtime_loads_selected_immutable_policy_set_for_denied_adapter_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    key_file = tmp_path / "run" / "local-api-key.json"
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=24),
    )
    identity.save(key_file)
    adapter_key_file = tmp_path / "run" / "source-adapter-api-key.json"
    adapter_identity = LocalApiKeyIdentity.issue(
        owner="alpaca-source-adapter",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=1),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    adapter_identity.save(adapter_key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}"
    _install_fixture_policy_catalog(database_url, identity)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("SOURCE_ADAPTER_API_KEY_FILE", str(adapter_key_file))
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_REVOKED_POLICY_SET)

    application = RuntimeSettings.from_environment().build_application()

    states = {
        entitlement.dataset_id: entitlement.status
        for entitlement in application.authorization_policy.source_entitlements
    }
    assert states == {
        "xtai-fixture-eod": "revoked",
        "xnas-fixture-eod": "active",
    }


def test_runtime_rejects_reusing_the_rest_identity_for_source_adapter_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 8, 12, 6, 55, tzinfo=UTC)
    key_file = tmp_path / "run" / "shared-local-api-key.json"
    identity = LocalApiKeyIdentity.issue(
        owner="source-administrator",
        environment="development",
        scopes={"market_data.collect", "source_credential.manage"},
        issued_at=observed_at - timedelta(minutes=1),
        expires_at=observed_at + timedelta(hours=1),
    )
    identity.save(key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'runtime-shared-identity.db'}"
    _install_fixture_policy_catalog(database_url, identity)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("FIXTURE_INFORMATION_CUTOFF", "2026-08-12T07:00:00Z")
    monkeypatch.setenv("FIXTURE_COLLECTION_OBSERVED_AT", "2026-08-12T06:55:00Z")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_API_KEY_MODE", "enabled")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("SOURCE_ADAPTER_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AUTHORIZATION_POLICY_SET_ID", FIXTURE_ACTIVE_POLICY_SET)

    with pytest.raises(RuntimeError, match="source_adapter_identity_must_be_distinct"):
        RuntimeSettings.from_environment().build_application()
