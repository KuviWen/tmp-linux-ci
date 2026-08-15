from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

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
    CanonicalPriceRow,
    CollectedSourcePartition,
    CompanyActionRecord,
    DataSupply,
    DecodedSourcePartition,
    HistoricalAvailabilityClaim,
    ListingLifecycleRecord,
    LoadedSourcePartition,
    SourceCollectionCoverage,
    SourcePartitionRequest,
    SourceRateLimited,
    TaiwanPriceSourceAdapter,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_eligibility_query import PriceEligibilityQuery
from stock_forecasting.price_qualification import TaiwanPriceQualificationWorkflow


class ForbiddenPriceAdapter:
    calls = 0

    def load(self, request: SourcePartitionRequest) -> NoReturn:
        self.calls += 1
        raise AssertionError("a policy-blocked source must not be contacted")


class LiteralPriceAdapter:
    def __init__(self, loaded: LoadedSourcePartition) -> None:
        self.loaded = loaded
        self.requests: list[SourcePartitionRequest] = []

    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition:
        self.requests.append(request)
        return self.loaded


class RateLimitedThenCollector:
    def __init__(self, collection: CollectedSourcePartition) -> None:
        self.collection = collection
        self.calls = 0

    def collect(self, request: SourcePartitionRequest) -> CollectedSourcePartition:
        self.calls += 1
        if self.calls == 1:
            raise SourceRateLimited(
                retry_after_seconds=30,
                rate_limit_policy_id="provider-rate-limit-v1",
            )
        return self.collection


class LiteralPriceDecoder:
    def __init__(self, decoded: DecodedSourcePartition) -> None:
        self.decoded = decoded

    def decode(self, collection: CollectedSourcePartition) -> DecodedSourcePartition:
        return self.decoded


def _qualified_price_policy(
    identity: LocalApiKeyIdentity,
    now: datetime,
) -> AuthorizationPolicy:
    allowed_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_7_years",
            "transform",
            "model",
            "internal_display",
            "backup_restore",
        }
    )
    return AuthorizationPolicy(
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
                dataset_id="twse-current-qualified-price",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=allowed_uses,
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-tw-price-v1",
                principal_id=identity.context.principal_id,
                dataset_id="twse-current-qualified-price",
                status="active",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=allowed_uses,
            ),
        ),
    )


