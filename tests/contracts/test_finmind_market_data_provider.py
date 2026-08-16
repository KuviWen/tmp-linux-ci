from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from email.message import Message
from pathlib import Path
from urllib.request import Request

import pytest

from stock_forecasting.alpaca_market_data import (
    ProviderHttpRequest,
    ProviderHttpResponse,
    UrllibProviderHttpTransport,
)
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import (
    CurrentSourcePrincipalAttributes,
    LocalApiKeyIdentity,
    build_taiwan_finmind_engineering_authorization_policy,
)
from stock_forecasting.data_supply import (
    DataSupply,
    ExternalSecurityAlias,
    ListingLifecycleRecord,
    MarketSessionRecord,
    SourceBundleMemberRequest,
    SourceCredentialRequired,
    SourcePartitionRequest,
    SourceRateLimited,
)
from stock_forecasting.finmind_market_data import (
    FinMindCredentialValidator,
    FinMindLiveContractValidator,
    FinMindPriceSourceAdapter,
    FinMindSourceCollector,
    FinMindSourceDecoder,
)
from stock_forecasting.finmind_provider_contract import (
    FINMIND_PROVIDER_DISTRIBUTIONS,
)
from stock_forecasting.market_data_reference import (
    MarketCalendarEvidence,
    MarketDataCompanyActionExpectation,
    MarketDataReferenceGraph,
    MarketDataReferenceListing,
)
from stock_forecasting.price_eligibility_query import PriceEligibilityQuery
from stock_forecasting.source_credentials import (
    CredentialNotReady,
    SecretLease,
    SecretRef,
    SecretUseContext,
)


class RecordingTransport:
    def __init__(self, response: ProviderHttpResponse) -> None:
        self.response = response
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.response


class MissingCredentialResolver:
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code

    def resolve_valid(
        self,
        provider_id: str,
        *,
        trace_id: str,
        request_id: str,
        work_id: str,
        source_id: str,
    ) -> SecretLease:
        assert provider_id == "finmind-free-api"
        raise CredentialNotReady(self.reason_code)


class LiteralTokenResolver:
    def resolve_valid(
        self,
        provider_id: str,
        *,
        trace_id: str,
        request_id: str,
        work_id: str,
        source_id: str,
    ) -> SecretLease:
        assert provider_id == "finmind-free-api"
        instant = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
        return SecretLease(
            SecretRef("secret-ref:finmind-contract"),
            {"token": "finmind-collector-secret"},
            SecretUseContext(
                workload_principal_id="workload:finmind-contract",
                environment="development",
                source_id=source_id,
                destination=provider_id,
                purpose="price_research_ingest",
                request_id=request_id,
                work_id=work_id,
                credential_version=1,
                lease_duration=timedelta(minutes=5),
                lease_not_before=instant,
                lease_expires_at=instant + timedelta(minutes=5),
            ),
            clock=lambda: instant,
            monotonic_clock=lambda: 0.0,
        )


