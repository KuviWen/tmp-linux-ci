from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.message import Message
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from stock_forecasting.data_supply import (
    CanonicalPriceRow,
    CollectedSourceBundleMember,
    CollectedSourcePartition,
    CollectorDecoderPriceSourceAdapter,
    CompanyActionRecord,
    DecodedSourcePartition,
    ListingLifecycleRecord,
    MarketSessionRecord,
    SourceCollectionCoverage,
    SourceCredentialRequired,
    SourcePartitionRequest,
    SourceQualityIssue,
    SourceRateLimited,
    SymbolIdentityRecord,
)
from stock_forecasting.source_credentials import (
    CredentialNotReady,
    CredentialValidationResult,
    SourceCredentialResolver,
)


@dataclass(frozen=True)
class ProviderHttpRequest:
    method: str
    url: str
    query: Mapping[str, str]
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True)
class ProviderHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class ProviderHttpTransport(Protocol):
    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse: ...


class _UrlResponse(Protocol):
    status: int
    headers: Message

    def read(self) -> bytes: ...

    def __enter__(self) -> _UrlResponse: ...

    def __exit__(self, *args: object) -> None: ...


class _UrlOpener(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> _UrlResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def _open_without_redirects(request: Request, *, timeout: float) -> _UrlResponse:
    return cast(_UrlResponse, _NO_REDIRECT_OPENER.open(request, timeout=timeout))


class UrllibProviderHttpTransport:
    _ALLOWED_HOSTS = frozenset({"data.alpaca.markets", "paper-api.alpaca.markets"})

    def __init__(
        self,
        *,
        opener: _UrlOpener | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("source_provider_timeout_invalid")
        self._opener = opener or _open_without_redirects
        self._timeout_seconds = timeout_seconds

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        parsed = urlsplit(request.url)
        if (
            request.method != "GET"
            or parsed.scheme != "https"
            or parsed.hostname not in self._ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise ValueError("source_provider_url_forbidden")
        query = urlencode(sorted(request.query.items()))
        url = f"{request.url}?{query}" if query else request.url
        urllib_request = Request(
            url,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener(urllib_request, timeout=self._timeout_seconds) as response:
                return ProviderHttpResponse(
                    status_code=response.status,
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as error:
            return ProviderHttpResponse(
                status_code=error.code,
                body=error.read(),
                headers=dict(error.headers.items()) if error.headers is not None else {},
            )
        except URLError:
            return ProviderHttpResponse(
                status_code=503,
                body=b'{"message":"provider transport unavailable"}',
            )


class AlpacaCredentialValidator:
    _VALIDATION_URL = "https://data.alpaca.markets/v2/stocks/AAPL/bars"
    _VALIDATION_QUERY = {
        "adjustment": "raw",
        "end": "2024-01-04T00:00:00Z",
        "feed": "sip",
        "limit": "1",
        "start": "2024-01-03T00:00:00Z",
        "timeframe": "1Day",
    }

    def __init__(self, transport: ProviderHttpTransport) -> None:
        self._transport = transport

    def validate(
        self,
        credential_fields: Mapping[str, str],
    ) -> CredentialValidationResult:
        try:
            api_key_id = credential_fields["api_key_id"]
            api_secret_key = credential_fields["api_secret_key"]
        except KeyError:
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_fields_invalid",
            )
        response = self._transport.send(
            ProviderHttpRequest(
                method="GET",
                url=self._VALIDATION_URL,
                query=self._VALIDATION_QUERY,
                headers={
                    "APCA-API-KEY-ID": api_key_id,
                    "APCA-API-SECRET-KEY": api_secret_key,
                    "Accept": "application/json",
                },
            )
        )
        if response.status_code in {401, 403}:
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_authentication_failed",
            )
        if response.status_code == 429:
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_validation_rate_limited",
            )
        if response.status_code != 200:
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_provider_unavailable",
            )
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_provider_schema_invalid",
            )
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
        )