def _price_read_policy(
    identity: LocalApiKeyIdentity,
    now: datetime,
) -> AuthorizationPolicy:
    return AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-price-read-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"price_research_eligibility.read"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-price-read-v1",
                dataset_id="price-research-eligibility",
                allowed_actions=frozenset({"price_research_eligibility.read"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-price-read-v1",
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


def _loaded_partition(
    *,
    now: datetime,
    listing_id: str,
    request_id: str,
    source_revision: str,
    raw_payload: bytes,
    complete: bool = True,
    revision_kind: str = "original",
    quality_issues: tuple[str, ...] = (),
    requested_start: date = date(2019, 8, 14),
) -> LoadedSourcePartition:
    coverage = SourceCollectionCoverage(
        requested_start=requested_start,
        requested_end=date(2026, 8, 14),
        observed_start=date(2019, 8, 14),
        observed_end=date(2026, 8, 14) if complete else date(2026, 8, 13),
        complete=complete,
    )
    collection = CollectedSourcePartition(
        request_id=request_id,
        source_id="twse-current-qualified-price",
        acquired_at=now,
        sanitized_source_uri="provider://qualified-price/bounded-partition",
        media_type="application/json",
        raw_payload=raw_payload,
        checkpoint_before=None,
        checkpoint_after="page:1",
        coverage=coverage,
        source_revision=source_revision,
    )
    decoded = DecodedSourcePartition(
        source_id=collection.source_id,
        schema_version="taiwan-unadjusted-eod-v1",
        source_revision=source_revision,
        prices=(
            CanonicalPriceRow(
                listing_id=listing_id,
                session_date=date(2026, 8, 14),
                open=Decimal("1008.00"),
                high=Decimal("1012.00"),
                low=Decimal("1007.00"),
                close=Decimal("1010.00"),
                volume=1100000,
            ),
        ),
        company_actions=(),
        listing_lifecycle=(
            ListingLifecycleRecord(
                listing_id=listing_id,
                effective_date=date(2019, 8, 14),
                status="active",
                source_event_id="lifecycle-001",
            ),
        ),
        adjusted_close_cross_checks=(Decimal("1010.00"),),
        identity_assertion_ids=("identity-assertion-001",),
        parent_object_ids=("identity-object-001",),
        revision_kind=revision_kind,  # type: ignore[arg-type]
        quality_issues=quality_issues,  # type: ignore[arg-type]
    )
    return LoadedSourcePartition(collection, decoded)


def test_unverified_taiwan_market_dependency_blocks_before_provider_access(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-test",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
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
        source_policies=(),
        source_entitlements=(),
    )
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'ticket-06.db'}",
        create_schema=True,
    )
    adapter = ForbiddenPriceAdapter()
    data_supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={"twse-current-qualified-price": adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-06-blocked",
        trace_id="trace-p2-trace-tw-01-blocked",
        source_id="twse-current-qualified-price",
        mode="current",
        listing_ids=("10000000-0000-4000-8000-000000000001",),
        start_date=date(2026, 8, 14),
        end_date=date(2026, 8, 14),
        expected_checkpoint=None,
    )

    outcome = data_supply.materialize(request)

    assert outcome.as_payload() == {
        "status": "policy_blocked",
        "reason_code": "dependency_evidence_unverified",
        "policy_reason_code": "source_policy_unknown",
        "dependency_id": "DEP-MKT-TW-01",
        "source_id": "twse-current-qualified-price",
        "source_mode": "current",
        "listing_ids": ["10000000-0000-4000-8000-000000000001"],
        "trace_id": "trace-p2-trace-tw-01-blocked",
        "policy_decision_id": outcome.policy_decision_id,
        "policy_evaluation_id": outcome.policy_evaluation_id,
        "policy_valid_until": "2026-08-15T01:00:00Z",
        "evaluated_at": "2026-08-15T01:00:00Z",
        "raw_object_id": None,
        "retrieval_receipt_id": None,
        "normalized_object_id": None,
        "source_revision": None,
        "checkpoint": None,
        "coverage": None,
        "dataset_version_id": None,
        "adjustment_version_id": None,
        "historical_availability_claim_id": None,
        "rate_limit_policy_id": None,
        "retry_after_seconds": None,
        "checks": {
            "policy": "blocked",
            "coverage": "not_evaluated",
            "schema": "not_evaluated",
            "integrity": "not_evaluated",
            "depth": "not_evaluated",
        },
    }
    assert adapter.calls == 0
    assert state_store.list_audit_events(trace_id=request.trace_id) == [
        {
            "action": "market_data.collect",
            "outcome": "denied",
            "reason_code": "source_policy_unknown",
            "trace_id": request.trace_id,
            "authentication_method": "local_api_key",
            "correlation_id": request.request_id,
            "credential_id": identity.context.credential_id,
            "data_protection_class": None,
            "dataset_id": request.source_id,
            "decision_id": outcome.policy_decision_id,
            "environment": "development",
            "evaluated_at": "2026-08-15T01:00:00Z",
            "evaluation_id": state_store.list_audit_events(trace_id=request.trace_id)[0][
                "evaluation_id"
            ],
            "grant_version_id": "grant-price-v1",
            "principal_id": identity.context.principal_id,
            "purpose": "price_research",
            "required_uses": [
                "backup_restore",
                "ingest",
                "internal_display",
                "model",
                "retain_7_years",
                "transform",
            ],
            "source_entitlement_version_id": None,
            "source_policy_version_id": None,
            "valid_until": "2026-08-15T01:00:00Z",
        }
    ]
    assert (
        state_store.get_price_research_eligibility(listing_id=request.listing_ids[0])
        == outcome.as_payload()
    )


