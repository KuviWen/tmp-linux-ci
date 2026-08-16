from __future__ import annotations

from datetime import date
from uuid import UUID

from stock_forecasting.alpaca_provider_contract import (
    ALPACA_PROVIDER_DISTRIBUTIONS,
    ALPACA_PROVIDER_ID,
)
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest


def test_us_segment_declares_ten_permanent_listing_identities_and_required_cases() -> None:
    manifest = load_us_stock_pool_manifest()

    assert manifest.manifest_id == "p2-stock-pool-10-plus-10-v1"
    assert manifest.market_targets == {"XTAI": 10, "US": 10}
    assert len(manifest.listings) == 10
    assert {listing.market for listing in manifest.listings} == {"XNAS", "XNYS"}
    assert len({listing.listing_id for listing in manifest.listings}) == 10
    assert len({listing.security_id for listing in manifest.listings}) == 10
    assert all(str(UUID(listing.listing_id)) == listing.listing_id for listing in manifest.listings)
    assert all(str(UUID(listing.issuer_id)) == listing.issuer_id for listing in manifest.listings)
    assert all(
        str(UUID(listing.security_id)) == listing.security_id for listing in manifest.listings
    )
    assert all(
        listing.external_security_code
        not in {listing.listing_id, listing.issuer_id, listing.security_id}
        for listing in manifest.listings
    )

    covered_cases = {
        coverage_case for listing in manifest.listings for coverage_case in listing.coverage_cases
    } | set(manifest.market_calendar_cases)
    assert covered_cases >= {
        "ordinary_share",
        "share_class",
        "adr",
        "ticker_change",
        "company_action",
        "half_day_session",
        "suspension",
        "historical_delisting",
    }

    listing_by_code = {listing.external_security_code: listing for listing in manifest.listings}
    assert listing_by_code["TSM"].security_kind == "adr"
    assert listing_by_code["BRK.B"].coverage_cases >= {
        "ordinary_share",
        "share_class",
    }
    assert listing_by_code["GOOG"].issuer_id == listing_by_code["GOOGL"].issuer_id
    assert listing_by_code["GOOG"].security_id != listing_by_code["GOOGL"].security_id
    assert [alias.security_code for alias in listing_by_code["META"].external_aliases] == [
        "FB",
        "META",
    ]
    assert listing_by_code["META"].external_aliases[0].valid_to == date(2022, 6, 8)
    assert listing_by_code["META"].external_aliases[1].valid_from == date(2022, 6, 9)
    assert listing_by_code["SIVB"].external_aliases[-1].valid_to == date(2023, 3, 28)

    assert manifest.market_calendar_evidence.session_date == date(2024, 11, 29)
    assert manifest.market_calendar_evidence.close_time == "13:00"
    assert manifest.market_calendar_evidence.source_url == (
        "https://www.nyse.com/markets/hours-calendars"
    )


def test_us_manifest_preserves_each_zero_fee_source_bundle_member_and_gap() -> None:
    manifest = load_us_stock_pool_manifest()

    assert manifest.source_basis.source_basis_id == "ALPACA-BASIC-US-MARKET-DATA-01"
    assert manifest.source_basis.provider_id == "alpaca-market-data-basic"
    assert manifest.source_basis.plan_id == "basic-2026-08-15"
    assert manifest.source_basis.credential_kind == "api_key_pair"
    assert manifest.source_basis.account_required is True
    assert manifest.source_basis.fee_required is False
    assert manifest.source_basis.terms_content_sha256 == (
        "2dc774d4aeeafbe4c7f0565e7842d932bc8bc10488af805fce43b8734e7b9859"
    )
    assert manifest.source_basis.qualification_status == "candidate_terms_not_archived"
    members = {member.dataset_id: member for member in manifest.source_basis.members}
    assert set(members) == {
        "alpaca-us-stock-bars-v2",
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-trading-calendar-v2",
    }
    assert members["alpaca-us-stock-bars-v2"].schema_version == "us-unadjusted-eod-v1"
    assert members["alpaca-us-stock-bars-v2"].price_semantics == "unadjusted"
    assert all(
        members[dataset_id].materialization_role == "required_observation"
        for dataset_id in {
            "alpaca-us-stock-bars-v2",
            "alpaca-us-corporate-actions-v1",
            "alpaca-us-trading-calendar-v2",
        }
    )
    assert members["alpaca-us-corporate-actions-v1"].known_gaps == (
        "provider_creation_time_not_guaranteed",
    )
    supplemental = {
        member.dataset_id: member for member in manifest.source_basis.supplemental_references
    }
    assert set(supplemental) == {"nasdaq-current-symbol-directory"}
    assert supplemental["nasdaq-current-symbol-directory"].provider_id == (
        "nasdaq-trader-public-reference"
    )
    assert supplemental["nasdaq-current-symbol-directory"].qualification_status == (
        "candidate_scope_limited"
    )
    assert (
        supplemental["nasdaq-current-symbol-directory"].materialization_role
        == "supplemental_qualification_reference"
    )
    assert all(member.distribution_url.startswith("https://") for member in members.values())
    assert all(
        member.rights_status == "unverified"
        for member in (*members.values(), *supplemental.values())
    )
    assert all(
        member.allowed_uses == frozenset() for member in (*members.values(), *supplemental.values())
    )
    assert all(
        member.attribution_requirement == "unresolved"
        for member in (*members.values(), *supplemental.values())
    )
    assert all(
        member.retention_limit == "unresolved"
        for member in (*members.values(), *supplemental.values())
    )
    assert all(
        member.deletion_requirement == "unresolved"
        for member in (*members.values(), *supplemental.values())
    )
    assert all(
        member.effective_from is None for member in (*members.values(), *supplemental.values())
    )
    assert all(
        member.effective_to is None for member in (*members.values(), *supplemental.values())
    )
    assert manifest.formal_qualification_artifact_id is None
    assert manifest.historical_availability_claim_id is None
    assert manifest.formally_qualified is False


def test_alpaca_provider_contract_catalog_matches_the_source_manifest() -> None:
    manifest = load_us_stock_pool_manifest()

    assert ALPACA_PROVIDER_ID == "alpaca-market-data-basic"
    assert [
        (
            distribution.policy_dataset_id,
            distribution.distribution_id,
            distribution.distribution_url,
        )
        for distribution in ALPACA_PROVIDER_DISTRIBUTIONS
    ] == [
        (
            "alpaca-us-stock-bars",
            "alpaca-us-stock-bars-v2",
            "https://data.alpaca.markets/v2/stocks/bars",
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
    ]
    assert manifest.source_basis.provider_id == ALPACA_PROVIDER_ID
    assert [
        (member.dataset_id, member.distribution_url) for member in manifest.source_basis.members
    ] == [
        (distribution.distribution_id, distribution.distribution_url)
        for distribution in ALPACA_PROVIDER_DISTRIBUTIONS
    ]
