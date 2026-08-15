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
    SourceCredentialRequired,
    SourcePartitionRequest,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore

ALPACA_BARS_DISTRIBUTION_ID = "alpaca-us-stock-bars-v2"
ALPACA_BARS_DISTRIBUTION_URL = "https://data.alpaca.markets/v2/stocks/bars"
ALPACA_SOURCE_ID = "alpaca-us-stock-bars"
ALPACA_SOURCE_BASIS_ID = "ALPACA-BASIC-US-MARKET-DATA-01"


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
            SourcePolicyVersion(
                version_id="policy-ticket-07-alpaca-bars-v1",
                dataset_id=ALPACA_SOURCE_ID,
                allowed_actions=collect_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=allowed_uses,
                access_basis="zero_fee_plan",
                source_basis_id=ALPACA_SOURCE_BASIS_ID,
                license_id="alpaca-customer-agreement-2026-08-15",
                terms_url=(
                    "https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf"
                ),
                terms_content_sha256=(
                    "2dc774d4aeeafbe4c7f0565e7842d932bc8bc10488af805fce43b8734e7b9859"
                ),
                attribution="Alpaca Market Data Basic",
                distributions=(
                    SourceDistribution(
                        dataset_id=ALPACA_BARS_DISTRIBUTION_ID,
                        distribution_url=ALPACA_BARS_DISTRIBUTION_URL,
                    ),
                ),
                provider_id="alpaca-market-data-basic",
                plan_id="basic-2026-08-15",
                principal_classification="individual_non_commercial",
                credential_kind="api_key_pair",
                account_required=True,
                fee_required=False,
            ),
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
            SourceEntitlement(
                version_id="entitlement-ticket-07-qualified-principal-v1",
                principal_id=identity.context.principal_id,
                dataset_id=ALPACA_SOURCE_ID,
                status="active",
                allowed_actions=collect_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=allowed_uses,
            ),
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
    )

    outcome = data_supply.materialize(request)

    assert outcome.status == "credential_required"
    assert outcome.reason_code == "source_credential_missing"
    assert outcome.policy_reason_code == "authorized"
    assert outcome.source_basis_id == ALPACA_SOURCE_BASIS_ID
    assert outcome.raw_object_id is None
    assert outcome.dataset_version_id is None
    assert adapter.calls == 1
    assert (
        application.state_store.get_price_research_eligibility(listing_id=request.listing_ids[0])
        == outcome.as_payload()
    )
    audit = application.state_store.list_audit_events(trace_id=request.trace_id)
    assert len(audit) == 1
    assert audit[0]["outcome"] == "allowed"
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
    assert page_response.status_code == 200
    assert "美股行情研究資格" in page_response.text
    assert "憑證待設定" in page_response.text
    assert "Alpaca Market Data Basic" in page_response.text
    assert "source_credential_missing" in page_response.text


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
                b'{"corporate_actions":[{"id":"ca-dividend-aapl","type":"cash_dividend","symbol":"AAPL","ex_date":"2024-02-09","cash":"0.24"}],"next_page_token":null}',
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
    )

    outcome = data_supply.materialize(request)

    assert outcome.status == "published"
    assert outcome.source_basis_id == ALPACA_SOURCE_BASIS_ID
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