def test_qualified_source_materializes_immutable_unadjusted_and_internal_adjustment_versions(
    tmp_path: Path,
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
    listing_id = "10000000-0000-4000-8000-000000000001"
    coverage = SourceCollectionCoverage(
        requested_start=date(2019, 8, 14),
        requested_end=date(2026, 8, 14),
        observed_start=date(2019, 8, 14),
        observed_end=date(2026, 8, 14),
        complete=True,
    )
    collection = CollectedSourcePartition(
        request_id="request-ticket-06-qualified",
        source_id="twse-current-qualified-price",
        acquired_at=now,
        sanitized_source_uri="provider://qualified-price/bounded-partition",
        media_type="application/json",
        raw_payload=b'{"unadjusted":true,"providerAdjustedClose":[995,1010]}',
        checkpoint_before=None,
        checkpoint_after="page:1",
        coverage=coverage,
        source_revision="source-revision-2026-08-15",
    )
    decoded = DecodedSourcePartition(
        source_id=collection.source_id,
        schema_version="taiwan-unadjusted-eod-v1",
        source_revision=collection.source_revision,
        prices=(
            CanonicalPriceRow(
                listing_id=listing_id,
                session_date=date(2026, 8, 13),
                open=Decimal("998.00"),
                high=Decimal("1002.00"),
                low=Decimal("997.00"),
                close=Decimal("1000.00"),
                volume=1000000,
            ),
            CanonicalPriceRow(
                listing_id=listing_id,
                session_date=date(2026, 8, 14),
                open=Decimal("1008.00"),
                high=Decimal("1012.00"),
                low=Decimal("1007.00"),
                close=Decimal("1010.00"),
                volume=1100000,
            ),
        ),
        company_actions=(
            CompanyActionRecord(
                listing_id=listing_id,
                effective_date=date(2026, 8, 14),
                kind="cash_dividend",
                value=Decimal("5.00"),
                currency="TWD",
                source_action_id="action-001",
            ),
        ),
        listing_lifecycle=(
            ListingLifecycleRecord(
                listing_id=listing_id,
                effective_date=date(2019, 8, 14),
                status="active",
                source_event_id="lifecycle-001",
            ),
        ),
        adjusted_close_cross_checks=(Decimal("995.00"), Decimal("1010.00")),
        identity_assertion_ids=("identity-assertion-001",),
        parent_object_ids=("identity-object-001",),
    )
    adapter = LiteralPriceAdapter(LoadedSourcePartition(collection, decoded))
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'ticket-06-qualified.db'}",
        create_schema=True,
    )
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={collection.source_id: adapter},
        object_repository=object_repository,
        state_store=state_store,
        clock=lambda: now,
    )
    request = SourcePartitionRequest(
        request_id=collection.request_id,
        trace_id="trace-p2-trace-tw-01-qualified",
        source_id=collection.source_id,
        mode="current",
        listing_ids=(listing_id,),
        start_date=coverage.requested_start,
        end_date=coverage.requested_end,
        expected_checkpoint=None,
    )

    first = data_supply.materialize(request)
    adapter.loaded = LoadedSourcePartition(
        collection=replace(collection, checkpoint_before="page:1"),
        decoded=decoded,
    )
    replay = data_supply.materialize(replace(request, expected_checkpoint="page:1"))

    assert first.status == "published"
    assert first.reason_code == "qualified_price_materialized"
    assert first.as_payload()["checks"] == {
        "policy": "passed",
        "coverage": "passed",
        "schema": "passed",
        "integrity": "passed",
        "depth": "passed",
    }
    assert first.dataset_version_id is not None
    assert first.adjustment_version_id is not None
    assert first.raw_object_id is not None
    assert first.normalized_object_id is not None
    normalized_payload = json.loads(object_repository.open_by_id(first.normalized_object_id).read())
    assert "providerAdjustedClose" not in json.dumps(normalized_payload)
    assert set(normalized_payload["prices"][0]) == {
        "close",
        "high",
        "listing_id",
        "low",
        "open",
        "session_date",
        "volume",
    }
    assert first.source_revision == "source-revision-2026-08-15"
    assert first.checkpoint == "page:1"
    assert replay.dataset_version_id == first.dataset_version_id
    assert replay.adjustment_version_id == first.adjustment_version_id
    assert len(adapter.requests) == 2
    assert all(call.policy_decision_id == first.policy_decision_id for call in adapter.requests)
    dataset = state_store.get_canonical_artifact(first.dataset_version_id)
    assert dataset["artifact_kind"] == "dataset_version"
    assert dataset["payload"]["price_semantics"] == "unadjusted"
    assert dataset["payload"]["schema_version"] == "taiwan-unadjusted-eod-v1"
    assert dataset["payload"]["source_revision"] == "source-revision-2026-08-15"
    assert dataset["payload"]["policy_decision_id"] == first.policy_decision_id
    adjustment = state_store.get_canonical_artifact(first.adjustment_version_id)
    assert adjustment["artifact_kind"] == "adjustment_version"
    assert adjustment["payload"]["method"] == "internal_total_return_adjustment_v1"
    assert adjustment["payload"]["adjusted_closes"] == [
        {
            "adjusted_close": "995.00",
            "listing_id": listing_id,
            "session_date": "2026-08-13",
        },
        {
            "adjusted_close": "1010.00",
            "listing_id": listing_id,
            "session_date": "2026-08-14",
        },
    ]
    assert adjustment["payload"]["provider_cross_check"] == "matched"
    trace = state_store.get_trace_evidence(request.trace_id)
    assert trace["execution_purpose"] == "price_research"
    assert set(trace["artifact_kinds"]) == {
        "raw_source_object",
        "source_retrieval_receipt",
        "normalized_price_object",
        "dataset_version",
        "adjustment_version",
    }
    assert trace["artifact_kinds"].count("source_retrieval_receipt") == 2