class SequenceTransport:
    def __init__(self, responses: list[ProviderHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _finmind_request() -> SourcePartitionRequest:
    primary, *members = FINMIND_PROVIDER_DISTRIBUTIONS
    return SourcePartitionRequest(
        request_id="request-ticket-06-finmind",
        trace_id="trace-p2-trace-tw-01-finmind",
        source_id=primary.policy_dataset_id,
        mode="historical",
        listing_ids=("10000000-0000-4000-8000-000000000001",),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint=None,
        distribution_id=primary.distribution_id,
        distribution_url=primary.distribution_url,
        source_basis_id="FINMIND-FREE-TAIWAN-MARKET-DATA-01",
        bundle_members=tuple(
            SourceBundleMemberRequest(
                dataset_id=member.policy_dataset_id,
                distribution_id=member.distribution_id,
                distribution_url=member.distribution_url,
                schema_version={
                    "TaiwanStockTradingDate": "finmind-taiwan-trading-date-v1",
                    "TaiwanStockDividendResult": "finmind-taiwan-dividend-result-v1",
                    "TaiwanStockDelisting": "finmind-taiwan-delisting-v1",
                    "TaiwanStockSplitPrice": "finmind-taiwan-split-price-v1",
                }[member.distribution_id],
            )
            for member in members
        ),
    )


class LiteralUrlResponse:
    status = 200
    headers = Message()

    def read(self, _amount: int | None = None) -> bytes:
        return b'{"status":200,"data":[]}'

    def __enter__(self) -> LiteralUrlResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


@pytest.mark.parametrize(
    ("credential_fields", "expected_reason"),
    [
        ({}, "source_credential_fields_invalid"),
        ({"unexpected": "value"}, "source_credential_fields_invalid"),
    ],
)
def test_finmind_credential_validation_rejects_missing_token_without_network(
    credential_fields: Mapping[str, str],
    expected_reason: str,
) -> None:
    transport = RecordingTransport(ProviderHttpResponse(500, b"must not be used"))

    outcome = FinMindCredentialValidator(transport).validate(credential_fields)

    assert outcome.readiness == "validation_failed"
    assert outcome.reason_code == expected_reason
    assert transport.requests == []


def test_finmind_credential_validation_uses_bearer_header_not_query() -> None:
    token = "finmind-validator-secret-token"
    transport = RecordingTransport(
        ProviderHttpResponse(
            200,
            json.dumps(
                {
                    "msg": "success",
                    "status": 200,
                    "data": [
                        {
                            "stock_id": "2330",
                            "date": "2024-01-03",
                            "open": 578.0,
                            "max": 585.0,
                            "min": 576.0,
                            "close": 581.0,
                            "trading_volume": 15318106,
                        }
                    ],
                }
            ).encode(),
        )
    )

    outcome = FinMindCredentialValidator(transport).validate({"token": token})

    assert outcome.readiness == "valid"
    assert outcome.reason_code == "source_credential_valid"
    assert outcome.evidence.authentication_status == "passed"
    assert outcome.source_contract_assessment is not None
    assert outcome.source_contract_assessment.live_validation == "passed"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == "https://api.finmindtrade.com/api/v4/data"
    assert request.query == {
        "data_id": "2330",
        "dataset": "TaiwanStockPrice",
        "end_date": "2024-01-03",
        "start_date": "2024-01-03",
    }
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert token not in request.url
    assert token not in request.query.values()


def test_shared_http_transport_accepts_only_the_configured_finmind_host() -> None:
    opened: list[Request] = []

    def opener(request: Request, *, timeout: float) -> LiteralUrlResponse:
        assert timeout == 4.0
        opened.append(request)
        return LiteralUrlResponse()

    transport = UrllibProviderHttpTransport(
        allowed_hosts=frozenset({"api.finmindtrade.com"}),
        opener=opener,
        timeout_seconds=4.0,
    )
    response = transport.send(
        ProviderHttpRequest(
            method="GET",
            url="https://api.finmindtrade.com/api/v4/data",
            query={"dataset": "TaiwanStockPrice"},
            headers={"Authorization": "Bearer opaque"},
        )
    )

    assert response.status_code == 200
    assert len(opened) == 1
    assert opened[0].full_url == (
        "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice"
    )
    with pytest.raises(ValueError, match="source_provider_url_forbidden"):
        transport.send(
            ProviderHttpRequest(
                method="GET",
                url="https://data.alpaca.markets/v2/stocks/bars",
                query={},
                headers={},
            )
        )


@pytest.mark.parametrize(
    "reason_code",
    [
        "source_credential_missing",
        "source_credential_authentication_failed",
        "source_credential_revoked",
    ],
)
def test_finmind_collector_never_contacts_provider_without_a_valid_token(
    reason_code: str,
) -> None:
    transport = RecordingTransport(ProviderHttpResponse(500, b"must not be used"))
    collector = FinMindSourceCollector(
        source_id="finmind-taiwan-stock-price",
        provider_id="finmind-free-api",
        credential_resolver=MissingCredentialResolver(reason_code),
        transport=transport,
        clock=lambda: datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
    )

    with pytest.raises(SourceCredentialRequired, match=reason_code):
        collector.collect(_finmind_request())

    assert transport.requests == []


@pytest.mark.parametrize("status_code", [402, 429])
def test_finmind_collector_preserves_quota_retry_evidence(status_code: int) -> None:
    collector = FinMindSourceCollector(
        source_id="finmind-taiwan-stock-price",
        provider_id="finmind-free-api",
        credential_resolver=LiteralTokenResolver(),
        transport=RecordingTransport(
            ProviderHttpResponse(
                status_code=status_code,
                body=b'{"msg":"quota exhausted"}',
                headers={"Retry-After": "17"},
            )
        ),
        clock=lambda: datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
    )

    with pytest.raises(SourceRateLimited) as raised:
        collector.collect(_finmind_request())

    assert raised.value.retry_after_seconds == 17
    assert raised.value.rate_limit_policy_id == "finmind-free-600-requests-per-hour-v1"


def test_finmind_collector_rejects_a_response_that_echoes_the_token() -> None:
    token = "finmind-collector-secret"
    collector = FinMindSourceCollector(
        source_id="finmind-taiwan-stock-price",
        provider_id="finmind-free-api",
        credential_resolver=LiteralTokenResolver(),
        transport=RecordingTransport(
            ProviderHttpResponse(
                200,
                json.dumps(
                    {
                        "status": 200,
                        "msg": "success",
                        "data": [{"provider_debug": token}],
                    }
                ).encode(),
            )
        ),
        clock=lambda: datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
    )

    with pytest.raises(ValueError) as raised:
        collector.collect(_finmind_request())

    assert str(raised.value) == "source_provider_credential_echo_detected"
    assert token not in str(raised.value)


def test_finmind_collector_builds_an_immutable_candidate_bundle() -> None:
    listing_id = "10000000-0000-4000-8000-000000000001"
    action_id = "finmind:TaiwanStockDividendResult:2330:2024-01-03:cash_dividend"
    reference_graph = MarketDataReferenceGraph(
        version_id="engineering-finmind-reference-v1",
        listings=(
            MarketDataReferenceListing(
                listing_id=listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="2330",
                        security_name="台積電",
                        valid_from=date(1994, 9, 5),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(1994, 9, 5),
                        status="active",
                        source_event_id="engineering-finmind-active-2330",
                    ),
                ),
            ),
        ),
        company_action_expectations=(
            MarketDataCompanyActionExpectation(
                action_id=action_id,
                listing_id=listing_id,
                effective_date=date(2024, 1, 3),
            ),
        ),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    calendar_evidence = MarketCalendarEvidence(
        version_id="engineering-xtai-calendar-2024-01-03-v1",
        coverage_start=date(2024, 1, 3),
        coverage_end=date(2024, 1, 3),
        sessions=(
            MarketSessionRecord(
                session_date=date(2024, 1, 3),
                open_time="09:00",
                close_time="13:30",
                session_kind="regular",
            ),
        ),
    )
    transport = SequenceTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"stock_id":"2330","date":"2024-01-03","open":578,"max":585,"min":576,"close":581,"trading_volume":15318106}]}',
            ),
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2024-01-03"}]}',
            ),
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2024-01-03","stock_id":"2330","stock_and_cache_dividend":3,"stock_or_cache_dividend":"\\u606f"}]}',
            ),
            ProviderHttpResponse(200, b'{"status":200,"msg":"success","data":[]}'),
            ProviderHttpResponse(200, b'{"status":200,"msg":"success","data":[]}'),
        ]
    )
    collector = FinMindSourceCollector(
        source_id="finmind-taiwan-stock-price",
        provider_id="finmind-free-api",
        reference_graph=reference_graph,
        market_calendar_evidence=calendar_evidence,
        credential_resolver=LiteralTokenResolver(),
        transport=transport,
        clock=lambda: datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
    )

    collection = collector.collect(_finmind_request())

    assert collection.coverage.complete is True
    assert collection.coverage.observed_start == date(2024, 1, 3)
    assert collection.coverage.observed_end == date(2024, 1, 3)
    assert collection.reference_graph_version_id == reference_graph.version_id
    assert collection.reference_graph_lifecycle_verified is True
    assert collection.company_action_completeness_verified is True
    assert collection.expected_company_action_ids == frozenset({action_id})
    assert collection.market_calendar_evidence_version_id == calendar_evidence.version_id
    assert collection.checkpoint_after is not None
    assert collection.checkpoint_after.startswith("sha256:")
    assert len(collection.bundle_members) == 4
    assert [request.query["dataset"] for request in transport.requests] == [
        "TaiwanStockPrice",
        "TaiwanStockTradingDate",
        "TaiwanStockDividendResult",
        "TaiwanStockDelisting",
        "TaiwanStockSplitPrice",
    ]
    assert transport.requests[0].query["data_id"] == "2330"
    assert transport.requests[2].query["data_id"] == "2330"
    assert "data_id" not in transport.requests[1].query
    assert "data_id" not in transport.requests[3].query
    assert "data_id" not in transport.requests[4].query
    assert all(
        request.headers["Authorization"] == "Bearer finmind-collector-secret"
        for request in transport.requests
    )
    assert all(
        "finmind-collector-secret" not in request.query.values() for request in transport.requests
    )

    decoded = FinMindSourceDecoder(
        source_id="finmind-taiwan-stock-price",
        reference_graph=reference_graph,
        market_calendar_evidence=calendar_evidence,
    ).decode(collection)

    assert decoded.schema_version == "taiwan-unadjusted-eod-v1"
    assert [
        (row.listing_id, row.session_date, str(row.close), row.volume) for row in decoded.prices
    ] == [(listing_id, date(2024, 1, 3), "581", 15318106)]
    assert [
        (
            action.listing_id,
            action.effective_date,
            action.kind,
            str(action.value),
            action.currency,
            action.source_action_id,
        )
        for action in decoded.company_actions
    ] == [
        (
            listing_id,
            date(2024, 1, 3),
            "cash_dividend",
            "3",
            "TWD",
            action_id,
        )
    ]
    assert decoded.listing_lifecycle == reference_graph.listings[0].lifecycle
    assert decoded.market_sessions == calendar_evidence.sessions
    assert decoded.quality_issues == ()


