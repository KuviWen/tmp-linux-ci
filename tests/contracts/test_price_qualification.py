from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import cast

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
from stock_forecasting.historical_evidence import (
    HistoricalEvidenceAttestationCommand,
    HistoricalEvidenceAttestationIssuer,
    HistoricalEvidenceCommand,
    HistoricalEvidenceWorkflow,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository, ObjectRef
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_qualification import (
    QualificationAuthorizationError,
    TaiwanPriceQualificationWorkflow,
)
from stock_forecasting.ticket_08_acceptance import build_ticket_08_engineering_governance


class _QualificationLiteralAdapter:
    source_access_mode: SourceAccessMode = "engineering_double"

    def __init__(self, loaded: LoadedSourcePartition) -> None:
        self.loaded = loaded

    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition:
        return self.loaded


def test_independently_verified_claim_can_create_formal_qualification_gate(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
    terms_content = b"Pinned OGDL terms for ticket 08 formal qualification"
    terms_sha256 = hashlib.sha256(terms_content).hexdigest()
    manifest = load_taiwan_stock_pool_manifest()
    repository = FilesystemObjectRepository(tmp_path / "objects")
    archived_manifest, _ = _archive_selection_sources(
        manifest,
        repository=repository,
        acquired_at=datetime(2026, 8, 14, 1, 0, tzinfo=UTC),
    )
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-08-qualification-governor",
        environment="development",
        scopes={"price_qualification.govern"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"public_source"},
    )
    collector = LocalApiKeyIdentity.issue(
        owner="ticket-08-history-collector",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"public_source"},
    )
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
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="ticket-08-formal-gate-grant-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"price_qualification.govern"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
            ActionGrant(
                version_id="ticket-08-history-collector-grant-v1",
                principal_id=collector.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=tuple(
            SourcePolicyVersion(
                version_id=f"ticket-08-{source_id}-policy-v1",
                dataset_id=source_id,
                allowed_actions=frozenset({"market_data.collect", "price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="public_source",
                resource_states=frozenset({"active"}),
                allowed_uses=required_uses,
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
            )
            for source_id in (manifest.current_source_id, manifest.historical_source_id)
        ),
        source_entitlements=(),
    )
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    listing_id = manifest.listings[0].listing_id
    sessions: list[str] = []
    candidate_session = date(2026, 5, 4)
    while len(sessions) < 41:
        if candidate_session.weekday() < 5:
            sessions.append(candidate_session.isoformat())
        candidate_session += timedelta(days=1)
    evidence: dict[str, object] = {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": "taiwan-unadjusted-eod-v1",
        "evidence_version": "ticket-08-formal-platform-history-v1",
        "revision": "rev-1",
        "observation_kind": "platform_observation",
        "observation_reference": "platform://ticket-08/formal-taiwan-history",
        "observed_at": "2026-08-15T22:00:00+00:00",
        "coverage": {"start": sessions[0], "end": sessions[-1]},
        "validity": {
            "valid_from": "2026-08-15T22:00:00+00:00",
            "valid_until": "2026-09-15T22:00:00+00:00",
        },
        "public_terms_url": "https://data.gov.tw/license",
        "calendar_version": "xtai-realized-calendar-v1",
        "adjustment_rule_version": "internal-price-adjustment/v1",
        "label_rule_version": "trend-label-rule/v1",
        "code_provenance": "git:ticket-08-formal-test",
        "listings": [
            {
                "listing_id": listing_id,
                "market": "XTAI",
                "security_id": manifest.listings[0].security_id,
                "symbols": [
                    {
                        "symbol": manifest.listings[0].external_security_code,
                        "valid_from": "2019-08-14",
                        "valid_to": None,
                    }
                ],
                "sessions": sessions,
                "unadjusted_prices": [
                    {"session_date": session, "close": "100.00"} for session in sessions
                ],
                "company_actions": [],
                "company_actions_status": "complete",
                "lifecycle": [
                    {
                        "status": "active",
                        "effective_date": "2019-08-14",
                        "source_event_id": "ticket-08-formal-listing",
                    }
                ],
            }
        ],
    }
    content = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    listing_evidence = cast(list[dict[str, object]], evidence["listings"])[0]
    historical_distribution_url = manifest.source_basis.datasets[0].distribution_url
    calendar_content = json.dumps(
        {
            "schema_version": "historical-realized-calendar/v1",
            "source_reference": historical_distribution_url,
            "market": "XTAI",
            "version": evidence["calendar_version"],
            "sessions": listing_evidence["sessions"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    reference_content = json.dumps(
        {
            "schema_version": "historical-listing-reference/v1",
            "source_reference": historical_distribution_url,
            "listing": {
                key: listing_evidence[key]
                for key in (
                    "listing_id",
                    "market",
                    "security_id",
                    "symbols",
                    "lifecycle",
                    "company_actions",
                )
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    history_issuer = HistoricalEvidenceAttestationIssuer(
        state_store,
        object_repository=repository,
        authorization_policy=policy,
        security_context=collector.context,
        clock=lambda: now,
    )
    attestation_id = history_issuer.issue(
        HistoricalEvidenceAttestationCommand(
            listing_id=listing_id,
            market="XTAI",
            source_id=manifest.historical_source_id,
            evidence_content=content,
            calendar_content=calendar_content,
            reference_content=reference_content,
            trace_id="trace-ticket-08-formal-attestation",
        )
    )
    history_workflow = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=repository,
        observed_at=now,
        authorization_policy=policy,
        security_context=identity.context,
    )
    claim = history_workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id=listing_id,
            market="XTAI",
            source_id=manifest.historical_source_id,
            trace_id="trace-ticket-08-formal-claim",
            attestation_id=attestation_id,
        )
    )
    assert claim.claim_id is not None
    platform_snapshot = state_store.get_canonical_artifact(claim.artifact_ids["feature_snapshot"])[
        "payload"
    ]
    assert isinstance(platform_snapshot, dict)
    assert platform_snapshot["information_cutoff"] == now.isoformat()
    workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
        object_repository=repository,
    )
    source_basis_id = workflow.register_open_data_source_basis_evidence(
        manifest=manifest,
        source_id=manifest.historical_source_id,
        terms_content=terms_content,
        trace_id="trace-ticket-08-source-basis",
    )

    gate_id = workflow.register_formal_qualification_gate(
        manifest=archived_manifest,
        historical_availability_claim_id=claim.claim_id,
        source_basis_evidence_id=source_basis_id,
        trace_id="trace-ticket-08-formal-gate",
    )
    gate_payload = state_store.get_verified_governance_artifact(
        artifact_id=gate_id,
        artifact_kind="taiwan_price_qualification_gate",
    )
    qualified_manifest = archived_manifest.with_formal_qualification_gate(
        artifact_id=gate_id,
        payload=gate_payload,
    )
    sources: list[dict[str, object]] = [
        {
            "source_id": qualified_manifest.current_source_id,
            "source_mode": "current",
            "status": "published",
            "dataset_version_id": "sha256:current-dataset",
            "adjustment_version_id": "sha256:current-adjustment",
            "historical_availability_claim_id": None,
        },
        {
            "source_id": qualified_manifest.historical_source_id,
            "source_mode": "historical",
            "status": "published",
            "dataset_version_id": "sha256:historical-dataset",
            "adjustment_version_id": "sha256:historical-adjustment",
            "historical_availability_claim_id": claim.claim_id,
        },
    ]
    assert workflow.formal_qualification_available(qualified_manifest, sources) is True

    incomplete_evidence = deepcopy(evidence)
    for field_name in (
        "adjustment_rule_version",
        "label_rule_version",
        "code_provenance",
    ):
        incomplete_evidence.pop(field_name)
    incomplete_content = json.dumps(
        incomplete_evidence,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    incomplete_attestation_id = history_issuer.issue(
        HistoricalEvidenceAttestationCommand(
            listing_id=listing_id,
            market="XTAI",
            source_id=manifest.historical_source_id,
            evidence_content=incomplete_content,
            calendar_content=calendar_content,
            reference_content=reference_content,
            trace_id="trace-ticket-08-incomplete-formal-attestation",
        )
    )
    incomplete_claim = history_workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id=listing_id,
            market="XTAI",
            source_id=manifest.historical_source_id,
            trace_id="trace-ticket-08-incomplete-formal-claim",
            attestation_id=incomplete_attestation_id,
        )
    )
    assert incomplete_claim.claim_id is not None
    with pytest.raises(
        ValueError,
        match="formal_gate_requires_complete_historical_reconstruction",
    ):
        workflow.register_formal_qualification_gate(
            manifest=archived_manifest,
            historical_availability_claim_id=incomplete_claim.claim_id,
            source_basis_evidence_id=source_basis_id,
            trace_id="trace-ticket-08-incomplete-formal-gate",
        )

    engineering_collector, engineering_governor, engineering_policy = (
        build_ticket_08_engineering_governance(
            observed_at=now,
            source_ids=(manifest.historical_source_id,),
        )
    )
    engineering_distribution_url = (
        f"https://archive.example.test/{manifest.historical_source_id}.json"
    )
    engineering_calendar_content = json.dumps(
        {
            "schema_version": "historical-realized-calendar/v1",
            "source_reference": engineering_distribution_url,
            "market": "XTAI",
            "version": evidence["calendar_version"],
            "sessions": listing_evidence["sessions"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    engineering_reference_content = json.dumps(
        {
            "schema_version": "historical-listing-reference/v1",
            "source_reference": engineering_distribution_url,
            "listing": {
                key: listing_evidence[key]
                for key in (
                    "listing_id",
                    "market",
                    "security_id",
                    "symbols",
                    "lifecycle",
                    "company_actions",
                )
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    engineering_attestation_id = HistoricalEvidenceAttestationIssuer(
        state_store,
        object_repository=repository,
        authorization_policy=engineering_policy,
        security_context=engineering_collector.context,
        clock=lambda: now,
    ).issue(
        HistoricalEvidenceAttestationCommand(
            listing_id=listing_id,
            market="XTAI",
            source_id=manifest.historical_source_id,
            evidence_content=content,
            calendar_content=engineering_calendar_content,
            reference_content=engineering_reference_content,
            trace_id="trace-ticket-08-engineering-attestation",
        )
    )
    engineering_claim = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=repository,
        observed_at=now,
        authorization_policy=engineering_policy,
        security_context=engineering_governor.context,
    ).execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id=listing_id,
            market="XTAI",
            source_id=manifest.historical_source_id,
            trace_id="trace-ticket-08-engineering-claim",
            attestation_id=engineering_attestation_id,
        )
    )
    assert engineering_claim.claim_id is not None
    with pytest.raises(ValueError, match="formal_gate_requires_verified_source_basis"):
        workflow.register_formal_qualification_gate(
            manifest=archived_manifest,
            historical_availability_claim_id=engineering_claim.claim_id,
            source_basis_evidence_id=source_basis_id,
            trace_id="trace-ticket-08-engineering-claim-formal-gate",
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


def test_finmind_materialization_cannot_mint_formal_history_without_archive_workflow(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
    terms_sha256 = hashlib.sha256(b"Pinned FinMind free-plan terms").hexdigest()
    repository = FilesystemObjectRepository(tmp_path / "objects")
    base_manifest = load_taiwan_stock_pool_manifest()
    manifest = replace(
        base_manifest.for_authenticated_source_path(),
        authenticated_source_basis=replace(
            base_manifest.authenticated_source_basis,
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
    literal_live_adapter = _QualificationLiteralAdapter(
        loaded("finmind-literal-live", "literal-live-v1")
    )
    literal_live_adapter.source_access_mode = "live_provider"
    supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={primary.policy_dataset_id: literal_live_adapter},
        object_repository=repository,
        state_store=state_store,
        clock=lambda: now,
    )
    outcome = supply.materialize(
        SourcePartitionRequest(
            request_id="finmind-literal-live",
            trace_id="trace-finmind-literal-live",
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
    assert outcome.status == "quarantined"
    assert (
        "historical_qualification_assessment"
        not in state_store.get_trace_evidence("trace-finmind-literal-live")["artifact_kinds"]
    )
    assert (
        "historical_qualification_evidence"
        not in state_store.get_trace_evidence("trace-finmind-literal-live")["artifact_kinds"]
    )

    workflow = TaiwanPriceQualificationWorkflow(
        state_store,
        authorization_policy=policy,
        security_context=identity.context,
        clock=lambda: now,
        object_repository=repository,
    )
    with pytest.raises(ValueError, match="qualified_claim_requires_historical_evidence"):
        workflow.register_historical_availability_claim(
            HistoricalAvailabilityClaim(
                source_id=primary.policy_dataset_id,
                evidence_level="archive_attested",
                evidence_status="qualified",
                observed_start=coverage.requested_start,
                observed_end=coverage.requested_end,
                schema_version="taiwan-unadjusted-eod-v1",
                exact_sessions_verified=True,
                integrity_verified=True,
                company_actions_verified=True,
                listing_lifecycle_verified=True,
                qualification_artifact_id=outcome.retrieval_receipt_id,
            ),
            trace_id="trace-finmind-no-archive-issuer",
        )


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
