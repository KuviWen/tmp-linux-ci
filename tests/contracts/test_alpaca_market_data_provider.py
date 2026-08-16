from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from email.message import Message
from urllib.request import Request

import pytest

from stock_forecasting.alpaca_market_data import (
    AlpacaCompanyActionExpectation,
    AlpacaCredentialValidator,
    AlpacaLiveContractValidator,
    AlpacaMarketCalendarEvidence,
    AlpacaReferenceGraph,
    AlpacaReferenceListing,
    AlpacaSourceCollector,
    AlpacaSourceDecoder,
    ProviderHttpRequest,
    ProviderHttpResponse,
    UrllibProviderHttpTransport,
    load_candidate_alpaca_market_calendar_evidence,
    load_candidate_alpaca_reference_graph,
)
from stock_forecasting.data_supply import (
    CollectedSourcePartition,
    CompanyActionRecord,
    ExternalSecurityAlias,
    ListingLifecycleRecord,
    MarketSessionRecord,
    SourceBundleMemberRequest,
    SourceCollectionCoverage,
    SourceCredentialRequired,
    SourcePartitionRequest,
    SourceRateLimited,
    SymbolIdentityRecord,
)
from stock_forecasting.source_credentials import CredentialNotReady


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


def _engineering_market_calendar_evidence() -> AlpacaMarketCalendarEvidence:
    return AlpacaMarketCalendarEvidence(
        version_id="engineering-nyse-calendar-2023-12-29-through-2024-01-04-v1",
        coverage_start=date(2023, 12, 29),
        coverage_end=date(2024, 1, 4),
        sessions=tuple(
            MarketSessionRecord(
                session_date=session_date,
                open_time="09:30",
                close_time="16:00",
                session_kind="regular",
            )
            for session_date in (
                date(2023, 12, 29),
                date(2024, 1, 2),
                date(2024, 1, 3),
                date(2024, 1, 4),
            )
        ),
    )


class LiteralProviderTransport:
    def __init__(self, response: ProviderHttpResponse) -> None:
        self.response = response
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.response


class MissingCredentialResolver:
    def __init__(self, reason_code: str = "source_credential_missing") -> None:
        self.reason_code = reason_code

    def resolve_valid(self, provider_id: str, *, trace_id: str) -> dict[str, str]:
        raise CredentialNotReady(self.reason_code)


class LiteralCredentialResolver:
    def __init__(self) -> None:
        self.provider_ids: list[str] = []

    def resolve_valid(self, provider_id: str, *, trace_id: str) -> dict[str, str]:
        self.provider_ids.append(provider_id)
        return {
            "api_key_id": "PK-COLLECTOR-CONTRACT",
            "api_secret_key": "collector-contract-secret",
        }


class SequenceProviderTransport:
    def __init__(self, responses: list[ProviderHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class LiteralUrlResponse:
    status = 200
    headers = Message()

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.read_sizes: list[int | None] = []

    def read(self, amount: int | None = None) -> bytes:
        self.read_sizes.append(amount)
        return self._body if amount is None else self._body[:amount]

    def __enter__(self) -> LiteralUrlResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _complete_reference_graph(
    *,
    listing_id: str,
    symbols: tuple[str, ...] = ("AAPL",),
    expected_action_ids: tuple[str, ...] = (),
) -> AlpacaReferenceGraph:
    aliases = tuple(
        ExternalSecurityAlias(
            security_code=symbol,
            security_name=f"{symbol} engineering reference",
            valid_from=date(2000 + index, 1, 1),
            valid_to=(date(2000 + index, 12, 31) if index < len(symbols) - 1 else None),
        )
        for index, symbol in enumerate(symbols)
    )
    return AlpacaReferenceGraph(
        version_id=f"engineering-reference-{listing_id}",
        listings=(
            AlpacaReferenceListing(
                listing_id=listing_id,
                aliases=aliases,
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2000, 1, 1),
                        status="active",
                        source_event_id=f"engineering-active-{listing_id}",
                    ),
                ),
            ),
        ),
        company_action_expectations=tuple(
            AlpacaCompanyActionExpectation(
                action_id=action_id,
                listing_id=listing_id,
                effective_date=date(2024, 1, 3),
            )
            for action_id in expected_action_ids
        ),
        lifecycle_complete=True,
        company_actions_complete=True,
    )