def test_finmind_zero_ohlc_row_is_not_a_complete_price_session() -> None:
    listing_id = "10000000-0000-4000-8000-000000000001"
    reference_graph = MarketDataReferenceGraph(
        version_id="engineering-finmind-suspension-v1",
        listings=(
            MarketDataReferenceListing(
                listing_id=listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="2317",
                        security_name="鴻海",
                        valid_from=date(1991, 6, 18),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(1991, 6, 18),
                        status="active",
                        source_event_id="engineering-finmind-active-2317",
                    ),
                ),
            ),
        ),
        company_action_expectations=(),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    calendar_evidence = MarketCalendarEvidence(
        version_id="engineering-xtai-calendar-2025-07-30-v1",
        coverage_start=date(2025, 7, 30),
        coverage_end=date(2025, 7, 30),
        sessions=(
            MarketSessionRecord(
                session_date=date(2025, 7, 30),
                open_time="09:00",
                close_time="13:30",
                session_kind="regular",
            ),
        ),
    )
    transport = SequenceTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"stock_id":"2317","date":"2025-07-30","open":0,"max":0,"min":0,"close":0,"trading_volume":0}]}',
            ),
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2025-07-30"}]}',
            ),
            ProviderHttpResponse(200, b'{"status":200,"msg":"success","data":[]}'),
            ProviderHttpResponse(200, b'{"status":200,"msg":"success","data":[]}'),
            ProviderHttpResponse(200, b'{"status":200,"msg":"success","data":[]}'),
        ]
    )
    collector = FinMindSourceCollector(
        source_id="finmind-taiwan-stock-price",
        provider_id="finmind-free-api",
        reference_graph=reference_graph,
        market_calendar_evidence=calendar_evidence,
        credential_resolver=LiteralTokenResolver(),
        transport=transport,
        clock=lambda: datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
    )

    collection = collector.collect(
        replace(
            _finmind_request(),
            start_date=date(2025, 7, 30),
            end_date=date(2025, 7, 30),
        )
    )
    decoded = FinMindSourceDecoder(
        source_id="finmind-taiwan-stock-price",
        reference_graph=reference_graph,
        market_calendar_evidence=calendar_evidence,
    ).decode(collection)

    assert collection.coverage.complete is False
    assert collection.coverage.observed_start is None
    assert collection.coverage.observed_end is None
    assert decoded.prices == ()


