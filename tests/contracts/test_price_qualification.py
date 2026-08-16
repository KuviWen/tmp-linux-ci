from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, uuid5

import pytest

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationAction,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceAccessMode,
    SourceDistribution,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.data_supply import (
    PRICE_RESEARCH_REQUIRED_USES,
    CanonicalPriceRow,
    CollectedSourceBundleMember,
    CollectedSourcePartition,
    DataSupply,
    DecodedSourcePartition,
    HistoricalArchiveAttestation,
    HistoricalAvailabilityClaim,
    ListingLifecycleRecord,
    LoadedSourcePartition,
    SourceBundleMemberRequest,
    SourceCollectionCoverage,
    SourcePartitionRequest,
    TaiwanStockPoolManifest,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.finmind_provider_contract import FINMIND_PROVIDER_DISTRIBUTIONS
from stock_forecasting.platform.object_repository import FilesystemObjectRepository, ObjectRef
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_eligibility_query import PriceEligibilityQuery
from stock_forecasting.price_qualification import (
    QualificationAuthorizationError,
    TaiwanPriceQualificationWorkflow,
)
from stock_forecasting.source_credentials import (
    CredentialValidationEvidence,
    SourceContractAssessment,
    pin_source_credential_lease,
)


class _QualificationLiteralAdapter:
    source_access_mode: SourceAccessMode = "live_provider"

    def __init__(self, loaded: LoadedSourcePartition) -> None:
        self.loaded = loaded

    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition:
        return self.loaded


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


def test_open_data_source_basis_is_derived_without_qualifying_history(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    terms_content = b"OGDL 1.0 terms captured from the qualified official distribution"
    terms_sha256 = hashlib.sha256(terms_content).hexdigest()
    manifest = load_taiwan_stock_pool_manifest()
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-open-data-governor",
        environment="development",
        scopes={"price_qualification.govern"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"public_source"},
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-open-data-qualification-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"price_qualification.govern"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-open-data-history-v1",
                dataset_id=manifest.historical_source_id,
                allowed_actions=frozenset({"price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="public_source",
                resource_states=frozenset({"active"}),
                allowed_uses=frozenset(
                    {
                        "ingest",
                        "retain_observed_history",
                        "transform",
                        "model",
                        "internal_display",
                        "backup_restore",
                    }
                ),
                access_basis="open_data_terms",
                license_id="OGDL-1.0",
                terms_url="https://data.gov.tw/license",
                terms_content_sha256=terms_sha256,
                attribution="政府資料開放授權條款－第1版（OGDL 1.0）",
                distributions=tuple(
                    SourceDistribution(
                        dataset_id=dataset.dataset_id,
                        distribution_url=dataset.distribution_url,
                    )
                    for dataset in manifest.source_basis.datasets
                ),
            ),
        ),
        source_entitlements=(),
    )
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    repository = FilesystemObjectRepository(tmp_path / "objects")
    workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
        object_repository=repository,
    )

    evidence_id = workflow.register_open_data_source_basis_evidence(
        manifest=manifest,
        source_id=manifest.historical_source_id,
        terms_content=terms_content,
        trace_id="trace-open-data-evidence",
    )

    assert state_store.get_verified_governance_artifact(
        artifact_id=evidence_id,
        artifact_kind="open_data_source_basis_evidence",
    ) == {
        "source_basis_id": "TWSE-OGDL-OPEN-DATA-01",
        "source_id": manifest.historical_source_id,
        "verification_status": "verified",
        "license_id": "OGDL-1.0",
        "terms_url": "https://data.gov.tw/license",
        "terms_content_sha256": terms_sha256,
        "terms_object_id": f"sha256:{terms_sha256}",
        "attribution": "政府資料開放授權條款－第1版（OGDL 1.0）",
        "source_policy_version_id": "policy-open-data-history-v1",
        "distributions": [
            {
                "dataset_id": dataset.dataset_id,
                "distribution_url": dataset.distribution_url,
            }
            for dataset in manifest.source_basis.datasets
        ],
    }
    claim = HistoricalAvailabilityClaim(
        source_id=manifest.historical_source_id,
        evidence_level="platform_observed",
        evidence_status="qualified",
        observed_start=date(2026, 8, 14),
        observed_end=date(2026, 8, 15),
        schema_version="taiwan-unadjusted-eod-v1",
        exact_sessions_verified=True,
        integrity_verified=True,
        company_actions_verified=True,
        listing_lifecycle_verified=True,
        qualification_artifact_id=evidence_id,
    )
    with pytest.raises(ValueError, match="qualified_claim_requires_historical_evidence"):
        workflow.register_historical_availability_claim(
            claim,
            trace_id="trace-terms-cannot-qualify-history",
        )
    with pytest.raises(ValueError, match="open_data_source_basis_evidence_invalid"):
        workflow.register_open_data_source_basis_evidence(
            manifest=manifest,
            source_id=manifest.historical_source_id,
            terms_content=b"",
            trace_id="trace-open-data-empty-terms",
        )