def _complete_reference_graph_for_listings(
    listing_symbols: dict[str, tuple[str, ...]],
    *,
    expected_actions: tuple[AlpacaCompanyActionExpectation, ...] = (),
) -> AlpacaReferenceGraph:
    return AlpacaReferenceGraph(
        version_id="engineering-reference-multi-listing-v1",
        listings=tuple(
            AlpacaReferenceListing(
                listing_id=listing_id,
                aliases=tuple(
                    ExternalSecurityAlias(
                        security_code=symbol,
                        security_name=f"{symbol} engineering reference",
                        valid_from=(date(2000 + index, 1, 1)),
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
        company_action_expectations=expected_actions,
        lifecycle_complete=True,
        company_actions_complete=True,
    )


def _collected_partition_for_reference_graph(
    reference_graph: AlpacaReferenceGraph,
    *,
    listing_ids: tuple[str, ...],
    start_date: date,
    end_date: date,
    bars: dict[str, list[dict[str, object]]],
    corporate_actions: dict[str, object] | None = None,
) -> CollectedSourcePartition:
    raw_payload = json.dumps(
        {
            "provider_id": "alpaca-market-data-basic",
            "schema_version": "alpaca-source-bundle-v1",
            "bars_pages": [{"bars": bars, "next_page_token": None}],
            "corporate_action_pages": [{"next_page_token": None, **(corporate_actions or {})}],
            "calendar": [],
            "reference_graph": reference_graph.partition_payload(
                listing_ids=listing_ids,
                start_date=start_date,
                end_date=end_date,
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return CollectedSourcePartition(
        request_id="request-ticket-07-dated-symbol-resolution",
        source_id="alpaca-us-stock-bars",
        acquired_at=datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        sanitized_source_uri="https://data.alpaca.markets/v2/stocks/bars",
        media_type="application/json",
        raw_payload=raw_payload,
        checkpoint_before=None,
        checkpoint_after="sha256:dated-symbol-resolution",
        coverage=SourceCollectionCoverage(
            requested_start=start_date,
            requested_end=end_date,
            observed_start=start_date,
            observed_end=end_date,
            complete=True,
        ),
        source_revision="sha256:dated-symbol-resolution",
        requested_listing_ids=listing_ids,
        reference_graph_version_id=reference_graph.version_id,
        reference_graph_lifecycle_verified=True,
        company_action_completeness_verified=True,
    )


def test_complete_reference_graph_requires_an_evidenced_active_event() -> None:
    listing_id = "70000000-0000-4000-8000-000000000011"
    listing = AlpacaReferenceListing(
        listing_id=listing_id,
        aliases=(
            ExternalSecurityAlias(
                security_code="OLD",
                security_name="Historical Example",
                valid_from=None,
                valid_to=date(2023, 3, 28),
            ),
        ),
        lifecycle=(
            ListingLifecycleRecord(
                listing_id=listing_id,
                effective_date=date(2023, 3, 28),
                status="delisted",
                source_event_id="reference-delisted-example",
            ),
        ),
    )

    with pytest.raises(ValueError, match="alpaca_reference_graph_invalid"):
        AlpacaReferenceGraph(
            version_id="engineering-us-reference-graph-v1",
            listings=(listing,),
            company_action_expectations=(),
            lifecycle_complete=True,
            company_actions_complete=True,
        )


def test_candidate_reference_graph_does_not_invent_unknown_active_dates() -> None:
    graph = load_candidate_alpaca_reference_graph()

    current = next(
        listing for listing in graph.listings if listing.aliases[-1].security_code == "AAPL"
    )
    delisted = next(
        listing for listing in graph.listings if listing.aliases[-1].security_code == "SIVB"
    )

    assert [(event.effective_date, event.status) for event in current.lifecycle] == [
        (date(1980, 12, 12), "active")
    ]
    assert [(event.effective_date, event.status) for event in delisted.lifecycle] == [
        (date(2023, 3, 28), "delisted")
    ]
    assert graph.lifecycle_complete is False


def test_candidate_calendar_evidence_is_limited_to_the_official_half_day_case() -> None:
    evidence = load_candidate_alpaca_market_calendar_evidence()

    assert evidence.coverage_start == date(2024, 11, 29)
    assert evidence.coverage_end == date(2024, 11, 29)
    assert evidence.sessions == (
        MarketSessionRecord(
            session_date=date(2024, 11, 29),
            open_time="09:30",
            close_time="13:00",
            session_kind="early_close",
        ),
    )
    assert (
        evidence.expected_sessions(
            start_date=date(2024, 11, 28),
            end_date=date(2024, 11, 29),
        )
        is None
    )


def test_alpaca_credential_validator_checks_a_fixed_raw_daily_bar_request() -> None:
    transport = LiteralProviderTransport(
        ProviderHttpResponse(
            status_code=200,
            body=b'{"bars":[{"t":"2024-01-03T05:00:00Z","c":184.25}],"next_page_token":null}',
        )
    )
    validator = AlpacaCredentialValidator(transport)

    result = validator.validate(
        {
            "api_key_id": "PK-PROVIDER-CONTRACT",
            "api_secret_key": "provider-contract-secret",
        }
    )

    assert result.readiness == "valid"
    assert result.reason_code == "source_credential_valid"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == "https://data.alpaca.markets/v2/stocks/AAPL/bars"
    assert request.query == {
        "adjustment": "raw",
        "end": "2024-01-04T00:00:00Z",
        "feed": "sip",
        "limit": "1",
        "start": "2024-01-03T00:00:00Z",
        "timeframe": "1Day",
    }
    assert request.headers == {
        "APCA-API-KEY-ID": "PK-PROVIDER-CONTRACT",
        "APCA-API-SECRET-KEY": "provider-contract-secret",
        "Accept": "application/json",
    }
    assert "PK-PROVIDER-CONTRACT" not in repr(request)
    assert "provider-contract-secret" not in repr(request)


def test_urllib_provider_transport_encodes_query_and_keeps_auth_in_headers() -> None:
    opened: list[tuple[Request, float]] = []

    def opener(request: Request, *, timeout: float) -> LiteralUrlResponse:
        opened.append((request, timeout))
        return LiteralUrlResponse(b'{"bars":[]}')

    transport = UrllibProviderHttpTransport(opener=opener, timeout_seconds=7.5)

    response = transport.send(
        ProviderHttpRequest(
            method="GET",
            url="https://data.alpaca.markets/v2/stocks/AAPL/bars",
            query={"adjustment": "raw", "timeframe": "1Day"},
            headers={
                "APCA-API-KEY-ID": "PK-URLLIB",
                "APCA-API-SECRET-KEY": "urllib-secret",
                "Accept": "application/json",
            },
        )
    )

    assert response == ProviderHttpResponse(200, b'{"bars":[]}', {})
    assert len(opened) == 1
    request, timeout = opened[0]
    assert request.full_url == (
        "https://data.alpaca.markets/v2/stocks/AAPL/bars?adjustment=raw&timeframe=1Day"
    )
    assert request.get_method() == "GET"
    assert request.get_header("Apca-api-key-id") == "PK-URLLIB"
    assert request.get_header("Apca-api-secret-key") == "urllib-secret"
    assert timeout == 7.5


def test_urllib_provider_transport_bounds_provider_response_bytes() -> None:
    response_body = b"x" * (8 * 1024 * 1024 + 1)
    opened_response = LiteralUrlResponse(response_body)

    response = UrllibProviderHttpTransport(
        opener=lambda _request, *, timeout: opened_response
    ).send(
        ProviderHttpRequest(
            method="GET",
            url="https://data.alpaca.markets/v2/stocks/AAPL/bars",
            query={},
            headers={},
        )
    )

    assert opened_response.read_sizes == [8 * 1024 * 1024 + 1]
    assert response == ProviderHttpResponse(
        502,
        b'{"message":"provider response too large"}',
        {},
    )


@pytest.mark.parametrize(
    (
        "status_code",
        "body",
        "expected_readiness",
        "expected_reason",
        "expected_source_contract_reason",
    ),
    [
        (
            401,
            b'{"message":"unauthorized"}',
            "validation_failed",
            "source_credential_authentication_failed",
            None,
        ),
        (
            403,
            b'{"message":"forbidden"}',
            "configured",
            "source_credential_validation_inconclusive",
            "source_contract_forbidden",
        ),
        (
            429,
            b'{"message":"rate limit exceeded"}',
            "configured",
            "source_credential_validation_inconclusive",
            "source_contract_rate_limited",
        ),
        (
            503,
            b'{"message":"unavailable"}',
            "configured",
            "source_credential_validation_inconclusive",
            "source_contract_unavailable",
        ),
        (
            200,
            b"not-json",
            "valid",
            "source_credential_valid",
            "source_contract_schema_invalid",
        ),
        (
            200,
            b'{"bars":{}}',
            "valid",
            "source_credential_valid",
            "source_contract_schema_invalid",
        ),
    ],
)
def test_alpaca_credential_validator_separates_auth_from_source_contract_health(
    status_code: int,
    body: bytes,
    expected_readiness: str,
    expected_reason: str,
    expected_source_contract_reason: str | None,
) -> None:
    validator = AlpacaCredentialValidator(
        LiteralProviderTransport(ProviderHttpResponse(status_code=status_code, body=body))
    )

    result = validator.validate({"api_key_id": "PK-INVALID", "api_secret_key": "invalid-secret"})

    assert result.readiness == expected_readiness
    assert result.reason_code == expected_reason
    assert (
        result.source_contract_assessment.source_contract_reason_code
        if result.source_contract_assessment is not None
        else None
    ) == expected_source_contract_reason


def test_alpaca_live_contract_probes_pool_data_pagination_actions_and_calendar() -> None:
    regular_symbols = "AAPL,AMZN,BRK.B,GME,GOOG,GOOGL,META,NVDA,TSM"
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                json.dumps(
                    {
                        "bars": {
                            symbol: [
                                {
                                    "t": "2024-01-03T05:00:00Z",
                                    "o": 1,
                                    "h": 2,
                                    "l": 0.5,
                                    "c": 1.5,
                                    "v": 100,
                                }
                            ]
                            for symbol in regular_symbols.split(",")
                        },
                        "next_page_token": None,
                    }
                ).encode(),
            ),
            ProviderHttpResponse(
                200,
                b'{"bars":{"SIVB":[{"t":"2023-03-08T05:00:00Z","o":267.83,"h":276.42,"l":250.01,"c":267.83,"v":11230900}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.25,"v":58414460}]},"next_page_token":"live-page-2"}',
            ),
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-04T05:00:00Z","o":182.15,"h":183.09,"l":180.88,"c":181.91,"v":71983600}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'{"cash_dividends":[{"id":"live-ca-aapl","symbol":"AAPL","cusip":"037833100","rate":"0.24","special":false,"foreign":false,"process_date":"2024-02-08","ex_date":"2024-02-09"}],"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'{"name_changes":[{"id":"live-name-meta","old_symbol":"FB","new_symbol":"META","process_date":"2022-06-09"}],"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'[{"date":"2024-11-29","open":"09:30","close":"13:00"}]',
            ),
        ]
    )

    result = AlpacaLiveContractValidator(transport).validate(
        {
            "api_key_id": "PK-LIVE-CONTRACT",
            "api_secret_key": "live-contract-secret",
        }
    )

    assert result.readiness == "valid"
    assert result.reason_code == "source_credential_valid"
    assert result.evidence.as_payload() == {"authentication_status": "passed"}
    assert result.source_contract_assessment is not None
    assert result.source_contract_assessment.as_payload() == {
        "contract_id": "alpaca-ticket-07-live-v1",
        "live_validation": "passed",
        "ticker_count": 10,
        "pagination_pages": 2,
        "symbol_lifecycle_probe": "passed",
        "datasets": [
            "alpaca-us-stock-bars-v2",
            "alpaca-us-corporate-actions-v1",
            "alpaca-us-trading-calendar-v2",
        ],
        "source_contract_reason_code": None,
    }
    assert [request.url for request in transport.requests] == [
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v1/corporate-actions",
        "https://data.alpaca.markets/v1/corporate-actions",
        "https://paper-api.alpaca.markets/v2/calendar",
    ]
    assert transport.requests[0].query["symbols"] == regular_symbols
    assert transport.requests[1].query["symbols"] == "SIVB"
    assert transport.requests[2].query["limit"] == "1"
    assert transport.requests[2].query["symbols"] == "AAPL"
    assert transport.requests[3].query["page_token"] == "live-page-2"
    assert transport.requests[5].query == {
        "end": "2022-06-10",
        "start": "2022-06-08",
        "symbols": "FB,META",
        "types": "name_change",
    }
    assert all("PK-LIVE-CONTRACT" not in repr(request) for request in transport.requests)
    assert all("live-contract-secret" not in repr(request) for request in transport.requests)


def test_live_contract_keeps_an_authenticated_credential_valid_when_source_probe_fails() -> None:
    regular_symbols = "AAPL,AMZN,BRK.B,GME,GOOG,GOOGL,META,NVDA,TSM"
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                json.dumps(
                    {
                        "bars": {
                            symbol: [
                                {
                                    "t": "2024-01-03T05:00:00Z",
                                    "o": 1,
                                    "h": 2,
                                    "l": 0.5,
                                    "c": 1.5,
                                    "v": 100,
                                }
                            ]
                            for symbol in regular_symbols.split(",")
                        },
                        "next_page_token": None,
                    }
                ).encode(),
            ),
            ProviderHttpResponse(503, b'{"message":"provider unavailable"}'),
        ]
    )

    result = AlpacaLiveContractValidator(transport).validate(
        {"api_key_id": "PK-AUTHENTICATED", "api_secret_key": "authenticated-secret"}
    )

    assert result.readiness == "valid"
    assert result.reason_code == "source_credential_valid"
    assert result.evidence.authentication_status == "passed"
    assert result.source_contract_assessment is not None
    assert result.source_contract_assessment.live_validation == "failed"
    assert (
        result.source_contract_assessment.source_contract_reason_code
        == "source_contract_unavailable"
    )


