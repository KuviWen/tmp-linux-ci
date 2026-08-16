from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, NoReturn

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.alpaca_market_data import (
    AlpacaCompanyActionExpectation,
    AlpacaMarketCalendarEvidence,
    AlpacaPriceSourceAdapter,
    AlpacaReferenceGraph,
    AlpacaReferenceListing,
    AlpacaSourceCollector,
    AlpacaSourceDecoder,
    ProviderHttpRequest,
    ProviderHttpResponse,
)
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationAction,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceDistribution,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.data_supply import (
    DataSupply,
    ExternalSecurityAlias,
    ListingLifecycleRecord,
    LoadedSourcePartition,
    MarketSessionRecord,
    SourceBundleMemberRequest,
    SourceCredentialRequired,
    SourcePartitionRequest,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.source_credentials import (
    CredentialValidationEvidence,
    CredentialValidationResult,
    SecretLease,
    SecretRef,
    SecretUseContext,
)

ALPACA_BARS_DISTRIBUTION_ID = "alpaca-us-stock-bars-v2"
ALPACA_BARS_DISTRIBUTION_URL = "https://data.alpaca.markets/v2/stocks/bars"
ALPACA_SOURCE_ID = "alpaca-us-stock-bars"
ALPACA_SOURCE_BASIS_ID = "ALPACA-BASIC-US-MARKET-DATA-01"
ENGINEERING_SOURCE_BASIS_ID = "ENGINEERING-ALPACA-CONTRACT-01"


def _engineering_market_calendar_evidence() -> AlpacaMarketCalendarEvidence:
    return AlpacaMarketCalendarEvidence(
        version_id="engineering-nyse-calendar-2024-01-02-through-2024-01-04-v1",
        coverage_start=date(2024, 1, 2),
        coverage_end=date(2024, 1, 4),
        sessions=tuple(
            MarketSessionRecord(
                session_date=session_date,
                open_time="09:30",
                close_time="16:00",
                session_kind="regular",
            )
            for session_date in (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
        ),
    )


def _engineering_reference_graph(
    listing_symbols: Mapping[str, tuple[str, ...]],
    *,
    expected_action_ids: frozenset[str] = frozenset(),
) -> AlpacaReferenceGraph:
    first_listing_id = next(iter(listing_symbols))
    return AlpacaReferenceGraph(
        version_id="engineering-us-reference-graph-v1",
        listings=tuple(
            AlpacaReferenceListing(
                listing_id=listing_id,
                aliases=tuple(
                    ExternalSecurityAlias(
                        security_code=symbol,
                        security_name=f"{symbol} engineering reference",
                        valid_from=date(2000 + index, 1, 1),
                        valid_to=(date(2000 + index, 12, 31) if index < len(symbols) - 1 else None),
                    )
                    for index, symbol in enumerate(symbols)
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2000, 1, 1),
                        status="active",
                        source_event_id=f"engineering-active-{listing_id}",
                    ),
                ),
            )
            for listing_id, symbols in listing_symbols.items()
        ),
        company_action_expectations=tuple(
            AlpacaCompanyActionExpectation(
                action_id=action_id,
                listing_id=first_listing_id,
                effective_date=date(2024, 1, 3),
            )
            for action_id in expected_action_ids
        ),
        lifecycle_complete=True,
        company_actions_complete=True,
    )


def _bundle_member_requests() -> tuple[SourceBundleMemberRequest, ...]:
    return (
        SourceBundleMemberRequest(
            dataset_id="alpaca-us-corporate-actions-v1",
            distribution_id="alpaca-us-corporate-actions-v1",
            distribution_url="https://data.alpaca.markets/v1/corporate-actions",
            schema_version="alpaca-corporate-actions-v1",
        ),
        SourceBundleMemberRequest(
            dataset_id="alpaca-us-trading-calendar-v2",
            distribution_id="alpaca-us-trading-calendar-v2",
            distribution_url="https://paper-api.alpaca.markets/v2/calendar",
            schema_version="alpaca-trading-calendar-v2",
        ),
    )


class CredentialRequiredPriceAdapter:
    source_access_mode = "engineering_double"

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        self.calls = 0

    def load(self, request: SourcePartitionRequest) -> NoReturn:
        self.calls += 1
        raise SourceCredentialRequired(self.reason_code)


class LiveProviderCredentialRequiredAdapter(CredentialRequiredPriceAdapter):
    source_access_mode = "live_provider"