def test_synthetic_published_sources_cannot_be_reported_as_formally_qualified(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-test",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=10),
        data_protection_classes={"licensed"},
    )
    listing_id = "10000000-0000-4000-8000-000000000001"
    current = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id="request-candidate-current",
        source_revision="revision-candidate-current",
        raw_payload=b'{"mode":"current"}',
    )
    historical = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id="request-candidate-historical",
        source_revision="revision-candidate-historical",
        raw_payload=b'{"mode":"historical"}',
    )
    adapter = LiteralPriceAdapter(current)
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'candidate-query.db'}",
        create_schema=True,
    )
    historical_claim_id = TaiwanPriceQualificationWorkflow(
        state_store
    ).register_historical_availability_claim(
        HistoricalAvailabilityClaim(
            source_id=current.collection.source_id,
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
        trace_id="trace-candidate-history-qualification",
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={current.collection.source_id: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )
    outcomes = []
    for mode, loaded in (("current", current), ("historical", historical)):
        adapter.loaded = loaded
        outcomes.append(
            data_supply.materialize(
                SourcePartitionRequest(
                    request_id=loaded.collection.request_id,
                    trace_id=f"trace-candidate-{mode}",
                    source_id=loaded.collection.source_id,
                    mode=mode,  # type: ignore[arg-type]
                    listing_ids=(listing_id,),
                    start_date=loaded.collection.coverage.requested_start,
                    end_date=loaded.collection.coverage.requested_end,
                    expected_checkpoint=None,
                    historical_availability_claim_id=(
                        historical_claim_id if mode == "historical" else None
                    ),
                )
            )
        )

    assert [outcome.status for outcome in outcomes] == ["published", "published"]
    assert outcomes[1].historical_availability_claim_id == historical_claim_id

    result = PriceEligibilityQuery(
        state_store,
        authorization_policy=_price_read_policy(identity, now),
        authorization_time=now,
    ).get_listing(
        listing_id=listing_id,
        trace_id="trace-candidate-query",
        security_context=identity.context,
    )

    assert isinstance(result, dict)
    assert result["status"] == "policy_blocked"
    assert result["reason_code"] == "qualification_evidence_unverified"
    assert result["formally_qualified"] is False
    assert result["checks"]["policy"] == "blocked"  # type: ignore[index]

    after_source_rights_expiry = now + timedelta(days=2)
    expired_result = PriceEligibilityQuery(
        state_store,
        authorization_policy=_price_read_policy(identity, after_source_rights_expiry),
        authorization_time=after_source_rights_expiry,
    ).get_listing(
        listing_id=listing_id,
        trace_id="trace-expired-source-rights-query",
        security_context=identity.context,
    )
    assert isinstance(expired_result, dict)
    assert expired_result["status"] == "policy_blocked"
    assert expired_result["reason_code"] == "source_rights_not_effective"
    assert expired_result["formally_qualified"] is False


@pytest.mark.parametrize(
    (
        "complete",
        "revision_kind",
        "quality_issues",
        "mode",
        "requested_start",
        "expected_reason",
    ),
    [
        (False, "original", (), "current", date(2019, 8, 14), "incomplete_coverage"),
        (
            True,
            "original",
            ("identity_ambiguous",),
            "current",
            date(2019, 8, 14),
            "identity_ambiguous",
        ),
        (
            True,
            "original",
            ("missing_company_action",),
            "current",
            date(2019, 8, 14),
            "missing_company_action",
        ),
        (True, "withdrawal", (), "current", date(2019, 8, 14), "source_withdrawn"),
        (
            True,
            "original",
            (),
            "historical",
            date(2025, 8, 14),
            "insufficient_history_depth",
        ),
        (
            True,
            "original",
            (),
            "historical",
            date(2019, 8, 14),
            "historical_evidence_unverified",
        ),
    ],
)
def test_invalid_source_partitions_are_quarantined_with_raw_evidence(
    tmp_path: Path,
    complete: bool,
    revision_kind: str,
    quality_issues: tuple[str, ...],
    mode: str,
    requested_start: date,
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
    listing_id = "10000000-0000-4000-8000-000000000001"
    loaded = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id=f"request-{expected_reason}",
        source_revision=f"revision-{expected_reason}",
        raw_payload=f'{{"case":"{expected_reason}"}}'.encode(),
        complete=complete,
        revision_kind=revision_kind,
        quality_issues=quality_issues,
        requested_start=requested_start,
    )
    adapter = LiteralPriceAdapter(loaded)
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'quarantine.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={loaded.collection.source_id: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )
    request = SourcePartitionRequest(
        request_id=loaded.collection.request_id,
        trace_id=f"trace-{expected_reason}",
        source_id=loaded.collection.source_id,
        mode=mode,  # type: ignore[arg-type]
        listing_ids=(listing_id,),
        start_date=loaded.collection.coverage.requested_start,
        end_date=loaded.collection.coverage.requested_end,
        expected_checkpoint=None,
    )

    outcome = data_supply.materialize(request)

    assert outcome.status == "quarantined"
    assert outcome.reason_code == expected_reason
    checks = outcome.as_payload()["checks"]
    assert isinstance(checks, dict)
    assert "blocked" in checks.values()
    assert outcome.dataset_version_id is None
    assert outcome.adjustment_version_id is None
    assert outcome.raw_object_id is not None
    assert outcome.source_revision == loaded.collection.source_revision
    assert state_store.get_price_research_eligibility(listing_id=listing_id) == (
        outcome.as_payload()
    )
    assert state_store.get_trace_evidence(request.trace_id)["artifact_kinds"] == [
        "raw_source_object",
        "source_retrieval_receipt",
        "quarantine_record",
    ]


def test_missing_requested_listing_is_quarantined_instead_of_published(
    tmp_path: Path,
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
    first_listing = "10000000-0000-4000-8000-000000000001"
    missing_listing = "10000000-0000-4000-8000-000000000002"
    loaded = _loaded_partition(
        now=now,
        listing_id=first_listing,
        request_id="request-missing-listing",
        source_revision="revision-missing-listing",
        raw_payload=b'{"case":"missing-listing"}',
    )
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'missing-listing.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={loaded.collection.source_id: LiteralPriceAdapter(loaded)},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )

    outcome = data_supply.materialize(
        SourcePartitionRequest(
            request_id=loaded.collection.request_id,
            trace_id="trace-missing-listing",
            source_id=loaded.collection.source_id,
            mode="current",
            listing_ids=(first_listing, missing_listing),
            start_date=loaded.collection.coverage.requested_start,
            end_date=loaded.collection.coverage.requested_end,
            expected_checkpoint=None,
        )
    )

    assert outcome.status == "quarantined"
    assert outcome.reason_code == "incomplete_coverage"
    assert outcome.dataset_version_id is None


def test_unapproved_price_schema_is_quarantined_and_reported_as_blocked(
    tmp_path: Path,
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
    listing_id = "10000000-0000-4000-8000-000000000001"
    loaded = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id="request-schema-drift",
        source_revision="revision-schema-drift",
        raw_payload=b'{"schema":"provider-v2"}',
    )
    loaded = replace(
        loaded,
        decoded=replace(loaded.decoded, schema_version="provider-v2"),
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={loaded.collection.source_id: LiteralPriceAdapter(loaded)},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=StateStore(
            f"sqlite+pysqlite:///{tmp_path / 'schema-drift.db'}",
            create_schema=True,
        ),
        clock=lambda: now,
    )

    outcome = data_supply.materialize(
        SourcePartitionRequest(
            request_id=loaded.collection.request_id,
            trace_id="trace-schema-drift",
            source_id=loaded.collection.source_id,
            mode="current",
            listing_ids=(listing_id,),
            start_date=loaded.collection.coverage.requested_start,
            end_date=loaded.collection.coverage.requested_end,
            expected_checkpoint=None,
        )
    )

    assert outcome.status == "quarantined"
    assert outcome.reason_code == "schema_incompatible"
    assert outcome.as_payload()["checks"]["schema"] == "blocked"  # type: ignore[index]
    assert outcome.dataset_version_id is None


def test_provider_adjusted_close_mismatch_blocks_integrity_publication(
    tmp_path: Path,
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
    listing_id = "10000000-0000-4000-8000-000000000001"
    loaded = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id="request-adjustment-mismatch",
        source_revision="revision-adjustment-mismatch",
        raw_payload=b'{"provider_adjusted_close":"1.00"}',
    )
    loaded = replace(
        loaded,
        decoded=replace(
            loaded.decoded,
            adjusted_close_cross_checks=(Decimal("1.00"), Decimal("1.00")),
        ),
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={loaded.collection.source_id: LiteralPriceAdapter(loaded)},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=StateStore(
            f"sqlite+pysqlite:///{tmp_path / 'adjustment-mismatch.db'}",
            create_schema=True,
        ),
        clock=lambda: now,
    )

    outcome = data_supply.materialize(
        SourcePartitionRequest(
            request_id=loaded.collection.request_id,
            trace_id="trace-adjustment-mismatch",
            source_id=loaded.collection.source_id,
            mode="current",
            listing_ids=(listing_id,),
            start_date=loaded.collection.coverage.requested_start,
            end_date=loaded.collection.coverage.requested_end,
            expected_checkpoint=None,
        )
    )

    assert outcome.status == "quarantined"
    assert outcome.reason_code == "adjustment_cross_check_mismatch"
    assert outcome.as_payload()["checks"]["integrity"] == "blocked"  # type: ignore[index]
    assert outcome.dataset_version_id is None
    assert outcome.adjustment_version_id is None


def test_rate_limited_collection_is_deferred_without_advancing_the_checkpoint(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-test",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    listing_id = "10000000-0000-4000-8000-000000000001"
    loaded = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id="request-rate-limited",
        source_revision="revision-rate-limited",
        raw_payload=b'{"retry":"succeeded"}',
    )
    collector = RateLimitedThenCollector(loaded.collection)
    adapter = TaiwanPriceSourceAdapter(
        source_id=loaded.collection.source_id,
        mode="current",
        adapter_version="taiwan-price-adapter-v1",
        rate_limit_policy_id="provider-rate-limit-v1",
        collector=collector,
        decoder=LiteralPriceDecoder(loaded.decoded),
    )
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'rate-limited.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={loaded.collection.source_id: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )
    request = SourcePartitionRequest(
        request_id=loaded.collection.request_id,
        trace_id="trace-rate-limited-first",
        source_id=loaded.collection.source_id,
        mode="current",
        listing_ids=(listing_id,),
        start_date=loaded.collection.coverage.requested_start,
        end_date=loaded.collection.coverage.requested_end,
        expected_checkpoint=None,
    )

    deferred = data_supply.materialize(request)

    assert deferred.status == "deferred"
    assert deferred.reason_code == "source_rate_limited"
    assert deferred.rate_limit_policy_id == "provider-rate-limit-v1"
    assert deferred.retry_after_seconds == 30
    assert (
        state_store.get_price_source_checkpoint(
            source_id=request.source_id,
            source_mode=request.mode,
        )
        is None
    )
    query_result = PriceEligibilityQuery(
        state_store,
        authorization_policy=_price_read_policy(identity, now),
        authorization_time=now,
    ).get_listing(
        listing_id=listing_id,
        trace_id="trace-rate-limited-query",
        security_context=identity.context,
    )
    assert isinstance(query_result, dict)
    assert query_result["status"] == "policy_blocked"
    assert query_result["reason_code"] == "source_collection_deferred"

    published = data_supply.materialize(replace(request, trace_id="trace-rate-limited-retry"))

    assert published.status == "published"
    assert published.checkpoint == "page:1"
    assert collector.calls == 2


def test_repeated_policy_evaluation_appends_a_new_eligibility_event(
    tmp_path: Path,
) -> None:
    first_instant = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    second_instant = first_instant + timedelta(minutes=5)
    instants = iter((first_instant, second_instant))
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-test",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=first_instant - timedelta(hours=1),
        expires_at=first_instant + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-repeat-evaluation-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=first_instant - timedelta(days=1),
                valid_to=first_instant + timedelta(days=1),
            ),
        ),
        source_policies=(),
        source_entitlements=(),
    )
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'repeat-evaluation.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: next(instants),
    )
    listing_id = "10000000-0000-4000-8000-000000000001"
    request = SourcePartitionRequest(
        request_id="request-repeat-evaluation",
        trace_id="trace-repeat-evaluation",
        source_id="twse-current-qualified-price",
        mode="current",
        listing_ids=(listing_id,),
        start_date=date(2019, 8, 14),
        end_date=date(2026, 8, 14),
        expected_checkpoint=None,
    )

    first = data_supply.materialize(request)
    second = data_supply.materialize(request)

    assert first.status == second.status == "policy_blocked"
    assert first.policy_evaluation_id != second.policy_evaluation_id
    assert state_store.get_price_research_eligibility(listing_id=listing_id)[
        "evaluated_at"
    ] == second_instant.isoformat().replace("+00:00", "Z")
    assert len(state_store.list_audit_events(trace_id=request.trace_id)) == 2