def test_live_contract_keeps_prior_authentication_when_a_later_probe_is_forbidden() -> None:
    regular_symbols = "AAPL,AMZN,BRK.B,GME,GOOG,GOOGL,META,NVDA,TSM"
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                json.dumps(
                    {
                        "bars": {
                            symbol: [
                                {
                                    "t": "2024-01-03T05:00:00Z",
                                    "o": 1,
                                    "h": 2,
                                    "l": 0.5,
                                    "c": 1.5,
                                    "v": 100,
                                }
                            ]
                            for symbol in regular_symbols.split(",")
                        },
                        "next_page_token": None,
                    }
                ).encode(),
            ),
            ProviderHttpResponse(403, b'{"message":"subscription does not permit SIP"}'),
        ]
    )

    result = AlpacaLiveContractValidator(transport).validate(
        {"api_key_id": "PK-AUTHENTICATED", "api_secret_key": "authenticated-secret"}
    )

    assert result.readiness == "valid"
    assert result.reason_code == "source_credential_valid"
    assert result.evidence.authentication_status == "passed"
    assert result.source_contract_assessment is not None
    assert result.source_contract_assessment.source_contract_reason_code == (
        "source_contract_forbidden"
    )


def test_live_contract_returns_stable_invalid_readiness_for_missing_fields() -> None:
    transport = LiteralProviderTransport(ProviderHttpResponse(500, b"must not be used"))

    result = AlpacaLiveContractValidator(transport).validate({"api_key_id": "PK-ONLY"})

    assert result.readiness == "validation_failed"
    assert result.reason_code == "source_credential_fields_invalid"
    assert result.evidence.authentication_status == "not_run"
    assert result.source_contract_assessment is None
    assert transport.requests == []


def test_live_contract_treats_an_initial_forbidden_response_as_inconclusive() -> None:
    result = AlpacaLiveContractValidator(
        LiteralProviderTransport(
            ProviderHttpResponse(403, b'{"message":"subscription does not permit SIP"}')
        )
    ).validate({"api_key_id": "PK-AUTHENTICATED", "api_secret_key": "authenticated-secret"})

    assert result.readiness == "configured"
    assert result.reason_code == "source_credential_validation_inconclusive"
    assert result.evidence.authentication_status == "not_run"
    assert result.source_contract_assessment is not None
    assert result.source_contract_assessment.live_validation == "failed"
    assert (
        result.source_contract_assessment.source_contract_reason_code == "source_contract_forbidden"
    )


def test_alpaca_collector_surfaces_forbidden_data_as_unavailable_not_credential_required() -> None:
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph(
            listing_id="70000000-0000-4000-8000-000000000001"
        ),
        credential_resolver=LiteralCredentialResolver(),
        transport=LiteralProviderTransport(
            ProviderHttpResponse(403, b'{"message":"subscription does not permit SIP"}')
        ),
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )

    with pytest.raises(RuntimeError, match="source_provider_unavailable"):
        collector.collect(
            SourcePartitionRequest(
                request_id="request-ticket-07-provider-forbidden",
                trace_id="trace-ticket-07-provider-forbidden",
                source_id="alpaca-us-stock-bars",
                mode="historical",
                listing_ids=("70000000-0000-4000-8000-000000000001",),
                start_date=date(2024, 1, 3),
                end_date=date(2024, 1, 3),
                expected_checkpoint=None,
                distribution_id="alpaca-us-stock-bars-v2",
                distribution_url="https://data.alpaca.markets/v2/stocks/bars",
                bundle_members=_bundle_member_requests(),
            )
        )


def test_alpaca_collector_rejects_a_repeated_pagination_token() -> None:
    repeated_page = ProviderHttpResponse(
        200,
        b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.25,"v":58414460}]},"next_page_token":"repeated"}',
    )
    transport = SequenceProviderTransport([repeated_page, repeated_page])
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph(
            listing_id="70000000-0000-4000-8000-000000000001"
        ),
        credential_resolver=LiteralCredentialResolver(),
        transport=transport,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )

    with pytest.raises(ValueError, match="source_provider_pagination_invalid"):
        collector.collect(
            SourcePartitionRequest(
                request_id="request-ticket-07-repeated-token",
                trace_id="trace-ticket-07-repeated-token",
                source_id="alpaca-us-stock-bars",
                mode="historical",
                listing_ids=("70000000-0000-4000-8000-000000000001",),
                start_date=date(2024, 1, 3),
                end_date=date(2024, 1, 3),
                expected_checkpoint=None,
                distribution_id="alpaca-us-stock-bars-v2",
                distribution_url="https://data.alpaca.markets/v2/stocks/bars",
                bundle_members=_bundle_member_requests(),
            )
        )

    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    "reason_code",
    [
        "source_credential_missing",
        "source_credential_authentication_failed",
        "source_credential_revoked",
    ],
)
def test_alpaca_collector_never_contacts_provider_without_a_valid_credential(
    reason_code: str,
) -> None:
    transport = LiteralProviderTransport(
        ProviderHttpResponse(status_code=500, body=b"must not be used")
    )
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph(
            listing_id="70000000-0000-4000-8000-000000000001"
        ),
        credential_resolver=MissingCredentialResolver(reason_code),
        transport=transport,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-missing-credential",
        trace_id="trace-p2-trace-us-01-missing-credential",
        source_id="alpaca-us-stock-bars",
        mode="historical",
        listing_ids=("70000000-0000-4000-8000-000000000001",),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint=None,
        distribution_id="alpaca-us-stock-bars-v2",
        distribution_url="https://data.alpaca.markets/v2/stocks/bars",
        bundle_members=_bundle_member_requests(),
    )

    with pytest.raises(SourceCredentialRequired, match=reason_code):
        collector.collect(request)

    assert transport.requests == []


