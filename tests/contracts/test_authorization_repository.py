import hashlib
import json
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
    TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
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


def test_ticket_06_authorization_init_installs_finmind_admin_and_adapter_contracts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-researcher",
        environment="development",
        scopes={
            "price_research_eligibility.read",
            "source_credential.read",
            "source_credential.manage",
        },
        data_protection_classes={"licensed", "restricted", "secret"},
        issued_at=now,
        expires_at=now.replace(day=16),
        principal_classification="individual_or_internal_group",
    )
    key_file = tmp_path / "ticket-06-local-api-key.json"
    identity.save(key_file)
    source_adapter_identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-finmind-source-adapter",
        environment="development",
        scopes={"market_data.collect"},
        data_protection_classes={"licensed", "secret"},
        issued_at=now,
        expires_at=now.replace(day=16),
        principal_classification="individual_or_internal_group",
    )
    source_adapter_key_file = tmp_path / "ticket-06-source-adapter-api-key.json"
    source_adapter_identity.save(source_adapter_key_file)
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
            "--source-adapter-key-file",
            str(source_adapter_key_file),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == '{"policy_set_count": 2, "status": "initialized"}\n'
    policy = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False)).get(
        TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
        principal_id=identity.context.principal_id,
    )
    assert {
        "finmind-taiwan-stock-price",
        "finmind-taiwan-trading-date",
        "finmind-taiwan-dividend-result",
        "finmind-taiwan-delisting",
        "finmind-taiwan-split-price",
        "price-research-eligibility",
        "source-credential-metadata",
    } == {item.dataset_id for item in policy.source_policies}
    assert policy.action_grants[0].actions == frozenset(
        {
            "price_research_eligibility.read",
            "source_credential.read",
            "source_credential.manage",
        }
    )
    adapter_policy = AuthorizationPolicyRepository(
        StateStore(database_url, create_schema=False)
    ).get(
        TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
        principal_id=source_adapter_identity.context.principal_id,
    )
    assert adapter_policy.action_grants[0].actions == frozenset({"market_data.collect"})


def test_ticket_07_authorization_init_installs_zero_fee_and_credential_contracts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-source-administrator",
        environment="development",
        scopes={
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
    source_adapter_identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-alpaca-source-adapter",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now,
        expires_at=now.replace(day=16),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    source_adapter_key_file = tmp_path / "ticket-07-source-adapter-api-key.json"
    source_adapter_identity.save(source_adapter_key_file)
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
            "--source-adapter-key-file",
            str(source_adapter_key_file),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == '{"policy_set_count": 2, "status": "initialized"}\n'
    repository = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False))
    policy = repository.get(
        TICKET_07_ENGINEERING_POLICY_SET,
        principal_id=identity.context.principal_id,
    )
    source_adapter_policy = repository.get(
        TICKET_07_ENGINEERING_POLICY_SET,
        principal_id=source_adapter_identity.context.principal_id,
    )
    policies = {item.dataset_id: item for item in policy.source_policies}
    assert set(policies) == {
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-stock-bars",
        "alpaca-us-trading-calendar-v2",
        "price-research-eligibility",
        "source-credential-metadata",
    }
    bars = policies["alpaca-us-stock-bars"]
    assert bars.access_basis == "engineering_contract"
    assert bars.source_basis_id == "ENGINEERING-ALPACA-CONTRACT-01"
    assert bars.provider_id is None
    assert bars.plan_id is None
    assert bars.fee_required is None
    assert bars.terms_content_sha256 is None
    assert all(
        policies[dataset_id].access_basis == "engineering_contract"
        for dataset_id in {
            "alpaca-us-stock-bars",
            "alpaca-us-corporate-actions-v1",
            "alpaca-us-trading-calendar-v2",
        }
    )
    assert policies["source-credential-metadata"].data_protection_class == "restricted"
    assert policy.action_grants[0].actions == frozenset(
        {
            "price_research_eligibility.read",
            "source_credential.read",
            "source_credential.manage",
        }
    )
    assert source_adapter_policy.action_grants[0].actions == frozenset({"market_data.collect"})
    assert {item.dataset_id for item in source_adapter_policy.source_entitlements} == {
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-stock-bars",
        "alpaca-us-trading-calendar-v2",
    }


