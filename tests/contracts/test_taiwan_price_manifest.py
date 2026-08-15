from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from stock_forecasting.data_supply import (
    ExternalSecurityAlias,
    StockPoolListing,
    load_taiwan_stock_pool_manifest,
)


def test_stock_pool_listing_can_preserve_a_time_bounded_external_code_change() -> None:
    listing = StockPoolListing(
        listing_id="20000000-0000-4000-8000-000000000001",
        issuer_id="20000000-0000-4000-8000-000000000002",
        security_id="20000000-0000-4000-8000-000000000003",
        market="XTAI",
        security_kind="ordinary_share",
        external_security_code="5678",
        external_aliases=(
            ExternalSecurityAlias(
                security_code="1234",
                security_name="歷史名稱",
                valid_from=date(2010, 1, 1),
                valid_to=date(2020, 6, 30),
            ),
            ExternalSecurityAlias(
                security_code="5678",
                security_name="現行名稱",
                valid_from=date(2020, 7, 1),
                valid_to=None,
            ),
        ),
        selection_evidence_ids=("official-code-change",),
        coverage_cases=frozenset({"ordinary_share", "ticker_change"}),
    )

    assert [alias.security_code for alias in listing.external_aliases] == ["1234", "5678"]
    overlapping_current_alias = replace(
        listing.external_aliases[1],
        valid_from=date(2020, 6, 30),
    )
    with pytest.raises(ValueError, match="taiwan_stock_pool_external_alias_ambiguous"):
        replace(
            listing,
            external_aliases=(listing.external_aliases[0], overlapping_current_alias),
        )


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
        "name_history",
        "ordinary_share",
        "suspension",
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

    assert manifest.selection_evidence_version == "twse-10-selection-evidence-v2"
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
        "name_history",
        "ordinary_share",
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
    historical_ordinary_share = available_evidence["twse-2448-common-share"]
    assert historical_ordinary_share.evidence_kind == "ordinary_share_at_delisting"
    assert historical_ordinary_share.effective_from == date(2021, 1, 6)
    assert historical_ordinary_share.effective_to == date(2021, 1, 6)
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


def test_taiwan_suspension_evidence_preserves_the_resume_boundary_and_listing_subject() -> None:
    manifest = load_taiwan_stock_pool_manifest()
    listing_by_code = {listing.external_security_code: listing for listing in manifest.listings}
    evidence_by_id = {evidence.evidence_id: evidence for evidence in manifest.evidence}

    suspension = evidence_by_id["twse-2317-trading-halt"]
    resume = evidence_by_id["twse-2317-trading-resume"]

    assert suspension.evidence_kind == "trading_suspension"
    assert suspension.effective_from == date(2025, 7, 30)
    assert suspension.effective_to == date(2025, 7, 30)
    assert [
        (subject.listing_id, subject.external_security_code) for subject in suspension.subjects
    ] == [(listing_by_code["2317"].listing_id, "2317")]
    assert resume.evidence_kind == "trading_resume"
    assert resume.source_content_sha256 == (
        "dd326e7841732c3e350092d417eefbd588a9682cb30ca452af7af3eccc0efc9c"
    )
    assert resume.effective_from == date(2025, 7, 31)
    assert resume.effective_to == date(2025, 7, 31)
    assert [
        (subject.listing_id, subject.external_security_code) for subject in resume.subjects
    ] == [(listing_by_code["2317"].listing_id, "2317")]


def test_taiwan_manifest_rejects_selection_evidence_from_another_listing() -> None:
    manifest = load_taiwan_stock_pool_manifest()
    listing_by_code = {listing.external_security_code: listing for listing in manifest.listings}
    original = listing_by_code["3714"]
    wrong_subject = replace(
        original,
        selection_evidence_ids=(
            "twse-3714-common-share",
            "twse-2330-cash-dividend",
        ),
    )

    with pytest.raises(ValueError, match="taiwan_stock_pool_listing_evidence_subject_mismatch"):
        replace(
            manifest,
            listings=tuple(
                wrong_subject if listing.listing_id == original.listing_id else listing
                for listing in manifest.listings
            ),
        )


def test_taiwan_manifest_rejects_unrelated_listing_relationship_evidence() -> None:
    manifest = load_taiwan_stock_pool_manifest()
    unrelated = replace(
        manifest.listing_relationships[0],
        evidence_id="twse-2330-cash-dividend",
    )

    with pytest.raises(ValueError, match="taiwan_stock_pool_listing_relationship_evidence_invalid"):
        replace(manifest, listing_relationships=(unrelated,))


def test_taiwan_manifest_rejects_unrelated_calendar_evidence() -> None:
    manifest = load_taiwan_stock_pool_manifest()
    unrelated = replace(
        manifest.market_calendar_evidence,
        evidence_id="twse-2330-cash-dividend",
    )

    with pytest.raises(ValueError, match="taiwan_stock_pool_calendar_evidence_invalid"):
        replace(manifest, market_calendar_evidence=unrelated)


def test_taiwan_selection_retrieval_receipts_reject_content_digest_corruption() -> None:
    manifest = load_taiwan_stock_pool_manifest()

    assert all(
        evidence.retrieval_receipt_id.startswith("sha256:") for evidence in manifest.evidence
    )
    with pytest.raises(ValueError, match="taiwan_stock_pool_evidence_receipt_mismatch"):
        replace(manifest.evidence[0], source_content_sha256="0" * 64)


def test_taiwan_selection_evidence_rejects_a_mismatched_assertion_kind() -> None:
    manifest = load_taiwan_stock_pool_manifest()
    dividend = next(
        evidence
        for evidence in manifest.evidence
        if evidence.evidence_id == "twse-2330-cash-dividend"
    )

    with pytest.raises(ValueError, match="taiwan_stock_pool_evidence_assertion_invalid"):
        replace(dividend, evidence_kind="display_name_change")