@pytest.mark.parametrize(
    ("delisting_payload", "expected_verified"),
    [
        (b'{"status":200,"msg":"success","data":[]}', False),
        (
            b'{"status":200,"msg":"success","data":[{"stock_id":"2448","date":"2021-01-06"}]}',
            True,
        ),
    ],
)
def test_finmind_collector_attests_the_exact_delisting_event(
    delisting_payload: bytes,
    expected_verified: bool,
) -> None:
    listing_id = "10000000-0000-4000-8000-000000000001"
    reference_graph = MarketDataReferenceGraph(
        version_id="engineering-finmind-delisting-v1",
        listings=(
            MarketDataReferenceListing(
                listing_id=listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="2448",
                        security_name="晶電",
                        valid_from=date(2001, 5, 25),
                        valid_to=date(2021, 1, 5),
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2001, 5, 25),
                        status="active",
                        source_event_id="engineering-finmind-active-2448",
                    ),
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2021, 1, 6),
                        status="delisted",
                        source_event_id="engineering-finmind-delisted-2448",
                    ),
                ),
            ),
        ),
        company_action_expectations=(),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    transport = SequenceTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"stock_id":"2448","date":"2021-01-05","open":42,"max":43,"min":41,"close":42,"trading_volume":1000}]}',
            ),
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2021-01-05"}]}',
            ),
            ProviderHttpResponse(200, b'{"status":200,"msg":"success","data":[]}'),
            ProviderHttpResponse(200, delisting_payload),
            ProviderHttpResponse(200, b'{"status":200,"msg":"success","data":[]}'),
        ]
    )
    collector = FinMindSourceCollector(
        source_id="finmind-taiwan-stock-price",
        provider_id="finmind-free-api",
        reference_graph=reference_graph,
        credential_resolver=LiteralTokenResolver(),
        transport=transport,
        clock=lambda: datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
    )

    collection = collector.collect(
        replace(
            _finmind_request(),
            start_date=date(2021, 1, 5),
            end_date=date(2021, 1, 6),
        )
    )

    assert collection.reference_graph_lifecycle_verified is expected_verified


