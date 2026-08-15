from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.data_supply import (
    HistoricalAvailabilityClaim,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_qualification import (
    QualificationAuthorizationError,
    TaiwanPriceQualificationWorkflow,
)


def test_historical_claim_cannot_be_minted_without_qualification_authorization() -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    manifest = load_taiwan_stock_pool_manifest()
    workflow = TaiwanPriceQualificationWorkflow(state_store)

    with pytest.raises(
        QualificationAuthorizationError,
        match="qualification_authorization_not_configured",
    ):
        workflow.register_historical_availability_claim(
            HistoricalAvailabilityClaim(
                source_id=manifest.historical_source_id,
                evidence_level="archive_attested",
                evidence_status="qualification_candidate",
                observed_start=date(2019, 8, 14),
                observed_end=date(2026, 8, 14),
                schema_version="taiwan-unadjusted-eod-v1",
                exact_sessions_verified=True,
                integrity_verified=True,
                company_actions_verified=True,
                listing_lifecycle_verified=True,
                qualification_artifact_id=None,
            ),
            trace_id="trace-unauthorized-candidate-claim",
        )

    with pytest.raises(KeyError):
        state_store.get_trace_evidence("trace-unauthorized-candidate-claim")


def test_formal_gate_rejects_an_existing_artifact_with_the_wrong_evidence_contract() -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
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
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-qualification-governor",
        environment="development",
        scopes={"price_qualification.govern"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    manifest = load_taiwan_stock_pool_manifest()
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-price-qualification-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"price_qualification.govern"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=tuple(
            SourcePolicyVersion(
                version_id=f"policy-price-qualification-{source_id}-v1",
                dataset_id=source_id,
                allowed_actions=frozenset({"price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=required_uses,
            )
            for source_id in (manifest.current_source_id, manifest.historical_source_id)
        ),
        source_entitlements=tuple(
            SourceEntitlement(
                version_id=f"entitlement-price-qualification-{source_id}-v1",
                principal_id=identity.context.principal_id,
                dataset_id=source_id,
                status="active",
                allowed_actions=frozenset({"price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=required_uses,
            )
            for source_id in (manifest.current_source_id, manifest.historical_source_id)
        ),
    )
    workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
    )
    historical_claim_id = workflow.register_historical_availability_claim(
        HistoricalAvailabilityClaim(
            source_id=manifest.historical_source_id,
            evidence_level="archive_attested",
            evidence_status="qualification_candidate",
            observed_start=date(2019, 8, 14),
            observed_end=date(2026, 8, 14),
            schema_version="taiwan-unadjusted-eod-v1",
            exact_sessions_verified=True,
            integrity_verified=True,
            company_actions_verified=True,
            listing_lifecycle_verified=True,
            qualification_artifact_id=None,
        ),
        trace_id="trace-candidate-claim",
    )
    claimed_manifest = replace(
        manifest,
        evidence_status="qualified",
        formal_qualification_artifact_id=historical_claim_id,
        historical_availability_claim_id=historical_claim_id,
    )
    sources: list[dict[str, object]] = [
        {
            "source_id": manifest.current_source_id,
            "source_mode": "current",
            "status": "published",
            "dataset_version_id": "sha256:current-dataset",
            "adjustment_version_id": "sha256:current-adjustment",
            "historical_availability_claim_id": None,
        },
        {
            "source_id": manifest.historical_source_id,
            "source_mode": "historical",
            "status": "published",
            "dataset_version_id": "sha256:historical-dataset",
            "adjustment_version_id": "sha256:historical-adjustment",
            "historical_availability_claim_id": historical_claim_id,
        },
    ]

    assert workflow.formal_qualification_available(claimed_manifest, sources) is False
    audit = state_store.list_audit_events(trace_id="trace-candidate-claim")
    assert len(audit) == 1
    assert audit[0]["action"] == "price_qualification.govern"
    assert audit[0]["outcome"] == "allowed"

    with pytest.raises(ValueError, match="formal_gate_requires_qualified_historical_claim"):
        workflow.register_formal_qualification_gate(
            manifest=manifest,
            historical_availability_claim_id=historical_claim_id,
            trace_id="trace-rejected-formal-gate",
        )
