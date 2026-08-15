from __future__ import annotations

from uuid import UUID

from stock_forecasting.data_supply import load_taiwan_stock_pool_manifest


def test_taiwan_segment_declares_versioned_ten_listing_qualification_contract() -> None:
    manifest = load_taiwan_stock_pool_manifest()

    assert manifest.manifest_id == "p2-stock-pool-10-plus-10-v1"
    assert manifest.market_targets == {"XTAI": 10, "US": 10}
    assert len(manifest.listings) == 10
    assert {listing.market for listing in manifest.listings} == {"XTAI"}
    assert {listing.security_kind for listing in manifest.listings} == {"ordinary_share"}
    assert len({listing.listing_id for listing in manifest.listings}) == 10
    assert all(str(UUID(listing.listing_id)) == listing.listing_id for listing in manifest.listings)

    covered_cases = {
        coverage_case for listing in manifest.listings for coverage_case in listing.coverage_cases
    }
    assert covered_cases == {
        "company_action",
        "historical_delisting",
        "ordinary_share",
        "suspension",
        "ticker_change",
    }
    assert manifest.market_calendar_cases == frozenset({"half_day_session"})
    assert manifest.evidence_status == "qualification_candidate"
    assert manifest.formally_qualified is False