def test_operator_authorization_init_installs_owner_controls_and_pending_source_rights(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
        issued_at=now,
        expires_at=now.replace(day=19),
        data_protection_classes={"internal", "licensed", "restricted", "secret"},
        principal_classification="individual_non_commercial",
    )
    owner_key_file = tmp_path / "owner-api-key.json"
    owner.save(owner_key_file)
    source_adapter = LocalApiKeyIdentity.issue(
        owner="owner-local-source-adapter",
        environment="local",
        scopes={"market_data.collect"},
        issued_at=now,
        expires_at=now.replace(day=19),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    source_adapter_key_file = tmp_path / "source-adapter-api-key.json"
    source_adapter.save(source_adapter_key_file)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'operator-authorization.db'}"
    StateStore(database_url, create_schema=True)

    exit_code = main(
        [
            "authorization",
            "init-operator",
            "--database-url",
            database_url,
            "--key-file",
            str(owner_key_file),
            "--source-adapter-key-file",
            str(source_adapter_key_file),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == '{"policy_set_count": 2, "status": "initialized"}\n'
    repository = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False))
    policy_set_id = "ticket-09-owner-operator-v1"
    owner_policy = repository.get(
        policy_set_id,
        principal_id=owner.context.principal_id,
    )
    adapter_policy = repository.get(
        policy_set_id,
        principal_id=source_adapter.context.principal_id,
    )
    assert {item.dataset_id for item in owner_policy.source_policies} == {
        "price-research-eligibility",
        "source-credential-metadata",
        "model-governance-ledger",
    }
    assert owner_policy.action_grants[0].actions == owner.context.scopes
    assert adapter_policy.action_grants[0].actions == frozenset({"market_data.collect"})
    assert adapter_policy.source_policies == ()
    assert adapter_policy.source_entitlements == ()