def test_collection_must_continue_from_the_durable_checkpoint(
    tmp_path: Path,
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
    listing_id = "10000000-0000-4000-8000-000000000001"
    first_loaded = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id="request-checkpoint-first",
        source_revision="revision-checkpoint-first",
        raw_payload=b'{"page":1}',
    )
    adapter = LiteralPriceAdapter(first_loaded)
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'durable-checkpoint.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={first_loaded.collection.source_id: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )
    first_request = SourcePartitionRequest(
        request_id=first_loaded.collection.request_id,
        trace_id="trace-checkpoint-first",
        source_id=first_loaded.collection.source_id,
        mode="current",
        listing_ids=(listing_id,),
        start_date=first_loaded.collection.coverage.requested_start,
        end_date=first_loaded.collection.coverage.requested_end,
        expected_checkpoint=None,
    )
    first = data_supply.materialize(first_request)
    second_collection = replace(
        first_loaded.collection,
        request_id="request-checkpoint-second",
        raw_payload=b'{"page":2}',
        source_revision="revision-checkpoint-second",
        checkpoint_before="page:1",
        checkpoint_after="page:2",
    )
    adapter.loaded = LoadedSourcePartition(
        collection=second_collection,
        decoded=replace(
            first_loaded.decoded,
            source_revision=second_collection.source_revision,
        ),
    )
    stale_request = replace(
        first_request,
        request_id=second_collection.request_id,
        trace_id="trace-checkpoint-stale",
    )

    with pytest.raises(ValueError, match="source_checkpoint_state_mismatch"):
        data_supply.materialize(stale_request)

    assert len(adapter.requests) == 1
    second = data_supply.materialize(
        replace(
            stale_request,
            trace_id="trace-checkpoint-second",
            expected_checkpoint="page:1",
        )
    )
    assert first.checkpoint == "page:1"
    assert second.checkpoint == "page:2"