def test_alpaca_collector_builds_one_immutable_paginated_source_bundle() -> None:
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.25,"v":58414460}]},"next_page_token":"bars-page-2"}',
            ),
            ProviderHttpResponse(
                200,
                b'{"bars":{"META":[{"t":"2024-01-03T05:00:00Z","o":344.98,"h":347.95,"l":343.18,"c":344.47,"v":15451100}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'{"cash_dividends":[{"id":"ca-dividend-aapl","symbol":"AAPL","cusip":"037833100","rate":"0.24","special":false,"foreign":false,"process_date":"2024-01-02","ex_date":"2024-01-03"}],"name_changes":[{"id":"ca-name-meta","old_symbol":"FB","old_cusip":"30303M102","new_symbol":"META","new_cusip":"30303M102","process_date":"2022-06-09"}],"next_page_token":null}',
            ),
            ProviderHttpResponse(
                200,
                b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]',
            ),
        ]
    )
    resolver = LiteralCredentialResolver()
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph_for_listings(
            {
                "70000000-0000-4000-8000-000000000001": ("AAPL",),
                "70000000-0000-4000-8000-000000000002": ("FB", "META"),
            }
        ),
        market_calendar_evidence=_engineering_market_calendar_evidence(),
        credential_resolver=resolver,
        transport=transport,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-source-bundle",
        trace_id="trace-p2-trace-us-01-source-bundle",
        source_id="alpaca-us-stock-bars",
        mode="historical",
        listing_ids=(
            "70000000-0000-4000-8000-000000000001",
            "70000000-0000-4000-8000-000000000002",
        ),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint="sha256:prior-observation",
        distribution_id="alpaca-us-stock-bars-v2",
        distribution_url="https://data.alpaca.markets/v2/stocks/bars",
        bundle_members=_bundle_member_requests(),
    )

    collection = collector.collect(request)

    assert resolver.provider_ids == ["alpaca-market-data-basic"]
    assert collection.request_id == request.request_id
    assert collection.source_id == request.source_id
    assert collection.acquired_at == datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    assert collection.sanitized_source_uri == request.distribution_url
    assert collection.media_type == "application/json"
    assert collection.checkpoint_before == "sha256:prior-observation"
    assert collection.checkpoint_after is not None
    assert collection.checkpoint_after.startswith("sha256:")
    assert collection.source_revision == collection.checkpoint_after
    assert collection.coverage.requested_start == date(2024, 1, 3)
    assert collection.coverage.requested_end == date(2024, 1, 3)
    assert collection.coverage.observed_start == date(2024, 1, 3)
    assert collection.coverage.observed_end == date(2024, 1, 3)
    assert collection.coverage.complete is True
    assert collection.market_calendar_evidence_version_id == (
        "engineering-nyse-calendar-2023-12-29-through-2024-01-04-v1"
    )
    bundle = json.loads(collection.raw_payload)
    assert bundle["provider_id"] == "alpaca-market-data-basic"
    assert bundle["market_calendar_evidence_version_id"] == (
        collection.market_calendar_evidence_version_id
    )
    assert len(bundle["bars_pages"]) == 2
    assert len(bundle["corporate_action_pages"]) == 1
    assert bundle["calendar"] == [{"close": "16:00", "date": "2024-01-03", "open": "09:30"}]

    assert [request.url for request in transport.requests] == [
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v1/corporate-actions",
        "https://paper-api.alpaca.markets/v2/calendar",
    ]
    assert transport.requests[0].query == {
        "adjustment": "raw",
        "asof": "-",
        "end": "2024-01-03",
        "feed": "sip",
        "limit": "10000",
        "sort": "asc",
        "start": "2024-01-03",
        "symbols": "AAPL,META",
        "timeframe": "1Day",
    }
    assert transport.requests[1].query["page_token"] == "bars-page-2"
    assert transport.requests[2].query == {
        "end": "2024-01-03",
        "limit": "1000",
        "region": "us",
        "start": "2024-01-03",
        "symbols": "AAPL,FB,META",
    }
    assert transport.requests[3].query == {
        "date_type": "TRADING",
        "end": "2024-01-03",
        "start": "2024-01-03",
    }
    assert all(
        request.headers["APCA-API-KEY-ID"] == "PK-COLLECTOR-CONTRACT"
        and request.headers["APCA-API-SECRET-KEY"] == "collector-contract-secret"
        for request in transport.requests
    )


@pytest.mark.parametrize("echo_response", ["bars", "actions", "calendar"])
def test_alpaca_collector_rejects_provider_responses_that_echo_credentials(
    echo_response: str,
) -> None:
    secret_value = "collector-contract-secret"
    bars_payload: dict[str, object] = {
        "bars": {
            "AAPL": [
                {
                    "t": "2024-01-03T05:00:00Z",
                    "o": 184.22,
                    "h": 185.88,
                    "l": 183.43,
                    "c": 184.25,
                    "v": 58414460,
                }
            ]
        },
        "next_page_token": None,
    }
    actions_payload: dict[str, object] = {
        "cash_dividends": [],
        "next_page_token": None,
    }
    calendar_payload: list[dict[str, object]] = [
        {"date": "2024-01-03", "open": "09:30", "close": "16:00"}
    ]
    if echo_response == "bars":
        bars_payload["echo"] = secret_value
    elif echo_response == "actions":
        actions_payload["echo"] = secret_value
    else:
        calendar_payload[0]["echo"] = secret_value
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(200, json.dumps(bars_payload).encode()),
            ProviderHttpResponse(200, json.dumps(actions_payload).encode()),
            ProviderHttpResponse(200, json.dumps(calendar_payload).encode()),
        ]
    )
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph(
            listing_id="70000000-0000-4000-8000-000000000001"
        ),
        credential_resolver=LiteralCredentialResolver(),
        transport=transport,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )

    with pytest.raises(ValueError) as raised:
        collector.collect(
            SourcePartitionRequest(
                request_id=f"request-ticket-07-secret-echo-{echo_response}",
                trace_id=f"trace-ticket-07-secret-echo-{echo_response}",
                source_id="alpaca-us-stock-bars",
                mode="historical",
                listing_ids=("70000000-0000-4000-8000-000000000001",),
                start_date=date(2024, 1, 3),
                end_date=date(2024, 1, 3),
                expected_checkpoint=None,
                distribution_id="alpaca-us-stock-bars-v2",
                distribution_url="https://data.alpaca.markets/v2/stocks/bars",
                bundle_members=_bundle_member_requests(),
            )
        )

    assert str(raised.value) == "source_provider_credential_echo_detected"
    assert secret_value not in str(raised.value)


def test_alpaca_collector_derives_company_action_completeness_from_reference_graph() -> None:
    listing_id = "70000000-0000-4000-8000-000000000001"
    graph = _complete_reference_graph(
        listing_id=listing_id,
        expected_action_ids=("ca-reference-required",),
    )
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.25,"v":58414460}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(200, b'{"cash_dividends":[],"next_page_token":null}'),
            ProviderHttpResponse(
                200,
                b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]',
            ),
        ]
    )
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=graph,
        credential_resolver=LiteralCredentialResolver(),
        transport=transport,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )

    collection = collector.collect(
        SourcePartitionRequest(
            request_id="request-reference-derived-action",
            trace_id="trace-reference-derived-action",
            source_id="alpaca-us-stock-bars",
            mode="historical",
            listing_ids=(listing_id,),
            start_date=date(2024, 1, 3),
            end_date=date(2024, 1, 3),
            expected_checkpoint=None,
            distribution_id="alpaca-us-stock-bars-v2",
            distribution_url="https://data.alpaca.markets/v2/stocks/bars",
            bundle_members=_bundle_member_requests(),
        )
    )

    assert collection.expected_company_action_ids == frozenset({"ca-reference-required"})
    assert collection.company_action_completeness_verified is True
    assert collection.reference_graph_lifecycle_verified is True
    assert collection.reference_graph_version_id == graph.version_id