def test_qualified_operator_authorization_is_bound_to_reviewed_rights_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
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
        issued_at=now,
        expires_at=now.replace(day=20),
        data_protection_classes={"internal", "licensed", "restricted", "secret"},
        principal_classification="individual_non_commercial",
    )
    owner_key_file = tmp_path / "owner-api-key.json"
    owner.save(owner_key_file)
    source_adapter = LocalApiKeyIdentity.issue(
        owner="owner-local-source-adapter",
        environment="local",
        scopes={"market_data.collect"},
        issued_at=now,
        expires_at=now.replace(day=20),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    source_adapter_key_file = tmp_path / "source-adapter-api-key.json"
    source_adapter.save(source_adapter_key_file)

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    required_uses = [
        "backup_restore",
        "ingest",
        "internal_display",
        "model",
        "retain_observed_history",
        "transform",
    ]
    providers = [
        {
            "provider_id": "finmind-free-api",
            "plan_id": "Free",
            "credential_kind": "api_token",
            "official_sender": "finmind.tw@gmail.com",
            "response_file": "finmind-response.txt",
            "distribution_ids": [
                "TaiwanStockPrice",
                "TaiwanStockTradingDate",
                "TaiwanStockDividendResult",
                "TaiwanStockDelisting",
                "TaiwanStockSplitPrice",
            ],
            "allowed_uses": required_uses,
            "terms_url": "https://finmind.github.io/PrivacyPolicy/",
            "terms_file": "finmind-terms.html",
        },
        {
            "provider_id": "alpaca-market-data-basic",
            "plan_id": "Basic",
            "credential_kind": "api_key_pair",
            "official_sender": "support@alpaca.markets",
            "response_file": "alpaca-response.txt",
            "distribution_ids": [
                "alpaca-us-stock-bars-v2",
                "alpaca-us-corporate-actions-v1",
                "alpaca-us-trading-calendar-v2",
            ],
            "allowed_uses": required_uses,
            "terms_url": "https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf",
            "terms_file": "alpaca-terms.pdf",
        },
    ]
    for provider in providers:
        response_path = evidence_root / str(provider["response_file"])
        response_path.write_text(
            "Evidence source: email\n"
            f"From: Provider <{provider['official_sender']}>\n"
            "Sent at: 2026-08-19 01:00:00 UTC+08:00\n"
            "Recipient: [redacted]@example.com\n"
            "Redactions: Recipient local-part\n\n"
            "All requested uses are allowed.\n",
            encoding="utf-8",
        )
        provider["response_sha256"] = hashlib.sha256(response_path.read_bytes()).hexdigest()
        terms_path = evidence_root / str(provider["terms_file"])
        terms_path.write_bytes(f"terms:{provider['provider_id']}".encode())
        provider["terms_sha256"] = hashlib.sha256(terms_path.read_bytes()).hexdigest()

    manifest_path = evidence_root / "formal-source-rights.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "formal-source-rights/v1",
                "reviewed_at": "2026-08-19T02:00:00+08:00",
                "reviewer_owner": "owner-local",
                "deployment_jurisdiction": "Taiwan",
                "usage_scope": "personal_noncommercial_single_user_local_only",
                "decision": "qualified",
                "providers": providers,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    database_url = f"sqlite+pysqlite:///{tmp_path / 'qualified-operator.db'}"
    StateStore(database_url, create_schema=True)

    exit_code = main(
        [
            "authorization",
            "init-qualified-operator",
            "--database-url",
            database_url,
            "--key-file",
            str(owner_key_file),
            "--source-adapter-key-file",
            str(source_adapter_key_file),
            "--rights-manifest",
            str(manifest_path),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "manifest_sha256": manifest_sha256,
        "policy_set_count": 2,
        "policy_set_id": f"ticket-09-owner-operator-qualified-{manifest_sha256[:16]}",
        "status": "initialized",
    }
    policy = AuthorizationPolicyRepository(StateStore(database_url, create_schema=False)).get(
        output["policy_set_id"], principal_id=source_adapter.context.principal_id
    )
    collect_policies = {
        item.distributions[0].dataset_id: item
        for item in policy.source_policies
        if item.allowed_actions == frozenset({"market_data.collect"})
    }
    assert set(collect_policies) == {
        "TaiwanStockPrice",
        "TaiwanStockTradingDate",
        "TaiwanStockDividendResult",
        "TaiwanStockDelisting",
        "TaiwanStockSplitPrice",
        "alpaca-us-stock-bars-v2",
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-trading-calendar-v2",
    }
    assert all(item.allowed_uses == frozenset(required_uses) for item in collect_policies.values())
    assert all(item.access_basis == "zero_fee_plan" for item in collect_policies.values())
    assert all(
        item.source_basis_id == f"sha256:{manifest_sha256}" for item in collect_policies.values()
    )
    assert {
        item.provider_id: (item.plan_id, item.credential_kind, item.fee_required)
        for item in collect_policies.values()
    } == {
        "finmind-free-api": ("Free", "api_token", False),
        "alpaca-market-data-basic": ("Basic", "api_key_pair", False),
    }


def test_qualified_operator_authorization_rejects_changed_response_evidence(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
    owner = LocalApiKeyIdentity.issue(
        owner="owner-local",
        environment="local",
        scopes={"source_credential.manage"},
        issued_at=now,
        expires_at=now.replace(day=20),
        data_protection_classes={"licensed", "restricted", "secret"},
        principal_classification="individual_non_commercial",
    )
    owner_key_file = tmp_path / "owner-api-key.json"
    owner.save(owner_key_file)
    source_adapter = LocalApiKeyIdentity.issue(
        owner="owner-local-source-adapter",
        environment="local",
        scopes={"market_data.collect"},
        issued_at=now,
        expires_at=now.replace(day=20),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    source_adapter_key_file = tmp_path / "source-adapter-api-key.json"
    source_adapter.save(source_adapter_key_file)
    response_path = tmp_path / "finmind-response.txt"
    response_path.write_text(
        "From: FinMind <finmind.tw@gmail.com>\n"
        "Sent at: 2026-08-19T01:00:00+08:00\n"
        "Recipient: [redacted]@example.com\n"
        "Redactions: Recipient local-part\n",
        encoding="utf-8",
    )
    terms_path = tmp_path / "finmind-terms.html"
    terms_path.write_text("terms", encoding="utf-8")
    manifest_path = tmp_path / "formal-source-rights.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "formal-source-rights/v1",
                "reviewed_at": "2026-08-19T02:00:00+08:00",
                "reviewer_owner": "owner-local",
                "deployment_jurisdiction": "Taiwan",
                "usage_scope": "personal_noncommercial_single_user_local_only",
                "decision": "qualified",
                "providers": [
                    {
                        "provider_id": "finmind-free-api",
                        "plan_id": "Free",
                        "credential_kind": "api_token",
                        "official_sender": "finmind.tw@gmail.com",
                        "response_file": response_path.name,
                        "response_sha256": "0" * 64,
                        "distribution_ids": [
                            "TaiwanStockPrice",
                            "TaiwanStockTradingDate",
                            "TaiwanStockDividendResult",
                            "TaiwanStockDelisting",
                            "TaiwanStockSplitPrice",
                        ],
                        "allowed_uses": [
                            "backup_restore",
                            "ingest",
                            "internal_display",
                            "model",
                            "retain_observed_history",
                            "transform",
                        ],
                        "terms_url": "https://finmind.github.io/PrivacyPolicy/",
                        "terms_file": terms_path.name,
                        "terms_sha256": hashlib.sha256(terms_path.read_bytes()).hexdigest(),
                    },
                    {},
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    database_url = f"sqlite+pysqlite:///{tmp_path / 'qualified-operator.db'}"
    StateStore(database_url, create_schema=True)

    with pytest.raises(ValueError, match="formal_source_rights_response_invalid"):
        main(
            [
                "authorization",
                "init-qualified-operator",
                "--database-url",
                database_url,
                "--key-file",
                str(owner_key_file),
                "--source-adapter-key-file",
                str(source_adapter_key_file),
                "--rights-manifest",
                str(manifest_path),
            ]
        )

    manifest_id = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    with pytest.raises(KeyError):
        AuthorizationPolicyRepository(StateStore(database_url, create_schema=False)).get(
            f"ticket-09-owner-operator-qualified-{manifest_id[:16]}",
            principal_id=owner.context.principal_id,
        )