def test_retrieval_receipts_are_append_only_when_raw_content_is_reobserved(
    tmp_path: Path,
) -> None:
    first_instant = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    second_instant = first_instant + timedelta(minutes=5)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-test",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=first_instant - timedelta(hours=1),
        expires_at=first_instant + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    listing_id = "10000000-0000-4000-8000-000000000001"
    first_loaded = _loaded_partition(
        now=first_instant,
        listing_id=listing_id,
        request_id="request-receipt-first",
        source_revision="revision-reobserved",
        raw_payload=b'{"same":"content"}',
    )
    adapter = LiteralPriceAdapter(first_loaded)
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'retrieval-receipts.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, first_instant),
        security_context=identity.context,
        adapters={first_loaded.collection.source_id: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: first_instant,
    )
    request = SourcePartitionRequest(
        request_id=first_loaded.collection.request_id,
        trace_id="trace-receipt-first",
        source_id=first_loaded.collection.source_id,
        mode="current",
        listing_ids=(listing_id,),
        start_date=first_loaded.collection.coverage.requested_start,
        end_date=first_loaded.collection.coverage.requested_end,
        expected_checkpoint=None,
    )
    first = data_supply.materialize(request)
    second_collection = replace(
        first_loaded.collection,
        request_id="request-receipt-second",
        acquired_at=second_instant,
        checkpoint_before="page:1",
        checkpoint_after="page:2",
    )
    adapter.loaded = LoadedSourcePartition(
        collection=second_collection,
        decoded=first_loaded.decoded,
    )

    second = data_supply.materialize(
        replace(
            request,
            request_id=second_collection.request_id,
            trace_id="trace-receipt-second",
            expected_checkpoint="page:1",
        )
    )

    assert second.raw_object_id == first.raw_object_id
    assert second.retrieval_receipt_id != first.retrieval_receipt_id
    assert state_store.get_trace_evidence("trace-receipt-second")["artifact_kinds"] == [
        "raw_source_object",
        "source_retrieval_receipt",
        "normalized_price_object",
        "dataset_version",
        "adjustment_version",
    ]