def test_alpaca_collector_requests_each_alias_overlapping_the_partition() -> None:
    listing_id = "70000000-0000-4000-8000-000000000012"
    reference_graph = AlpacaReferenceGraph(
        version_id="engineering-us-reference-graph-alias-v1",
        listings=(
            AlpacaReferenceListing(
                listing_id=listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="OLD",
                        security_name="Example Corp.",
                        valid_from=date(2010, 1, 4),
                        valid_to=date(2023, 12, 31),
                    ),
                    ExternalSecurityAlias(
                        security_code="NEW",
                        security_name="Example Corp.",
                        valid_from=date(2024, 1, 1),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2010, 1, 4),
                        status="active",
                        source_event_id="engineering-active-example",
                    ),
                ),
            ),
        ),
        company_action_expectations=(),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"bars":{"OLD":[{"t":"2023-12-29T05:00:00Z","o":10,"h":11,"l":9,"c":10.5,"v":100}],"NEW":[{"t":"2024-01-02T05:00:00Z","o":11,"h":12,"l":10,"c":11.5,"v":120}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(200, b'{"cash_dividends":[],"next_page_token":null}'),
            ProviderHttpResponse(
                200,
                b'[{"date":"2023-12-29","open":"09:30","close":"16:00"},{"date":"2024-01-02","open":"09:30","close":"16:00"}]',
            ),
        ]
    )
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=reference_graph,
        market_calendar_evidence=_engineering_market_calendar_evidence(),
        credential_resolver=LiteralCredentialResolver(),
        transport=transport,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )

    collection = collector.collect(
        SourcePartitionRequest(
            request_id="request-ticket-07-alias-window",
            trace_id="trace-ticket-07-alias-window",
            source_id="alpaca-us-stock-bars",
            mode="historical",
            listing_ids=(listing_id,),
            start_date=date(2023, 12, 29),
            end_date=date(2024, 1, 2),
            expected_checkpoint=None,
            distribution_id="alpaca-us-stock-bars-v2",
            distribution_url="https://data.alpaca.markets/v2/stocks/bars",
            bundle_members=_bundle_member_requests(),
        )
    )

    assert transport.requests[0].query["symbols"] == "OLD,NEW"
    assert collection.coverage.complete is True
    assert json.loads(collection.raw_payload)[
        "reference_graph"
    ] == reference_graph.partition_payload(
        listing_ids=(listing_id,),
        start_date=date(2023, 12, 29),
        end_date=date(2024, 1, 2),
    )

    pre_change_transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"bars":{"OLD":[{"t":"2023-12-29T05:00:00Z","o":10,"h":11,"l":9,"c":10.5,"v":100}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(200, b'{"cash_dividends":[],"next_page_token":null}'),
            ProviderHttpResponse(
                200,
                b'[{"date":"2023-12-29","open":"09:30","close":"16:00"}]',
            ),
        ]
    )
    pre_change_collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=reference_graph,
        market_calendar_evidence=_engineering_market_calendar_evidence(),
        credential_resolver=LiteralCredentialResolver(),
        transport=pre_change_transport,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )

    pre_change = pre_change_collector.collect(
        SourcePartitionRequest(
            request_id="request-ticket-07-pre-change-alias",
            trace_id="trace-ticket-07-pre-change-alias",
            source_id="alpaca-us-stock-bars",
            mode="historical",
            listing_ids=(listing_id,),
            start_date=date(2023, 12, 29),
            end_date=date(2023, 12, 29),
            expected_checkpoint=None,
            distribution_id="alpaca-us-stock-bars-v2",
            distribution_url="https://data.alpaca.markets/v2/stocks/bars",
            bundle_members=_bundle_member_requests(),
        )
    )

    assert pre_change_transport.requests[0].query["symbols"] == "OLD"
    assert pre_change.coverage.complete is True


def test_alpaca_collector_preserves_provider_retry_after_and_policy_id() -> None:
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph(
            listing_id="70000000-0000-4000-8000-000000000001"
        ),
        credential_resolver=LiteralCredentialResolver(),
        transport=LiteralProviderTransport(
            ProviderHttpResponse(
                status_code=429,
                body=b'{"message":"rate limit exceeded"}',
                headers={"Retry-After": "17"},
            )
        ),
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-rate-limit",
        trace_id="trace-p2-trace-us-01-rate-limit",
        source_id="alpaca-us-stock-bars",
        mode="current",
        listing_ids=("70000000-0000-4000-8000-000000000001",),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint="sha256:stable-before-rate-limit",
        distribution_id="alpaca-us-stock-bars-v2",
        distribution_url="https://data.alpaca.markets/v2/stocks/bars",
        bundle_members=_bundle_member_requests(),
    )

    with pytest.raises(SourceRateLimited) as raised:
        collector.collect(request)

    assert raised.value.retry_after_seconds == 17
    assert raised.value.rate_limit_policy_id == "alpaca-basic-200-requests-per-minute-v1"


def test_alpaca_collector_marks_a_missing_symbol_session_incomplete() -> None:
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph(
            listing_id="70000000-0000-4000-8000-000000000001"
        ),
        credential_resolver=LiteralCredentialResolver(),
        transport=SequenceProviderTransport(
            [
                ProviderHttpResponse(200, b'{"bars":{},"next_page_token":null}'),
                ProviderHttpResponse(
                    200,
                    b'{"next_page_token":null}',
                ),
                ProviderHttpResponse(
                    200,
                    b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]',
                ),
            ]
        ),
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )
    request = SourcePartitionRequest(
        request_id="request-ticket-07-incomplete",
        trace_id="trace-p2-trace-us-01-incomplete",
        source_id="alpaca-us-stock-bars",
        mode="historical",
        listing_ids=("70000000-0000-4000-8000-000000000001",),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        expected_checkpoint=None,
        distribution_id="alpaca-us-stock-bars-v2",
        distribution_url="https://data.alpaca.markets/v2/stocks/bars",
        bundle_members=_bundle_member_requests(),
    )

    collection = collector.collect(request)

    assert collection.coverage.complete is False
    assert collection.coverage.observed_start is None
    assert collection.coverage.observed_end is None


def test_alpaca_collector_rejects_an_interior_session_omitted_by_provider_calendar() -> None:
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        reference_graph=_complete_reference_graph(
            listing_id="70000000-0000-4000-8000-000000000001"
        ),
        market_calendar_evidence=_engineering_market_calendar_evidence(),
        credential_resolver=LiteralCredentialResolver(),
        transport=SequenceProviderTransport(
            [
                ProviderHttpResponse(
                    200,
                    b'{"bars":{"AAPL":[{"t":"2024-01-02T05:00:00Z","o":185,"h":186,"l":184,"c":185,"v":100},{"t":"2024-01-04T05:00:00Z","o":186,"h":187,"l":185,"c":186,"v":200}]},"next_page_token":null}',
                ),
                ProviderHttpResponse(200, b'{"next_page_token":null}'),
                ProviderHttpResponse(
                    200,
                    b'[{"date":"2024-01-02","open":"09:30","close":"16:00"},{"date":"2024-01-04","open":"09:30","close":"16:00"}]',
                ),
            ]
        ),
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
    )

    collection = collector.collect(
        SourcePartitionRequest(
            request_id="request-ticket-07-calendar-omission",
            trace_id="trace-ticket-07-calendar-omission",
            source_id="alpaca-us-stock-bars",
            mode="historical",
            listing_ids=("70000000-0000-4000-8000-000000000001",),
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            expected_checkpoint=None,
            distribution_id="alpaca-us-stock-bars-v2",
            distribution_url="https://data.alpaca.markets/v2/stocks/bars",
            bundle_members=_bundle_member_requests(),
        )
    )

    assert collection.coverage.complete is False
    calendar_member = next(
        member
        for member in collection.bundle_members
        if member.dataset_id == "alpaca-us-trading-calendar-v2"
    )
    assert calendar_member.coverage.complete is False