def test_finmind_materialization_fails_closed_before_network_when_token_is_missing(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-finmind-source-adapter",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_or_internal_group",
    )
    policy = build_taiwan_finmind_engineering_authorization_policy(identity.context)
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ticket-06-finmind.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_override=policy,
    )
    transport = RecordingTransport(ProviderHttpResponse(500, b"must not be used"))
    adapter = FinMindPriceSourceAdapter(
        source_id="finmind-taiwan-stock-price",
        mode="historical",
        adapter_version="finmind-ticket-06-v1",
        rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
        source_access_mode="engineering_double",
        collector=FinMindSourceCollector(
            source_id="finmind-taiwan-stock-price",
            provider_id="finmind-free-api",
            credential_resolver=MissingCredentialResolver("source_credential_missing"),
            transport=transport,
            clock=lambda: now,
            rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
        ),
        decoder=FinMindSourceDecoder(
            source_id="finmind-taiwan-stock-price",
            reference_graph=MarketDataReferenceGraph(
                version_id="engineering-unreachable-v1",
                listings=(
                    MarketDataReferenceListing(
                        listing_id="10000000-0000-4000-8000-000000000001",
                        aliases=(
                            ExternalSecurityAlias(
                                security_code="2330",
                                security_name="TSMC",
                                valid_from=date(1994, 9, 5),
                                valid_to=None,
                            ),
                        ),
                        lifecycle=(
                            ListingLifecycleRecord(
                                listing_id="10000000-0000-4000-8000-000000000001",
                                effective_date=date(1994, 9, 5),
                                status="active",
                                source_event_id="engineering-active",
                            ),
                        ),
                    ),
                ),
                company_action_expectations=(),
                lifecycle_complete=True,
                company_actions_complete=True,
            ),
        ),
    )
    outcome = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={"finmind-taiwan-stock-price": adapter},
        object_repository=application.object_repository,
        state_store=application.state_store,
        clock=lambda: now,
    ).materialize(
        replace(
            _finmind_request(),
            source_basis_id="ENGINEERING-FINMIND-CONTRACT-01",
        )
    )

    assert outcome.status == "credential_required"
    assert outcome.reason_code == "source_credential_missing"
    assert outcome.source_basis_id == "ENGINEERING-FINMIND-CONTRACT-01"
    assert outcome.raw_object_id is None
    assert transport.requests == []
    eligibility = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=policy,
        authorization_time=now,
        source_authorization_policy=lambda _principal_id: policy,
        source_principal_attributes=lambda _principal_id: (
            CurrentSourcePrincipalAttributes.from_verified_security_context(identity.context)
        ),
        object_repository=application.object_repository,
    ).get_listing(
        listing_id="10000000-0000-4000-8000-000000000001",
        trace_id="trace-ticket-06-finmind-query",
        security_context=identity.context,
    )
    assert isinstance(eligibility, dict)
    assert eligibility["market"] == "XTAI"
    assert eligibility["status"] == "credential_required"
    assert eligibility["reason_code"] == "source_credential_missing"
    assert eligibility["source_basis_id"] == "FINMIND-FREE-TAIWAN-MARKET-DATA-01"
    assert eligibility["source_basis"]["provider_id"] == "finmind-free-api"  # type: ignore[index]
    assert eligibility["formally_qualified"] is False


