from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from email.message import Message
from urllib.request import Request

import pytest

from stock_forecasting.alpaca_market_data import (
    AlpacaCredentialValidator,
    AlpacaLiveContractValidator,
    AlpacaSourceCollector,
    AlpacaSourceDecoder,
    ProviderHttpRequest,
    ProviderHttpResponse,
    UrllibProviderHttpTransport,
)
from stock_forecasting.data_supply import (
    CollectedSourcePartition,
    CompanyActionRecord,
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
        ),
        SourceBundleMemberRequest(
            dataset_id="alpaca-us-trading-calendar-v2",
            distribution_id="alpaca-us-trading-calendar-v2",
            distribution_url="https://paper-api.alpaca.markets/v2/calendar",
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

    def resolve_valid(self, provider_id: str) -> dict[str, str]:
        raise CredentialNotReady(self.reason_code)


class LiteralCredentialResolver:
    def __init__(self) -> None:
        self.provider_ids: list[str] = []

    def resolve_valid(self, provider_id: str) -> dict[str, str]:
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

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> LiteralUrlResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


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


@pytest.mark.parametrize(
    ("status_code", "body", "expected_reason"),
    [
        (401, b'{"message":"unauthorized"}', "source_credential_authentication_failed"),
        (403, b'{"message":"forbidden"}', "source_credential_authentication_failed"),
        (429, b'{"message":"rate limit exceeded"}', "source_credential_validation_rate_limited"),
        (503, b'{"message":"unavailable"}', "source_credential_provider_unavailable"),
        (200, b"not-json", "source_credential_provider_schema_invalid"),
        (200, b'{"bars":{}}', "source_credential_provider_schema_invalid"),
    ],
)
def test_alpaca_credential_validator_preserves_actionable_failure_reasons(
    status_code: int,
    body: bytes,
    expected_reason: str,
) -> None:
    validator = AlpacaCredentialValidator(
        LiteralProviderTransport(ProviderHttpResponse(status_code=status_code, body=body))
    )

    result = validator.validate({"api_key_id": "PK-INVALID", "api_secret_key": "invalid-secret"})

    assert result.readiness == "validation_failed"
    assert result.reason_code == expected_reason


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
    assert result.evidence == {
        "contract_id": "alpaca-ticket-07-live-v1",
        "live_validation": "passed",
        "ticker_count": 10,
        "pagination_pages": 2,
        "datasets": [
            "alpaca-us-stock-bars-v2",
            "alpaca-us-corporate-actions-v1",
            "alpaca-us-trading-calendar-v2",
        ],
    }
    assert [request.url for request in transport.requests] == [
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v2/stocks/AAPL/bars",
        "https://data.alpaca.markets/v2/stocks/AAPL/bars",
        "https://data.alpaca.markets/v1/corporate-actions",
        "https://paper-api.alpaca.markets/v2/calendar",
    ]
    assert transport.requests[0].query["symbols"] == regular_symbols
    assert transport.requests[1].query["symbols"] == "SIVB"
    assert transport.requests[2].query["limit"] == "1"
    assert transport.requests[3].query["page_token"] == "live-page-2"
    assert all("PK-LIVE-CONTRACT" not in repr(request) for request in transport.requests)
    assert all("live-contract-secret" not in repr(request) for request in transport.requests)


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
        listing_symbols={
            "70000000-0000-4000-8000-000000000001": ("AAPL",),
        },
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
        listing_symbols={
            "70000000-0000-4000-8000-000000000001": ("AAPL",),
            "70000000-0000-4000-8000-000000000002": ("FB", "META"),
        },
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
    bundle = json.loads(collection.raw_payload)
    assert bundle["provider_id"] == "alpaca-market-data-basic"
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
        "asof": "2024-01-03",
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


def test_alpaca_collector_preserves_provider_retry_after_and_policy_id() -> None:
    collector = AlpacaSourceCollector(
        source_id="alpaca-us-stock-bars",
        provider_id="alpaca-market-data-basic",
        listing_symbols={
            "70000000-0000-4000-8000-000000000001": ("AAPL",),
        },
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
        listing_symbols={
            "70000000-0000-4000-8000-000000000001": ("AAPL",),
        },
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


def test_alpaca_decoder_rejects_cross_listing_symbol_reuse() -> None:
    with pytest.raises(ValueError, match="source_identity_mapping_ambiguous"):
        AlpacaSourceDecoder(
            source_id="alpaca-us-stock-bars",
            listing_symbols={
                "70000000-0000-4000-8000-000000000001": ("AAPL",),
                "70000000-0000-4000-8000-000000000002": ("AAPL",),
            },
        )


def test_alpaca_decoder_uses_permanent_listing_ids_and_internal_action_semantics() -> None:
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
    )
    aapl_listing_id = "70000000-0000-4000-8000-000000000001"
    meta_listing_id = "70000000-0000-4000-8000-000000000002"
    decoder = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        listing_symbols={
            aapl_listing_id: ("AAPL",),
            meta_listing_id: ("FB", "META"),
        },
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
            listing_id=meta_listing_id,
            symbol="FB",
            valid_from=None,
            valid_to=date(2022, 6, 8),
            source_event_id="ca-name-meta",
        ),
        SymbolIdentityRecord(
            listing_id=meta_listing_id,
            symbol="META",
            valid_from=date(2022, 6, 9),
            valid_to=None,
            source_event_id="ca-name-meta",
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
        f"alpaca-symbol:{aapl_listing_id}:AAPL",
        f"alpaca-symbol:{meta_listing_id}:META",
        "ca-dividend-aapl",
        "ca-name-meta",
        "ca-split-aapl",
    }
    assert decoded.quality_issues == ()


def test_alpaca_decoder_marks_changed_checkpoint_as_a_correction() -> None:
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
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        listing_symbols={"70000000-0000-4000-8000-000000000001": ("AAPL",)},
    ).decode(collection)

    assert decoded.revision_kind == "correction"


def test_alpaca_decoder_flags_a_reference_graph_company_action_that_is_missing() -> None:
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
    )

    decoded = AlpacaSourceDecoder(
        source_id="alpaca-us-stock-bars",
        listing_symbols={"70000000-0000-4000-8000-000000000001": ("AAPL",)},
        expected_company_action_ids=frozenset({"ca-required-aapl"}),
    ).decode(collection)

    assert decoded.quality_issues == ("missing_company_action",)