def test_alpaca_decoder_rejects_overlapping_cross_listing_symbol_reuse() -> None:
    with pytest.raises(ValueError, match="source_identity_mapping_ambiguous"):
        AlpacaSourceDecoder(
            source_id="alpaca-us-stock-bars",
            reference_graph=_complete_reference_graph_for_listings(
                {
                    "70000000-0000-4000-8000-000000000001": ("AAPL",),
                    "70000000-0000-4000-8000-000000000002": ("AAPL",),
                }
            ),
        )


def test_alpaca_decoder_resolves_non_overlapping_symbol_reuse_by_effective_date() -> None:
    first_listing_id = "70000000-0000-4000-8000-000000000021"
    second_listing_id = "70000000-0000-4000-8000-000000000022"
    reference_graph = AlpacaReferenceGraph(
        version_id="engineering-symbol-reuse-v1",
        listings=(
            AlpacaReferenceListing(
                listing_id=first_listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="SAME",
                        security_name="First Same",
                        valid_from=date(2010, 1, 1),
                        valid_to=date(2020, 12, 31),
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=first_listing_id,
                        effective_date=date(2010, 1, 1),
                        status="active",
                        source_event_id="first-same-active",
                    ),
                ),
            ),
            AlpacaReferenceListing(
                listing_id=second_listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="SAME",
                        security_name="Second Same",
                        valid_from=date(2021, 1, 1),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=second_listing_id,
                        effective_date=date(2021, 1, 1),
                        status="active",
                        source_event_id="second-same-active",
                    ),
                ),
            ),
        ),
        company_action_expectations=(),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    collection = _collected_partition_for_reference_graph(
        reference_graph,
        listing_ids=(first_listing_id, second_listing_id),
        start_date=date(2020, 12, 31),
        end_date=date(2021, 1, 4),
        bars={
            "SAME": [
                {"t": "2020-12-31T05:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
                {"t": "2021-01-04T05:00:00Z", "o": 20, "h": 21, "l": 19, "c": 20.5, "v": 200},
            ]
        },
        corporate_actions={
            "cash_dividends": [
                {"id": "first-dividend", "symbol": "SAME", "ex_date": "2020-12-31", "rate": "0.1"},
                {"id": "second-dividend", "symbol": "SAME", "ex_date": "2021-01-04", "rate": "0.2"},
            ]
        },
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    ).decode(collection)

    assert [(row.listing_id, row.session_date) for row in decoded.prices] == [
        (first_listing_id, date(2020, 12, 31)),
        (second_listing_id, date(2021, 1, 4)),
    ]
    assert [(action.listing_id, action.source_action_id) for action in decoded.company_actions] == [
        (first_listing_id, "first-dividend"),
        (second_listing_id, "second-dividend"),
    ]
    assert decoded.quality_issues == ()


def test_alpaca_decoder_quarantines_out_of_window_aliases_and_duplicate_prices() -> None:
    listing_id = "70000000-0000-4000-8000-000000000023"
    reference_graph = AlpacaReferenceGraph(
        version_id="engineering-provider-faithful-rename-v1",
        listings=(
            AlpacaReferenceListing(
                listing_id=listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="OLD",
                        security_name="Rename Example",
                        valid_from=date(2010, 1, 1),
                        valid_to=date(2022, 6, 8),
                    ),
                    ExternalSecurityAlias(
                        security_code="NEW",
                        security_name="Rename Example",
                        valid_from=date(2022, 6, 9),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2010, 1, 1),
                        status="active",
                        source_event_id="rename-example-active",
                    ),
                ),
            ),
        ),
        company_action_expectations=(),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    collection = _collected_partition_for_reference_graph(
        reference_graph,
        listing_ids=(listing_id,),
        start_date=date(2022, 6, 8),
        end_date=date(2022, 6, 9),
        bars={
            "OLD": [
                {"t": "2022-06-08T04:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
                {"t": "2022-06-08T04:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
            ],
            "NEW": [
                {"t": "2022-06-08T04:00:00Z", "o": 99, "h": 100, "l": 98, "c": 99.5, "v": 999},
                {"t": "2022-06-09T04:00:00Z", "o": 11, "h": 12, "l": 10, "c": 11.5, "v": 120},
            ],
        },
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    ).decode(collection)

    assert [(row.listing_id, row.session_date, row.close) for row in decoded.prices] == [
        (listing_id, date(2022, 6, 8), Decimal("10.5")),
        (listing_id, date(2022, 6, 9), Decimal("11.5")),
    ]
    assert decoded.quality_issues == ("identity_ambiguous",)


@pytest.mark.parametrize(
    ("old_symbol", "new_symbol"),
    [
        ("OLD", "UNKNOWN"),
        ("UNKNOWN", "NEW"),
        ("OLD", "OTHERNEW"),
    ],
)
def test_alpaca_decoder_quarantines_partially_resolved_or_cross_listing_name_changes(
    old_symbol: str,
    new_symbol: str,
) -> None:
    listing_id = "70000000-0000-4000-8000-000000000024"
    other_listing_id = "70000000-0000-4000-8000-000000000025"
    reference_graph = AlpacaReferenceGraph(
        version_id="engineering-name-change-resolution-v1",
        listings=(
            AlpacaReferenceListing(
                listing_id=listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="OLD",
                        security_name="Rename Example",
                        valid_from=date(2010, 1, 1),
                        valid_to=date(2022, 6, 8),
                    ),
                    ExternalSecurityAlias(
                        security_code="NEW",
                        security_name="Rename Example",
                        valid_from=date(2022, 6, 9),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2010, 1, 1),
                        status="active",
                        source_event_id="rename-example-active",
                    ),
                ),
            ),
            AlpacaReferenceListing(
                listing_id=other_listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="OTHERNEW",
                        security_name="Other Rename Example",
                        valid_from=date(2022, 6, 9),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=other_listing_id,
                        effective_date=date(2022, 6, 9),
                        status="active",
                        source_event_id="other-rename-example-active",
                    ),
                ),
            ),
        ),
        company_action_expectations=(),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    collection = replace(
        _collected_partition_for_reference_graph(
            reference_graph,
            listing_ids=(listing_id, other_listing_id),
            start_date=date(2022, 6, 9),
            end_date=date(2022, 6, 9),
            bars={},
            corporate_actions={
                "name_changes": [
                    {
                        "id": "unresolved-name-change",
                        "old_symbol": old_symbol,
                        "new_symbol": new_symbol,
                        "process_date": "2022-06-09",
                    }
                ]
            },
        ),
        expected_company_action_ids=frozenset({"unresolved-name-change"}),
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    ).decode(collection)

    assert "unresolved-name-change" not in decoded.identity_assertion_ids
    assert decoded.quality_issues == ("identity_ambiguous", "missing_company_action")


def test_alpaca_decoder_quarantines_duplicate_company_action_ids_across_pages() -> None:
    listing_id = "70000000-0000-4000-8000-000000000026"
    reference_graph = _complete_reference_graph(listing_id=listing_id)
    collection = _collected_partition_for_reference_graph(
        reference_graph,
        listing_ids=(listing_id,),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        bars={
            "AAPL": [
                {
                    "t": "2024-01-03T05:00:00Z",
                    "o": 184.22,
                    "h": 185.88,
                    "l": 183.43,
                    "c": 184.25,
                    "v": 58414460,
                }
            ]
        },
    )
    payload = json.loads(collection.raw_payload)
    action = {
        "id": "duplicate-dividend",
        "symbol": "AAPL",
        "ex_date": "2024-01-03",
        "rate": "0.24",
    }
    payload["corporate_action_pages"] = [
        {"cash_dividends": [action], "next_page_token": "actions-page-2"},
        {"cash_dividends": [action], "next_page_token": None},
    ]

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    ).decode(
        replace(
            collection,
            raw_payload=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        )
    )

    assert [action.source_action_id for action in decoded.company_actions] == ["duplicate-dividend"]
    assert decoded.quality_issues == ("duplicate_company_action",)


