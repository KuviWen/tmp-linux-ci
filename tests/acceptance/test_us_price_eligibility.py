from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NoReturn

from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.alpaca_market_data import (
    AlpacaPriceSourceAdapter,
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
    SourceBundleMemberRequest,
    SourceCredentialRequired,
    SourcePartitionRequest,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore

ALPACA_BARS_DISTRIBUTION_ID = "alpaca-us-stock-bars-v2"
ALPACA_BARS_DISTRIBUTION_URL = "https://data.alpaca.markets/v2/stocks/bars"
ALPACA_SOURCE_ID = "alpaca-us-stock-bars"
ALPACA_SOURCE_BASIS_ID = "ALPACA-BASIC-US-MARKET-DATA-01"
ENGINEERING_SOURCE_BASIS_ID = "ENGINEERING-ALPACA-CONTRACT-01"


def _bundle_member_requests() -> tuple[SourceBundleMemberRequest, ...]:
    return (
        SourceBundleMemberRequest(
            dataset_id="alpaca-us-corporate-actions-v1",
            distribution_id="alpaca-us-corporate-actions-v1",
            distribution_url="https://data.alpaca.markets/v1/corporate-actions",
        ),
        SourceBundleMemberRequest(
            dataset_id="alpaca-us-trading-calendar-v2",
            distribution_id="alpaca-us-trading-calendar-v2",
            distribution_url="https://paper-api.alpaca.markets/v2/calendar",
        ),
    )


class CredentialRequiredPriceAdapter:
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        self.calls = 0

    def load(self, request: SourcePartitionRequest) -> NoReturn:
        self.calls += 1
        raise SourceCredentialRequired(self.reason_code)


class EngineeringCredentialResolver:
    def resolve_valid(self, provider_id: str) -> dict[str, str]:
        assert provider_id == "alpaca-market-data-basic"
        return {
            "api_key_id": "PK-ENGINEERING-ONLY",
            "api_secret_key": "engineering-contract-secret",
        }


class EngineeringProviderTransport:
    def __init__(self, responses: list[ProviderHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


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
    grant_actions = collect_actions | read_actions
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
        ),
    )


def test_valid_source_policy_with_missing_credential_fails_closed_before_network(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-qualified-individual",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
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


def test_engineering_provider_contract_materializes_through_common_data_supply(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    listing_id = "70000000-0000-4000-8000-000000000001"
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-engineering-contract",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
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
        ]
    )
    listing_symbols = {listing_id: ("AAPL",)}
    adapter = AlpacaPriceSourceAdapter(
        source_id=ALPACA_SOURCE_ID,
        mode="current",
        adapter_version="alpaca-market-data-basic-v1",
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        collector=AlpacaSourceCollector(
            source_id=ALPACA_SOURCE_ID,
            provider_id="alpaca-market-data-basic",
            listing_symbols=listing_symbols,
            credential_resolver=EngineeringCredentialResolver(),
            transport=transport,
            clock=lambda: now,
            rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        ),
        decoder=AlpacaSourceDecoder(
            source_id=ALPACA_SOURCE_ID,
            listing_symbols=listing_symbols,
        ),
    )
    state_store = StateStore(
        f"sqlite+pysqlite:///{tmp_path / 'ticket-07-us-published.db'}",
        create_schema=True,
    )
    data_supply = DataSupply(
        authorization_policy=_qualified_zero_fee_policy(identity, now),
        security_context=identity.context,
        adapters={ALPACA_SOURCE_ID: adapter},
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
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
            if source_policy.dataset_id != "alpaca-us-corporate-actions-v1"
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
