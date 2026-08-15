from __future__ import annotations

from datetime import date
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
    assert manifest.current_source_id == "twse-open-data-current"
    assert manifest.historical_source_id == "twse-contracted-history"
    assert manifest.formal_qualification_artifact_id is None
    assert manifest.historical_availability_claim_id is None
    assert manifest.formally_qualified is False


def test_taiwan_segment_binds_real_selection_and_calendar_cases_to_official_evidence() -> None:
    manifest = load_taiwan_stock_pool_manifest()

    assert manifest.selection_evidence_version == "twse-10-selection-evidence-v1"
    assert manifest.selection_as_of == date(2026, 8, 15)
    assert {listing.external_security_code for listing in manifest.listings} == {
        "2317",
        "2308",
        "2330",
        "2382",
        "2454",
        "2448",
        "2881",
        "2887",
        "3711",
        "3714",
    }
    assert len({listing.issuer_id for listing in manifest.listings}) == 10
    assert len({listing.security_id for listing in manifest.listings}) == 10
    assert all(str(UUID(listing.issuer_id)) == listing.issuer_id for listing in manifest.listings)
    assert all(
        str(UUID(listing.security_id)) == listing.security_id for listing in manifest.listings
    )
    assert all(
        listing.external_security_code
        not in {listing.issuer_id, listing.security_id, listing.listing_id}
        for listing in manifest.listings
    )

    listing_by_code = {listing.external_security_code: listing for listing in manifest.listings}
    assert listing_by_code["2330"].coverage_cases >= {
        "ordinary_share",
        "company_action",
    }
    assert listing_by_code["2317"].coverage_cases >= {"ordinary_share", "suspension"}
    assert listing_by_code["2887"].coverage_cases >= {
        "ordinary_share",
        "ticker_change",
    }
    assert [
        (alias.security_name, alias.valid_from, alias.valid_to)
        for alias in listing_by_code["2887"].external_aliases
    ] == [
        ("台新金", date(2002, 2, 18), date(2025, 7, 23)),
        ("台新新光金", date(2025, 7, 24), None),
    ]
    assert {
        code
        for code, listing in listing_by_code.items()
        if "historical_delisting" in listing.coverage_cases
    } == {"2448"}

    transition = manifest.listing_relationships[0]
    assert len(manifest.listing_relationships) == 1
    assert transition.relationship_type == "share_exchange_successor"
    assert transition.effective_on == date(2021, 1, 6)
    assert transition.predecessor_listing_id == listing_by_code["2448"].listing_id
    assert transition.successor_listing_id == listing_by_code["3714"].listing_id
    assert transition.predecessor_listing_id != transition.successor_listing_id

    available_evidence = {evidence.evidence_id: evidence for evidence in manifest.evidence}
    assert available_evidence
    for listing in manifest.listings:
        assert listing.selection_evidence_ids
        assert set(listing.selection_evidence_ids) <= available_evidence.keys()
        evidenced_cases = {
            available_evidence[evidence_id].coverage_case
            for evidence_id in listing.selection_evidence_ids
        }
        assert listing.coverage_cases <= evidenced_cases
    assert all(evidence.publisher in {"TWSE", "issuer"} for evidence in manifest.evidence)
    assert all(evidence.source_url.startswith("https://") for evidence in manifest.evidence)
    assert all(
        len(evidence.source_content_sha256) == 64 and int(evidence.source_content_sha256, 16) >= 0
        for evidence in manifest.evidence
    )
    assert all(evidence.retrieved_on <= manifest.selection_as_of for evidence in manifest.evidence)

    half_day = manifest.market_calendar_evidence
    assert half_day.coverage_case == "half_day_session"
    assert half_day.regime == "historical_saturday_shortened_session"
    assert half_day.valid_from == date(1998, 4, 4)
    assert half_day.valid_to == date(2000, 12, 30)
    assert half_day.modern_training_window_applicability == "not_applicable"
    assert half_day.evidence_id in available_evidence
    assert transition.evidence_id in available_evidence
