from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    EntitlementStatus,
    IdentityVerificationError,
    LocalApiKeyIdentity,
    LocalApiKeyVerifier,
    OperationIntent,
    SourceEntitlement,
    SourcePolicyVersion,
)
from stock_forecasting.cli import main


def test_loopback_development_key_creates_a_trusted_security_context() -> None:
    issued_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    credential, verifier = LocalApiKeyVerifier.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=24),
    )

    context = verifier.authenticate(
        credential.authorization_header(),
        client_host="127.0.0.1",
        environment="development",
        authenticated_at=issued_at + timedelta(minutes=1),
    )

    assert context.trusted is True
    assert context.owner == "local-researcher"
    assert context.environment == "development"
    assert context.scopes == frozenset({"fixture_pipeline.execute", "research_prediction.read"})
    assert context.expires_at == issued_at + timedelta(hours=24)
    assert context.authentication_method == "local_api_key"


def test_trusted_security_context_cannot_be_copied_with_forged_claims() -> None:
    issued_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"research_prediction.read"},
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=24),
    )

    with pytest.raises(TypeError, match="trusted_security_context_factory_required"):
        replace(
            identity.context,
            scopes=frozenset({"fixture_pipeline.execute", "research_prediction.read"}),
        )


def test_local_api_key_rejects_a_non_loopback_client() -> None:
    issued_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    credential, verifier = LocalApiKeyVerifier.issue(
        owner="local-researcher",
        environment="development",
        scopes={"research_prediction.read"},
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=24),
    )

    with pytest.raises(
        IdentityVerificationError,
        match="local_api_key_loopback_required",
    ):
        verifier.authenticate(
            credential.authorization_header(),
            client_host="192.0.2.10",
            environment="development",
            authenticated_at=issued_at + timedelta(minutes=1),
        )


def test_local_api_key_file_reloads_one_identity_without_exposing_secret_in_repr(
    tmp_path: Path,
) -> None:
    issued_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    key_file = tmp_path / "run" / "local-api-key.json"
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=24),
    )

    identity.save(key_file)
    reloaded = LocalApiKeyIdentity.load(key_file)
    context = reloaded.verifier.authenticate(
        identity.credential.authorization_header(),
        client_host="127.0.0.1",
        environment="development",
        authenticated_at=issued_at + timedelta(minutes=1),
    )

    assert context.principal_id == identity.context.principal_id
    assert context.scopes == identity.context.scopes
    assert "secret=<redacted>" in repr(reloaded.credential)
    assert identity.credential.authorization_header() not in repr(reloaded)


def test_reusing_an_owner_label_does_not_reuse_the_principal_identity() -> None:
    issued_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)

    first = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"research_prediction.read"},
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=24),
    )
    second = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"research_prediction.read"},
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=24),
    )

    assert first.context.principal_id != second.context.principal_id


def test_local_api_key_rejects_a_lifetime_over_thirty_days() -> None:
    issued_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="local_api_key_lifetime_exceeded"):
        LocalApiKeyIdentity.issue(
            owner="local-researcher",
            environment="development",
            scopes={"research_prediction.read"},
            issued_at=issued_at,
            expires_at=issued_at + timedelta(days=31),
        )