class EngineeringCredentialResolver:
    def resolve_valid(
        self,
        provider_id: str,
        *,
        trace_id: str,
        request_id: str,
        work_id: str,
        source_id: str,
    ) -> SecretLease:
        assert provider_id == "alpaca-market-data-basic"
        return SecretLease(
            SecretRef("secret-ref:engineering-contract"),
            {
                "api_key_id": "PK-ENGINEERING-ONLY",
                "api_secret_key": "engineering-contract-secret",
            },
            SecretUseContext(
                workload_principal_id="workload:engineering-source-adapter",
                environment="development",
                source_id=source_id,
                destination=provider_id,
                purpose="price_research_ingest",
                request_id=request_id,
                work_id=work_id,
                credential_version=1,
                lease_duration=timedelta(minutes=5),
                lease_not_before=datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
                lease_expires_at=datetime(2026, 8, 15, 8, 5, tzinfo=UTC),
            ),
            clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
            monotonic_clock=lambda: 0.0,
        )


class ValidCredentialValidator:
    def validate(self, _credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )


class EngineeringProviderTransport:
    def __init__(self, responses: list[ProviderHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class BundleQualityMutationAdapter:
    source_access_mode = "engineering_double"

    def __init__(
        self,
        delegate: AlpacaPriceSourceAdapter,
        problem: Literal[
            "schema", "coverage", "duplicate", "duplicate_action", "calendar_omission"
        ],
    ) -> None:
        self._delegate = delegate
        self._problem = problem

    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition:
        loaded = self._delegate.load(request)
        if self._problem == "duplicate_action":
            return replace(
                loaded,
                decoded=replace(
                    loaded.decoded,
                    quality_issues=tuple(
                        sorted((*loaded.decoded.quality_issues, "duplicate_company_action"))
                    ),
                ),
            )
        members = loaded.collection.bundle_members
        if self._problem == "duplicate":
            members = (*members, members[-1])
        elif self._problem != "calendar_omission":
            members = tuple(
                (
                    replace(member, schema_version="unexpected-schema-v9")
                    if self._problem == "schema"
                    else replace(member, coverage=replace(member.coverage, complete=False))
                )
                if member.dataset_id == "alpaca-us-trading-calendar-v2"
                else member
                for member in members
            )
        return replace(loaded, collection=replace(loaded.collection, bundle_members=members))


def _qualified_zero_fee_policy(
    identity: LocalApiKeyIdentity,
    now: datetime,
) -> AuthorizationPolicy:
    allowed_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_observed_history",
            "transform",
            "model",
            "internal_display",
            "backup_restore",
        }
    )
    collect_actions: frozenset[AuthorizationAction] = frozenset({"market_data.collect"})
    read_actions: frozenset[AuthorizationAction] = frozenset({"price_research_eligibility.read"})
    credential_actions: frozenset[AuthorizationAction] = frozenset(
        {"source_credential.read", "source_credential.manage"}
    )
    grant_actions = collect_actions | read_actions | credential_actions
    engineering_source_policies = tuple(
        SourcePolicyVersion(
            version_id=f"policy-ticket-07-{dataset_id}-v1",
            dataset_id=dataset_id,
            allowed_actions=collect_actions,
            purposes=frozenset({"price_research"}),
            environments=frozenset({"development"}),
            data_protection_class="licensed",
            resource_states=frozenset({"active"}),
            allowed_uses=allowed_uses,
            access_basis="engineering_contract",
            source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
            distributions=(
                SourceDistribution(
                    dataset_id=distribution_id,
                    distribution_url=distribution_url,
                ),
            ),
        )
        for dataset_id, distribution_id, distribution_url in (
            (
                ALPACA_SOURCE_ID,
                ALPACA_BARS_DISTRIBUTION_ID,
                ALPACA_BARS_DISTRIBUTION_URL,
            ),
            (
                "alpaca-us-corporate-actions-v1",
                "alpaca-us-corporate-actions-v1",
                "https://data.alpaca.markets/v1/corporate-actions",
            ),
            (
                "alpaca-us-trading-calendar-v2",
                "alpaca-us-trading-calendar-v2",
                "https://paper-api.alpaca.markets/v2/calendar",
            ),
        )
    )
    engineering_entitlements = tuple(
        SourceEntitlement(
            version_id=f"entitlement-ticket-07-{dataset_id}-v1",
            principal_id=identity.context.principal_id,
            dataset_id=dataset_id,
            status="active",
            allowed_actions=collect_actions,
            purposes=frozenset({"price_research"}),
            environments=frozenset({"development"}),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=1),
            allowed_uses=allowed_uses,
        )
        for dataset_id in (
            ALPACA_SOURCE_ID,
            "alpaca-us-corporate-actions-v1",
            "alpaca-us-trading-calendar-v2",
        )
    )
    return AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-ticket-07-us-price-v1",
                principal_id=identity.context.principal_id,
                actions=grant_actions,
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            *engineering_source_policies,
            SourcePolicyVersion(
                version_id="policy-ticket-07-price-read-v1",
                dataset_id="price-research-eligibility",
                allowed_actions=read_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
            ),
            SourcePolicyVersion(
                version_id="policy-ticket-07-credential-manage-v1",
                dataset_id="source-credential-metadata",
                allowed_actions=credential_actions,
                purposes=frozenset({"source_administration"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=(
            *engineering_entitlements,
            SourceEntitlement(
                version_id="entitlement-ticket-07-price-read-v1",
                principal_id=identity.context.principal_id,
                dataset_id="price-research-eligibility",
                status="active",
                allowed_actions=read_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
            SourceEntitlement(
                version_id="entitlement-ticket-07-credential-manage-v1",
                principal_id=identity.context.principal_id,
                dataset_id="source-credential-metadata",
                status="active",
                allowed_actions=credential_actions,
                purposes=frozenset({"source_administration"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
    )


def test_valid_source_policy_with_missing_credential_fails_closed_before_network(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-qualified-individual",
        environment="development",
        scopes={
            "market_data.collect",
            "price_research_eligibility.read",
            "source_credential.manage",
            "source_credential.read",
        },
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    adapter = CredentialRequiredPriceAdapter("source_credential_missing")
    policy = _qualified_zero_fee_policy(identity, now)
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ticket-07-us-price.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_override=policy,
    )
    data_supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=application.object_repository,
        state_store=application.state_store,
        clock=lambda: now,
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-credential-missing",
        trace_id="trace-p2-trace-us-01-credential-missing",
        source_id=ALPACA_SOURCE_ID,
        mode="historical",
        listing_ids=("70000000-0000-4000-8000-000000000001",),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint=None,
        distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
        distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
        source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
        bundle_members=_bundle_member_requests(),
        expected_company_action_ids=frozenset({"ca-dividend-aapl"}),
    )

    outcome = data_supply.materialize(request)

    assert outcome.status == "credential_required"
    assert outcome.reason_code == "source_credential_missing"
    assert outcome.policy_reason_code == "authorized"
    assert outcome.source_basis_id == ENGINEERING_SOURCE_BASIS_ID
    assert outcome.raw_object_id is None
    assert outcome.dataset_version_id is None
    assert adapter.calls == 1
    assert (
        application.state_store.get_price_research_eligibility(listing_id=request.listing_ids[0])
        == outcome.as_payload()
    )
    audit = application.state_store.list_audit_events(trace_id=request.trace_id)
    assert len(audit) == 3
    assert {event["dataset_id"] for event in audit} == {
        ALPACA_SOURCE_ID,
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-trading-calendar-v2",
    }
    assert all(event["outcome"] == "allowed" for event in audit)
    eligibility = application.price_eligibility_query.get_listing(
        listing_id=request.listing_ids[0],
        trace_id="trace-p2-trace-us-01-credential-query",
        security_context=identity.context,
    )
    assert isinstance(eligibility, dict)
    assert eligibility["market"] == "XNAS"
    assert eligibility["status"] == "credential_required"
    assert eligibility["reason_code"] == "source_credential_missing"
    assert eligibility["source_basis_id"] == ALPACA_SOURCE_BASIS_ID
    assert eligibility["source_basis"]["provider_id"] == "alpaca-market-data-basic"  # type: ignore[index]
    assert eligibility["formally_qualified"] is False
    assert eligibility["downstream_readiness"] == {
        "new_collection": "credential_required",
        "feature_materialization": "credential_required",
        "training": "credential_required",
        "research_display": "credential_required",
    }

    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {"Authorization": identity.credential.authorization_header()}
    api_response = client.get(
        f"/api/v1/research/listings/{request.listing_ids[0]}/price-eligibility",
        headers=headers,
    )
    page_response = client.get(
        f"/research/listings/{request.listing_ids[0]}/price-eligibility",
        headers=headers,
    )

    assert api_response.status_code == 200
    assert api_response.json()["status"] == "credential_required"
    assert api_response.json()["downstream_readiness"]["training"] == "credential_required"
    assert page_response.status_code == 200
    assert "美股行情研究資格" in page_response.text
    assert "憑證待設定" in page_response.text
    assert "Alpaca Market Data Basic" in page_response.text
    assert "source_credential_missing" in page_response.text
    assert "下游一致阻擋" in page_response.text


def test_source_egress_audit_failure_prevents_live_provider_contact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-egress-audit",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    adapter = LiveProviderCredentialRequiredAdapter("source_credential_missing")
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'ticket-07-egress-audit.db'}",
        create_schema=True,
    )
    monkeypatch.setattr(
        state_store,
        "record_security_event",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("audit_store_unavailable")),
        raising=False,
    )
    engineering_policy = _qualified_zero_fee_policy(identity, now)
    live_policy = AuthorizationPolicy(
        action_grants=engineering_policy.action_grants,
        source_policies=tuple(
            replace(
                source_policy,
                access_basis="principal_entitlement",
                source_basis_id=None,
                distributions=(),
            )
            if source_policy.dataset_id.startswith("alpaca-us-")
            else source_policy
            for source_policy in engineering_policy.source_policies
        ),
        source_entitlements=engineering_policy.source_entitlements,
    )
    data_supply = DataSupply(
        authorization_policy=live_policy,
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-egress-audit",
        trace_id="trace-ticket-07-egress-audit",
        source_id=ALPACA_SOURCE_ID,
        mode="historical",
        listing_ids=("70000000-0000-4000-8000-000000000001",),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint=None,
        distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
        distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
        source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
        bundle_members=_bundle_member_requests(),
    )

    with pytest.raises(OSError, match="audit_store_unavailable"):
        data_supply.materialize(request)

    assert adapter.calls == 0
    monkeypatch.undo()
    audited_request = replace(
        request,
        request_id="request-ticket-07-egress-audit-recorded",
        trace_id="trace-ticket-07-egress-audit-recorded",
    )
    assert data_supply.materialize(audited_request).status == "credential_required"
    egress_audit = [
        event
        for event in state_store.list_audit_events(trace_id=audited_request.trace_id)
        if event["action"] == "source.egress"
    ]
    assert egress_audit == [
        {
            "action": "source.egress",
            "outcome": "allowed",
            "reason_code": "source_egress_authorized",
            "trace_id": audited_request.trace_id,
            "policy_evaluation_id": egress_audit[0]["policy_evaluation_id"],
            "dataset_id": ALPACA_SOURCE_ID,
            "distribution_id": ALPACA_BARS_DISTRIBUTION_ID,
        }
    ]


def test_provider_unavailability_is_published_through_materialize_rest_and_ui(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    listing_id = "70000000-0000-4000-8000-000000000001"
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-provider-unavailable",
        environment="development",
        scopes={
            "market_data.collect",
            "price_research_eligibility.read",
            "source_credential.manage",
            "source_credential.read",
        },
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    policy = _qualified_zero_fee_policy(identity, now)
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ticket-07-unavailable.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_override=policy,
        source_credential_validators={"alpaca-market-data-basic": ValidCredentialValidator()},
    )
    application.operations_control.set_source_credential(
        provider_id="alpaca-market-data-basic",
        credential_fields={
            "api_key_id": "PK-UNAVAILABLE",
            "api_secret_key": "unavailable-secret",
        },
        trace_id="trace-ticket-07-provider-unavailable-set",
        security_context=identity.context,
    )
    application.operations_control.validate_source_credential(
        provider_id="alpaca-market-data-basic",
        trace_id="trace-ticket-07-provider-unavailable-validate",
        security_context=identity.context,
    )
    transport = EngineeringProviderTransport(
        [ProviderHttpResponse(503, b'{"message":"provider unavailable"}')]
    )
    reference_graph = _engineering_reference_graph({listing_id: ("AAPL",)})
    adapter = AlpacaPriceSourceAdapter(
        source_id=ALPACA_SOURCE_ID,
        mode="current",
        adapter_version="alpaca-market-data-basic-v1",
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        source_access_mode="engineering_double",
        collector=AlpacaSourceCollector(
            source_id=ALPACA_SOURCE_ID,
            provider_id="alpaca-market-data-basic",
            reference_graph=reference_graph,
            market_calendar_evidence=_engineering_market_calendar_evidence(),
            credential_resolver=EngineeringCredentialResolver(),
            transport=transport,
            clock=lambda: now,
            rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        ),
        decoder=AlpacaSourceDecoder(
            source_id=ALPACA_SOURCE_ID,
            reference_graph=reference_graph,
        ),
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-provider-unavailable",
        trace_id="trace-ticket-07-provider-unavailable",
        source_id=ALPACA_SOURCE_ID,
        mode="current",
        listing_ids=(listing_id,),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint=None,
        distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
        distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
        source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
        bundle_members=_bundle_member_requests(),
    )

    outcome = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=application.object_repository,
        state_store=application.state_store,
        clock=lambda: now,
    ).materialize(request)

    assert outcome.status == "unavailable"
    assert outcome.reason_code == "source_provider_unavailable"
    assert outcome.raw_object_id is None
    assert outcome.dataset_version_id is None
    assert len(transport.requests) == 1
    eligibility = application.price_eligibility_query.get_listing(
        listing_id=listing_id,
        trace_id="trace-ticket-07-provider-unavailable-query",
        security_context=identity.context,
    )
    assert isinstance(eligibility, dict)
    assert eligibility["status"] == "unavailable"
    assert eligibility["downstream_readiness"] == {
        "new_collection": "unavailable",
        "feature_materialization": "unavailable",
        "training": "unavailable",
        "research_display": "unavailable",
    }

    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {"Authorization": identity.credential.authorization_header()}
    api_response = client.get(
        f"/api/v1/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )
    page_response = client.get(
        f"/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )

    assert api_response.status_code == 200
    assert api_response.json()["status"] == "unavailable"
    assert page_response.status_code == 200
    assert "來源暫時不可用" in page_response.text
    assert "source_provider_unavailable" in page_response.text


def test_engineering_provider_contract_materializes_through_common_data_supply(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    listing_id = "70000000-0000-4000-8000-000000000001"
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-engineering-contract",
        environment="development",
        scopes={
            "market_data.collect",
            "price_research_eligibility.read",
            "source_credential.manage",
            "source_credential.read",
        },
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    transport = EngineeringProviderTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.25,"v":58414460}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'{"cash_dividends":[{"id":"ca-dividend-aapl","symbol":"AAPL","cusip":"037833100","rate":"0.24","special":false,"foreign":false,"process_date":"2024-02-08","ex_date":"2024-02-09"}],"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]',
            ),
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.30,"v":58414460}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'{"cash_dividends":[{"id":"ca-dividend-aapl","symbol":"AAPL","cusip":"037833100","rate":"0.24","special":false,"foreign":false,"process_date":"2024-02-08","ex_date":"2024-02-09"}],"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]',
            ),
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.30,"v":58414460}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'{"cash_dividends":[],"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]',
            ),
        ]
    )
    listing_symbols = {listing_id: ("AAPL",)}
    reference_graph = _engineering_reference_graph(
        listing_symbols,
        expected_action_ids=frozenset({"ca-dividend-aapl"}),
    )
    adapter = AlpacaPriceSourceAdapter(
        source_id=ALPACA_SOURCE_ID,
        mode="current",
        adapter_version="alpaca-market-data-basic-v1",
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        source_access_mode="engineering_double",
        collector=AlpacaSourceCollector(
            source_id=ALPACA_SOURCE_ID,
            provider_id="alpaca-market-data-basic",
            reference_graph=reference_graph,
            market_calendar_evidence=_engineering_market_calendar_evidence(),
            credential_resolver=EngineeringCredentialResolver(),
            transport=transport,
            clock=lambda: now,
            rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        ),
        decoder=AlpacaSourceDecoder(
            source_id=ALPACA_SOURCE_ID,
            reference_graph=reference_graph,
        ),
    )
    policy = _qualified_zero_fee_policy(identity, now)
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ticket-07-us-published.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_override=policy,
    )
    state_store = application.state_store
    data_supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=application.object_repository,
        state_store=state_store,
        clock=lambda: now,
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-engineering-materialization",
        trace_id="trace-p2-trace-us-01-engineering-materialization",
        source_id=ALPACA_SOURCE_ID,
        mode="current",
        listing_ids=(listing_id,),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint=None,
        distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
        distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
        source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
        bundle_members=_bundle_member_requests(),
        expected_company_action_ids=frozenset({"ca-dividend-aapl"}),
    )

    outcome = data_supply.materialize(request)

    assert outcome.status == "published"
    assert outcome.source_basis_id == ENGINEERING_SOURCE_BASIS_ID
    assert outcome.raw_object_id is not None
    assert outcome.normalized_object_id is not None
    assert outcome.dataset_version_id is not None
    assert outcome.adjustment_version_id is not None
    assert len(transport.requests) == 3
    trace = state_store.get_trace_evidence(request.trace_id)
    adjustment = state_store.get_canonical_artifact(outcome.adjustment_version_id)
    assert adjustment["payload"]["adjusted_closes"] == [
        {
            "adjusted_close": "184.01",
            "listing_id": listing_id,
            "session_date": "2024-01-03",
        }
    ]
    assert outcome.raw_object_id in trace["artifact_ids"]
    assert outcome.dataset_version_id in trace["artifact_ids"]
    assert outcome.adjustment_version_id in trace["artifact_ids"]
    artifacts = [
        state_store.get_canonical_artifact(artifact_id) for artifact_id in trace["artifact_ids"]
    ]
    retrieval_receipt = next(
        artifact
        for artifact in artifacts
        if artifact["artifact_kind"] == "source_retrieval_receipt"
    )
    dataset = state_store.get_canonical_artifact(outcome.dataset_version_id)
    assert retrieval_receipt["payload"]["reference_graph"] == {
        "version_id": reference_graph.version_id,
        "lifecycle_complete": True,
        "company_actions_complete": True,
    }
    assert retrieval_receipt["payload"]["market_calendar_evidence_version_id"] == (
        "engineering-nyse-calendar-2024-01-02-through-2024-01-04-v1"
    )
    assert dataset["payload"]["reference_graph"] == {
        "version_id": reference_graph.version_id,
        "lifecycle_complete": True,
        "company_actions_complete": True,
    }
    member_receipts = [
        artifact
        for artifact in artifacts
        if artifact["artifact_kind"] == "source_bundle_member_receipt"
    ]
    assert {receipt["payload"]["dataset_id"] for receipt in member_receipts} == {
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-trading-calendar-v2",
    }
    assert all(receipt["payload"]["policy_decision_id"] for receipt in member_receipts)
    assert all(receipt["payload"]["raw_object_id"] for receipt in member_receipts)

    correction = data_supply.materialize(
        replace(
            request,
            request_id="request-ticket-07-engineering-correction",
            trace_id="trace-p2-trace-us-01-engineering-correction",
            expected_checkpoint=outcome.checkpoint,
            revision_kind="correction",
        )
    )

    assert correction.status == "quarantined"
    assert correction.reason_code == "correction_requires_review"
    assert correction.dataset_version_id is None
    assert len(transport.requests) == 6

    missing_action = data_supply.materialize(
        replace(
            request,
            request_id="request-ticket-07-missing-reference-action",
            trace_id="trace-p2-trace-us-01-missing-reference-action",
            expected_checkpoint=correction.checkpoint,
            expected_company_action_ids=frozenset({"ca-dividend-aapl"}),
            revision_kind="correction",
        )
    )

    assert missing_action.status == "quarantined"
    assert missing_action.reason_code == "missing_company_action"
    assert missing_action.dataset_version_id is None
    assert len(transport.requests) == 9

    configured = application.operations_control.set_source_credential(
        provider_id="alpaca-market-data-basic",
        credential_fields={
            "api_key_id": "PK-ENGINEERING-READINESS",
            "api_secret_key": "engineering-readiness-secret",
        },
        trace_id="trace-ticket-07-readiness-set",
        security_context=identity.context,
    )
    assert isinstance(configured, dict)
    revoked = application.operations_control.revoke_source_credential(
        provider_id="alpaca-market-data-basic",
        trace_id="trace-ticket-07-readiness-revoke",
        security_context=identity.context,
    )
    assert isinstance(revoked, dict)
    eligibility = application.price_eligibility_query.get_listing(
        listing_id=listing_id,
        trace_id="trace-ticket-07-readiness-query",
        security_context=identity.context,
    )

    assert isinstance(eligibility, dict)
    assert eligibility["status"] == "credential_required"
    assert eligibility["reason_code"] == "source_credential_revoked"
    assert eligibility["downstream_readiness"] == {
        "new_collection": "credential_required",
        "feature_materialization": "credential_required",
        "training": "credential_required",
        "research_display": "credential_required",
    }