def test_alpaca_decoder_uses_permanent_listing_ids_and_internal_action_semantics() -> None:
    aapl_listing_id = "70000000-0000-4000-8000-000000000001"
    meta_listing_id = "70000000-0000-4000-8000-000000000002"
    reference_graph = _complete_reference_graph_for_listings(
        {
            aapl_listing_id: ("AAPL",),
            meta_listing_id: ("FB", "META"),
        }
    )
    reference_graph = replace(
        reference_graph,
        listings=(
            reference_graph.listing(aapl_listing_id),
            replace(
                reference_graph.listing(meta_listing_id),
                aliases=(
                    ExternalSecurityAlias(
                        security_code="FB",
                        security_name="FB engineering reference",
                        valid_from=date(2012, 5, 18),
                        valid_to=date(2022, 6, 8),
                    ),
                    ExternalSecurityAlias(
                        security_code="META",
                        security_name="META engineering reference",
                        valid_from=date(2022, 6, 9),
                        valid_to=None,
                    ),
                ),
            ),
        ),
    )
    requested_listing_ids = (aapl_listing_id, meta_listing_id)
    raw_payload = json.dumps(
        {
            "provider_id": "alpaca-market-data-basic",
            "schema_version": "alpaca-source-bundle-v1",
            "bars_pages": [
                {
                    "bars": {
                        "AAPL": [
                            {
                                "t": "2024-01-03T05:00:00Z",
                                "o": 184.22,
                                "h": 185.88,
                                "l": 183.43,
                                "c": 184.25,
                                "v": 58414460,
                            }
                        ],
                        "META": [
                            {
                                "t": "2024-01-03T05:00:00Z",
                                "o": 344.98,
                                "h": 347.95,
                                "l": 343.18,
                                "c": 344.47,
                                "v": 15451100,
                            }
                        ],
                    },
                    "next_page_token": None,
                }
            ],
            "corporate_action_pages": [
                {
                    "cash_dividends": [
                        {
                            "id": "ca-dividend-aapl",
                            "symbol": "AAPL",
                            "cusip": "037833100",
                            "ex_date": "2024-02-09",
                            "process_date": "2024-02-08",
                            "rate": "0.24",
                            "special": False,
                            "foreign": False,
                        }
                    ],
                    "forward_splits": [
                        {
                            "id": "ca-split-aapl",
                            "symbol": "AAPL",
                            "cusip": "037833100",
                            "process_date": "2020-08-31",
                            "ex_date": "2020-08-31",
                            "old_rate": "1",
                            "new_rate": "4",
                        }
                    ],
                    "name_changes": [
                        {
                            "id": "ca-name-meta",
                            "old_symbol": "FB",
                            "old_cusip": "30303M102",
                            "new_symbol": "META",
                            "new_cusip": "30303M102",
                            "process_date": "2022-06-09",
                        }
                    ],
                    "next_page_token": None,
                }
            ],
            "calendar": [{"date": "2024-01-03", "open": "09:30", "close": "13:00"}],
            "reference_graph": reference_graph.partition_payload(
                listing_ids=requested_listing_ids,
                start_date=date(2024, 1, 3),
                end_date=date(2024, 1, 3),
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    collection = CollectedSourcePartition(
        request_id="request-ticket-07-decode",
        source_id="alpaca-us-stock-bars",
        acquired_at=datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        sanitized_source_uri="https://data.alpaca.markets/v2/stocks/bars",
        media_type="application/json",
        raw_payload=raw_payload,
        checkpoint_before=None,
        checkpoint_after="sha256:source-bundle",
        coverage=SourceCollectionCoverage(
            requested_start=date(2024, 1, 3),
            requested_end=date(2024, 1, 3),
            observed_start=date(2024, 1, 3),
            observed_end=date(2024, 1, 3),
            complete=True,
        ),
        source_revision="sha256:source-bundle",
        requested_listing_ids=requested_listing_ids,
        reference_graph_version_id=reference_graph.version_id,
        reference_graph_lifecycle_verified=True,
        company_action_completeness_verified=True,
    )
    decoder = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    )

    decoded = decoder.decode(collection)

    assert decoded.schema_version == "us-unadjusted-eod-v1"
    assert decoded.source_revision == collection.source_revision
    decoded_prices = [
        (row.listing_id, row.session_date, row.close, row.volume) for row in decoded.prices
    ]
    assert decoded_prices == [
        (aapl_listing_id, date(2024, 1, 3), Decimal("184.25"), 58414460),
        (meta_listing_id, date(2024, 1, 3), Decimal("344.47"), 15451100),
    ]
    assert decoded.company_actions == (
        CompanyActionRecord(
            listing_id=aapl_listing_id,
            effective_date=date(2024, 2, 9),
            kind="cash_dividend",
            value=Decimal("0.24"),
            currency="USD",
            source_action_id="ca-dividend-aapl",
        ),
        CompanyActionRecord(
            listing_id=aapl_listing_id,
            effective_date=date(2020, 8, 31),
            kind="split",
            value=Decimal("4"),
            currency=None,
            source_action_id="ca-split-aapl",
        ),
    )
    assert decoded.symbol_identities == (
        SymbolIdentityRecord(
            listing_id=aapl_listing_id,
            symbol="AAPL",
            valid_from=date(2000, 1, 1),
            valid_to=None,
            source_event_id=(f"engineering-reference-multi-listing-v1:{aapl_listing_id}:AAPL"),
        ),
        SymbolIdentityRecord(
            listing_id=meta_listing_id,
            symbol="FB",
            valid_from=date(2012, 5, 18),
            valid_to=date(2022, 6, 8),
            source_event_id=(f"engineering-reference-multi-listing-v1:{meta_listing_id}:FB"),
        ),
        SymbolIdentityRecord(
            listing_id=meta_listing_id,
            symbol="META",
            valid_from=date(2022, 6, 9),
            valid_to=None,
            source_event_id=(f"engineering-reference-multi-listing-v1:{meta_listing_id}:META"),
        ),
    )
    assert decoded.market_sessions == (
        MarketSessionRecord(
            session_date=date(2024, 1, 3),
            open_time="09:30",
            close_time="13:00",
            session_kind="early_close",
        ),
    )
    assert set(decoded.identity_assertion_ids) == {
        f"engineering-reference-multi-listing-v1:{aapl_listing_id}:AAPL",
        f"engineering-reference-multi-listing-v1:{meta_listing_id}:FB",
        f"engineering-reference-multi-listing-v1:{meta_listing_id}:META",
        "ca-dividend-aapl",
        "ca-name-meta",
        "ca-split-aapl",
    }
    assert decoded.quality_issues == ()

    with pytest.raises(ValueError, match="source_reference_graph_lineage_mismatch"):
        AlpacaSourceDecoder(
            source_id="alpaca-us-stock-bars",
            reference_graph=reference_graph,
        ).decode(replace(collection, company_action_completeness_verified=False))


def test_alpaca_decoder_marks_changed_checkpoint_as_a_correction() -> None:
    listing_id = "70000000-0000-4000-8000-000000000001"
    reference_graph = _complete_reference_graph(listing_id=listing_id)
    collection = CollectedSourcePartition(
        request_id="request-ticket-07-correction",
        source_id="alpaca-us-stock-bars",
        acquired_at=datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        sanitized_source_uri="https://data.alpaca.markets/v2/stocks/bars",
        media_type="application/json",
        raw_payload=json.dumps(
            {
                "provider_id": "alpaca-market-data-basic",
                "schema_version": "alpaca-source-bundle-v1",
                "bars_pages": [{"bars": {}, "next_page_token": None}],
                "corporate_action_pages": [{"next_page_token": None}],
                "calendar": [],
                "reference_graph": reference_graph.partition_payload(
                    listing_ids=(listing_id,),
                    start_date=date(2024, 1, 3),
                    end_date=date(2024, 1, 3),
                ),
            }
        ).encode(),
        checkpoint_before="sha256:prior-version",
        checkpoint_after="sha256:corrected-version",
        coverage=SourceCollectionCoverage(
            requested_start=date(2024, 1, 3),
            requested_end=date(2024, 1, 3),
            observed_start=None,
            observed_end=None,
            complete=False,
        ),
        source_revision="sha256:corrected-version",
        requested_listing_ids=(listing_id,),
        reference_graph_version_id=reference_graph.version_id,
        reference_graph_lifecycle_verified=True,
        company_action_completeness_verified=True,
        revision_kind="correction",
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    ).decode(collection)

    assert decoded.revision_kind == "correction"
    assert decoded.quality_issues == ("correction_requires_review",)


def test_alpaca_decoder_does_not_treat_a_new_partition_checkpoint_as_a_correction() -> None:
    listing_id = "70000000-0000-4000-8000-000000000001"
    reference_graph = _complete_reference_graph(listing_id=listing_id)
    collection = CollectedSourcePartition(
        request_id="request-ticket-07-next-partition",
        source_id="alpaca-us-stock-bars",
        acquired_at=datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        sanitized_source_uri="https://data.alpaca.markets/v2/stocks/bars",
        media_type="application/json",
        raw_payload=json.dumps(
            {
                "provider_id": "alpaca-market-data-basic",
                "schema_version": "alpaca-source-bundle-v1",
                "bars_pages": [{"bars": {}, "next_page_token": None}],
                "corporate_action_pages": [{"next_page_token": None}],
                "calendar": [],
                "reference_graph": reference_graph.partition_payload(
                    listing_ids=(listing_id,),
                    start_date=date(2024, 1, 4),
                    end_date=date(2024, 1, 4),
                ),
            }
        ).encode(),
        checkpoint_before="sha256:prior-partition",
        checkpoint_after="sha256:next-partition",
        coverage=SourceCollectionCoverage(
            requested_start=date(2024, 1, 4),
            requested_end=date(2024, 1, 4),
            observed_start=None,
            observed_end=None,
            complete=False,
        ),
        source_revision="sha256:next-partition",
        requested_listing_ids=(listing_id,),
        reference_graph_version_id=reference_graph.version_id,
        reference_graph_lifecycle_verified=True,
        company_action_completeness_verified=True,
        revision_kind="original",
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    ).decode(collection)

    assert decoded.revision_kind == "original"
    assert "correction_requires_review" not in decoded.quality_issues


def test_alpaca_decoder_flags_a_reference_graph_company_action_that_is_missing() -> None:
    listing_id = "70000000-0000-4000-8000-000000000001"
    reference_graph = _complete_reference_graph(
        listing_id=listing_id,
        expected_action_ids=("ca-required-aapl",),
    )
    collection = CollectedSourcePartition(
        request_id="request-ticket-07-missing-action",
        source_id="alpaca-us-stock-bars",
        acquired_at=datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        sanitized_source_uri="https://data.alpaca.markets/v2/stocks/bars",
        media_type="application/json",
        raw_payload=json.dumps(
            {
                "provider_id": "alpaca-market-data-basic",
                "schema_version": "alpaca-source-bundle-v1",
                "bars_pages": [{"bars": {}, "next_page_token": None}],
                "corporate_action_pages": [{"next_page_token": None}],
                "calendar": [],
                "reference_graph": reference_graph.partition_payload(
                    listing_ids=(listing_id,),
                    start_date=date(2024, 1, 3),
                    end_date=date(2024, 1, 3),
                ),
            }
        ).encode(),
        checkpoint_before=None,
        checkpoint_after="sha256:without-required-action",
        coverage=SourceCollectionCoverage(
            requested_start=date(2024, 1, 3),
            requested_end=date(2024, 1, 3),
            observed_start=None,
            observed_end=None,
            complete=False,
        ),
        source_revision="sha256:without-required-action",
        requested_listing_ids=(listing_id,),
        reference_graph_version_id=reference_graph.version_id,
        reference_graph_lifecycle_verified=True,
        company_action_completeness_verified=True,
        expected_company_action_ids=frozenset({"ca-required-aapl"}),
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=reference_graph,
    ).decode(collection)

    assert decoded.quality_issues == ("missing_company_action",)


def test_alpaca_decoder_uses_a_versioned_reference_graph_instead_of_bar_dates() -> None:
    from stock_forecasting.alpaca_market_data import (
        AlpacaCompanyActionExpectation,
        AlpacaReferenceGraph,
        AlpacaReferenceListing,
    )

    listing_id = "70000000-0000-4000-8000-000000000001"
    graph = AlpacaReferenceGraph(
        version_id="engineering-us-reference-graph-v1",
        listings=(
            AlpacaReferenceListing(
                listing_id=listing_id,
                aliases=(
                    ExternalSecurityAlias(
                        security_code="OLD",
                        security_name="Example Corp.",
                        valid_from=date(2010, 1, 4),
                        valid_to=date(2023, 12, 31),
                    ),
                    ExternalSecurityAlias(
                        security_code="NEW",
                        security_name="Example Corp.",
                        valid_from=date(2024, 1, 1),
                        valid_to=None,
                    ),
                ),
                lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2010, 1, 4),
                        status="active",
                        source_event_id="reference-active-example",
                    ),
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2021, 1, 28),
                        status="suspended",
                        source_event_id="reference-suspension-example",
                    ),
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2021, 1, 29),
                        status="active",
                        source_event_id="reference-resumption-example",
                    ),
                ),
            ),
        ),
        company_action_expectations=(
            AlpacaCompanyActionExpectation(
                action_id="ca-required-example",
                listing_id=listing_id,
                effective_date=date(2024, 1, 3),
            ),
        ),
        lifecycle_complete=True,
        company_actions_complete=True,
    )
    reference_payload = graph.partition_payload(
        listing_ids=(listing_id,),
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
    )
    collection = CollectedSourcePartition(
        request_id="request-versioned-reference-graph",
        source_id="alpaca-us-stock-bars",
        acquired_at=datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        sanitized_source_uri="https://data.alpaca.markets/v2/stocks/bars",
        media_type="application/json",
        raw_payload=json.dumps(
            {
                "provider_id": "alpaca-market-data-basic",
                "schema_version": "alpaca-source-bundle-v1",
                "bars_pages": [
                    {
                        "bars": {
                            "NEW": [
                                {
                                    "t": "2024-01-03T05:00:00Z",
                                    "o": 10,
                                    "h": 11,
                                    "l": 9,
                                    "c": 10.5,
                                    "v": 100,
                                }
                            ]
                        },
                        "next_page_token": None,
                    }
                ],
                "corporate_action_pages": [
                    {
                        "cash_dividends": [
                            {
                                "id": "ca-required-example",
                                "symbol": "NEW",
                                "ex_date": "2024-01-03",
                                "rate": "0.10",
                            }
                        ],
                        "next_page_token": None,
                    }
                ],
                "calendar": [{"date": "2024-01-03", "open": "09:30", "close": "16:00"}],
                "reference_graph": reference_payload,
            },
            sort_keys=True,
        ).encode(),
        checkpoint_before=None,
        checkpoint_after="sha256:versioned-reference-graph",
        coverage=SourceCollectionCoverage(
            requested_start=date(2024, 1, 3),
            requested_end=date(2024, 1, 3),
            observed_start=date(2024, 1, 3),
            observed_end=date(2024, 1, 3),
            complete=True,
        ),
        source_revision="sha256:versioned-reference-graph",
        requested_listing_ids=(listing_id,),
        reference_graph_version_id=graph.version_id,
        expected_company_action_ids=frozenset({"ca-required-example"}),
        reference_graph_lifecycle_verified=True,
        company_action_completeness_verified=True,
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        reference_graph=graph,
    ).decode(collection)

    assert decoded.listing_lifecycle == graph.listings[0].lifecycle
    assert decoded.symbol_identities == (
        SymbolIdentityRecord(
            listing_id=listing_id,
            symbol="OLD",
            valid_from=date(2010, 1, 4),
            valid_to=date(2023, 12, 31),
            source_event_id=f"engineering-us-reference-graph-v1:{listing_id}:OLD",
        ),
        SymbolIdentityRecord(
            listing_id=listing_id,
            symbol="NEW",
            valid_from=date(2024, 1, 1),
            valid_to=None,
            source_event_id=f"engineering-us-reference-graph-v1:{listing_id}:NEW",
        ),
    )
    assert decoded.quality_issues == ()
