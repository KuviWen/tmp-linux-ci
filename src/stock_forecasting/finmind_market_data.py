from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from stock_forecasting.authorization import SourceAccessMode
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
    SourceBundleMemberRequest,
    SourceCollectionCoverage,
    SourceCredentialRequired,
    SourcePartitionRequest,
    SourceQualityIssue,
    SourceRateLimited,
    SourceUnavailable,
    SymbolIdentityRecord,
)
from stock_forecasting.finmind_provider_contract import (
    FINMIND_CREDENTIAL_PROBE_CONTRACT_ID,
    FINMIND_DATA_URL,
    FINMIND_DELISTING_DISTRIBUTION,
    FINMIND_DIVIDEND_RESULT_DISTRIBUTION,
    FINMIND_LIVE_VALIDATION_CONTRACT_ID,
    FINMIND_PRICE_DISTRIBUTION,
    FINMIND_PROVIDER_DISTRIBUTIONS,
    FINMIND_REQUIRED_BUNDLE_DISTRIBUTIONS,
    FINMIND_SPLIT_PRICE_DISTRIBUTION,
    FINMIND_TRADING_DATE_DISTRIBUTION,
)
from stock_forecasting.market_data_reference import (
    MarketCalendarEvidence,
    MarketDataReferenceGraph,
    MarketDataReferenceListing,
)
from stock_forecasting.provider_http import ProviderHttpRequest, ProviderHttpTransport
from stock_forecasting.source_credentials import (
    CredentialNotReady,
    CredentialValidationEvidence,
    CredentialValidationResult,
    SourceContractAssessment,
    SourceCredentialResolver,
)


