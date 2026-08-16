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
    ExternalSecurityAlias,
    ListingLifecycleRecord,
    MarketSessionRecord,
    SourceCollectionCoverage,
    SourceCredentialRequired,
    SourcePartitionRequest,
    SourceQualityIssue,
    SourceRateLimited,
    SourceRevisionKind,
    SourceUnavailable,
    SymbolIdentityRecord,
)
from stock_forecasting.source_credentials import (
    CredentialNotReady,
    CredentialValidationEvidence,
    CredentialValidationResult,
    SourceContractAssessment,
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


@dataclass(frozen=True)
class AlpacaReferenceListing:
    listing_id: str
    aliases: tuple[ExternalSecurityAlias, ...]
    lifecycle: tuple[ListingLifecycleRecord, ...]

    def __post_init__(self) -> None:
        if (
            not self.listing_id
            or not self.aliases
            or any(event.listing_id != self.listing_id for event in self.lifecycle)
        ):
            raise ValueError("alpaca_reference_listing_invalid")


@dataclass(frozen=True)
class AlpacaCompanyActionExpectation:
    action_id: str
    listing_id: str
    effective_date: date

    def __post_init__(self) -> None:
        if not self.action_id or not self.listing_id:
            raise ValueError("alpaca_company_action_expectation_invalid")


@dataclass(frozen=True)
class AlpacaReferenceGraph:
    version_id: str
    listings: tuple[AlpacaReferenceListing, ...]
    company_action_expectations: tuple[AlpacaCompanyActionExpectation, ...]
    lifecycle_complete: bool
    company_actions_complete: bool

    def __post_init__(self) -> None:
        listing_ids = {listing.listing_id for listing in self.listings}
        if (
            not self.version_id
            or not self.listings
            or len(listing_ids) != len(self.listings)
            or (
                self.lifecycle_complete
                and any(
                    not listing.lifecycle
                    or not any(event.status == "active" for event in listing.lifecycle)
                    for listing in self.listings
                )
            )
            or any(
                expectation.listing_id not in listing_ids
                for expectation in self.company_action_expectations
            )
            or len({item.action_id for item in self.company_action_expectations})
            != len(self.company_action_expectations)
        ):
            raise ValueError("alpaca_reference_graph_invalid")

    def partition_payload(
        self,
        *,
        listing_ids: tuple[str, ...],
        start_date: date,
        end_date: date,
    ) -> dict[str, object]:
        requested = set(listing_ids)
        listings = tuple(listing for listing in self.listings if listing.listing_id in requested)
        if len(listings) != len(requested):
            raise ValueError("alpaca_reference_graph_listing_missing")
        expectations = tuple(
            expectation
            for expectation in self.company_action_expectations
            if expectation.listing_id in requested
            and start_date <= expectation.effective_date <= end_date
        )
        return {
            "version_id": self.version_id,
            "lifecycle_complete": self.lifecycle_complete,
            "company_actions_complete": self.company_actions_complete,
            "listings": [
                {
                    "listing_id": listing.listing_id,
                    "aliases": [
                        {
                            "symbol": alias.security_code,
                            "valid_from": (
                                alias.valid_from.isoformat()
                                if alias.valid_from is not None
                                else None
                            ),
                            "valid_to": (
                                alias.valid_to.isoformat() if alias.valid_to is not None else None
                            ),
                        }
                        for alias in listing.aliases
                    ],
                    "lifecycle": [
                        {
                            "effective_date": event.effective_date.isoformat(),
                            "status": event.status,
                            "source_event_id": event.source_event_id,
                        }
                        for event in listing.lifecycle
                    ],
                }
                for listing in listings
            ],
            "expected_company_action_ids": sorted(
                expectation.action_id for expectation in expectations
            ),
        }

    def listing(self, listing_id: str) -> AlpacaReferenceListing:
        try:
            return next(listing for listing in self.listings if listing.listing_id == listing_id)
        except StopIteration as error:
            raise ValueError("alpaca_reference_graph_listing_missing") from error

    def expected_company_action_ids(
        self,
        *,
        listing_ids: tuple[str, ...],
        start_date: date,
        end_date: date,
    ) -> frozenset[str]:
        payload = self.partition_payload(
            listing_ids=listing_ids,
            start_date=start_date,
            end_date=end_date,
        )
        return frozenset(cast(list[str], payload["expected_company_action_ids"]))


def load_candidate_alpaca_reference_graph() -> AlpacaReferenceGraph:
    from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest

    manifest = load_us_stock_pool_manifest()
    listings: list[AlpacaReferenceListing] = []
    for listing in manifest.listings:
        dated_aliases = tuple(
            alias.valid_from for alias in listing.external_aliases if alias.valid_from is not None
        )
        lifecycle = []
        if dated_aliases:
            lifecycle.append(
                ListingLifecycleRecord(
                    listing_id=listing.listing_id,
                    effective_date=min(dated_aliases),
                    status="active",
                    source_event_id=f"{manifest.selection_evidence_version}:selection-active",
                )
            )
        final_alias = listing.external_aliases[-1]
        if final_alias.valid_to is not None:
            lifecycle.append(
                ListingLifecycleRecord(
                    listing_id=listing.listing_id,
                    effective_date=final_alias.valid_to,
                    status="delisted",
                    source_event_id=f"{manifest.selection_evidence_version}:selection-delisted",
                )
            )
        listings.append(
            AlpacaReferenceListing(
                listing_id=listing.listing_id,
                aliases=listing.external_aliases,
                lifecycle=tuple(lifecycle),
            )
        )
    return AlpacaReferenceGraph(
        version_id=manifest.selection_evidence_version,
        listings=tuple(listings),
        company_action_expectations=(),
        lifecycle_complete=False,
        company_actions_complete=False,
    )


@dataclass(frozen=True)
class AlpacaMarketCalendarEvidence:
    version_id: str
    coverage_start: date
    coverage_end: date
    sessions: tuple[MarketSessionRecord, ...]

    def __post_init__(self) -> None:
        session_dates = tuple(session.session_date for session in self.sessions)
        if (
            not self.version_id
            or self.coverage_start > self.coverage_end
            or len(set(session_dates)) != len(session_dates)
            or any(
                not self.coverage_start <= session_date <= self.coverage_end
                for session_date in session_dates
            )
        ):
            raise ValueError("alpaca_market_calendar_evidence_invalid")

    def expected_sessions(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[MarketSessionRecord, ...] | None:
        if start_date < self.coverage_start or end_date > self.coverage_end:
            return None
        return tuple(
            sorted(
                (
                    session
                    for session in self.sessions
                    if start_date <= session.session_date <= end_date
                ),
                key=lambda session: session.session_date,
            )
        )


def load_candidate_alpaca_market_calendar_evidence() -> AlpacaMarketCalendarEvidence:
    from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest

    manifest = load_us_stock_pool_manifest()
    evidence = manifest.market_calendar_evidence
    return AlpacaMarketCalendarEvidence(
        version_id=f"{manifest.selection_evidence_version}:market-calendar",
        coverage_start=evidence.session_date,
        coverage_end=evidence.session_date,
        sessions=(
            MarketSessionRecord(
                session_date=evidence.session_date,
                open_time=evidence.open_time,
                close_time=evidence.close_time,
                session_kind="early_close",
            ),
        ),
    )


class ProviderHttpTransport(Protocol):
    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse: ...


class _UrlResponse(Protocol):
    status: int
    headers: Message

    def read(self, amount: int | None = None) -> bytes: ...

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
    _MAX_RESPONSE_BYTES = 8 * 1024 * 1024

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
                return self._bounded_response(
                    status_code=response.status,
                    body=response.read(self._MAX_RESPONSE_BYTES + 1),
                    headers=dict(response.headers.items()),
                )
        except HTTPError as error:
            return self._bounded_response(
                status_code=error.code,
                body=error.read(self._MAX_RESPONSE_BYTES + 1),
                headers=dict(error.headers.items()) if error.headers is not None else {},
            )
        except URLError:
            return ProviderHttpResponse(
                status_code=503,
                body=b'{"message":"provider transport unavailable"}',
            )

    def _bounded_response(
        self,
        *,
        status_code: int,
        body: bytes,
        headers: Mapping[str, str],
    ) -> ProviderHttpResponse:
        if len(body) > self._MAX_RESPONSE_BYTES:
            return ProviderHttpResponse(
                status_code=502,
                body=b'{"message":"provider response too large"}',
            )
        return ProviderHttpResponse(status_code=status_code, body=body, headers=headers)


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
        if response.status_code == 401:
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_authentication_failed",
                evidence=CredentialValidationEvidence(authentication_status="failed"),
            )
        if response.status_code == 403:
            return CredentialValidationResult(
                readiness="configured",
                reason_code="source_credential_validation_inconclusive",
                source_contract_assessment=SourceContractAssessment(
                    contract_id="alpaca-credential-probe-v1",
                    live_validation="failed",
                    source_contract_reason_code="source_contract_forbidden",
                ),
            )
        if response.status_code == 429:
            return CredentialValidationResult(
                readiness="configured",
                reason_code="source_credential_validation_inconclusive",
                source_contract_assessment=SourceContractAssessment(
                    contract_id="alpaca-credential-probe-v1",
                    live_validation="failed",
                    source_contract_reason_code="source_contract_rate_limited",
                ),
            )
        if response.status_code != 200:
            return CredentialValidationResult(
                readiness="configured",
                reason_code="source_credential_validation_inconclusive",
                source_contract_assessment=SourceContractAssessment(
                    contract_id="alpaca-credential-probe-v1",
                    live_validation="failed",
                    source_contract_reason_code="source_contract_unavailable",
                ),
            )
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
            return CredentialValidationResult(
                readiness="valid",
                reason_code="source_credential_valid",
                evidence=CredentialValidationEvidence(authentication_status="passed"),
                source_contract_assessment=SourceContractAssessment(
                    contract_id="alpaca-credential-probe-v1",
                    live_validation="failed",
                    source_contract_reason_code="source_contract_schema_invalid",
                ),
            )
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
            source_contract_assessment=SourceContractAssessment(
                contract_id="alpaca-credential-probe-v1",
                live_validation="passed",
                ticker_count=1,
                datasets=("alpaca-us-stock-bars-v2",),
            ),
        )