@pytest.mark.parametrize(
    ("problem", "expected_reason"),
    [
        ("schema", "bundle_member_schema_incompatible"),
        ("coverage", "bundle_member_incomplete_coverage"),
        ("duplicate", "bundle_member_incomplete_coverage"),
        ("duplicate_action", "duplicate_company_action"),
        ("calendar_omission", "bundle_member_incomplete_coverage"),
    ],
)
def test_bundle_member_quality_issue_is_quarantined_before_dataset_publication(
    tmp_path: Path,
    problem: Literal["schema", "coverage", "duplicate", "duplicate_action", "calendar_omission"],
    expected_reason: str,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    listing_id = "70000000-0000-4000-8000-000000000001"
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-bundle-quality",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    start_date = date(2024, 1, 2) if problem == "calendar_omission" else date(2024, 1, 3)
    end_date = date(2024, 1, 4) if problem == "calendar_omission" else date(2024, 1, 3)
    bars_payload = (
        (
            b'{"bars":{"AAPL":[{"t":"2024-01-02T05:00:00Z","o":185,'
            b'"h":186,"l":184,"c":185,"v":100},{"t":"2024-01-04T05:00:00Z",'
            b'"o":186,"h":187,"l":185,"c":186,"v":200}]},"next_page_token":null}'
        )
        if problem == "calendar_omission"
        else (
            b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,'
            b'"h":185.88,"l":183.43,"c":184.25,"v":58414460}]},'
            b'"next_page_token":null}'
        )
    )
    calendar_payload = (
        b'[{"date":"2024-01-02","open":"09:30","close":"16:00"},{"date":"2024-01-04","open":"09:30","close":"16:00"}]'
        if problem == "calendar_omission"
        else b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]'
    )
    transport = EngineeringProviderTransport(
        [
            ProviderHttpResponse(200, bars_payload),
            ProviderHttpResponse(200, b'{"cash_dividends":[],"next_page_token":null}'),
            ProviderHttpResponse(200, calendar_payload),
        ]
    )
    listing_symbols = {listing_id: ("AAPL",)}
    reference_graph = _engineering_reference_graph(listing_symbols)
    delegate = AlpacaPriceSourceAdapter(
        source_id=ALPACA_SOURCE_ID,
        mode="current",
        adapter_version="alpaca-market-data-basic-v1",
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        source_access_mode="engineering_double",
        collector=AlpacaSourceCollector(
            source_id=ALPACA_SOURCE_ID,
            provider_id="alpaca-market-data-basic",
            reference_graph=reference_graph,
            market_calendar_evidence=_engineering_market_calendar_evidence(),
            credential_resolver=EngineeringCredentialResolver(),
            transport=transport,
            clock=lambda: now,
            rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        ),
        decoder=AlpacaSourceDecoder(
            source_id=ALPACA_SOURCE_ID,
            reference_graph=reference_graph,
        ),
    )
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'ticket-07-bundle-quality.db'}",
        create_schema=True,
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-bundle-quality",
        trace_id="trace-ticket-07-bundle-quality",
        source_id=ALPACA_SOURCE_ID,
        mode="current",
        listing_ids=(listing_id,),
        start_date=start_date,
        end_date=end_date,
        expected_checkpoint=None,
        distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
        distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
        source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
        bundle_members=_bundle_member_requests(),
    )

    outcome = DataSupply(
        authorization_policy=_qualified_zero_fee_policy(identity, now),
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: BundleQualityMutationAdapter(delegate, problem)},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    ).materialize(request)

    assert outcome.status == "quarantined"
    assert outcome.reason_code == expected_reason
    assert outcome.raw_object_id is not None
    assert outcome.dataset_version_id is None
    assert outcome.adjustment_version_id is None
    trace = state_store.get_trace_evidence(request.trace_id)
    artifacts = [
        state_store.get_canonical_artifact(artifact_id) for artifact_id in trace["artifact_ids"]
    ]
    assert any(
        artifact["artifact_kind"] == "source_bundle_member_receipt" for artifact in artifacts
    )
    assert not any(artifact["artifact_kind"] == "dataset_version" for artifact in artifacts)


