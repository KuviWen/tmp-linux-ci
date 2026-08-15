from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceDistribution,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.data_supply import (
    HistoricalAvailabilityClaim,
    TaiwanStockPoolManifest,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository, ObjectRef
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
                        "retain_7_years",
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


def test_formal_gate_rejects_an_existing_artifact_with_the_wrong_evidence_contract(
    tmp_path: Path,
) -> None:
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