class AlpacaLiveContractValidator:
    """Opt-in provider contract probe; evidence never contains credential material."""

    _REGULAR_SYMBOLS = ("AAPL", "AMZN", "BRK.B", "GME", "GOOG", "GOOGL", "META", "NVDA", "TSM")
    _BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
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
            return self._source_contract_failure("source_contract_schema_invalid")

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
            return self._after_authentication(sivb)
        if not self._valid_multi_symbol_bars(sivb, {"SIVB"}):
            return self._source_contract_failure("source_contract_schema_invalid")

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
                "symbols": "AAPL",
                "timeframe": "1Day",
            }
            if page_token is not None:
                query["page_token"] = page_token
            page = self._request_json(self._BARS_URL, query, headers)
            if isinstance(page, CredentialValidationResult):
                return self._after_authentication(page)
            if not self._valid_single_symbol_page(page):
                return self._source_contract_failure("source_contract_schema_invalid")
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
                return self._source_contract_failure("source_contract_schema_invalid")
            seen_tokens.add(next_token)
            page_token = next_token
        if pagination_pages < 2:
            return self._source_contract_failure("source_contract_schema_invalid")

        actions = self._request_json(
            self._ACTIONS_URL,
            {"symbols": "AAPL", "start": "2024-02-01", "end": "2024-02-29"},
            headers,
        )
        if isinstance(actions, CredentialValidationResult):
            return self._after_authentication(actions)
        dividends = cast(dict[str, object], actions).get("cash_dividends")
        if not isinstance(dividends, list) or not any(
            isinstance(item, dict)
            and item.get("symbol") == "AAPL"
            and isinstance(item.get("rate"), str | int | float)
            and isinstance(item.get("ex_date"), str)
            for item in dividends
        ):
            return self._source_contract_failure("source_contract_schema_invalid")

        name_changes = self._request_json(
            self._ACTIONS_URL,
            {
                "end": "2022-06-10",
                "start": "2022-06-08",
                "symbols": "FB,META",
                "types": "name_change",
            },
            headers,
        )
        if isinstance(name_changes, CredentialValidationResult):
            return self._after_authentication(name_changes)
        name_change_rows = cast(dict[str, object], name_changes).get("name_changes")
        if not isinstance(name_change_rows, list) or not any(
            isinstance(item, dict)
            and item.get("old_symbol") == "FB"
            and item.get("new_symbol") == "META"
            and item.get("process_date") == "2022-06-09"
            for item in name_change_rows
        ):
            return self._source_contract_failure("source_contract_schema_invalid")

        calendar = self._request_json(
            self._CALENDAR_URL,
            {"start": "2024-11-29", "end": "2024-11-29"},
            headers,
        )
        if isinstance(calendar, CredentialValidationResult):
            return self._after_authentication(calendar)
        if not isinstance(calendar, list) or not any(
            isinstance(item, dict)
            and item.get("date") == "2024-11-29"
            and item.get("close") == "13:00"
            for item in calendar
        ):
            return self._source_contract_failure("source_contract_schema_invalid")

        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(
                authentication_status="passed",
            ),
            source_contract_assessment=SourceContractAssessment(
                contract_id="alpaca-ticket-07-live-v1",
                live_validation="passed",
                ticker_count=10,
                pagination_pages=pagination_pages,
                symbol_lifecycle_probe="passed",
                datasets=(
                    "alpaca-us-stock-bars-v2",
                    "alpaca-us-corporate-actions-v1",
                    "alpaca-us-trading-calendar-v2",
                ),
            ),
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
        if response.status_code == 401:
            return self._failure("source_credential_authentication_failed")
        if response.status_code == 403:
            return self._failure("source_credential_provider_forbidden")
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
        source_contract_reason = {
            "source_credential_provider_forbidden": "source_contract_forbidden",
            "source_credential_validation_rate_limited": "source_contract_rate_limited",
            "source_credential_provider_unavailable": "source_contract_unavailable",
            "source_credential_provider_schema_invalid": "source_contract_schema_invalid",
        }.get(reason_code)
        if source_contract_reason is not None:
            return CredentialValidationResult(
                readiness="configured",
                reason_code="source_credential_validation_inconclusive",
                source_contract_assessment=SourceContractAssessment(
                    contract_id="alpaca-ticket-07-live-v1",
                    live_validation="failed",
                    source_contract_reason_code=source_contract_reason,
                ),
            )
        return CredentialValidationResult(
            readiness="validation_failed",
            reason_code=reason_code,
            evidence=CredentialValidationEvidence(
                authentication_status=(
                    "not_run" if reason_code == "source_credential_fields_invalid" else "failed"
                )
            ),
        )

    @staticmethod
    def _after_authentication(
        result: CredentialValidationResult,
    ) -> CredentialValidationResult:
        if result.reason_code == "source_credential_authentication_failed":
            return result
        assessment = result.source_contract_assessment
        reason = (
            assessment.source_contract_reason_code if assessment is not None else None
        ) or "source_contract_probe_failed"
        return AlpacaLiveContractValidator._source_contract_failure(reason)

    @staticmethod
    def _source_contract_failure(reason_code: str) -> CredentialValidationResult:
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
            source_contract_assessment=SourceContractAssessment(
                contract_id="alpaca-ticket-07-live-v1",
                live_validation="failed",
                source_contract_reason_code=reason_code,
            ),
        )