def _valid_finmind_price_values(
    value: object,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None:
    if not isinstance(value, dict):
        return None
    try:
        open_price, high, low, close, volume = (
            Decimal(str(value[field]))
            for field in ("open", "max", "min", "close", "Trading_Volume")
        )
    except (InvalidOperation, KeyError, ValueError):
        return None
    if (
        not all(item.is_finite() for item in (open_price, high, low, close, volume))
        or any(item <= 0 for item in (open_price, high, low, close))
        or volume < 0
        or volume != volume.to_integral_value()
        or high < low
        or not low <= open_price <= high
        or not low <= close <= high
    ):
        return None
    return open_price, high, low, close, volume


class FinMindSourceCollector:
    _REQUIRED_BUNDLE_MEMBERS = {
        distribution.policy_dataset_id: (
            distribution.distribution_id,
            distribution.distribution_url,
        )
        for distribution in FINMIND_REQUIRED_BUNDLE_DISTRIBUTIONS
    }

    def __init__(
        self,
        *,
        source_id: str,
        provider_id: str,
        reference_graph: MarketDataReferenceGraph | None = None,
        market_calendar_evidence: MarketCalendarEvidence | None = None,
        credential_resolver: SourceCredentialResolver,
        transport: ProviderHttpTransport,
        clock: Callable[[], datetime],
        rate_limit_policy_id: str,
    ) -> None:
        self._source_id = source_id
        self._provider_id = provider_id
        self._reference_graph = reference_graph or load_candidate_finmind_reference_graph()
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
                work_id=f"{request.request_id}:finmind-collect",
                source_id=self._source_id,
            )
        except CredentialNotReady as error:
            raise SourceCredentialRequired(error.reason_code) from error
        if request.source_id != self._source_id:
            raise ValueError("source_adapter_request_mismatch")
        try:
            token = lease.credential_fields()["token"]
        except KeyError as error:
            raise SourceCredentialRequired("source_credential_fields_invalid") from error
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        aliases_by_listing = {
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
        if any(not aliases for aliases in aliases_by_listing.values()):
            raise ValueError("source_reference_graph_alias_missing")
        symbols = tuple(
            dict.fromkeys(
                alias.security_code for aliases in aliases_by_listing.values() for alias in aliases
            )
        )
        price_rows = self._request_per_symbol(
            dataset=FINMIND_PRICE_DISTRIBUTION.distribution_id,
            symbols=symbols,
            request=request,
            headers=headers,
            token=token,
        )
        trading_date_rows = self._request_dataset(
            dataset=FINMIND_TRADING_DATE_DISTRIBUTION.distribution_id,
            query={},
            headers=headers,
            token=token,
        )
        dividend_rows = self._request_per_symbol(
            dataset=FINMIND_DIVIDEND_RESULT_DISTRIBUTION.distribution_id,
            symbols=symbols,
            request=request,
            headers=headers,
            token=token,
        )
        delisting_rows = self._request_dataset(
            dataset=FINMIND_DELISTING_DISTRIBUTION.distribution_id,
            query={},
            headers=headers,
            token=token,
        )
        split_rows = self._request_dataset(
            dataset=FINMIND_SPLIT_PRICE_DISTRIBUTION.distribution_id,
            query={},
            headers=headers,
            token=token,
        )
        observed_sessions = self._observed_sessions(
            trading_date_rows,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        expected_sessions = (
            self._market_calendar_evidence.expected_sessions(
                start_date=request.start_date,
                end_date=request.end_date,
            )
            if self._market_calendar_evidence is not None
            else None
        )
        calendar_exact = (
            expected_sessions is not None
            and tuple(session.session_date for session in expected_sessions) == observed_sessions
        )
        observed_price_dates = self._observed_price_dates(price_rows)
        complete = calendar_exact and all(
            not self._listing_active_on(
                self._reference_graph.listing(listing_id),
                session_date=session_date,
            )
            or any(
                session_date in observed_price_dates.get(alias.security_code, set())
                for alias in aliases
                if self._alias_active(alias, session_date=session_date)
            )
            for listing_id, aliases in aliases_by_listing.items()
            for session_date in observed_sessions
        )
        expected_action_ids = self._reference_graph.expected_company_action_ids(
            listing_ids=request.listing_ids,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        observed_action_ids = self._observed_action_ids(
            dividend_rows,
            split_rows,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        company_actions_complete = (
            self._reference_graph.company_actions_complete
            and expected_action_ids <= observed_action_ids
        )
        lifecycle_complete = self._lifecycle_complete(
            request=request,
            rows=delisting_rows,
        )
        bundle = {
            "provider_id": self._provider_id,
            "schema_version": "finmind-source-bundle-v1",
            "prices": price_rows,
            "trading_dates": trading_date_rows,
            "dividend_results": dividend_rows,
            "delistings": delisting_rows,
            "split_prices": split_rows,
            "reference_graph": self._reference_graph.partition_payload(
                listing_ids=request.listing_ids,
                start_date=request.start_date,
                end_date=request.end_date,
            ),
            "market_calendar_evidence_version_id": (
                self._market_calendar_evidence.version_id
                if self._market_calendar_evidence is not None
                else None
            ),
        }
        raw_payload = self._canonical_bytes(bundle)
        checkpoint = f"sha256:{hashlib.sha256(raw_payload).hexdigest()}"
        all_price_dates = {
            observed_date for dates in observed_price_dates.values() for observed_date in dates
        }
        member_requests = {member.dataset_id: member for member in request.bundle_members}
        return CollectedSourcePartition(
            request_id=request.request_id,
            source_id=request.source_id,
            acquired_at=self._clock(),
            sanitized_source_uri=FINMIND_PRICE_DISTRIBUTION.distribution_url,
            media_type="application/json",
            raw_payload=raw_payload,
            checkpoint_before=request.expected_checkpoint,
            checkpoint_after=checkpoint,
            coverage=SourceCollectionCoverage(
                requested_start=request.start_date,
                requested_end=request.end_date,
                observed_start=min(all_price_dates) if all_price_dates else None,
                observed_end=max(all_price_dates) if all_price_dates else None,
                complete=complete,
            ),
            source_revision=checkpoint,
            requested_listing_ids=request.listing_ids,
            reference_graph_version_id=self._reference_graph.version_id,
            reference_graph_lifecycle_verified=lifecycle_complete,
            company_action_completeness_verified=company_actions_complete,
            expected_company_action_ids=expected_action_ids,
            market_calendar_evidence_version_id=(
                self._market_calendar_evidence.version_id
                if self._market_calendar_evidence is not None
                else None
            ),
            revision_kind=request.revision_kind,
            bundle_members=tuple(
                self._bundle_member(
                    request=request,
                    declared=member_requests[distribution.policy_dataset_id],
                    distribution_id=distribution.distribution_id,
                    rows=rows,
                    complete=member_complete,
                )
                for distribution, rows, member_complete in (
                    (
                        FINMIND_TRADING_DATE_DISTRIBUTION,
                        trading_date_rows,
                        calendar_exact,
                    ),
                    (
                        FINMIND_DIVIDEND_RESULT_DISTRIBUTION,
                        dividend_rows,
                        company_actions_complete,
                    ),
                    (
                        FINMIND_DELISTING_DISTRIBUTION,
                        delisting_rows,
                        lifecycle_complete,
                    ),
                    (
                        FINMIND_SPLIT_PRICE_DISTRIBUTION,
                        split_rows,
                        company_actions_complete,
                    ),
                )
            ),
        )

    def _request_per_symbol(
        self,
        *,
        dataset: str,
        symbols: tuple[str, ...],
        request: SourcePartitionRequest,
        headers: Mapping[str, str],
        token: str,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for symbol in symbols:
            rows.extend(
                self._request_dataset(
                    dataset=dataset,
                    query={
                        "data_id": symbol,
                        "start_date": request.start_date.isoformat(),
                        "end_date": request.end_date.isoformat(),
                    },
                    headers=headers,
                    token=token,
                )
            )
        return rows

    def _request_dataset(
        self,
        *,
        dataset: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        token: str,
    ) -> list[dict[str, object]]:
        response = self._transport.send(
            ProviderHttpRequest(
                method="GET",
                url=FINMIND_DATA_URL,
                query={"dataset": dataset, **query},
                headers=headers,
            )
        )
        if response.status_code == 401:
            raise SourceCredentialRequired("source_credential_authentication_failed")
        if response.status_code in {402, 429}:
            retry_after = response.headers.get("Retry-After", "60")
            try:
                retry_after_seconds = int(retry_after)
                if retry_after_seconds < 0:
                    raise ValueError
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
        rows = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != 200
            or not isinstance(rows, list)
            or not all(isinstance(row, dict) for row in rows)
        ):
            raise ValueError("source_provider_schema_invalid")
        if FinMindCredentialValidator._contains_token(payload, token=token):
            raise ValueError("source_provider_credential_echo_detected")
        return cast(list[dict[str, object]], rows)

    def _lifecycle_complete(
        self,
        *,
        request: SourcePartitionRequest,
        rows: list[dict[str, object]],
    ) -> bool:
        if not self._reference_graph.lifecycle_complete:
            return False
        observed = {
            (str(row.get("stock_id")), str(row.get("date")))
            for row in rows
            if row.get("stock_id") is not None and row.get("date") is not None
        }
        expected: set[tuple[str, str]] = set()
        for listing_id in request.listing_ids:
            listing = self._reference_graph.listing(listing_id)
            for event in listing.lifecycle:
                if (
                    event.status != "delisted"
                    or not request.start_date <= event.effective_date <= request.end_date
                ):
                    continue
                alias = self._delisting_alias(
                    listing,
                    effective_date=event.effective_date,
                )
                if alias is None:
                    return False
                expected.add((alias.security_code, event.effective_date.isoformat()))
        return expected <= observed

    @classmethod
    def _delisting_alias(
        cls,
        listing: MarketDataReferenceListing,
        *,
        effective_date: date,
    ) -> ExternalSecurityAlias | None:
        active = tuple(
            alias
            for alias in listing.aliases
            if cls._alias_active(alias, session_date=effective_date)
        )
        if len(active) == 1:
            return active[0]
        if active:
            return None
        ended = tuple(
            alias
            for alias in listing.aliases
            if alias.valid_to is not None and alias.valid_to < effective_date
        )
        if not ended:
            return None
        latest_valid_to = max(alias.valid_to for alias in ended if alias.valid_to is not None)
        latest = tuple(alias for alias in ended if alias.valid_to == latest_valid_to)
        return latest[0] if len(latest) == 1 else None

    @staticmethod
    def _observed_sessions(
        rows: list[dict[str, object]],
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[date, ...]:
        try:
            dates = tuple(
                sorted(
                    date.fromisoformat(str(row["date"]))
                    for row in rows
                    if start_date <= date.fromisoformat(str(row["date"])) <= end_date
                )
            )
        except (KeyError, ValueError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        if len(dates) != len(set(dates)):
            raise ValueError("source_provider_schema_invalid")
        return dates

    @staticmethod
    def _observed_price_dates(
        rows: list[dict[str, object]],
    ) -> dict[str, set[date]]:
        observed: dict[str, set[date]] = {}
        try:
            for row in rows:
                if _valid_finmind_price_values(row) is None:
                    continue
                observed.setdefault(str(row["stock_id"]), set()).add(
                    date.fromisoformat(str(row["date"]))
                )
        except (InvalidOperation, KeyError, ValueError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        return observed

    @staticmethod
    def _observed_action_ids(
        dividend_rows: list[dict[str, object]],
        split_rows: list[dict[str, object]],
        *,
        start_date: date,
        end_date: date,
    ) -> frozenset[str]:
        return frozenset(
            {
                "finmind:TaiwanStockDividendResult:"
                f"{row.get('stock_id')}:{row.get('date')}:cash_dividend"
                for row in dividend_rows
                if row.get("stock_or_cache_dividend") == "息"
                and FinMindSourceCollector._row_in_partition(
                    row,
                    start_date=start_date,
                    end_date=end_date,
                )
            }
            | {
                f"finmind:TaiwanStockSplitPrice:{row.get('stock_id')}:{row.get('date')}:split"
                for row in split_rows
                if row.get("stock_id") is not None
                and row.get("date") is not None
                and row.get("type") == "分割"
                and FinMindSourceCollector._row_in_partition(
                    row,
                    start_date=start_date,
                    end_date=end_date,
                )
            }
        )

    @staticmethod
    def _row_in_partition(
        row: Mapping[str, object],
        *,
        start_date: date,
        end_date: date,
    ) -> bool:
        try:
            observed_on = date.fromisoformat(str(row["date"]))
        except (KeyError, ValueError):
            return False
        return start_date <= observed_on <= end_date

    def _listing_active_on(
        self,
        listing: MarketDataReferenceListing,
        *,
        session_date: date,
    ) -> bool:
        if not self._reference_graph.lifecycle_complete:
            return True
        applicable = tuple(
            event for event in listing.lifecycle if event.effective_date <= session_date
        )
        if not applicable:
            return False
        latest = max(applicable, key=lambda event: event.effective_date)
        return latest.status == "active"

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
    def _alias_active(alias: ExternalSecurityAlias, *, session_date: date) -> bool:
        return (alias.valid_from is None or alias.valid_from <= session_date) and (
            alias.valid_to is None or alias.valid_to >= session_date
        )

    @staticmethod
    def _canonical_bytes(payload: object) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _bundle_member(
        cls,
        *,
        request: SourcePartitionRequest,
        declared: SourceBundleMemberRequest,
        distribution_id: str,
        rows: list[dict[str, object]],
        complete: bool,
    ) -> CollectedSourceBundleMember:
        return CollectedSourceBundleMember(
            dataset_id=declared.dataset_id,
            distribution_id=distribution_id,
            distribution_url=FINMIND_DATA_URL,
            media_type="application/json",
            raw_payload=cls._canonical_bytes(rows),
            coverage=SourceCollectionCoverage(
                requested_start=request.start_date,
                requested_end=request.end_date,
                observed_start=request.start_date if complete else None,
                observed_end=request.end_date if complete else None,
                complete=complete,
            ),
            schema_version=declared.schema_version,
            known_gaps=("provider_revision_history_not_attested",),
        )


def load_candidate_finmind_reference_graph() -> MarketDataReferenceGraph:
    from stock_forecasting.data_supply import load_taiwan_stock_pool_manifest

    manifest = load_taiwan_stock_pool_manifest()
    listings = tuple(
        MarketDataReferenceListing(
            listing_id=listing.listing_id,
            aliases=listing.external_aliases,
            lifecycle=tuple(
                [
                    ListingLifecycleRecord(
                        listing_id=listing.listing_id,
                        effective_date=valid_from,
                        status="active",
                        source_event_id=(f"{manifest.selection_evidence_version}:selection-active"),
                    )
                ]
                if (
                    valid_from := next(
                        (
                            alias.valid_from
                            for alias in listing.external_aliases
                            if alias.valid_from is not None
                        ),
                        None,
                    )
                )
                is not None
                else []
            ),
        )
        for listing in manifest.listings
    )
    return MarketDataReferenceGraph(
        version_id=manifest.selection_evidence_version,
        listings=listings,
        company_action_expectations=(),
        lifecycle_complete=False,
        company_actions_complete=False,
    )


class FinMindSourceDecoder:
    def __init__(
        self,
        *,
        source_id: str,
        reference_graph: MarketDataReferenceGraph,
        market_calendar_evidence: MarketCalendarEvidence | None = None,
    ) -> None:
        self._source_id = source_id
        self._reference_graph = reference_graph
        self._market_calendar_evidence = market_calendar_evidence
        aliases_by_symbol: dict[str, list[tuple[str, ExternalSecurityAlias]]] = {}
        for listing in reference_graph.listings:
            for alias in listing.aliases:
                entries = aliases_by_symbol.setdefault(alias.security_code, [])
                if any(
                    self._intervals_overlap(alias, existing_alias) for _, existing_alias in entries
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
            or bundle.get("provider_id") != "finmind-free-api"
            or bundle.get("schema_version") != "finmind-source-bundle-v1"
            or not collection.requested_listing_ids
            or collection.reference_graph_version_id != self._reference_graph.version_id
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
        start_date = collection.coverage.requested_start
        end_date = collection.coverage.requested_end
        prices = self._decode_prices(
            bundle,
            quality_issues,
            start_date=start_date,
            end_date=end_date,
        )
        company_actions, action_assertions = self._decode_actions(
            bundle,
            quality_issues,
            expected_action_ids=collection.expected_company_action_ids,
            start_date=start_date,
            end_date=end_date,
        )
        requested_listings = tuple(
            self._reference_graph.listing(listing_id)
            for listing_id in collection.requested_listing_ids
        )
        lifecycle = self._decode_lifecycle(
            bundle,
            requested_listings,
            quality_issues,
            start_date=start_date,
            end_date=end_date,
        )
        symbol_identities = tuple(
            SymbolIdentityRecord(
                listing_id=listing.listing_id,
                symbol=alias.security_code,
                valid_from=alias.valid_from,
                valid_to=alias.valid_to,
                source_event_id=(
                    f"{self._reference_graph.version_id}:{listing.listing_id}:"
                    f"{alias.security_code}:{alias.valid_from}:{alias.valid_to}"
                ),
            )
            for listing in requested_listings
            for alias in listing.aliases
            if self._interval_overlaps_partition(
                alias,
                start_date=start_date,
                end_date=end_date,
            )
        )
        if not collection.reference_graph_lifecycle_verified:
            quality_issues.add("identity_ambiguous")
        if not collection.company_action_completeness_verified:
            quality_issues.add("missing_company_action")
        if collection.revision_kind == "correction":
            quality_issues.add("correction_requires_review")
        market_sessions = self._market_sessions(collection, bundle)
        return DecodedSourcePartition(
            source_id=collection.source_id,
            schema_version="taiwan-unadjusted-eod-v1",
            source_revision=collection.source_revision,
            prices=tuple(sorted(prices, key=lambda row: (row.listing_id, row.session_date))),
            company_actions=tuple(
                sorted(
                    company_actions,
                    key=lambda item: (item.listing_id, item.effective_date, item.source_action_id),
                )
            ),
            listing_lifecycle=tuple(
                sorted(
                    lifecycle,
                    key=lambda item: (item.listing_id, item.effective_date, item.source_event_id),
                )
            ),
            adjusted_close_cross_checks=(),
            identity_assertion_ids=tuple(
                sorted(
                    {item.source_event_id for item in symbol_identities}
                    | action_assertions
                    | {item.source_event_id for item in lifecycle}
                )
            ),
            parent_object_ids=(),
            symbol_identities=symbol_identities,
            market_sessions=market_sessions,
            revision_kind=collection.revision_kind,
            quality_issues=tuple(sorted(quality_issues)),
        )

    def _decode_prices(
        self,
        bundle: Mapping[str, object],
        quality_issues: set[SourceQualityIssue],
        *,
        start_date: date,
        end_date: date,
    ) -> list[CanonicalPriceRow]:
        rows = bundle.get("prices")
        if not isinstance(rows, list):
            raise ValueError("source_provider_schema_invalid")
        prices: list[CanonicalPriceRow] = []
        seen: set[tuple[str, date]] = set()
        try:
            for value in rows:
                if not isinstance(value, dict):
                    raise TypeError
                session_date = date.fromisoformat(str(value["date"]))
                if not start_date <= session_date <= end_date:
                    continue
                listing_id = self._listing_for_symbol(
                    str(value["stock_id"]),
                    effective_date=session_date,
                )
                if listing_id is None:
                    quality_issues.add("identity_ambiguous")
                    continue
                key = (listing_id, session_date)
                if key in seen:
                    quality_issues.add("identity_ambiguous")
                    continue
                seen.add(key)
                parsed = _valid_finmind_price_values(value)
                if parsed is None:
                    raw_prices = tuple(
                        self._decimal(value[field]) for field in ("open", "max", "min", "close")
                    )
                    if all(item == 0 for item in raw_prices):
                        continue
                    raise ValueError
                open_price, high, low, close, volume_decimal = parsed
                prices.append(
                    CanonicalPriceRow(
                        listing_id=listing_id,
                        session_date=session_date,
                        open=open_price,
                        high=high,
                        low=low,
                        close=close,
                        volume=int(volume_decimal),
                    )
                )
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise ValueError("source_provider_schema_invalid") from error
        return prices

    def _decode_actions(
        self,
        bundle: Mapping[str, object],
        quality_issues: set[SourceQualityIssue],
        *,
        expected_action_ids: frozenset[str],
        start_date: date,
        end_date: date,
    ) -> tuple[list[CompanyActionRecord], set[str]]:
        dividend_rows = bundle.get("dividend_results")
        split_rows = bundle.get("split_prices")
        if not isinstance(dividend_rows, list) or not isinstance(split_rows, list):
            raise ValueError("source_provider_schema_invalid")
        actions: list[CompanyActionRecord] = []
        assertions: set[str] = set()
        try:
            for value in dividend_rows:
                if not isinstance(value, dict):
                    raise TypeError
                if value.get("stock_or_cache_dividend") != "息":
                    continue
                effective_date = date.fromisoformat(str(value["date"]))
                if not start_date <= effective_date <= end_date:
                    continue
                symbol = str(value["stock_id"])
                listing_id = self._listing_for_symbol(symbol, effective_date=effective_date)
                if listing_id is None:
                    if symbol in self._aliases_by_symbol:
                        quality_issues.add("identity_ambiguous")
                    continue
                action_id = (
                    f"finmind:TaiwanStockDividendResult:{symbol}:"
                    f"{effective_date.isoformat()}:cash_dividend"
                )
                if action_id in assertions:
                    quality_issues.add("duplicate_company_action")
                    continue
                assertions.add(action_id)
                amount = self._decimal(value["stock_and_cache_dividend"])
                if amount <= 0:
                    raise ValueError
                actions.append(
                    CompanyActionRecord(
                        listing_id=listing_id,
                        effective_date=effective_date,
                        kind="cash_dividend",
                        value=amount,
                        currency="TWD",
                        source_action_id=action_id,
                    )
                )
            for value in split_rows:
                if not isinstance(value, dict):
                    raise TypeError
                if value.get("type") != "分割":
                    continue
                effective_date = date.fromisoformat(str(value["date"]))
                if not start_date <= effective_date <= end_date:
                    continue
                symbol = str(value["stock_id"])
                listing_id = self._listing_for_symbol(symbol, effective_date=effective_date)
                if listing_id is None:
                    if symbol in self._aliases_by_symbol:
                        quality_issues.add("identity_ambiguous")
                    continue
                action_id = (
                    f"finmind:TaiwanStockSplitPrice:{symbol}:{effective_date.isoformat()}:split"
                )
                if action_id in assertions:
                    quality_issues.add("duplicate_company_action")
                    continue
                before = self._decimal(value["before_price"])
                after = self._decimal(value["after_price"])
                if after <= 0 or before <= 0:
                    raise ValueError
                assertions.add(action_id)
                actions.append(
                    CompanyActionRecord(
                        listing_id=listing_id,
                        effective_date=effective_date,
                        kind="split",
                        value=before / after,
                        currency=None,
                        source_action_id=action_id,
                    )
                )
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise ValueError("source_provider_schema_invalid") from error
        if not expected_action_ids <= assertions:
            quality_issues.add("missing_company_action")
        return actions, assertions

    def _decode_lifecycle(
        self,
        bundle: Mapping[str, object],
        requested_listings: tuple[MarketDataReferenceListing, ...],
        quality_issues: set[SourceQualityIssue],
        *,
        start_date: date,
        end_date: date,
    ) -> set[ListingLifecycleRecord]:
        rows = bundle.get("delistings")
        if not isinstance(rows, list):
            raise ValueError("source_provider_schema_invalid")
        lifecycle = {
            event
            for listing in requested_listings
            for event in listing.lifecycle
            if event.effective_date <= end_date
        }
        try:
            for value in rows:
                if not isinstance(value, dict):
                    raise TypeError
                symbol = str(value["stock_id"])
                if symbol not in self._aliases_by_symbol:
                    continue
                effective_date = date.fromisoformat(str(value["date"]))
                if not start_date <= effective_date <= end_date:
                    continue
                candidates = {listing_id for listing_id, _ in self._aliases_by_symbol[symbol]}
                if len(candidates) != 1:
                    quality_issues.add("identity_ambiguous")
                    continue
                listing_id = candidates.pop()
                if any(
                    event.listing_id == listing_id
                    and event.effective_date == effective_date
                    and event.status == "delisted"
                    for event in lifecycle
                ):
                    continue
                lifecycle.add(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=effective_date,
                        status="delisted",
                        source_event_id=(
                            f"finmind:TaiwanStockDelisting:{symbol}:{effective_date.isoformat()}"
                        ),
                    )
                )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("source_provider_schema_invalid") from error
        return lifecycle

    def _market_sessions(
        self,
        collection: CollectedSourcePartition,
        bundle: Mapping[str, object],
    ) -> tuple[MarketSessionRecord, ...]:
        if self._market_calendar_evidence is None:
            return ()
        if (
            collection.market_calendar_evidence_version_id
            != self._market_calendar_evidence.version_id
            or bundle.get("market_calendar_evidence_version_id")
            != self._market_calendar_evidence.version_id
        ):
            raise ValueError("source_market_calendar_lineage_mismatch")
        expected = self._market_calendar_evidence.expected_sessions(
            start_date=collection.coverage.requested_start,
            end_date=collection.coverage.requested_end,
        )
        if expected is None:
            return ()
        rows = bundle.get("trading_dates")
        if not isinstance(rows, list):
            raise ValueError("source_provider_schema_invalid")
        observed = FinMindSourceCollector._observed_sessions(
            cast(list[dict[str, object]], rows),
            start_date=collection.coverage.requested_start,
            end_date=collection.coverage.requested_end,
        )
        if observed != tuple(session.session_date for session in expected):
            raise ValueError("source_market_calendar_lineage_mismatch")
        return expected

    def _listing_for_symbol(self, symbol: str, *, effective_date: date) -> str | None:
        candidates = {
            listing_id
            for listing_id, alias in self._aliases_by_symbol.get(symbol, ())
            if FinMindSourceCollector._alias_active(alias, session_date=effective_date)
        }
        if len(candidates) != 1:
            return None
        return candidates.pop()

    @staticmethod
    def _intervals_overlap(
        first: ExternalSecurityAlias,
        second: ExternalSecurityAlias,
    ) -> bool:
        return (first.valid_from or date.min) <= (second.valid_to or date.max) and (
            second.valid_from or date.min
        ) <= (first.valid_to or date.max)

    @staticmethod
    def _decimal(value: object) -> Decimal:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result

    @staticmethod
    def _interval_overlaps_partition(
        alias: ExternalSecurityAlias,
        *,
        start_date: date,
        end_date: date,
    ) -> bool:
        return (alias.valid_from is None or alias.valid_from <= end_date) and (
            alias.valid_to is None or alias.valid_to >= start_date
        )


class FinMindPriceSourceAdapter(CollectorDecoderPriceSourceAdapter):
    pass


class FinMindCredentialValidator:
    source_access_mode: SourceAccessMode = "live_provider"

    def __init__(self, transport: ProviderHttpTransport) -> None:
        self._transport = transport

    def validate(
        self,
        credential_fields: Mapping[str, str],
    ) -> CredentialValidationResult:
        if set(credential_fields) != {"token"} or not credential_fields.get("token"):
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_fields_invalid",
            )
        token = credential_fields["token"]
        response = self._transport.send(
            ProviderHttpRequest(
                method="GET",
                url=FINMIND_DATA_URL,
                query={
                    "data_id": "2330",
                    "dataset": FINMIND_PRICE_DISTRIBUTION.distribution_id,
                    "end_date": "2024-01-03",
                    "start_date": "2024-01-03",
                },
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
        )
        if response.status_code == 401:
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_authentication_failed",
                evidence=CredentialValidationEvidence(authentication_status="failed"),
            )
        if response.status_code in {402, 429}:
            return self._contract_failure("source_contract_rate_limited")
        if response.status_code == 403:
            return self._contract_failure("source_contract_forbidden")
        if response.status_code != 200:
            return self._contract_failure("source_contract_unavailable")
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        rows = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != 200
            or not isinstance(rows, list)
            or not rows
            or not all(self._valid_price_row(row) for row in rows)
            or self._contains_token(payload, token=token)
        ):
            return CredentialValidationResult(
                readiness="valid",
                reason_code="source_credential_valid",
                evidence=CredentialValidationEvidence(authentication_status="passed"),
                source_contract_assessment=SourceContractAssessment(
                    contract_id=FINMIND_CREDENTIAL_PROBE_CONTRACT_ID,
                    live_validation="failed",
                    source_contract_reason_code="source_contract_schema_invalid",
                ),
            )
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
            source_contract_assessment=SourceContractAssessment(
                contract_id=FINMIND_CREDENTIAL_PROBE_CONTRACT_ID,
                live_validation="passed",
                ticker_count=1,
                datasets=(FINMIND_PRICE_DISTRIBUTION.distribution_id,),
            ),
        )

    @staticmethod
    def _valid_price_row(value: object) -> bool:
        if not isinstance(value, dict) or not all(
            isinstance(value.get(field), str | int | float)
            for field in (
                "stock_id",
                "date",
                "open",
                "max",
                "min",
                "close",
                "Trading_Volume",
            )
        ):
            return False
        return _valid_finmind_price_values(value) is not None

    @classmethod
    def _contains_token(cls, value: object, *, token: str) -> bool:
        if isinstance(value, str):
            return token in value
        if isinstance(value, Mapping):
            return any(
                cls._contains_token(item, token=token) for pair in value.items() for item in pair
            )
        if isinstance(value, list):
            return any(cls._contains_token(item, token=token) for item in value)
        return False

    @staticmethod
    def _contract_failure(reason_code: str) -> CredentialValidationResult:
        return CredentialValidationResult(
            readiness="configured",
            reason_code="source_credential_validation_inconclusive",
            source_contract_assessment=SourceContractAssessment(
                contract_id=FINMIND_CREDENTIAL_PROBE_CONTRACT_ID,
                live_validation="failed",
                source_contract_reason_code=reason_code,
            ),
        )


class FinMindLiveContractValidator:
    source_access_mode: SourceAccessMode = "live_provider"
    _CURRENT_LISTING_PRICE_PROBE_DATE = date(2025, 12, 11)

    def __init__(self, transport: ProviderHttpTransport) -> None:
        self._transport = transport

    def validate(
        self,
        credential_fields: Mapping[str, str],
    ) -> CredentialValidationResult:
        if set(credential_fields) != {"token"} or not credential_fields.get("token"):
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_fields_invalid",
            )
        token = credential_fields["token"]
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        from stock_forecasting.data_supply import load_taiwan_stock_pool_manifest

        manifest = load_taiwan_stock_pool_manifest()
        authentication_confirmed = False
        for listing in manifest.listings:
            probe_end = (
                listing.external_aliases[-1].valid_to or self._CURRENT_LISTING_PRICE_PROBE_DATE
            )
            probe_start = (
                probe_end - timedelta(days=20)
                if listing.external_aliases[-1].valid_to is not None
                else probe_end
            )
            rows_or_failure = self._request_rows(
                dataset=FINMIND_PRICE_DISTRIBUTION.distribution_id,
                query={
                    "data_id": listing.external_security_code,
                    "start_date": probe_start.isoformat(),
                    "end_date": probe_end.isoformat(),
                },
                headers=headers,
                token=token,
                authentication_confirmed=authentication_confirmed,
            )
            if isinstance(rows_or_failure, CredentialValidationResult):
                return rows_or_failure
            authentication_confirmed = True
            if not any(
                FinMindCredentialValidator._valid_price_row(row)
                and row.get("stock_id") == listing.external_security_code
                and isinstance(row.get("date"), str)
                and probe_start.isoformat() <= cast(str, row["date"]) <= probe_end.isoformat()
                for row in rows_or_failure
            ):
                return self._contract_failure("source_contract_schema_invalid")
        trading_dates = self._request_rows(
            dataset=FINMIND_TRADING_DATE_DISTRIBUTION.distribution_id,
            query={},
            headers=headers,
            token=token,
            authentication_confirmed=authentication_confirmed,
        )
        if isinstance(trading_dates, CredentialValidationResult):
            return trading_dates
        if not any(row.get("date") == "2024-01-03" for row in trading_dates):
            return self._contract_failure("source_contract_schema_invalid")
        dividend_results = self._request_rows(
            dataset=FINMIND_DIVIDEND_RESULT_DISTRIBUTION.distribution_id,
            query={
                "data_id": "2330",
                "start_date": "2025-12-11",
                "end_date": "2025-12-11",
            },
            headers=headers,
            token=token,
            authentication_confirmed=authentication_confirmed,
        )
        if isinstance(dividend_results, CredentialValidationResult):
            return dividend_results
        if not any(
            row.get("stock_id") == "2330"
            and row.get("date") == "2025-12-11"
            and row.get("stock_or_cache_dividend") == "息"
            and self._positive_number(row.get("stock_and_cache_dividend"))
            for row in dividend_results
        ):
            return self._contract_failure("source_contract_schema_invalid")
        delistings = self._request_rows(
            dataset=FINMIND_DELISTING_DISTRIBUTION.distribution_id,
            query={},
            headers=headers,
            token=token,
            authentication_confirmed=authentication_confirmed,
        )
        if isinstance(delistings, CredentialValidationResult):
            return delistings
        if not any(
            row.get("stock_id") == "2448" and row.get("date") == "2021-01-06" for row in delistings
        ):
            return self._contract_failure("source_contract_schema_invalid")
        split_prices = self._request_rows(
            dataset=FINMIND_SPLIT_PRICE_DISTRIBUTION.distribution_id,
            query={},
            headers=headers,
            token=token,
            authentication_confirmed=authentication_confirmed,
        )
        if isinstance(split_prices, CredentialValidationResult):
            return split_prices
        if not any(
            row.get("stock_id") == "0050"
            and row.get("date") == "2025-06-18"
            and row.get("type") == "分割"
            and self._positive_number(row.get("before_price"))
            and self._positive_number(row.get("after_price"))
            for row in split_prices
        ):
            return self._contract_failure("source_contract_schema_invalid")
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
            source_contract_assessment=SourceContractAssessment(
                contract_id=FINMIND_LIVE_VALIDATION_CONTRACT_ID,
                live_validation="passed",
                ticker_count=len(manifest.listings),
                symbol_lifecycle_probe="passed",
                universe_manifest_id=manifest.manifest_id,
                reference_graph_version_id=manifest.selection_evidence_version,
                listing_ids=tuple(listing.listing_id for listing in manifest.listings),
                datasets=tuple(
                    distribution.distribution_id for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
                ),
            ),
        )

    def _request_rows(
        self,
        *,
        dataset: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        token: str,
        authentication_confirmed: bool,
    ) -> list[dict[str, object]] | CredentialValidationResult:
        response = self._transport.send(
            ProviderHttpRequest(
                method="GET",
                url=FINMIND_DATA_URL,
                query={"dataset": dataset, **query},
                headers=headers,
            )
        )
        if response.status_code == 401:
            return CredentialValidationResult(
                readiness="validation_failed",
                reason_code="source_credential_authentication_failed",
                evidence=CredentialValidationEvidence(authentication_status="failed"),
            )
        if response.status_code in {402, 429}:
            return self._probe_failure(
                "source_contract_rate_limited",
                authentication_confirmed=authentication_confirmed,
            )
        if response.status_code == 403:
            return self._probe_failure(
                "source_contract_forbidden",
                authentication_confirmed=authentication_confirmed,
            )
        if response.status_code != 200:
            return self._probe_failure(
                "source_contract_unavailable",
                authentication_confirmed=authentication_confirmed,
            )
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        rows = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != 200
            or not isinstance(rows, list)
            or not all(isinstance(row, dict) for row in rows)
            or FinMindCredentialValidator._contains_token(payload, token=token)
        ):
            return self._probe_failure(
                "source_contract_schema_invalid",
                authentication_confirmed=authentication_confirmed,
            )
        return cast(list[dict[str, object]], rows)

    @staticmethod
    def _positive_number(value: object) -> bool:
        if isinstance(value, bool):
            return False
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return False
        return number.is_finite() and number > 0

    @staticmethod
    def _probe_failure(
        reason_code: str,
        *,
        authentication_confirmed: bool,
    ) -> CredentialValidationResult:
        if authentication_confirmed:
            return FinMindLiveContractValidator._contract_failure(reason_code)
        return CredentialValidationResult(
            readiness="configured",
            reason_code="source_credential_validation_inconclusive",
            source_contract_assessment=SourceContractAssessment(
                contract_id=FINMIND_LIVE_VALIDATION_CONTRACT_ID,
                live_validation="failed",
                source_contract_reason_code=reason_code,
            ),
        )

    @staticmethod
    def _contract_failure(reason_code: str) -> CredentialValidationResult:
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
            source_contract_assessment=SourceContractAssessment(
                contract_id=FINMIND_LIVE_VALIDATION_CONTRACT_ID,
                live_validation="failed",
                source_contract_reason_code=reason_code,
            ),
        )