@pytest.mark.parametrize("revision_kind", ["late_arrival", "correction"])
def test_late_and_corrected_partitions_publish_new_versions_without_overwrite(
    tmp_path: Path,
    revision_kind: str,
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
    listing_id = "10000000-0000-4000-8000-000000000001"
    original = _loaded_partition(
        now=now,
        listing_id=listing_id,
        request_id=f"request-{revision_kind}-original",
        source_revision="revision-original",
        raw_payload=b'{"close":"1010.00","revision":"original"}',
    )
    adapter = LiteralPriceAdapter(original)
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'revisions.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_price_policy(identity, now),
        security_context=identity.context,
        adapters={original.collection.source_id: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )
    first_request = SourcePartitionRequest(
        request_id=original.collection.request_id,
        trace_id=f"trace-{revision_kind}-original",
        source_id=original.collection.source_id,
        mode="current",
        listing_ids=(listing_id,),
        start_date=original.collection.coverage.requested_start,
        end_date=original.collection.coverage.requested_end,
        expected_checkpoint=None,
    )
    first = data_supply.materialize(first_request)
    revised = _loaded_partition(
        now=now + timedelta(minutes=5),
        listing_id=listing_id,
        request_id=f"request-{revision_kind}-revised",
        source_revision=f"revision-{revision_kind}",
        raw_payload=f'{{"close":"1011.00","revision":"{revision_kind}"}}'.encode(),
        revision_kind=revision_kind,
    )
    revised = replace(
        revised,
        collection=replace(revised.collection, checkpoint_before="page:1"),
    )
    adapter.loaded = revised

    second = data_supply.materialize(
        replace(
            first_request,
            request_id=revised.collection.request_id,
            trace_id=f"trace-{revision_kind}-revised",
            expected_checkpoint="page:1",
        )
    )

    assert first.status == "published"
    assert second.status == "published"
    assert first.dataset_version_id is not None
    assert second.dataset_version_id is not None
    assert second.dataset_version_id != first.dataset_version_id
    assert second.adjustment_version_id != first.adjustment_version_id
    assert (
        state_store.get_canonical_artifact(first.dataset_version_id)["payload"]["source_revision"]
        == "revision-original"
    )
    assert (
        state_store.get_canonical_artifact(second.dataset_version_id)["payload"]["source_revision"]
        == f"revision-{revision_kind}"
    )