class AlpacaSourceCollector:
    _MAX_PAGES = 1000
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
        reference_graph: AlpacaReferenceGraph,
        market_calendar_evidence: AlpacaMarketCalendarEvidence | None = None,
        credential_resolver: SourceCredentialResolver,
        transport: ProviderHttpTransport,
        clock: Callable[[], datetime],
        rate_limit_policy_id: str,
    ) -> None:
        self._source_id = source_id
        self._provider_id = provider_id
        self._reference_graph = reference_graph
        self._market_calendar_evidence = market_calendar_evidence
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
            lease = self._credential_resolver.resolve_valid(
                self._provider_id,
                trace_id=request.trace_id,
                request_id=request.request_id,
                work_id=request.request_id,
                source_id=request.source_id,
            )
        except CredentialNotReady as error:
            raise SourceCredentialRequired(error.reason_code) from error
        credential_fields = lease.credential_fields(accessed_at=self._clock())
        if request.source_id != self._source_id:
            raise ValueError("source_adapter_request_mismatch")
        try:
            partition_aliases = {
                listing_id: tuple(
                    alias
                    for alias in self._reference_graph.listing(listing_id).aliases
                    if self._alias_overlaps(
                        alias,
                        start_date=request.start_date,
                        end_date=request.end_date,
                    )
                )
                for listing_id in request.listing_ids
            }
            if any(not aliases for aliases in partition_aliases.values()):
                raise ValueError("source_reference_graph_alias_missing")
            requested_symbols = tuple(
                dict.fromkeys(
                    alias.security_code
                    for aliases in partition_aliases.values()
                    for alias in aliases
                )
            )
            all_symbols = tuple(
                sorted(
                    {
                        alias.security_code
                        for listing_id in request.listing_ids
                        for alias in self._reference_graph.listing(listing_id).aliases
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
                "asof": "-",
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
        observed_sessions = self._calendar_sessions(calendar)
        expected_sessions = (
            self._market_calendar_evidence.expected_sessions(
                start_date=request.start_date,
                end_date=request.end_date,
            )
            if self._market_calendar_evidence is not None
            else None
        )
        calendar_exact = expected_sessions is not None and observed_sessions == expected_sessions
        bundle = {
            "bars_pages": bars_pages,
            "calendar": calendar,
            "corporate_action_pages": corporate_action_pages,
            "provider_id": self._provider_id,
            "market_calendar_evidence_version_id": (
                self._market_calendar_evidence.version_id
                if self._market_calendar_evidence is not None
                else None
            ),
            "reference_graph": self._reference_graph.partition_payload(
                listing_ids=request.listing_ids,
                start_date=request.start_date,
                end_date=request.end_date,
            ),
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
        session_dates = {session.session_date for session in observed_sessions}
        complete = calendar_exact and all(
            self._listing_session_observed(
                aliases=aliases,
                session_date=session_date,
                observed_dates=observed_dates,
            )
            for aliases in partition_aliases.values()
            for session_date in session_dates
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
            requested_listing_ids=request.listing_ids,
            reference_graph_version_id=self._reference_graph.version_id,
            reference_graph_lifecycle_verified=self._reference_graph.lifecycle_complete,
            company_action_completeness_verified=(self._reference_graph.company_actions_complete),
            expected_company_action_ids=(
                self._reference_graph.expected_company_action_ids(
                    listing_ids=request.listing_ids,
                    start_date=request.start_date,
                    end_date=request.end_date,
                )
            ),
            market_calendar_evidence_version_id=(
                self._market_calendar_evidence.version_id
                if self._market_calendar_evidence is not None
                else None
            ),
            revision_kind=request.revision_kind,
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
                        complete=calendar_exact,
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
            if (
                not isinstance(token, str)
                or not token
                or token in seen_tokens
                or len(pages) >= self._MAX_PAGES
            ):
                raise ValueError("source_provider_pagination_invalid")
            seen_tokens.add(token)
            next_page_token = token

    def _request_json(self, request: ProviderHttpRequest) -> object:
        response = self._transport.send(request)
        if response.status_code == 401:
            raise SourceCredentialRequired("source_credential_authentication_failed")
        if response.status_code == 403:
            raise SourceUnavailable()
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
            raise SourceUnavailable()
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        credential_values = tuple(
            value
            for header_name in ("APCA-API-KEY-ID", "APCA-API-SECRET-KEY")
            if (value := request.headers.get(header_name))
        )
        if self._contains_credential_value(payload, credential_values=credential_values):
            raise ValueError("source_provider_credential_echo_detected")
        return payload

    @classmethod
    def _contains_credential_value(
        cls,
        value: object,
        *,
        credential_values: tuple[str, ...],
    ) -> bool:
        if isinstance(value, str):
            return any(credential_value in value for credential_value in credential_values)
        if isinstance(value, Mapping):
            return any(
                cls._contains_credential_value(item, credential_values=credential_values)
                for pair in value.items()
                for item in pair
            )
        if isinstance(value, list):
            return any(
                cls._contains_credential_value(item, credential_values=credential_values)
                for item in value
            )
        return False

    @staticmethod
    def _alias_overlaps(
        alias: ExternalSecurityAlias,
        *,
        start_date: date,
        end_date: date,
    ) -> bool:
        return (alias.valid_from is None or alias.valid_from <= end_date) and (
            alias.valid_to is None or alias.valid_to >= start_date
        )

    @staticmethod
    def _listing_session_observed(
        *,
        aliases: tuple[ExternalSecurityAlias, ...],
        session_date: date,
        observed_dates: Mapping[str, set[date]],
    ) -> bool:
        active_symbols = tuple(
            alias.security_code
            for alias in aliases
            if (alias.valid_from is None or alias.valid_from <= session_date)
            and (alias.valid_to is None or alias.valid_to >= session_date)
        )
        return bool(active_symbols) and any(
            session_date in observed_dates.get(symbol, set()) for symbol in active_symbols
        )

    @staticmethod
    def _calendar_sessions(
        calendar: list[object],
    ) -> tuple[MarketSessionRecord, ...]:
        try:
            sessions = tuple(
                sorted(
                    (
                        MarketSessionRecord(
                            session_date=date.fromisoformat(str(session["date"])),
                            open_time=str(session["open"]),
                            close_time=str(session["close"]),
                            session_kind=(
                                "regular" if str(session["close"]) == "16:00" else "early_close"
                            ),
                        )
                        for session in calendar
                        if isinstance(session, Mapping)
                    ),
                    key=lambda session: session.session_date,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        if len(sessions) != len(calendar) or len({item.session_date for item in sessions}) != len(
            sessions
        ):
            raise ValueError("source_provider_schema_invalid")
        return sessions

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
        reference_graph: AlpacaReferenceGraph,
    ) -> None:
        self._source_id = source_id
        self._reference_graph = reference_graph
        self._listing_symbols = {
            listing.listing_id: tuple(alias.security_code for alias in listing.aliases)
            for listing in reference_graph.listings
        }
        aliases_by_symbol: dict[str, list[tuple[str, ExternalSecurityAlias]]] = {}
        for listing in reference_graph.listings:
            if not listing.aliases:
                raise ValueError("source_identity_mapping_invalid")
            for alias in listing.aliases:
                entries = aliases_by_symbol.setdefault(alias.security_code, [])
                if any(
                    self._alias_intervals_overlap(alias, existing_alias)
                    for _, existing_alias in entries
                ):
                    raise ValueError("source_identity_mapping_ambiguous")
                entries.append((listing.listing_id, alias))
        self._aliases_by_symbol = {
            symbol: tuple(entries) for symbol, entries in aliases_by_symbol.items()
        }

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
        if (
            not collection.requested_listing_ids
            or collection.reference_graph_version_id != self._reference_graph.version_id
            or collection.reference_graph_lifecycle_verified
            is not self._reference_graph.lifecycle_complete
            or collection.company_action_completeness_verified
            is not self._reference_graph.company_actions_complete
        ):
            raise ValueError("source_reference_graph_lineage_mismatch")
        expected_reference_payload = self._reference_graph.partition_payload(
            listing_ids=collection.requested_listing_ids,
            start_date=collection.coverage.requested_start,
            end_date=collection.coverage.requested_end,
        )
        if bundle.get("reference_graph") != expected_reference_payload:
            raise ValueError("source_reference_graph_lineage_mismatch")
        quality_issues: set[SourceQualityIssue] = set()
        prices = self._decode_prices(bundle, quality_issues)
        company_actions, _, action_assertions = self._decode_actions(
            bundle,
            quality_issues,
            collection.expected_company_action_ids,
        )
        market_sessions = self._decode_market_sessions(bundle)
        requested_reference_listings = tuple(
            self._reference_graph.listing(listing_id)
            for listing_id in collection.requested_listing_ids
        )
        listing_lifecycle = tuple(
            event for listing in requested_reference_listings for event in listing.lifecycle
        )
        symbol_identities = tuple(
            SymbolIdentityRecord(
                listing_id=listing.listing_id,
                symbol=alias.security_code,
                valid_from=alias.valid_from,
                valid_to=alias.valid_to,
                source_event_id=(
                    f"{self._reference_graph.version_id}:{listing.listing_id}:{alias.security_code}"
                ),
            )
            for listing in requested_reference_listings
            for alias in listing.aliases
        )
        symbol_assertions = {identity.source_event_id for identity in symbol_identities}
        if not collection.reference_graph_lifecycle_verified:
            quality_issues.add("identity_ambiguous")
        if not collection.company_action_completeness_verified:
            quality_issues.add("missing_company_action")
        revision_kind: SourceRevisionKind = collection.revision_kind
        if revision_kind == "correction":
            quality_issues.add("correction_requires_review")
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
            symbol_identities=symbol_identities,
            market_sessions=tuple(market_sessions),
            revision_kind=revision_kind,
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
        seen_keys: set[tuple[str, date]] = set()
        try:
            for page in pages:
                if not isinstance(page, dict) or not isinstance(page.get("bars"), dict):
                    raise TypeError
                for symbol, rows in page["bars"].items():
                    if not isinstance(symbol, str) or not isinstance(rows, list):
                        raise TypeError
                    for row in rows:
                        if not isinstance(row, dict):
                            raise TypeError
                        session_date = datetime.fromisoformat(
                            str(row["t"]).replace("Z", "+00:00")
                        ).date()
                        listing_id = self._listing_for_symbol(
                            symbol,
                            effective_date=session_date,
                        )
                        if listing_id is None:
                            quality_issues.add("identity_ambiguous")
                            continue
                        key = (listing_id, session_date)
                        if key in seen_keys:
                            quality_issues.add("identity_ambiguous")
                            continue
                        seen_keys.add(key)
                        prices.append(
                            CanonicalPriceRow(
                                listing_id=listing_id,
                                session_date=session_date,
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
        expected_company_action_ids: frozenset[str],
    ) -> tuple[list[CompanyActionRecord], list[SymbolIdentityRecord], set[str]]:
        company_actions: list[CompanyActionRecord] = []
        symbol_identities: list[SymbolIdentityRecord] = []
        assertion_ids: set[str] = set()
        seen_action_ids: set[str] = set()
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
                if action_id in seen_action_ids:
                    quality_issues.add("duplicate_company_action")
                    continue
                seen_action_ids.add(action_id)
                if action_type == "name_change":
                    old_symbol = action.get("old_symbol") or action.get("initiating_symbol")
                    new_symbol = action.get("new_symbol") or action.get("target_symbol")
                    effective_date = self._action_date(action)
                    listing_id = self._listing_for_action_symbols(
                        old_symbol,
                        new_symbol,
                        effective_date=effective_date,
                    )
                    if listing_id is None:
                        quality_issues.add("identity_ambiguous")
                        continue
                    assertion_ids.add(action_id)
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
                effective_date = self._action_date(action)
                listing_id = self._listing_for_symbol(
                    str(symbol),
                    effective_date=effective_date,
                )
                if listing_id is None:
                    quality_issues.add("identity_ambiguous")
                    continue
                assertion_ids.add(action_id)
                if action_type == "cash_dividend":
                    company_actions.append(
                        CompanyActionRecord(
                            listing_id=listing_id,
                            effective_date=effective_date,
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
                            effective_date=effective_date,
                            kind="split",
                            value=new_rate / old_rate,
                            currency=None,
                            source_action_id=action_id,
                        )
                    )
                else:
                    quality_issues.add("missing_company_action")
        if not expected_company_action_ids <= assertion_ids:
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

    def _listing_for_action_symbols(
        self,
        old_symbol: object,
        new_symbol: object,
        *,
        effective_date: date,
    ) -> str | None:
        if not isinstance(old_symbol, str) or not isinstance(new_symbol, str):
            return None
        old_listing_id = self._listing_for_symbol(
            old_symbol,
            effective_date=effective_date - timedelta(days=1),
        )
        new_listing_id = self._listing_for_symbol(
            new_symbol,
            effective_date=effective_date,
        )
        if old_listing_id is None or old_listing_id != new_listing_id:
            return None
        return old_listing_id

    def _listing_for_symbol(self, symbol: str, *, effective_date: date) -> str | None:
        listing_ids = {
            listing_id
            for listing_id, alias in self._aliases_by_symbol.get(symbol, ())
            if self._alias_is_active(alias, effective_date=effective_date)
        }
        if len(listing_ids) != 1:
            return None
        return listing_ids.pop()

    @staticmethod
    def _alias_is_active(alias: ExternalSecurityAlias, *, effective_date: date) -> bool:
        return (alias.valid_from is None or alias.valid_from <= effective_date) and (
            alias.valid_to is None or alias.valid_to >= effective_date
        )

    @staticmethod
    def _alias_intervals_overlap(
        first: ExternalSecurityAlias,
        second: ExternalSecurityAlias,
    ) -> bool:
        first_start = first.valid_from or date.min
        first_end = first.valid_to or date.max
        second_start = second.valid_from or date.min
        second_end = second.valid_to or date.max
        return first_start <= second_end and second_start <= first_end

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