def test_local_key_cli_initializes_ephemeral_secret_file_without_printing_credential(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_file = tmp_path / "run" / "local-api-key.json"
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(hours=24)
    arguments = [
        "local-key",
        "init",
        "--path",
        str(key_file),
        "--owner",
        "local-researcher",
        "--environment",
        "development",
        "--scope",
        "fixture_pipeline.execute",
        "--scope",
        "research_prediction.read",
        "--issued-at",
        issued_at.isoformat(),
        "--expires-at",
        expires_at.isoformat(),
    ]

    return_code = main(arguments)
    reused_return_code = main(arguments)

    identity = LocalApiKeyIdentity.load(key_file)
    output = capsys.readouterr().out
    assert return_code == 0
    assert reused_return_code == 0
    assert output.splitlines() == [
        '{"status": "initialized"}',
        '{"status": "existing"}',
    ]
    assert identity.credential.authorization_header() not in output


def test_local_key_cli_defaults_to_a_fresh_short_lived_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_file = tmp_path / "run" / "local-api-key.json"
    before = datetime.now(UTC)
    arguments = [
        "local-key",
        "init",
        "--path",
        str(key_file),
        "--owner",
        "local-researcher",
        "--environment",
        "development",
        "--scope",
        "fixture_pipeline.execute",
        "--scope",
        "research_prediction.read",
    ]

    return_code = main(arguments)
    reused_return_code = main(arguments)
    after = datetime.now(UTC)
    identity = LocalApiKeyIdentity.load(key_file)

    assert return_code == 0
    assert reused_return_code == 0
    assert before <= identity.context.issued_at <= after
    assert identity.context.expires_at - identity.context.issued_at == timedelta(hours=24)
    assert capsys.readouterr().out.splitlines() == [
        '{"status": "initialized"}',
        '{"status": "existing"}',
    ]


def test_active_grant_entitlement_and_policy_allow_fixture_pipeline() -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    credential, verifier = LocalApiKeyVerifier.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
    )
    context = verifier.authenticate(
        credential.authorization_header(),
        client_host="127.0.0.1",
        environment="development",
        authenticated_at=now,
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-local-research-v1",
                principal_id=context.principal_id,
                actions=frozenset({"fixture_pipeline.execute"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-xtai-fixture-v1",
                dataset_id="xtai-fixture-eod",
                allowed_actions=frozenset({"fixture_pipeline.execute"}),
                purposes=frozenset({"fixture_research"}),
                environments=frozenset({"development"}),
                data_protection_class="internal",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-xtai-fixture-v1",
                principal_id=context.principal_id,
                dataset_id="xtai-fixture-eod",
                status="active",
                allowed_actions=frozenset({"fixture_pipeline.execute"}),
                purposes=frozenset({"fixture_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
    )

    decision = policy.evaluate(
        context,
        OperationIntent(
            action="fixture_pipeline.execute",
            dataset_id="xtai-fixture-eod",
            purpose="fixture_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id="trace-ticket-04-allow",
            correlation_id="correlation-ticket-04-allow",
        ),
    )

    assert decision.allowed is True
    assert decision.reason_code == "authorized"
    assert decision.grant_version_id == "grant-local-research-v1"
    assert decision.source_policy_version_id == "policy-xtai-fixture-v1"
    assert decision.source_entitlement_version_id == "entitlement-xtai-fixture-v1"
    assert decision.data_protection_class == "internal"
    assert decision.trace_id == "trace-ticket-04-allow"


@pytest.mark.parametrize(
    ("scenario", "expected_reason"),
    [
        ("grant_missing", "action_grant_missing"),
        ("entitlement_missing", "source_entitlement_missing"),
        ("policy_unknown", "source_policy_unknown"),
        ("suspended", "source_entitlement_suspended"),
        ("expired", "source_entitlement_expired"),
        ("revoked", "source_entitlement_revoked"),
        ("purpose_removed", "source_entitlement_purpose_denied"),
        ("classification_denied", "data_protection_class_denied"),
    ],
)
def test_authorization_decision_matrix_fails_closed(
    scenario: str,
    expected_reason: str,
) -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    credential, verifier = LocalApiKeyVerifier.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
    )
    context = verifier.authenticate(
        credential.authorization_header(),
        client_host="127.0.0.1",
        environment="development",
        authenticated_at=now,
    )
    grant = ActionGrant(
        version_id="grant-local-research-v1",
        principal_id=context.principal_id,
        actions=frozenset(set() if scenario == "grant_missing" else {"fixture_pipeline.execute"}),
        environment="development",
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
    )
    source_policy = SourcePolicyVersion(
        version_id="policy-xtai-fixture-v1",
        dataset_id="xtai-fixture-eod",
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset({"fixture_research"}),
        environments=frozenset({"development"}),
        data_protection_class=("restricted" if scenario == "classification_denied" else "internal"),
        resource_states=frozenset({"active"}),
    )
    entitlement = SourceEntitlement(
        version_id=f"entitlement-xtai-fixture-{scenario}-v1",
        principal_id=context.principal_id,
        dataset_id="xtai-fixture-eod",
        status=cast(
            EntitlementStatus,
            scenario if scenario in {"suspended", "expired", "revoked"} else "active",
        ),
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset(set() if scenario == "purpose_removed" else {"fixture_research"}),
        environments=frozenset({"development"}),
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
    )
    policy = AuthorizationPolicy(
        action_grants=(grant,),
        source_policies=() if scenario == "policy_unknown" else (source_policy,),
        source_entitlements=() if scenario == "entitlement_missing" else (entitlement,),
    )

    decision = policy.evaluate(
        context,
        OperationIntent(
            action="fixture_pipeline.execute",
            dataset_id="xtai-fixture-eod",
            purpose="fixture_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id=f"trace-ticket-04-{scenario}",
            correlation_id=f"correlation-ticket-04-{scenario}",
        ),
    )

    assert decision.allowed is False
    assert decision.reason_code == expected_reason
    assert decision.trace_id == f"trace-ticket-04-{scenario}"


def test_platform_admin_identity_cannot_bypass_a_missing_source_entitlement() -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    credential, verifier = LocalApiKeyVerifier.issue(
        owner="platform-admin",
        environment="development",
        scopes={"fixture_pipeline.execute"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
    )
    context = verifier.authenticate(
        credential.authorization_header(),
        client_host="127.0.0.1",
        environment="development",
        authenticated_at=now,
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-platform-admin-v1",
                principal_id=context.principal_id,
                actions=frozenset({"fixture_pipeline.execute"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-xtai-fixture-v1",
                dataset_id="xtai-fixture-eod",
                allowed_actions=frozenset({"fixture_pipeline.execute"}),
                purposes=frozenset({"fixture_research"}),
                environments=frozenset({"development"}),
                data_protection_class="internal",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=(),
    )

    decision = policy.evaluate(
        context,
        OperationIntent(
            action="fixture_pipeline.execute",
            dataset_id="xtai-fixture-eod",
            purpose="fixture_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id="trace-ticket-04-platform-admin",
            correlation_id="trace-ticket-04-platform-admin",
        ),
    )

    assert decision.allowed is False
    assert decision.reason_code == "source_entitlement_missing"


def test_conflicting_entitlement_versions_fail_closed_instead_of_using_tuple_order() -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    credential, verifier = LocalApiKeyVerifier.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
    )
    context = verifier.authenticate(
        credential.authorization_header(),
        client_host="127.0.0.1",
        environment="development",
        authenticated_at=now,
    )
    grant = ActionGrant(
        version_id="grant-local-research-v1",
        principal_id=context.principal_id,
        actions=frozenset({"fixture_pipeline.execute"}),
        environment="development",
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
    )
    source_policy = SourcePolicyVersion(
        version_id="policy-xtai-fixture-v1",
        dataset_id="xtai-fixture-eod",
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset({"fixture_research"}),
        environments=frozenset({"development"}),
        data_protection_class="internal",
        resource_states=frozenset({"active"}),
    )
    active = SourceEntitlement(
        version_id="entitlement-xtai-active-v1",
        principal_id=context.principal_id,
        dataset_id="xtai-fixture-eod",
        status="active",
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset({"fixture_research"}),
        environments=frozenset({"development"}),
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
    )
    revoked = SourceEntitlement(
        version_id="entitlement-xtai-revoked-v2",
        principal_id=context.principal_id,
        dataset_id="xtai-fixture-eod",
        status="revoked",
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset({"fixture_research"}),
        environments=frozenset({"development"}),
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
    )
    policy = AuthorizationPolicy(
        action_grants=(grant,),
        source_policies=(source_policy,),
        source_entitlements=(active, revoked),
    )

    decision = policy.evaluate(
        context,
        OperationIntent(
            action="fixture_pipeline.execute",
            dataset_id="xtai-fixture-eod",
            purpose="fixture_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id="trace-ticket-04-entitlement-conflict",
            correlation_id="trace-ticket-04-entitlement-conflict",
        ),
    )

    assert decision.allowed is False
    assert decision.reason_code == "source_entitlement_conflict"
    assert decision.source_entitlement_version_id is None


def test_non_overlapping_entitlement_history_selects_the_version_effective_at_evaluation() -> None:
    now = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute"},
        issued_at=now - timedelta(days=2),
        expires_at=now + timedelta(days=2),
    )
    context = identity.context
    grant = ActionGrant(
        version_id="grant-current",
        principal_id=context.principal_id,
        actions=frozenset({"fixture_pipeline.execute"}),
        environment="development",
        valid_from=now - timedelta(days=2),
        valid_to=now + timedelta(days=2),
    )
    policy = SourcePolicyVersion(
        version_id="policy-current",
        dataset_id="xtai-fixture-eod",
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset({"fixture_research"}),
        environments=frozenset({"development"}),
        data_protection_class="internal",
        resource_states=frozenset({"active"}),
        valid_from=now - timedelta(days=2),
        valid_to=now + timedelta(hours=12),
    )
    historical = SourceEntitlement(
        version_id="entitlement-historical",
        principal_id=context.principal_id,
        dataset_id="xtai-fixture-eod",
        status="expired",
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset({"fixture_research"}),
        environments=frozenset({"development"}),
        valid_from=now - timedelta(days=2),
        valid_to=now - timedelta(days=1),
    )
    current = SourceEntitlement(
        version_id="entitlement-current",
        principal_id=context.principal_id,
        dataset_id="xtai-fixture-eod",
        status="active",
        allowed_actions=frozenset({"fixture_pipeline.execute"}),
        purposes=frozenset({"fixture_research"}),
        environments=frozenset({"development"}),
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
    )

    decision = AuthorizationPolicy(
        action_grants=(grant,),
        source_policies=(policy,),
        source_entitlements=(historical, current),
    ).evaluate(
        context,
        OperationIntent(
            action="fixture_pipeline.execute",
            dataset_id="xtai-fixture-eod",
            purpose="fixture_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id="trace-effective-entitlement",
            correlation_id="trace-effective-entitlement",
        ),
    )

    assert decision.allowed is True
    assert decision.source_entitlement_version_id == "entitlement-current"
    assert decision.valid_until == now + timedelta(hours=12)