def test_application_exposes_finmind_through_the_ticket_07_adapter_identity(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    local_identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-local-researcher",
        environment="development",
        scopes={"source_credential.read", "source_credential.manage"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        data_protection_classes={"restricted", "secret"},
    )
    source_identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-finmind-source-adapter",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_or_internal_group",
    )

    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ticket-06-application.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=local_identity,
        source_adapter_security_context=source_identity.context,
    )

    assert application.finmind_price_adapter is not None
    assert application.finmind_price_adapter.source_id == "finmind-taiwan-stock-price"
    assert application.finmind_price_adapter.source_access_mode == "live_provider"


def _finmind_live_price_responses() -> list[ProviderHttpResponse]:
    from stock_forecasting.data_supply import load_taiwan_stock_pool_manifest

    manifest = load_taiwan_stock_pool_manifest()
    return [
        ProviderHttpResponse(
            200,
            json.dumps(
                {
                    "status": 200,
                    "msg": "success",
                    "data": [
                        {
                            "stock_id": listing.external_security_code,
                            "date": (
                                listing.external_aliases[-1].valid_to or date(2025, 12, 11)
                            ).isoformat(),
                            "open": 100,
                            "max": 102,
                            "min": 99,
                            "close": 101,
                            "trading_volume": 1000,
                        }
                    ],
                }
            ).encode(),
        )
        for listing in manifest.listings
    ]


_FINMIND_DIVIDEND_PROBE = (
    b'{"status":200,"msg":"success","data":[{"date":"2025-12-11","stock_id":"2330",'
    b'"stock_and_cache_dividend":6,"stock_or_cache_dividend":"\\u606f"}]}'
)
_FINMIND_SPLIT_PROBE = (
    b'{"status":200,"msg":"success","data":[{"date":"2025-06-18","stock_id":"0050",'
    b'"type":"\\u5206\\u5272","before_price":188.65,"after_price":47.16}]}'
)


