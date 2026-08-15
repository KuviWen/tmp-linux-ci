from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationDecision,
    AuthorizationPolicy,
    CurrentSourcePrincipalAttributes,
    EntitlementStatus,
    IdentityVerificationError,
    LocalApiKeyIdentity,
    LocalApiKeyVerifier,
    OperationIntent,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceRightsEvidenceError,
    SourceUseRight,
    authorization_audit_payload,
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


@pytest.mark.parametrize(
    ("policy_uses", "entitlement_uses", "expected_reason"),
    [
        (
            frozenset({"ingest"}),
            frozenset(
                {
                    "ingest",
                    "retain_7_years",
                    "transform",
                    "model",
                    "internal_display",
                    "backup_restore",
                }
            ),
            "source_policy_use_denied",
        ),
        (
            frozenset(
                {
                    "ingest",
                    "retain_7_years",
                    "transform",
                    "model",
                    "internal_display",
                    "backup_restore",
                }
            ),
            frozenset({"ingest"}),
            "source_entitlement_use_denied",
        ),
    ],
)
def test_price_source_rights_fail_closed_when_a_required_use_is_missing(
    policy_uses: frozenset[SourceUseRight],
    entitlement_uses: frozenset[SourceUseRight],
    expected_reason: str,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-test",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    required_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_7_years",
            "transform",
            "model",
            "internal_display",
            "backup_restore",
        }
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-price-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-tw-price-v1",
                dataset_id="tw-qualified-price-current",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=policy_uses,
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-tw-price-v1",
                principal_id=identity.context.principal_id,
                dataset_id="tw-qualified-price-current",
                status="active",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=entitlement_uses,
            ),
        ),
    )

    decision = policy.evaluate(
        identity.context,
        OperationIntent(
            action="market_data.collect",
            dataset_id="tw-qualified-price-current",
            purpose="price_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id="trace-p2-trace-tw-01",
            correlation_id="request-ticket-06",
            required_uses=required_uses,
        ),
    )

    assert decision.allowed is False
    assert decision.reason_code == expected_reason


def test_price_source_rights_allow_collection_only_when_all_required_uses_are_effective() -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-test",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    required_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_7_years",
            "transform",
            "model",
            "internal_display",
            "backup_restore",
        }
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-price-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-tw-price-v1",
                dataset_id="tw-qualified-price-current",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=required_uses,
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-tw-price-v1",
                principal_id=identity.context.principal_id,
                dataset_id="tw-qualified-price-current",
                status="active",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=required_uses,
            ),
        ),
    )

    decision = policy.evaluate(
        identity.context,
        OperationIntent(
            action="market_data.collect",
            dataset_id="tw-qualified-price-current",
            purpose="price_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id="trace-p2-trace-tw-01",
            correlation_id="request-ticket-06",
            required_uses=required_uses,
        ),
    )

    assert decision.allowed is True
    assert decision.reason_code == "authorized"
    assert decision.required_uses == required_uses
    assert authorization_audit_payload(decision)["required_uses"] == sorted(required_uses)


@dataclass(frozen=True)
class _CurrentSourceRightsContract:
    now: datetime
    source_id: str
    policy: AuthorizationPolicy
    prior: AuthorizationDecision
    prior_evidence: dict[str, object]
    required_uses: frozenset[SourceUseRight]
    current_subject: CurrentSourcePrincipalAttributes


def _current_source_rights_contract() -> _CurrentSourceRightsContract:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    source_id = "twse-rights-binding-contract"
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-source-workload",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    required_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_7_years",
            "transform",
            "model",
            "internal_display",
            "backup_restore",
        }
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-rights-binding-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-rights-binding-v1",
                dataset_id=source_id,
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=required_uses,
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-rights-binding-v1",
                principal_id=identity.context.principal_id,
                dataset_id=source_id,
                status="active",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=required_uses,
            ),
        ),
    )
    prior = policy.evaluate(
        identity.context,
        OperationIntent(
            action="market_data.collect",
            dataset_id=source_id,
            purpose="price_research",
            environment="development",
            resource_state="active",
            evaluated_at=now,
            trace_id="trace-rights-binding-materialization",
            correlation_id="request-rights-binding",
            required_uses=required_uses,
        ),
    )
    prior_evidence = {
        **authorization_audit_payload(prior),
        "outcome": "allowed",
        "trace_id": prior.trace_id,
    }
    return _CurrentSourceRightsContract(
        now=now,
        source_id=source_id,
        policy=policy,
        prior=prior,
        prior_evidence=prior_evidence,
        required_uses=required_uses,
        current_subject=CurrentSourcePrincipalAttributes(
            principal_id=identity.context.principal_id,
            evidence_id="subject-attributes-rights-binding-v1",
            environment="development",
            data_protection_classes=frozenset({"licensed"}),
            valid_from=now - timedelta(minutes=5),
            valid_to=now + timedelta(minutes=5),
        ),
    )


