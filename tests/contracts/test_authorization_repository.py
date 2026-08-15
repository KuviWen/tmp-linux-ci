from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_forecasting.authorization import (
    LocalApiKeyIdentity,
    build_fixture_authorization_policy,
)
from stock_forecasting.authorization_repository import (
    FIXTURE_ACTIVE_POLICY_SET,
    FIXTURE_REVOKED_POLICY_SET,
    TICKET_06_POLICY_BLOCKED_SET,
    TICKET_07_ENGINEERING_POLICY_SET,
    AuthorizationPolicyRepository,
)
from stock_forecasting.cli import main
from stock_forecasting.platform.state_store import ImmutableStateConflict, StateStore


def test_immutable_authorization_policy_set_is_loaded_from_authoritative_state(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=now,
        expires_at=now.replace(day=15),
    )
    database_url = f"sqlite+pysqlite:///{tmp_path / 'policy.db'}"
    first_store = StateStore(database_url, create_schema=True)
    first_repository = AuthorizationPolicyRepository(first_store)
    active = build_fixture_authorization_policy(identity.context)

    first_repository.install("fixture-active", active)
    reloaded = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False)).get(
        "fixture-active", principal_id=identity.context.principal_id
    )

    assert reloaded == active


def test_authorization_policy_set_cannot_be_replaced_with_different_content() -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=now,
        expires_at=now.replace(day=15),
    )
    repository = AuthorizationPolicyRepository(
        StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    )
    repository.install("fixture-policy", build_fixture_authorization_policy(identity.context))

    with pytest.raises(ImmutableStateConflict, match="immutable_authorization_policy_conflict"):
        repository.install(
            "fixture-policy",
            build_fixture_authorization_policy(
                identity.context,
                entitlement_states={"XTAI": "revoked"},
            ),
        )


def test_authorization_init_cli_installs_main_and_admin_policy_sets_without_secrets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=now,
        expires_at=now.replace(day=15),
    )
    key_file = tmp_path / "local-api-key.json"
    admin_key_file = tmp_path / "platform-admin-api-key.json"
    identity.save(key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'authorization-init.db'}"
    StateStore(database_url, create_schema=True)

    exit_code = main(
        [
            "authorization",
            "init-fixtures",
            "--database-url",
            database_url,
            "--key-file",
            str(key_file),
            "--platform-admin-key-file",
            str(admin_key_file),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output == '{"policy_set_count": 7, "status": "initialized"}\n'
    assert identity.credential.authorization_header() not in output
    repository = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False))
    assert (
        repository.get(
            FIXTURE_ACTIVE_POLICY_SET,
            principal_id=identity.context.principal_id,
        )
        .source_entitlements[0]
        .status
        == "active"
    )
    admin_identity = LocalApiKeyIdentity.load(admin_key_file)
    assert (
        repository.get(
            FIXTURE_REVOKED_POLICY_SET,
            principal_id=admin_identity.context.principal_id,
        )
        .source_entitlements[0]
        .status
        == "revoked"
    )


def test_ticket_06_authorization_init_installs_metadata_read_but_no_price_source_rights(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-researcher",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
        issued_at=now,
        expires_at=now.replace(day=16),
    )
    key_file = tmp_path / "ticket-06-local-api-key.json"
    identity.save(key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ticket-06-authorization.db'}"
    StateStore(database_url, create_schema=True)

    exit_code = main(
        [
            "authorization",
            "init-ticket-06",
            "--database-url",
            database_url,
            "--key-file",
            str(key_file),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == '{"policy_set_count": 1, "status": "initialized"}\n'
    policy = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False)).get(
        TICKET_06_POLICY_BLOCKED_SET,
        principal_id=identity.context.principal_id,
    )
    assert [item.dataset_id for item in policy.source_policies] == ["price-research-eligibility"]
    assert [item.dataset_id for item in policy.source_entitlements] == [
        "price-research-eligibility"
    ]


def test_ticket_07_authorization_init_installs_zero_fee_and_credential_contracts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-source-administrator",
        environment="development",
        scopes={
            "market_data.collect",
            "price_research_eligibility.read",
            "source_credential.read",
            "source_credential.manage",
        },
        issued_at=now,
        expires_at=now.replace(day=16),
        data_protection_classes={"licensed", "restricted", "secret"},
    )
    key_file = tmp_path / "ticket-07-local-api-key.json"
    identity.save(key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ticket-07-authorization.db'}"
    StateStore(database_url, create_schema=True)

    exit_code = main(
        [
            "authorization",
            "init-ticket-07",
            "--database-url",
            database_url,
            "--key-file",
            str(key_file),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == '{"policy_set_count": 1, "status": "initialized"}\n'
    policy = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False)).get(
        TICKET_07_ENGINEERING_POLICY_SET,
        principal_id=identity.context.principal_id,
    )
    policies = {item.dataset_id: item for item in policy.source_policies}
    assert set(policies) == {
        "alpaca-us-stock-bars",
        "price-research-eligibility",
        "source-credential-metadata",
    }
    bars = policies["alpaca-us-stock-bars"]
    assert bars.access_basis == "zero_fee_plan"
    assert bars.source_basis_id == "ALPACA-BASIC-US-MARKET-DATA-01"
    assert bars.provider_id == "alpaca-market-data-basic"
    assert bars.plan_id == "basic-2026-08-15"
    assert bars.fee_required is False
    assert bars.terms_content_sha256 is not None
    assert policies["source-credential-metadata"].data_protection_class == "restricted"