@pytest.mark.parametrize(
    ("dividend_payload", "split_payload"),
    [
        (b'{"status":200,"msg":"success","data":[]}', _FINMIND_SPLIT_PROBE),
        (_FINMIND_DIVIDEND_PROBE, b'{"status":200,"msg":"success","data":[]}'),
    ],
)
def test_finmind_live_contract_requires_known_company_action_rows(
    dividend_payload: bytes,
    split_payload: bytes,
) -> None:
    responses = _finmind_live_price_responses()
    responses.extend(
        [
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2024-01-03"}]}',
            ),
            ProviderHttpResponse(200, dividend_payload),
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2021-01-06","stock_id":"2448","stock_name":"Epistar"}]}',
            ),
            ProviderHttpResponse(200, split_payload),
        ]
    )

    result = FinMindLiveContractValidator(SequenceTransport(responses)).validate(
        {"token": "finmind-live-contract-secret"}
    )

    assert result.readiness == "valid"
    assert result.evidence.authentication_status == "passed"
    assert result.source_contract_assessment is not None
    assert result.source_contract_assessment.live_validation == "failed"
    assert (
        result.source_contract_assessment.source_contract_reason_code
        == "source_contract_schema_invalid"
    )


def test_finmind_live_contract_binds_all_ten_listings_and_required_datasets() -> None:
    from stock_forecasting.data_supply import load_taiwan_stock_pool_manifest

    manifest = load_taiwan_stock_pool_manifest()
    price_probe_dates = {
        listing.external_security_code: (
            listing.external_aliases[-1].valid_to or date(2025, 12, 11)
        )
        for listing in manifest.listings
    }
    responses = _finmind_live_price_responses()
    responses.extend(
        [
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2024-01-03"}]}',
            ),
            ProviderHttpResponse(200, _FINMIND_DIVIDEND_PROBE),
            ProviderHttpResponse(
                200,
                b'{"status":200,"msg":"success","data":[{"date":"2021-01-06","stock_id":"2448","stock_name":"Epistar"}]}',
            ),
            ProviderHttpResponse(200, _FINMIND_SPLIT_PROBE),
        ]
    )
    transport = SequenceTransport(responses)

    result = FinMindLiveContractValidator(transport).validate(
        {"token": "finmind-live-contract-secret"}
    )

    assert result.readiness == "valid"
    assert result.reason_code == "source_credential_valid"
    assert result.evidence.authentication_status == "passed"
    assert result.source_contract_assessment is not None
    assert result.source_contract_assessment.live_validation == "passed"
    assert result.source_contract_assessment.contract_id == "finmind-ticket-06-live-v1"
    assert result.source_contract_assessment.ticker_count == 10
    assert result.source_contract_assessment.datasets == (
        "TaiwanStockPrice",
        "TaiwanStockTradingDate",
        "TaiwanStockDividendResult",
        "TaiwanStockDelisting",
        "TaiwanStockSplitPrice",
    )
    assert result.source_contract_assessment.universe_manifest_id == manifest.manifest_id
    assert result.source_contract_assessment.reference_graph_version_id == (
        manifest.selection_evidence_version
    )
    assert result.source_contract_assessment.symbol_lifecycle_probe == "passed"
    assert result.source_contract_assessment.listing_ids == tuple(
        listing.listing_id for listing in manifest.listings
    )
    assert len(transport.requests) == 14
    assert {
        (request.query["data_id"], request.query["start_date"])
        for request in transport.requests[:10]
    } == {
        (
            listing.external_security_code,
            price_probe_dates[listing.external_security_code].isoformat(),
        )
        for listing in manifest.listings
    }
    assert all(
        request.query["end_date"] == request.query["start_date"]
        for request in transport.requests[:10]
    )
    assert all(
        request.headers["Authorization"] == "Bearer finmind-live-contract-secret"
        for request in transport.requests
    )


def test_finmind_live_contract_treats_initial_forbidden_as_inconclusive() -> None:
    transport = RecordingTransport(ProviderHttpResponse(403, b'{"msg":"forbidden","status":403}'))

    result = FinMindLiveContractValidator(transport).validate(
        {"token": "finmind-live-contract-secret"}
    )

    assert result.readiness == "configured"
    assert result.reason_code == "source_credential_validation_inconclusive"
    assert result.evidence.authentication_status == "not_run"
    assert result.source_contract_assessment is not None
    assert result.source_contract_assessment.live_validation == "failed"
    assert (
        result.source_contract_assessment.source_contract_reason_code == "source_contract_forbidden"
    )