def test_bundle_member_policy_is_checked_before_any_provider_contact(tmp_path: Path) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-member-policy",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    policy = _qualified_zero_fee_policy(identity, now)
    policy = AuthorizationPolicy(
        action_grants=policy.action_grants,
        source_policies=tuple(
            source_policy
            for source_policy in policy.source_policies
            if source_policy.dataset_id != "alpaca-us-trading-calendar-v2"
        ),
        source_entitlements=policy.source_entitlements,
    )
    adapter = CredentialRequiredPriceAdapter("source_credential_missing")
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'ticket-07-member-policy.db'}",
        create_schema=True,
    )
    outcome = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=state_store,
        clock=lambda: now,
    ).materialize(
        SourcePartitionRequest(
            request_id="request-ticket-07-member-policy",
            trace_id="trace-p2-trace-us-01-member-policy",
            source_id=ALPACA_SOURCE_ID,
            mode="current",
            listing_ids=("70000000-0000-4000-8000-000000000001",),
            start_date=date(2024, 1, 3),
            end_date=date(2024, 1, 3),
            expected_checkpoint=None,
            distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
            distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
            source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
            bundle_members=_bundle_member_requests(),
        )
    )

    assert outcome.status == "policy_blocked"
    assert adapter.calls == 0
    assert outcome.source_basis_id == ENGINEERING_SOURCE_BASIS_ID
    audit = state_store.list_audit_events(trace_id=outcome.trace_id)
    assert [event["dataset_id"] for event in audit] == [
        "alpaca-us-corporate-actions-v1",
        ALPACA_SOURCE_ID,
        "alpaca-us-trading-calendar-v2",
    ]
    assert [event["outcome"] for event in audit] == ["allowed", "allowed", "denied"]