class AlpacaLiveContractValidator:
    """Opt-in provider contract probe; evidence never contains credential material."""

    _REGULAR_SYMBOLS = ("AAPL", "AMZN", "BRK.B", "GME", "GOOG", "GOOGL", "META", "NVDA", "TSM")
    _BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
    _SINGLE_BARS_URL = "https://data.alpaca.markets/v2/stocks/AAPL/bars"
    _ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"
    _CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"

    def __init__(self, transport: ProviderHttpTransport) -> None:
        self._transport = transport

    def validate(
        self,
        credential_fields: Mapping[str, str],
    ) -> CredentialValidationResult:
        try:
            headers = {
                "APCA-API-KEY-ID": credential_fields["api_key_id"],
                "APCA-API-SECRET-KEY": credential_fields["api_secret_key"],
                "Accept": "application/json",
            }
        except KeyError:
            return self._failure("source_credential_fields_invalid")

        regular = self._request_json(
            self._BARS_URL,
            {
                "adjustment": "raw",
                "end": "2024-01-04T00:00:00Z",
                "feed": "sip",
                "start": "2024-01-03T00:00:00Z",
                "symbols": ",".join(self._REGULAR_SYMBOLS),
                "timeframe": "1Day",
            },
            headers,
        )
        if isinstance(regular, CredentialValidationResult):
            return regular
        if not self._valid_multi_symbol_bars(regular, set(self._REGULAR_SYMBOLS)):
            return self._failure("source_credential_provider_schema_invalid")

        sivb = self._request_json(
            self._BARS_URL,
            {
                "adjustment": "raw",
                "end": "2023-03-09T00:00:00Z",
                "feed": "sip",
                "start": "2023-03-08T00:00:00Z",
                "symbols": "SIVB",
                "timeframe": "1Day",
            },
            headers,
        )
        if isinstance(sivb, CredentialValidationResult):
            return sivb
        if not self._valid_multi_symbol_bars(sivb, {"SIVB"}):
            return self._failure("source_credential_provider_schema_invalid")

        pagination_pages = 0
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            query = {
                "adjustment": "raw",
                "end": "2024-01-05T23:59:59Z",
                "feed": "sip",
                "limit": "1",
                "start": "2024-01-03T00:00:00Z",
                "timeframe": "1Day",
            }
            if page_token is not None:
                query["page_token"] = page_token
            page = self._request_json(self._SINGLE_BARS_URL, query, headers)
            if isinstance(page, CredentialValidationResult):
                return page
            if not self._valid_single_symbol_page(page):
                return self._failure("source_credential_provider_schema_invalid")
            pagination_pages += 1
            next_token = cast(dict[str, object], page).get("next_page_token")
            if next_token is None:
                break
            if (
                not isinstance(next_token, str)
                or not next_token
                or next_token in seen_tokens
                or pagination_pages >= 20
            ):
                return self._failure("source_credential_provider_schema_invalid")
            seen_tokens.add(next_token)
            page_token = next_token
        if pagination_pages < 2:
            return self._failure("source_credential_provider_schema_invalid")

        actions = self._request_json(
            self._ACTIONS_URL,
            {"symbols": "AAPL", "start": "2024-02-01", "end": "2024-02-29"},
            headers,
        )
        if isinstance(actions, CredentialValidationResult):
            return actions
        dividends = cast(dict[str, object], actions).get("cash_dividends")
        if not isinstance(dividends, list) or not any(
            isinstance(item, dict)
            and item.get("symbol") == "AAPL"
            and isinstance(item.get("rate"), str | int | float)
            and isinstance(item.get("ex_date"), str)
            for item in dividends
        ):
            return self._failure("source_credential_provider_schema_invalid")

        calendar = self._request_json(
            self._CALENDAR_URL,
            {"start": "2024-11-29", "end": "2024-11-29"},
            headers,
        )
        if isinstance(calendar, CredentialValidationResult):
            return calendar
        if not isinstance(calendar, list) or not any(
            isinstance(item, dict)
            and item.get("date") == "2024-11-29"
            and item.get("close") == "13:00"
            for item in calendar
        ):
            return self._failure("source_credential_provider_schema_invalid")

        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence={
                "contract_id": "alpaca-ticket-07-live-v1",
                "live_validation": "passed",
                "ticker_count": 10,
                "pagination_pages": pagination_pages,
                "datasets": [
                    "alpaca-us-stock-bars-v2",
                    "alpaca-us-corporate-actions-v1",
                    "alpaca-us-trading-calendar-v2",
                ],
            },
        )

    def _request_json(
        self,
        url: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> object | CredentialValidationResult:
        response = self._transport.send(
            ProviderHttpRequest(method="GET", url=url, query=query, headers=headers)
        )
        if response.status_code in {401, 403}:
            return self._failure("source_credential_authentication_failed")
        if response.status_code == 429:
            return self._failure("source_credential_validation_rate_limited")
        if response.status_code != 200:
            return self._failure("source_credential_provider_unavailable")
        try:
            return cast(object, json.loads(response.body))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._failure("source_credential_provider_schema_invalid")

    @staticmethod
    def _valid_bar(item: object) -> bool:
        return isinstance(item, dict) and all(
            isinstance(item.get(field), str | int | float)
            for field in ("t", "o", "h", "l", "c", "v")
        )

    @classmethod
    def _valid_multi_symbol_bars(cls, payload: object, symbols: set[str]) -> bool:
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
            return False
        bars = payload["bars"]
        return set(bars) == symbols and all(
            isinstance(items, list) and items and all(cls._valid_bar(item) for item in items)
            for items in bars.values()
        )

    @classmethod
    def _valid_single_symbol_page(cls, payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        bars = payload.get("bars")
        return (
            isinstance(bars, dict)
            and isinstance(bars.get("AAPL"), list)
            and all(cls._valid_bar(item) for item in bars["AAPL"])
        )

    @staticmethod
    def _failure(reason_code: str) -> CredentialValidationResult:
        return CredentialValidationResult(
            readiness="validation_failed",
            reason_code=reason_code,
            evidence={
                "contract_id": "alpaca-ticket-07-live-v1",
                "live_validation": "failed",
                "reason_code": reason_code,
            },
        )


class AlpacaSourceCollector:
    _REQUIRED_BUNDLE_MEMBERS = {
        "alpaca-us-corporate-actions-v1": (
            "alpaca-us-corporate-actions-v1",
            "https://data.alpaca.markets/v1/corporate-actions",
        ),
        "alpaca-us-trading-calendar-v2": (
            "alpaca-us-trading-calendar-v2",
            "https://paper-api.alpaca.markets/v2/calendar",
        ),
    }

    def __init__(
        self,
        *,
        source_id: str,
        provider_id: str,
        listing_symbols: Mapping[str, tuple[str, ...]],
        credential_resolver: SourceCredentialResolver,
        transport: ProviderHttpTransport,
        clock: Callable[[], datetime],
        rate_limit_policy_id: str,
    ) -> None:
        self._source_id = source_id
        self._provider_id = provider_id
        self._listing_symbols = dict(listing_symbols)
        self._credential_resolver = credential_resolver
        self._transport = transport
        self._clock = clock
        self._rate_limit_policy_id = rate_limit_policy_id

    def collect(self, request: SourcePartitionRequest) -> CollectedSourcePartition:
        declared_members = {
            member.dataset_id: (member.distribution_id, member.distribution_url)
            for member in request.bundle_members
        }
        if declared_members != self._REQUIRED_BUNDLE_MEMBERS:
            raise ValueError("source_bundle_member_request_mismatch")
        try:
            credential_fields = self._credential_resolver.resolve_valid(self._provider_id)
        except CredentialNotReady as error:
            raise SourceCredentialRequired(error.reason_code) from error
        if request.source_id != self._source_id:
            raise ValueError("source_adapter_request_mismatch")
        try:
            requested_symbols = tuple(
                self._listing_symbols[listing_id][-1] for listing_id in request.listing_ids
            )
            all_symbols = tuple(
                sorted(
                    {
                        symbol
                        for listing_id in request.listing_ids
                        for symbol in self._listing_symbols[listing_id]
                    }
                )
            )
            headers = {
                "APCA-API-KEY-ID": credential_fields["api_key_id"],
                "APCA-API-SECRET-KEY": credential_fields["api_secret_key"],
                "Accept": "application/json",
            }
        except (IndexError, KeyError) as error:
            raise SourceCredentialRequired("source_credential_fields_invalid") from error
        bars_pages = self._paginated_request(
            url="https://data.alpaca.markets/v2/stocks/bars",
            query={
                "adjustment": "raw",
                "asof": request.end_date.isoformat(),
                "end": request.end_date.isoformat(),
                "feed": "sip",
                "limit": "10000",
                "sort": "asc",
                "start": request.start_date.isoformat(),
                "symbols": ",".join(requested_symbols),
                "timeframe": "1Day",
            },
            headers=headers,
        )
        corporate_action_pages = self._paginated_request(
            url="https://data.alpaca.markets/v1/corporate-actions",
            query={
                "end": request.end_date.isoformat(),
                "limit": "1000",
                "region": "us",
                "start": request.start_date.isoformat(),
                "symbols": ",".join(all_symbols),
            },
            headers=headers,
        )
        calendar = self._request_json(
            ProviderHttpRequest(
                method="GET",
                url="https://paper-api.alpaca.markets/v2/calendar",
                query={
                    "date_type": "TRADING",
                    "end": request.end_date.isoformat(),
                    "start": request.start_date.isoformat(),
                },
                headers=headers,
            )
        )
        if not isinstance(calendar, list) or not all(
            isinstance(session, dict) for session in calendar
        ):
            raise ValueError("source_provider_schema_invalid")
        bundle = {
            "bars_pages": bars_pages,
            "calendar": calendar,
            "corporate_action_pages": corporate_action_pages,
            "provider_id": self._provider_id,
            "schema_version": "alpaca-source-bundle-v1",
        }
        raw_payload = json.dumps(
            bundle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        checkpoint = f"sha256:{hashlib.sha256(raw_payload).hexdigest()}"
        observed_dates = self._observed_bar_dates(bars_pages)
        session_dates = {date.fromisoformat(str(session["date"])) for session in calendar}
        complete = bool(session_dates) and all(
            session_dates <= observed_dates.get(symbol, set()) for symbol in requested_symbols
        )
        all_observed_dates = {
            observed_date for dates in observed_dates.values() for observed_date in dates
        }
        action_payload = json.dumps(
            corporate_action_pages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        calendar_payload = json.dumps(
            calendar,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        calendar_observed_start = min(session_dates) if session_dates else None
        calendar_observed_end = max(session_dates) if session_dates else None
        return CollectedSourcePartition(
            request_id=request.request_id,
            source_id=request.source_id,
            acquired_at=self._clock(),
            sanitized_source_uri="https://data.alpaca.markets/v2/stocks/bars",
            media_type="application/json",
            raw_payload=raw_payload,
            checkpoint_before=request.expected_checkpoint,
            checkpoint_after=checkpoint,
            coverage=SourceCollectionCoverage(
                requested_start=request.start_date,
                requested_end=request.end_date,
                observed_start=min(all_observed_dates) if all_observed_dates else None,
                observed_end=max(all_observed_dates) if all_observed_dates else None,
                complete=complete,
            ),
            source_revision=checkpoint,
            bundle_members=(
                CollectedSourceBundleMember(
                    dataset_id="alpaca-us-corporate-actions-v1",
                    distribution_id="alpaca-us-corporate-actions-v1",
                    distribution_url="https://data.alpaca.markets/v1/corporate-actions",
                    media_type="application/json",
                    raw_payload=action_payload,
                    coverage=SourceCollectionCoverage(
                        requested_start=request.start_date,
                        requested_end=request.end_date,
                        observed_start=request.start_date,
                        observed_end=request.end_date,
                        complete=True,
                    ),
                    schema_version="alpaca-corporate-actions-v1",
                    known_gaps=("provider_creation_time_not_guaranteed",),
                ),
                CollectedSourceBundleMember(
                    dataset_id="alpaca-us-trading-calendar-v2",
                    distribution_id="alpaca-us-trading-calendar-v2",
                    distribution_url="https://paper-api.alpaca.markets/v2/calendar",
                    media_type="application/json",
                    raw_payload=calendar_payload,
                    coverage=SourceCollectionCoverage(
                        requested_start=request.start_date,
                        requested_end=request.end_date,
                        observed_start=calendar_observed_start,
                        observed_end=calendar_observed_end,
                        complete=bool(session_dates),
                    ),
                    schema_version="alpaca-trading-calendar-v2",
                    known_gaps=("calendar_history_subject_to_provider_coverage",),
                ),
            ),
        )

    def _paginated_request(
        self,
        *,
        url: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> list[dict[str, object]]:
        pages: list[dict[str, object]] = []
        next_page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            page_query = dict(query)
            if next_page_token is not None:
                page_query["page_token"] = next_page_token
            payload = self._request_json(
                ProviderHttpRequest(
                    method="GET",
                    url=url,
                    query=page_query,
                    headers=headers,
                )
            )
            if not isinstance(payload, dict):
                raise ValueError("source_provider_schema_invalid")
            pages.append(payload)
            token = payload.get("next_page_token")
            if token is None:
                return pages
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise ValueError("source_provider_pagination_invalid")
            seen_tokens.add(token)
            next_page_token = token

    def _request_json(self, request: ProviderHttpRequest) -> object:
        response = self._transport.send(request)
        if response.status_code in {401, 403}:
            raise SourceCredentialRequired("source_credential_authentication_failed")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            try:
                retry_after_seconds = int(retry_after)
            except ValueError:
                retry_after_seconds = 60
            raise SourceRateLimited(
                retry_after_seconds=retry_after_seconds,
                rate_limit_policy_id=self._rate_limit_policy_id,
            )
        if response.status_code != 200:
            raise RuntimeError("source_provider_unavailable")
        try:
            return json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("source_provider_schema_invalid") from error

    @staticmethod
    def _observed_bar_dates(
        pages: list[dict[str, object]],
    ) -> dict[str, set[date]]:
        observed: dict[str, set[date]] = {}
        try:
            for page in pages:
                bars = page["bars"]
                if not isinstance(bars, dict):
                    raise TypeError
                for symbol, rows in bars.items():
                    if not isinstance(symbol, str) or not isinstance(rows, list):
                        raise TypeError
                    observed.setdefault(symbol, set()).update(
                        datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).date()
                        for row in rows
                        if isinstance(row, dict)
                    )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        return observed


class AlpacaSourceDecoder:
    def __init__(
        self,
        *,
        source_id: str,
        listing_symbols: Mapping[str, tuple[str, ...]],
        expected_company_action_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._source_id = source_id
        self._listing_symbols = dict(listing_symbols)
        symbol_to_listing: dict[str, str] = {}
        for listing_id, symbols in self._listing_symbols.items():
            if not symbols:
                raise ValueError("source_identity_mapping_invalid")
            for symbol in symbols:
                if symbol in symbol_to_listing and symbol_to_listing[symbol] != listing_id:
                    raise ValueError("source_identity_mapping_ambiguous")
                symbol_to_listing[symbol] = listing_id
        self._symbol_to_listing = symbol_to_listing
        self._expected_company_action_ids = expected_company_action_ids

    def decode(self, collection: CollectedSourcePartition) -> DecodedSourcePartition:
        if collection.source_id != self._source_id:
            raise ValueError("source_decoder_lineage_mismatch")
        try:
            bundle = json.loads(collection.raw_payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        if (
            not isinstance(bundle, dict)
            or bundle.get("provider_id") != "alpaca-market-data-basic"
            or bundle.get("schema_version") != "alpaca-source-bundle-v1"
        ):
            raise ValueError("source_provider_schema_invalid")
        quality_issues: set[SourceQualityIssue] = set()
        prices = self._decode_prices(bundle, quality_issues)
        company_actions, symbol_identities, action_assertions = self._decode_actions(
            bundle,
            quality_issues,
        )
        market_sessions = self._decode_market_sessions(bundle)
        observed_listings = {price.listing_id for price in prices}
        first_session_by_listing = {
            listing_id: min(
                price.session_date for price in prices if price.listing_id == listing_id
            )
            for listing_id in observed_listings
        }
        listing_lifecycle = tuple(
            ListingLifecycleRecord(
                listing_id=listing_id,
                effective_date=first_session_by_listing[listing_id],
                status="active",
                source_event_id=f"alpaca-active:{listing_id}",
            )
            for listing_id in sorted(observed_listings)
        )
        symbol_assertions = {
            f"alpaca-symbol:{price.listing_id}:{symbol}"
            for symbol, listing_id in self._symbol_to_listing.items()
            for price in prices
            if price.listing_id == listing_id and symbol == self._listing_symbols[listing_id][-1]
        }
        return DecodedSourcePartition(
            source_id=collection.source_id,
            schema_version="us-unadjusted-eod-v1",
            source_revision=collection.source_revision,
            prices=tuple(sorted(prices, key=lambda row: (row.listing_id, row.session_date))),
            company_actions=tuple(company_actions),
            listing_lifecycle=listing_lifecycle,
            adjusted_close_cross_checks=(),
            identity_assertion_ids=tuple(sorted(symbol_assertions | action_assertions)),
            parent_object_ids=(),
            symbol_identities=tuple(symbol_identities),
            market_sessions=tuple(market_sessions),
            revision_kind=(
                "correction"
                if collection.checkpoint_before is not None
                and collection.checkpoint_before != collection.checkpoint_after
                else "original"
            ),
            quality_issues=tuple(sorted(quality_issues)),
        )

    def _decode_prices(
        self,
        bundle: Mapping[str, object],
        quality_issues: set[SourceQualityIssue],
    ) -> list[CanonicalPriceRow]:
        prices: list[CanonicalPriceRow] = []
        pages = bundle.get("bars_pages")
        if not isinstance(pages, list):
            raise ValueError("source_provider_schema_invalid")
        try:
            for page in pages:
                if not isinstance(page, dict) or not isinstance(page.get("bars"), dict):
                    raise TypeError
                for symbol, rows in page["bars"].items():
                    if not isinstance(symbol, str) or not isinstance(rows, list):
                        raise TypeError
                    listing_id = self._symbol_to_listing.get(symbol)
                    if listing_id is None:
                        quality_issues.add("identity_ambiguous")
                        continue
                    for row in rows:
                        if not isinstance(row, dict):
                            raise TypeError
                        prices.append(
                            CanonicalPriceRow(
                                listing_id=listing_id,
                                session_date=datetime.fromisoformat(
                                    str(row["t"]).replace("Z", "+00:00")
                                ).date(),
                                open=Decimal(str(row["o"])),
                                high=Decimal(str(row["h"])),
                                low=Decimal(str(row["l"])),
                                close=Decimal(str(row["c"])),
                                volume=int(row["v"]),
                            )
                        )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        return prices

    def _decode_actions(
        self,
        bundle: Mapping[str, object],
        quality_issues: set[SourceQualityIssue],
    ) -> tuple[list[CompanyActionRecord], list[SymbolIdentityRecord], set[str]]:
        company_actions: list[CompanyActionRecord] = []
        symbol_identities: list[SymbolIdentityRecord] = []
        assertion_ids: set[str] = set()
        pages = bundle.get("corporate_action_pages")
        if not isinstance(pages, list):
            raise ValueError("source_provider_schema_invalid")
        for page in pages:
            if not isinstance(page, dict):
                raise ValueError("source_provider_schema_invalid")
            actions = self._action_rows(page)
            for action in actions:
                action_id = action.get("id")
                action_type = action.get("type") or action.get("corporate_action_type")
                if not isinstance(action_id, str) or not isinstance(action_type, str):
                    raise ValueError("source_provider_schema_invalid")
                assertion_ids.add(action_id)
                if action_type == "name_change":
                    old_symbol = action.get("old_symbol") or action.get("initiating_symbol")
                    new_symbol = action.get("new_symbol") or action.get("target_symbol")
                    effective_date = self._action_date(action)
                    listing_id = self._listing_for_action_symbols(old_symbol, new_symbol)
                    if listing_id is None:
                        quality_issues.add("identity_ambiguous")
                        continue
                    symbol_identities.extend(
                        (
                            SymbolIdentityRecord(
                                listing_id=listing_id,
                                symbol=str(old_symbol),
                                valid_from=None,
                                valid_to=effective_date - timedelta(days=1),
                                source_event_id=action_id,
                            ),
                            SymbolIdentityRecord(
                                listing_id=listing_id,
                                symbol=str(new_symbol),
                                valid_from=effective_date,
                                valid_to=None,
                                source_event_id=action_id,
                            ),
                        )
                    )
                    continue
                symbol = action.get("symbol") or action.get("initiating_symbol")
                listing_id = self._symbol_to_listing.get(str(symbol))
                if listing_id is None:
                    quality_issues.add("identity_ambiguous")
                    continue
                if action_type == "cash_dividend":
                    company_actions.append(
                        CompanyActionRecord(
                            listing_id=listing_id,
                            effective_date=self._action_date(action),
                            kind="cash_dividend",
                            value=Decimal(
                                str(
                                    action.get("rate") or action.get("cash") or action.get("amount")
                                )
                            ),
                            currency="USD",
                            source_action_id=action_id,
                        )
                    )
                elif action_type in {"forward_split", "reverse_split"}:
                    old_rate = Decimal(str(action.get("old_rate", "1")))
                    new_rate = Decimal(str(action.get("new_rate", "1")))
                    if old_rate == 0:
                        raise ValueError("source_provider_schema_invalid")
                    company_actions.append(
                        CompanyActionRecord(
                            listing_id=listing_id,
                            effective_date=self._action_date(action),
                            kind="split",
                            value=new_rate / old_rate,
                            currency=None,
                            source_action_id=action_id,
                        )
                    )
                else:
                    quality_issues.add("missing_company_action")
        if not self._expected_company_action_ids <= assertion_ids:
            quality_issues.add("missing_company_action")
        return company_actions, symbol_identities, assertion_ids

    @staticmethod
    def _action_rows(payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, dict):
            raise ValueError("source_provider_schema_invalid")
        provider_types = {
            "cash_dividends": "cash_dividend",
            "forward_splits": "forward_split",
            "name_changes": "name_change",
            "reverse_splits": "reverse_split",
        }
        rows: list[dict[str, object]] = []
        for provider_type, values in payload.items():
            if provider_type == "next_page_token":
                continue
            if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
                raise ValueError("source_provider_schema_invalid")
            action_type = provider_types.get(provider_type, provider_type)
            for value in values:
                rows.append({"type": action_type, **value})
        return rows

    @staticmethod
    def _action_date(action: Mapping[str, object]) -> date:
        value = action.get("effective_date") or action.get("ex_date") or action.get("process_date")
        if not isinstance(value, str):
            raise ValueError("source_provider_schema_invalid")
        return date.fromisoformat(value)

    def _listing_for_action_symbols(self, *symbols: object) -> str | None:
        listing_ids = {
            self._symbol_to_listing[symbol]
            for symbol in symbols
            if isinstance(symbol, str) and symbol in self._symbol_to_listing
        }
        if len(listing_ids) != 1:
            return None
        return listing_ids.pop()

    @staticmethod
    def _decode_market_sessions(
        bundle: Mapping[str, object],
    ) -> list[MarketSessionRecord]:
        calendar = bundle.get("calendar")
        if not isinstance(calendar, list):
            raise ValueError("source_provider_schema_invalid")
        sessions: list[MarketSessionRecord] = []
        try:
            for item in calendar:
                if not isinstance(item, dict):
                    raise TypeError
                close_time = str(item["close"])
                sessions.append(
                    MarketSessionRecord(
                        session_date=date.fromisoformat(str(item["date"])),
                        open_time=str(item["open"]),
                        close_time=close_time,
                        session_kind="early_close" if close_time < "16:00" else "regular",
                    )
                )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        return sessions


class AlpacaPriceSourceAdapter(CollectorDecoderPriceSourceAdapter):
    pass
