from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import cast

from stock_forecasting.data_supply import (
    ExternalSecurityAlias,
    ListingLifecycleRecord,
    MarketSessionRecord,
)


@dataclass(frozen=True)
class MarketDataReferenceListing:
    listing_id: str
    aliases: tuple[ExternalSecurityAlias, ...]
    lifecycle: tuple[ListingLifecycleRecord, ...]

    def __post_init__(self) -> None:
        if (
            not self.listing_id
            or not self.aliases
            or any(event.listing_id != self.listing_id for event in self.lifecycle)
        ):
            raise ValueError("market_data_reference_listing_invalid")


@dataclass(frozen=True)
class MarketDataCompanyActionExpectation:
    action_id: str
    listing_id: str
    effective_date: date

    def __post_init__(self) -> None:
        if not self.action_id or not self.listing_id:
            raise ValueError("market_data_company_action_expectation_invalid")


@dataclass(frozen=True)
class MarketDataReferenceGraph:
    version_id: str
    listings: tuple[MarketDataReferenceListing, ...]
    company_action_expectations: tuple[MarketDataCompanyActionExpectation, ...]
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
            raise ValueError("market_data_reference_graph_invalid")

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
            raise ValueError("market_data_reference_graph_listing_missing")
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

    def listing(self, listing_id: str) -> MarketDataReferenceListing:
        try:
            return next(listing for listing in self.listings if listing.listing_id == listing_id)
        except StopIteration as error:
            raise ValueError("market_data_reference_graph_listing_missing") from error

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


@dataclass(frozen=True)
class MarketCalendarEvidence:
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
            raise ValueError("market_calendar_evidence_invalid")

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