def test_engineering_contract_cannot_authorize_a_live_provider_adapter(tmp_path: Path) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-live-provider-boundary",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    adapter = LiveProviderCredentialRequiredAdapter("source_credential_missing")
    outcome = DataSupply(
        authorization_policy=_qualified_zero_fee_policy(identity, now),
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=StateStore(
            f"sqlite+pysqlite:///{tmp_path / 'ticket-07-live-provider-boundary.db'}",
            create_schema=True,
        ),
        clock=lambda: now,
    ).materialize(
        SourcePartitionRequest(
            request_id="request-ticket-07-live-provider-boundary",
            trace_id="trace-ticket-07-live-provider-boundary",
            source_id=ALPACA_SOURCE_ID,
            mode="current",
            listing_ids=("70000000-0000-4000-8000-000000000001",),
            start_date=date(2024, 1, 3),
            end_date=date(2024, 1, 3),
            expected_checkpoint=None,
            distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
            distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
            source_basis_id=ENGINEERING_SOURCE_BASIS_ID,
            bundle_members=_bundle_member_requests(),
        )
    )

    assert outcome.status == "policy_blocked"
    assert outcome.policy_reason_code == "engineering_contract_live_provider_denied"
    assert adapter.calls == 0


def test_candidate_source_basis_has_no_collect_policy_and_keeps_us_identity(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-candidate-basis",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    adapter = CredentialRequiredPriceAdapter("source_credential_missing")
    outcome = DataSupply(
        authorization_policy=AuthorizationPolicy(
            action_grants=(
                ActionGrant(
                    version_id="grant-ticket-07-candidate-v1",
                    principal_id=identity.context.principal_id,
                    actions=frozenset({"market_data.collect"}),
                    environment="development",
                    valid_from=now - timedelta(days=1),
                    valid_to=now + timedelta(days=1),
                ),
            ),
            source_policies=(),
            source_entitlements=(),
        ),
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        state_store=StateStore(
            f"sqlite+pysqlite:///{tmp_path / 'ticket-07-candidate.db'}",
            create_schema=True,
        ),
        clock=lambda: now,
    ).materialize(
        SourcePartitionRequest(
            request_id="request-ticket-07-candidate-policy",
            trace_id="trace-p2-trace-us-01-candidate-policy",
            source_id=ALPACA_SOURCE_ID,
            mode="current",
            listing_ids=("70000000-0000-4000-8000-000000000001",),
            start_date=date(2024, 1, 3),
            end_date=date(2024, 1, 3),
            expected_checkpoint=None,
            distribution_id=ALPACA_BARS_DISTRIBUTION_ID,
            distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
            source_basis_id=ALPACA_SOURCE_BASIS_ID,
            bundle_members=_bundle_member_requests(),
        )
    )

    assert outcome.status == "policy_blocked"
    assert outcome.source_basis_id == ALPACA_SOURCE_BASIS_ID
    assert adapter.calls == 0