def test_finmind_zero_fee_basis_can_enter_the_same_formal_governance_path(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
    terms_content = b"Pinned FinMind free-plan terms for a qualified source policy"
    terms_sha256 = hashlib.sha256(terms_content).hexdigest()
    base_manifest = load_taiwan_stock_pool_manifest()
    basis = replace(
        base_manifest.authenticated_source_basis,
        terms_content_sha256=terms_sha256,
    )
    source_id = "finmind-taiwan-stock-price"
    manifest = replace(
        base_manifest.for_authenticated_source_path(),
        authenticated_source_basis=basis,
    )
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-finmind-governor",
        environment="development",
        scopes={"price_qualification.govern"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
        principal_classification="individual_or_internal_group",
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-finmind-qualification-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"price_qualification.govern"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=tuple(
            SourcePolicyVersion(
                version_id=f"policy-finmind-{distribution.policy_dataset_id}-v1",
                dataset_id=distribution.policy_dataset_id,
                allowed_actions=frozenset({"price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=PRICE_RESEARCH_REQUIRED_USES,
                access_basis="zero_fee_plan",
                source_basis_id=basis.source_basis_id,
                license_id="FinMind-Free-Plan-Terms",
                terms_url=basis.terms_url,
                terms_content_sha256=terms_sha256,
                attribution="FinMind",
                distributions=(
                    SourceDistribution(
                        dataset_id=distribution.distribution_id,
                        distribution_url=distribution.distribution_url,
                    ),
                ),
                provider_id=basis.provider_id,
                plan_id=basis.plan_id,
                principal_classification=basis.principal_classification,
                credential_kind=basis.credential_kind,
                account_required=True,
                fee_required=False,
            )
            for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
        ),
        source_entitlements=tuple(
            SourceEntitlement(
                version_id=f"entitlement-finmind-{distribution.policy_dataset_id}-v1",
                principal_id=identity.context.principal_id,
                dataset_id=distribution.policy_dataset_id,
                status="active",
                allowed_actions=frozenset({"price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=PRICE_RESEARCH_REQUIRED_USES,
            )
            for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
        ),
    )
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
    )

    evidence_id = workflow.register_zero_fee_source_basis_evidence(
        manifest=manifest,
        source_id=source_id,
        terms_content=terms_content,
        trace_id="trace-finmind-source-basis",
    )

    evidence = state_store.get_verified_governance_artifact(
        artifact_id=evidence_id,
        artifact_kind="zero_fee_source_basis_evidence",
    )
    assert evidence["source_basis_id"] == "FINMIND-FREE-TAIWAN-MARKET-DATA-01"
    assert evidence["source_id"] == source_id
    assert evidence["provider_id"] == "finmind-free-api"
    assert evidence["credential_kind"] == "bearer_token"
    assert evidence["terms_content_sha256"] == terms_sha256
    assert evidence["distributions"] == [
        {
            "dataset_id": member.dataset_id,
            "distribution_url": member.distribution_url,
        }
        for member in basis.members
    ]


def test_finmind_materialization_evidence_reaches_the_formal_eligibility_query(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
    terms_content = b"Pinned FinMind free-plan terms for full-gate contract evidence"
    terms_sha256 = hashlib.sha256(terms_content).hexdigest()
    repository = FilesystemObjectRepository(tmp_path / "objects")
    archived, _ = _archive_selection_sources(
        load_taiwan_stock_pool_manifest(),
        repository=repository,
        acquired_at=datetime(2026, 8, 15, 1, 0, tzinfo=UTC),
    )
    manifest = replace(
        archived.for_authenticated_source_path(),
        authenticated_source_basis=replace(
            archived.authenticated_source_basis,
            terms_content_sha256=terms_sha256,
        ),
    )
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-finmind-full-gate",
        environment="development",
        scopes={
            "market_data.collect",
            "price_qualification.govern",
            "price_research_eligibility.read",
        },
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
        principal_classification="individual_or_internal_group",
    )
    source_actions: frozenset[AuthorizationAction] = frozenset(
        {"market_data.collect", "price_qualification.govern"}
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-finmind-full-gate-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset(
                    {
                        "market_data.collect",
                        "price_qualification.govern",
                        "price_research_eligibility.read",
                    }
                ),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=tuple(
            SourcePolicyVersion(
                version_id=f"policy-finmind-full-{distribution.policy_dataset_id}-v1",
                dataset_id=distribution.policy_dataset_id,
                allowed_actions=source_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=PRICE_RESEARCH_REQUIRED_USES,
                access_basis="zero_fee_plan",
                source_basis_id=manifest.authenticated_source_basis.source_basis_id,
                license_id="FinMind-Free-Plan-Terms",
                terms_url=manifest.authenticated_source_basis.terms_url,
                terms_content_sha256=terms_sha256,
                attribution="FinMind",
                distributions=(
                    SourceDistribution(
                        dataset_id=distribution.distribution_id,
                        distribution_url=distribution.distribution_url,
                    ),
                ),
                provider_id=manifest.authenticated_source_basis.provider_id,
                plan_id=manifest.authenticated_source_basis.plan_id,
                principal_classification=(
                    manifest.authenticated_source_basis.principal_classification
                ),
                credential_kind=manifest.authenticated_source_basis.credential_kind,
                account_required=True,
                fee_required=False,
            )
            for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
        )
        + (
            SourcePolicyVersion(
                version_id="policy-finmind-full-price-read-v1",
                dataset_id="price-research-eligibility",
                allowed_actions=frozenset({"price_research_eligibility.read"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=tuple(
            SourceEntitlement(
                version_id=f"entitlement-finmind-full-{distribution.policy_dataset_id}-v1",
                principal_id=identity.context.principal_id,
                dataset_id=distribution.policy_dataset_id,
                status="active",
                allowed_actions=source_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=PRICE_RESEARCH_REQUIRED_USES,
            )
            for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
        )
        + (
            SourceEntitlement(
                version_id="entitlement-finmind-full-price-read-v1",
                principal_id=identity.context.principal_id,
                dataset_id="price-research-eligibility",
                status="active",
                allowed_actions=frozenset({"price_research_eligibility.read"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
    )
    primary, *bundle_distributions = FINMIND_PROVIDER_DISTRIBUTIONS
    listing_ids = tuple(listing.listing_id for listing in manifest.listings)
    coverage = SourceCollectionCoverage(
        requested_start=date(2024, 1, 3),
        requested_end=date(2024, 1, 3),
        observed_start=date(2024, 1, 3),
        observed_end=date(2024, 1, 3),
        complete=True,
    )
    bundle_requests = tuple(
        SourceBundleMemberRequest(
            dataset_id=distribution.policy_dataset_id,
            distribution_id=distribution.distribution_id,
            distribution_url=distribution.distribution_url,
            schema_version=f"{distribution.distribution_id}-v1",
        )
        for distribution in bundle_distributions
    )

    def loaded(
        request_id: str,
        revision: str,
        *,
        checkpoint_before: str | None = None,
    ) -> LoadedSourcePartition:
        collection = CollectedSourcePartition(
            request_id=request_id,
            source_id=primary.policy_dataset_id,
            acquired_at=now,
            sanitized_source_uri=primary.distribution_url,
            media_type="application/json",
            raw_payload=f'{{"request_id":"{request_id}"}}'.encode(),
            checkpoint_before=checkpoint_before,
            checkpoint_after=f"checkpoint:{revision}",
            coverage=coverage,
            source_revision=revision,
            bundle_members=tuple(
                CollectedSourceBundleMember(
                    dataset_id=distribution.policy_dataset_id,
                    distribution_id=distribution.distribution_id,
                    distribution_url=distribution.distribution_url,
                    media_type="application/json",
                    raw_payload=distribution.distribution_id.encode(),
                    coverage=coverage,
                    schema_version=f"{distribution.distribution_id}-v1",
                )
                for distribution in bundle_distributions
            ),
            requested_listing_ids=listing_ids,
            reference_graph_version_id=manifest.selection_evidence_version,
            reference_graph_lifecycle_verified=True,
            company_action_completeness_verified=True,
            market_calendar_evidence_version_id="engineering-xtai-calendar-2024-01-03-v1",
            historical_archive_attestation=archive_attestation,
        )
        decoded = DecodedSourcePartition(
            source_id=primary.policy_dataset_id,
            schema_version="taiwan-unadjusted-eod-v1",
            source_revision=revision,
            prices=tuple(
                CanonicalPriceRow(
                    listing_id=listing_id,
                    session_date=date(2024, 1, 3),
                    open=Decimal("100"),
                    high=Decimal("102"),
                    low=Decimal("99"),
                    close=Decimal("101"),
                    volume=1000,
                )
                for listing_id in listing_ids
            ),
            company_actions=(),
            listing_lifecycle=tuple(
                ListingLifecycleRecord(
                    listing_id=listing_id,
                    effective_date=date(2024, 1, 3),
                    status="active",
                    source_event_id=f"engineering-active:{listing_id}",
                )
                for listing_id in listing_ids
            ),
            adjusted_close_cross_checks=(),
            identity_assertion_ids=tuple(
                f"engineering-identity:{listing_id}" for listing_id in listing_ids
            ),
            parent_object_ids=(),
        )
        return LoadedSourcePartition(collection=collection, decoded=decoded)

    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    credential_authorization: dict[str, object] = {
        "evaluation_id": "ticket-06-finmind-test-credential-set",
        "action": "source_credential.manage",
        "reason_code": "source_credential_manage_authorized",
    }
    configured = state_store.publish_source_credential(
        provider_id="finmind-free-api",
        secret_ref_id="secret-ref:finmind-test-contract",
        readiness="configured",
        reason_code="source_credential_configured",
        configured_at=now.isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
        authorization=credential_authorization,
        trace_id="trace-finmind-test-credential-set",
    )
    contract_assessment = SourceContractAssessment(
        contract_id="finmind-ticket-06-live-v1",
        live_validation="passed",
        ticker_count=10,
        datasets=tuple(
            sorted(distribution.distribution_id for distribution in FINMIND_PROVIDER_DISTRIBUTIONS)
        ),
        symbol_lifecycle_probe="passed",
        universe_manifest_id=manifest.manifest_id,
        reference_graph_version_id=manifest.selection_evidence_version,
        listing_ids=listing_ids,
    )
    validation = state_store.record_source_credential_validation(
        provider_id="finmind-free-api",
        readiness="valid",
        reason_code="source_credential_valid",
        validated_at=now.isoformat(),
        expected_version=cast(int, configured["version"]),
        expected_secret_ref_id=cast(str, configured["secret_ref_id"]),
        validation_evidence=CredentialValidationEvidence(
            authentication_status="passed"
        ).as_payload(),
        source_contract_assessment=contract_assessment.as_payload(),
        authorization={
            "evaluation_id": "ticket-06-finmind-test-credential-validate",
            "action": "source_credential.manage",
            "reason_code": "source_credential_manage_authorized",
        },
        trace_id="trace-finmind-test-credential-validate",
    )
    current_credential = cast(dict[str, object], validation["credential"])
    pin_source_credential_lease(
        state_store,
        provider_id="finmind-free-api",
        current=current_credential,
        trace_id="trace-finmind-assessment",
        workload_principal_id=identity.context.principal_id,
        environment="development",
        source_id=primary.policy_dataset_id,
        destination="finmind-free-api",
        purpose="price_research_ingest",
        request_id="finmind-assessment",
        work_id="finmind-assessment:finmind-collect",
        lease_duration=timedelta(minutes=5),
        lease_issued_at=now,
    )
    contract_artifact_id = validation["source_contract_assessment_artifact_id"]
    assert isinstance(contract_artifact_id, str)
    archive_attestation = HistoricalArchiveAttestation(
        provider_id="finmind-free-api",
        archive_id="finmind-historical-reconstruction-contract",
        archive_version_id="finmind-archive-snapshot-2024-01-03-v1",
        revision_as_of=now - timedelta(days=1),
        credential_version=cast(int, current_credential["version"]),
        credential_lease_pin_event_id=str(
            uuid5(
                NAMESPACE_URL,
                "source-credential-lease-pin:finmind-free-api:finmind-assessment:finmind-collect",
            )
        ),
        source_contract_assessment_artifact_id=contract_artifact_id,
    )
    engineering_adapter = _QualificationLiteralAdapter(
        loaded("finmind-engineering", "engineering-v1")
    )
    engineering_adapter.source_access_mode = "engineering_double"
    engineering_supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={primary.policy_dataset_id: engineering_adapter},
        object_repository=repository,
        state_store=state_store,
        clock=lambda: now,
    )
    engineering_outcome = engineering_supply.materialize(
        SourcePartitionRequest(
            request_id="finmind-engineering",
            trace_id="trace-finmind-engineering",
            source_id=primary.policy_dataset_id,
            mode="historical",
            listing_ids=listing_ids,
            start_date=coverage.requested_start,
            end_date=coverage.requested_end,
            expected_checkpoint=None,
            distribution_id=primary.distribution_id,
            distribution_url=primary.distribution_url,
            source_basis_id=manifest.authenticated_source_basis.source_basis_id,
            bundle_members=bundle_requests,
        )
    )
    assert engineering_outcome.status == "quarantined"
    assert (
        "historical_qualification_assessment"
        not in state_store.get_trace_evidence("trace-finmind-engineering")["artifact_kinds"]
    )
    adapter = _QualificationLiteralAdapter(
        loaded(
            "finmind-assessment",
            "assessment-v1",
            checkpoint_before="checkpoint:engineering-v1",
        )
    )
    data_supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={primary.policy_dataset_id: adapter},
        object_repository=repository,
        state_store=state_store,
        clock=lambda: now,
    )

    def request(
        request_id: str,
        mode: str,
        *,
        claim_id: str | None = None,
        expected_checkpoint: str | None = None,
    ) -> SourcePartitionRequest:
        return SourcePartitionRequest(
            request_id=request_id,
            trace_id=f"trace-{request_id}",
            source_id=primary.policy_dataset_id,
            mode=mode,  # type: ignore[arg-type]
            listing_ids=listing_ids,
            start_date=coverage.requested_start,
            end_date=coverage.requested_end,
            expected_checkpoint=expected_checkpoint,
            distribution_id=primary.distribution_id,
            distribution_url=primary.distribution_url,
            source_basis_id=manifest.authenticated_source_basis.source_basis_id,
            bundle_members=bundle_requests,
            historical_availability_claim_id=claim_id,
        )

    assessment_outcome = data_supply.materialize(
        request(
            "finmind-assessment",
            "historical",
            expected_checkpoint="checkpoint:engineering-v1",
        )
    )
    assert assessment_outcome.status == "quarantined"
    assessment_trace = state_store.get_trace_evidence("trace-finmind-assessment")
    assessment_id = next(
        artifact_id
        for artifact_id, kind in zip(
            assessment_trace["artifact_ids"],
            assessment_trace["artifact_kinds"],
            strict=True,
        )
        if kind == "historical_qualification_assessment"
    )
    workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
        object_repository=repository,
    )
    qualification_evidence_id = workflow.register_historical_qualification_evidence(
        manifest=manifest,
        assessment_artifact_id=assessment_id,
        trace_id="trace-finmind-history-evidence",
    )
    assert coverage.observed_start is not None
    assert coverage.observed_end is not None
    claim_id = workflow.register_historical_availability_claim(
        HistoricalAvailabilityClaim(
            source_id=manifest.historical_source_id,
            evidence_level="archive_attested",
            evidence_status="qualified",
            observed_start=coverage.observed_start,
            observed_end=coverage.observed_end,
            schema_version="taiwan-unadjusted-eod-v1",
            exact_sessions_verified=True,
            integrity_verified=True,
            company_actions_verified=True,
            listing_lifecycle_verified=True,
            qualification_artifact_id=qualification_evidence_id,
        ),
        trace_id="trace-finmind-history-claim",
    )
    source_basis_evidence_id = workflow.register_zero_fee_source_basis_evidence(
        manifest=manifest,
        source_id=manifest.historical_source_id,
        terms_content=terms_content,
        trace_id="trace-finmind-full-source-basis",
    )
    gate_id = workflow.register_formal_qualification_gate(
        manifest=manifest,
        historical_availability_claim_id=claim_id,
        source_basis_evidence_id=source_basis_evidence_id,
        trace_id="trace-finmind-formal-gate",
    )
    qualified_manifest = replace(
        manifest,
        evidence_status="qualified",
        historical_availability_claim_id=claim_id,
        formal_qualification_artifact_id=gate_id,
    )
    adapter.loaded = loaded("finmind-current", "current-v1")
    current_outcome = data_supply.materialize(request("finmind-current", "current"))
    adapter.loaded = loaded(
        "finmind-historical",
        "historical-v1",
        checkpoint_before="checkpoint:assessment-v1",
    )
    historical_outcome = data_supply.materialize(
        request(
            "finmind-historical",
            "historical",
            claim_id=claim_id,
            expected_checkpoint="checkpoint:assessment-v1",
        )
    )

    sources = state_store.list_price_research_eligibility(listing_id=listing_ids[0])
    assert [current_outcome.status, historical_outcome.status] == ["published", "published"]
    assert workflow.formal_qualification_available(qualified_manifest, sources) is True
    persisted_gate = state_store.find_latest_price_qualification_gate(
        manifest_id=manifest.manifest_id,
        source_path_id=manifest.source_path_id,
    )
    assert persisted_gate is not None
    reloaded_manifest = load_taiwan_stock_pool_manifest().for_authenticated_source_path()
    reloaded_manifest = reloaded_manifest.with_formal_qualification_gate(
        artifact_id=persisted_gate[0],
        payload=persisted_gate[1],
    )
    assert workflow.formal_qualification_available(reloaded_manifest, sources) is True
    state_store.revoke_source_credential(
        provider_id="finmind-free-api",
        revoked_at=now.isoformat(),
        authorization={
            "evaluation_id": "ticket-06-finmind-test-credential-revoke",
            "action": "source_credential.manage",
            "reason_code": "source_credential_manage_authorized",
        },
        trace_id="trace-finmind-test-credential-revoke",
    )
    result = PriceEligibilityQuery(
        state_store,
        authorization_policy=policy,
        authorization_time=now,
        object_repository=repository,
    ).get_listing(
        listing_id=listing_ids[0],
        trace_id="trace-finmind-qualified-query",
        security_context=identity.context,
    )
    assert isinstance(result, dict)
    assert result["status"] == "credential_required"
    assert result["reason_code"] == "source_credential_revoked"
    assert result["formally_qualified"] is False, result
    assert result["source_basis_id"] == "FINMIND-FREE-TAIWAN-MARKET-DATA-01"


def test_formal_gate_rejects_an_existing_artifact_with_the_wrong_evidence_contract(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    required_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_observed_history",
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

    with pytest.raises(ValueError, match="formal_gate_requires_verified_source_archive"):
        workflow.register_formal_qualification_gate(
            manifest=manifest,
            historical_availability_claim_id=historical_claim_id,
            trace_id="trace-rejected-formal-gate",
        )

    rejected_audit = state_store.list_audit_events(trace_id="trace-rejected-formal-gate")
    assert len(rejected_audit) == 2
    assert {event["outcome"] for event in rejected_audit} == {"allowed"}
    rejection_trace = state_store.get_trace_evidence("trace-rejected-formal-gate")
    assert rejection_trace["artifact_kinds"] == ["qualification_governance_rejection"]
    rejection = state_store.get_canonical_artifact(rejection_trace["artifact_ids"][0])
    assert rejection["payload"] == {
        "operation": "register_formal_qualification_gate",
        "reason_code": "formal_gate_requires_verified_source_archive",
    }

    repository = FilesystemObjectRepository(tmp_path / "source-archive")
    archived_manifest, archive_objects = _archive_selection_sources(
        manifest,
        repository=repository,
        acquired_at=now,
    )
    archive_workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
        object_repository=repository,
    )
    with pytest.raises(ValueError, match="formal_gate_requires_qualified_historical_claim"):
        archive_workflow.register_formal_qualification_gate(
            manifest=archived_manifest,
            historical_availability_claim_id=historical_claim_id,
            trace_id="trace-archive-valid-claim-rejected",
        )

    Path(archive_objects[0].uri).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="formal_gate_requires_verified_source_archive"):
        archive_workflow.register_formal_qualification_gate(
            manifest=archived_manifest,
            historical_availability_claim_id=historical_claim_id,
            trace_id="trace-archive-corrupt-gate-rejected",
        )

    unreadable_workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
        object_repository=UnreadableObjectRepository(tmp_path / "unreadable-archive"),
    )
    with pytest.raises(ValueError, match="formal_gate_requires_verified_source_archive"):
        unreadable_workflow.register_formal_qualification_gate(
            manifest=archived_manifest,
            historical_availability_claim_id=historical_claim_id,
            trace_id="trace-archive-unreadable-gate-rejected",
        )
    unreadable_audit = state_store.list_audit_events(
        trace_id="trace-archive-unreadable-gate-rejected"
    )
    assert len(unreadable_audit) == 2
    assert {event["outcome"] for event in unreadable_audit} == {"allowed"}
    unreadable_trace = state_store.get_trace_evidence("trace-archive-unreadable-gate-rejected")
    unreadable_rejection = state_store.get_canonical_artifact(unreadable_trace["artifact_ids"][0])
    assert unreadable_rejection["payload"] == {
        "operation": "register_formal_qualification_gate",
        "reason_code": "formal_gate_requires_verified_source_archive",
    }

    with pytest.raises(
        ValueError,
        match="candidate_claim_cannot_assert_qualification_evidence",
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
                qualification_artifact_id="sha256:not-permitted-for-candidate",
            ),
            trace_id="trace-rejected-historical-claim",
        )

    claim_audit = state_store.list_audit_events(trace_id="trace-rejected-historical-claim")
    assert len(claim_audit) == 1
    assert claim_audit[0]["outcome"] == "allowed"
    claim_trace = state_store.get_trace_evidence("trace-rejected-historical-claim")
    claim_rejection = state_store.get_canonical_artifact(claim_trace["artifact_ids"][0])
    assert claim_rejection["payload"] == {
        "operation": "register_historical_availability_claim",
        "reason_code": "candidate_claim_cannot_assert_qualification_evidence",
    }

    partially_denied_policy = AuthorizationPolicy(
        action_grants=policy.action_grants,
        source_policies=policy.source_policies,
        source_entitlements=tuple(
            replace(
                entitlement,
                version_id="entitlement-price-qualification-historical-revoked-v2",
                status="revoked",
            )
            if entitlement.dataset_id == manifest.historical_source_id
            else entitlement
            for entitlement in policy.source_entitlements
        ),
    )
    partially_denied_workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=partially_denied_policy,
        security_context=identity.context,
        clock=lambda: now,
    )
    with pytest.raises(QualificationAuthorizationError, match="source_entitlement_revoked"):
        partially_denied_workflow.register_formal_qualification_gate(
            manifest=manifest,
            historical_availability_claim_id=historical_claim_id,
            trace_id="trace-partially-denied-formal-gate",
        )

    partial_audit = state_store.list_audit_events(trace_id="trace-partially-denied-formal-gate")
    assert len(partial_audit) == 2
    assert {event["outcome"] for event in partial_audit} == {"allowed", "denied"}
    partial_trace = state_store.get_trace_evidence("trace-partially-denied-formal-gate")
    partial_rejection = state_store.get_canonical_artifact(partial_trace["artifact_ids"][0])
    assert partial_rejection["payload"] == {
        "operation": "register_formal_qualification_gate",
        "reason_code": "source_entitlement_revoked",
    }


def _archive_selection_sources(
    manifest: TaiwanStockPoolManifest,
    *,
    repository: FilesystemObjectRepository,
    acquired_at: datetime,
) -> tuple[TaiwanStockPoolManifest, list[ObjectRef]]:
    archived_sources = []
    object_refs = []
    for reference in manifest.source_references:
        content = f"selection-source:{reference.source_reference_id}".encode()
        checksum = hashlib.sha256(content).hexdigest()
        object_ref = repository.put_verified(
            BytesIO(content),
            expected_checksum=checksum,
            metadata={"source_reference_id": reference.source_reference_id},
        )
        pending = replace(
            reference,
            observed_content_sha256=checksum,
            archival_status="verified",
            acquired_at=acquired_at,
            raw_object_id=object_ref.object_id,
            retrieval_receipt_id="pending",
        )
        archived_sources.append(
            replace(
                pending,
                retrieval_receipt_id=pending.expected_retrieval_receipt_id,
            )
        )
        object_refs.append(object_ref)
    return replace(manifest, source_references=tuple(archived_sources)), object_refs


class UnreadableObjectRepository(FilesystemObjectRepository):
    def open_by_id(self, object_id: str) -> BytesIO:
        raise PermissionError(object_id)