def test_current_source_rights_reject_prior_authorization_bound_to_another_source() -> None:
    contract = _current_source_rights_contract()

    with pytest.raises(
        SourceRightsEvidenceError,
        match="source_rights_prior_evidence_mismatch",
    ):
        contract.policy.evaluate_current_source_rights(
            contract.prior_evidence,
            expected_dataset_id="tpex-rights-binding-contract",
            expected_evaluation_id=contract.prior.evaluation_id,
            expected_decision_id=contract.prior.decision_id,
            expected_trace_id=contract.prior.trace_id,
            expected_correlation_id=contract.prior.correlation_id,
            current_runtime_environment="development",
            current_subject=contract.current_subject,
            evaluated_at=contract.now + timedelta(minutes=1),
            trace_id="trace-rights-binding-query",
            correlation_id="request-rights-binding-query",
            required_uses=contract.required_uses,
        )


def test_current_source_rights_use_current_runtime_and_subject_qualification() -> None:
    contract = _current_source_rights_contract()

    allowed = contract.policy.evaluate_current_source_rights(
        contract.prior_evidence,
        expected_dataset_id=contract.source_id,
        expected_evaluation_id=contract.prior.evaluation_id,
        expected_decision_id=contract.prior.decision_id,
        expected_trace_id=contract.prior.trace_id,
        expected_correlation_id=contract.prior.correlation_id,
        current_runtime_environment="development",
        current_subject=contract.current_subject,
        evaluated_at=contract.now + timedelta(minutes=1),
        trace_id="trace-current-subject-allowed",
        correlation_id="request-current-subject-allowed",
        required_uses=contract.required_uses,
    )
    cross_environment = contract.policy.evaluate_current_source_rights(
        contract.prior_evidence,
        expected_dataset_id=contract.source_id,
        expected_evaluation_id=contract.prior.evaluation_id,
        expected_decision_id=contract.prior.decision_id,
        expected_trace_id=contract.prior.trace_id,
        expected_correlation_id=contract.prior.correlation_id,
        current_runtime_environment="production",
        current_subject=replace(contract.current_subject, environment="production"),
        evaluated_at=contract.now + timedelta(minutes=1),
        trace_id="trace-current-subject-production",
        correlation_id="request-current-subject-production",
        required_uses=contract.required_uses,
    )
    qualification_removed = contract.policy.evaluate_current_source_rights(
        contract.prior_evidence,
        expected_dataset_id=contract.source_id,
        expected_evaluation_id=contract.prior.evaluation_id,
        expected_decision_id=contract.prior.decision_id,
        expected_trace_id=contract.prior.trace_id,
        expected_correlation_id=contract.prior.correlation_id,
        current_runtime_environment="development",
        current_subject=replace(
            contract.current_subject,
            evidence_id="subject-attributes-rights-binding-v2",
            data_protection_classes=frozenset(),
        ),
        evaluated_at=contract.now + timedelta(minutes=1),
        trace_id="trace-current-subject-qualification-removed",
        correlation_id="request-current-subject-qualification-removed",
        required_uses=contract.required_uses,
    )

    assert allowed.allowed is True
    assert allowed.reason_code == "authorized"
    assert allowed.runtime_environment == "development"
    assert allowed.subject_attributes_evidence_id == contract.current_subject.evidence_id
    assert allowed.subject_data_protection_classes == frozenset({"licensed"})
    assert allowed.valid_until == contract.current_subject.valid_to
    assert cross_environment.allowed is False
    assert cross_environment.reason_code == "action_grant_environment_denied"
    assert qualification_removed.allowed is False
    assert qualification_removed.reason_code == "data_protection_class_denied"


def test_current_source_rights_reject_future_dated_prior_authorization() -> None:
    contract = _current_source_rights_contract()

    with pytest.raises(
        SourceRightsEvidenceError,
        match="source_rights_prior_evidence_invalid",
    ):
        contract.policy.evaluate_current_source_rights(
            contract.prior_evidence,
            expected_dataset_id=contract.source_id,
            expected_evaluation_id=contract.prior.evaluation_id,
            expected_decision_id=contract.prior.decision_id,
            expected_trace_id=contract.prior.trace_id,
            expected_correlation_id=contract.prior.correlation_id,
            current_runtime_environment="development",
            current_subject=contract.current_subject,
            evaluated_at=contract.now - timedelta(microseconds=1),
            trace_id="trace-future-prior-evidence",
            correlation_id="request-future-prior-evidence",
            required_uses=contract.required_uses,
        )
