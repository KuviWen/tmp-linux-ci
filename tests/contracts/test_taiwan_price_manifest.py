from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from importlib.resources import files
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest

import stock_forecasting.data_supply as data_supply
from stock_forecasting.data_supply import (
    ExternalSecurityAlias,
    StockPoolListing,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.finmind_provider_contract import (
    FINMIND_CREDENTIAL_PROBE_CONTRACT_ID,
    FINMIND_LIVE_VALIDATION_CONTRACT_ID,
    FINMIND_PROVIDER_DISTRIBUTIONS,
    FINMIND_PROVIDER_ID,
    FINMIND_REQUIRED_BUNDLE_DISTRIBUTIONS,
    FINMIND_VALIDATION_CONTRACT_IDS,
    FINMIND_VALIDATION_DATASET_IDS,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository


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
    assert manifest.historical_source_id == "twse-open-data-observed-history"
    assert manifest.source_basis.as_payload() == {
        "source_basis_id": "TWSE-OGDL-OPEN-DATA-01",
        "basis_type": "open_data_terms",
        "license_id": "OGDL-1.0",
        "terms_url": "https://data.gov.tw/license",
        "attribution": "政府資料開放授權條款－第1版（OGDL 1.0）",
        "account_required": False,
        "application_required": False,
        "fee_required": False,
        "history_strategy": "prospective_platform_observation",
        "qualification_status": "documented_not_archived",
        "datasets": [
            {
                "dataset_id": "11549",
                "dataset_url": "https://data.gov.tw/dataset/11549",
                "distribution_url": (
                    "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data"
                ),
                "qualification_scope": "current_eod",
            },
            {
                "dataset_id": "89748",
                "dataset_url": "https://data.gov.tw/dataset/89748",
                "distribution_url": (
                    "https://www.twse.com.tw/exchangeReport/TWT48U_ALL?response=open_data"
                ),
                "qualification_scope": "current_corporate_action",
            },
            {
                "dataset_id": "31612",
                "dataset_url": "https://data.gov.tw/dataset/31612",
                "distribution_url": ("https://mopsfin.twse.com.tw/opendata/t187ap45_L.csv"),
                "qualification_scope": "current_dividend",
            },
            {
                "dataset_id": "18419",
                "dataset_url": "https://data.gov.tw/dataset/18419",
                "distribution_url": ("https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv"),
                "qualification_scope": "current_listing_reference",
            },
            {
                "dataset_id": "11542",
                "dataset_url": "https://data.gov.tw/dataset/11542",
                "distribution_url": (
                    "https://www.twse.com.tw/company/newlisting?response=open_data"
                ),
                "qualification_scope": "selection_support",
            },
        ],
    }
    assert manifest.formal_qualification_artifact_id is None
    assert manifest.historical_availability_claim_id is None
    assert manifest.formally_qualified is False

    finmind_path = manifest.for_authenticated_source_path()
    assert finmind_path.current_source_id == "finmind-taiwan-stock-price"
    assert finmind_path.historical_source_id == "finmind-taiwan-stock-price"
    assert finmind_path.source_path_id == "taiwan-finmind-free-v1"


def test_taiwan_manifest_declares_finmind_free_authenticated_candidate_bundle() -> None:
    manifest = load_taiwan_stock_pool_manifest()

    source_basis = manifest.authenticated_source_basis
    assert source_basis.source_basis_id == "FINMIND-FREE-TAIWAN-MARKET-DATA-01"
    assert source_basis.provider_id == "finmind-free-api"
    assert source_basis.plan_id == "free-token-2026-08-16"
    assert source_basis.principal_classification == "individual_or_internal_group"
    assert source_basis.credential_kind == "bearer_token"
    assert source_basis.account_required is True
    assert source_basis.fee_required is False
    assert source_basis.terms_url == "https://finmind.github.io/en/PrivacyPolicy/"
    assert source_basis.terms_content_sha256 is None
    assert source_basis.qualification_status == "candidate_terms_not_archived"
    members = {member.dataset_id: member for member in source_basis.members}
    assert set(members) == {
        "TaiwanStockDelisting",
        "TaiwanStockDividendResult",
        "TaiwanStockPrice",
        "TaiwanStockSplitPrice",
        "TaiwanStockTradingDate",
    }
    assert members["TaiwanStockPrice"].price_semantics == "unadjusted"
    assert all(member.provider_id == "finmind-free-api" for member in members.values())
    assert all(
        member.distribution_url == "https://api.finmindtrade.com/api/v4/data"
        for member in members.values()
    )
    assert all(member.allowed_uses == frozenset() for member in members.values())
    assert all(member.rights_status == "unverified" for member in members.values())
    assert {member.dataset_id for member in source_basis.supplemental_references} == {
        "TaiwanStockInfo"
    }
    assert manifest.formally_qualified is False


def test_authenticated_candidate_rejects_a_member_from_another_provider() -> None:
    basis = load_taiwan_stock_pool_manifest().authenticated_source_basis

    with pytest.raises(ValueError, match="zero_fee_authenticated_source_basis_invalid"):
        replace(
            basis,
            members=(
                replace(basis.members[0], provider_id="unexpected-provider"),
                *basis.members[1:],
            ),
        )


def test_zero_fee_bundle_member_rejects_qualified_without_archived_terms() -> None:
    member = load_taiwan_stock_pool_manifest().authenticated_source_basis.members[0]

    with pytest.raises(ValueError, match="zero_fee_source_bundle_member_invalid"):
        replace(member, qualification_status="qualified")  # type: ignore[arg-type]


def test_zero_fee_bundle_member_rejects_adjusted_price_semantics() -> None:
    member = load_taiwan_stock_pool_manifest().authenticated_source_basis.members[0]

    with pytest.raises(ValueError, match="zero_fee_source_bundle_member_invalid"):
        replace(member, price_semantics="adjusted")  # type: ignore[arg-type]


def test_taiwan_candidate_rejects_the_wrong_provider_or_credential_kind() -> None:
    manifest = load_taiwan_stock_pool_manifest()

    with pytest.raises(ValueError, match="taiwan_stock_pool_manifest_invalid"):
        replace(
            manifest,
            authenticated_source_basis=replace(
                manifest.authenticated_source_basis,
                credential_kind="api_key_pair",
            ),
        )
    with pytest.raises(ValueError, match="taiwan_stock_pool_manifest_invalid"):
        replace(
            manifest,
            authenticated_source_basis=replace(
                manifest.authenticated_source_basis,
                supplemental_references=(
                    replace(
                        manifest.authenticated_source_basis.supplemental_references[0],
                        provider_id="another-provider",
                    ),
                ),
            ),
        )


def test_taiwan_candidate_rejects_a_noncanonical_distribution_url() -> None:
    manifest = load_taiwan_stock_pool_manifest()
    basis = manifest.authenticated_source_basis

    with pytest.raises(ValueError, match="taiwan_stock_pool_manifest_invalid"):
        replace(
            manifest,
            authenticated_source_basis=replace(
                basis,
                members=(
                    replace(basis.members[0], distribution_url="https://evil.example/data"),
                    *basis.members[1:],
                ),
            ),
        )


def test_finmind_provider_contract_catalog_matches_taiwan_candidate_manifest() -> None:
    source_basis = load_taiwan_stock_pool_manifest().authenticated_source_basis

    assert FINMIND_PROVIDER_ID == "finmind-free-api"
    assert [
        (
            distribution.policy_dataset_id,
            distribution.distribution_id,
            distribution.distribution_url,
        )
        for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
    ] == [
        (
            "finmind-taiwan-stock-price",
            "TaiwanStockPrice",
            "https://api.finmindtrade.com/api/v4/data",
        ),
        (
            "finmind-taiwan-trading-date",
            "TaiwanStockTradingDate",
            "https://api.finmindtrade.com/api/v4/data",
        ),
        (
            "finmind-taiwan-dividend-result",
            "TaiwanStockDividendResult",
            "https://api.finmindtrade.com/api/v4/data",
        ),
        (
            "finmind-taiwan-delisting",
            "TaiwanStockDelisting",
            "https://api.finmindtrade.com/api/v4/data",
        ),
        (
            "finmind-taiwan-split-price",
            "TaiwanStockSplitPrice",
            "https://api.finmindtrade.com/api/v4/data",
        ),
    ]
    assert source_basis.provider_id == FINMIND_PROVIDER_ID
    assert [member.dataset_id for member in source_basis.members] == [
        distribution.distribution_id for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
    ]
    assert FINMIND_PROVIDER_DISTRIBUTIONS[1:] == FINMIND_REQUIRED_BUNDLE_DISTRIBUTIONS
    assert (
        frozenset(
            {
                "TaiwanStockDelisting",
                "TaiwanStockDividendResult",
                "TaiwanStockPrice",
                "TaiwanStockSplitPrice",
                "TaiwanStockTradingDate",
            }
        )
        == FINMIND_VALIDATION_DATASET_IDS
    )
    assert (
        frozenset({FINMIND_CREDENTIAL_PROBE_CONTRACT_ID, FINMIND_LIVE_VALIDATION_CONTRACT_ID})
        == FINMIND_VALIDATION_CONTRACT_IDS
    )


def test_taiwan_segment_binds_real_selection_and_calendar_cases_to_official_evidence() -> None:
    manifest = load_taiwan_stock_pool_manifest()

    assert manifest.selection_evidence_version == "twse-10-selection-evidence-v3"
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
    assert all(
        reference.publisher in {"TWSE", "issuer"} for reference in manifest.source_references
    )
    assert all(
        reference.source_url.startswith("https://") for reference in manifest.source_references
    )
    assert all(
        len(reference.observed_content_sha256) == 64
        and int(reference.observed_content_sha256, 16) >= 0
        for reference in manifest.source_references
    )
    assert all(
        reference.observed_on <= manifest.selection_as_of
        for reference in manifest.source_references
    )

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
    source_by_id = {
        reference.source_reference_id: reference for reference in manifest.source_references
    }

    assert suspension.evidence_kind == "trading_suspension"
    assert suspension.effective_from == date(2025, 7, 30)
    assert suspension.effective_to == date(2025, 7, 30)
    assert [
        (subject.listing_id, subject.external_security_code) for subject in suspension.subjects
    ] == [(listing_by_code["2317"].listing_id, "2317")]
    assert resume.evidence_kind == "trading_resume"
    assert source_by_id[resume.source_reference_id].observed_content_sha256 == (
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


def test_taiwan_selection_evidence_separates_one_source_acquisition_from_derived_assertions() -> (
    None
):
    manifest = load_taiwan_stock_pool_manifest()
    evidence_by_id = {evidence.evidence_id: evidence for evidence in manifest.evidence}
    shared_assertion_ids = {
        "twse-3714-common-share",
        "twse-2448-common-share",
        "twse-2448-3714-share-exchange",
        "twse-2448-delisting",
    }
    source_reference_ids = {
        evidence_by_id[evidence_id].source_reference_id for evidence_id in shared_assertion_ids
    }

    assert source_reference_ids == {"twse-2448-3714-pdf-acquisition"}
    source_by_id = {
        reference.source_reference_id: reference for reference in manifest.source_references
    }
    shared_source = source_by_id["twse-2448-3714-pdf-acquisition"]
    assert shared_source.observed_content_sha256 == (
        "c5170640828b61a12e4d9c5ca41666a42491c562ef60e3846be0dcf2d3fa7003"
    )
    assert shared_source.archival_status == "not_archived"
    assert shared_source.raw_object_id is None
    assert shared_source.retrieval_receipt_id is None


def test_verified_selection_source_archive_rehashes_object_repository_bytes(
    tmp_path: Path,
) -> None:
    manifest = load_taiwan_stock_pool_manifest()
    content = b"selection-source-archive-contract-fixture"
    checksum = hashlib.sha256(content).hexdigest()
    repository = FilesystemObjectRepository(tmp_path)
    object_ref = repository.put_verified(
        BytesIO(content),
        expected_checksum=checksum,
        metadata={"source": "contract-fixture"},
    )
    source = manifest.source_references[0]
    pending_receipt = replace(
        source,
        observed_content_sha256=checksum,
        archival_status="verified",
        acquired_at=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
        raw_object_id=object_ref.object_id,
        retrieval_receipt_id="pending",
    )
    verified_source = replace(
        pending_receipt,
        retrieval_receipt_id=pending_receipt.expected_retrieval_receipt_id,
    )
    verified_manifest = replace(
        manifest,
        source_references=(verified_source, *manifest.source_references[1:]),
    )

    future_pending_receipt = replace(
        verified_source,
        acquired_at=datetime(2026, 8, 16, 9, 0, tzinfo=UTC),
        retrieval_receipt_id="pending",
    )
    future_source = replace(
        future_pending_receipt,
        retrieval_receipt_id=future_pending_receipt.expected_retrieval_receipt_id,
    )
    with pytest.raises(ValueError, match="taiwan_stock_pool_future_source_reference"):
        replace(
            manifest,
            source_references=(future_source, *manifest.source_references[1:]),
        )

    verified_manifest.verify_source_archive(repository)
    assert verified_manifest.formally_qualified is False

    Path(object_ref.uri).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="taiwan_stock_pool_source_archive_invalid"):
        verified_manifest.verify_source_archive(repository)


def test_manifest_loader_requires_and_rehashes_verified_source_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_taiwan_stock_pool_manifest()
    source = manifest.source_references[0]
    content = b"loader-selection-source-archive-contract-fixture"
    checksum = hashlib.sha256(content).hexdigest()
    repository = FilesystemObjectRepository(tmp_path / "objects")
    acquired_at = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
    object_ref = repository.put_verified(
        BytesIO(content),
        expected_checksum=checksum,
        metadata={"source": "loader-contract-fixture"},
    )
    pending_receipt = replace(
        source,
        observed_content_sha256=checksum,
        archival_status="verified",
        acquired_at=acquired_at,
        raw_object_id=object_ref.object_id,
        retrieval_receipt_id="pending",
    )
    verified_source = replace(
        pending_receipt,
        retrieval_receipt_id=pending_receipt.expected_retrieval_receipt_id,
    )

    bundled_manifest = files("stock_forecasting").joinpath(
        "manifests/p2_taiwan_stock_pool_contract_v1.json"
    )
    payload = json.loads(bundled_manifest.read_text(encoding="utf-8"))
    source_payload = next(
        item
        for item in payload["selection_source_references"]
        if item["source_reference_id"] == source.source_reference_id
    )
    source_payload.update(
        {
            "observed_content_sha256": verified_source.observed_content_sha256,
            "archival_status": verified_source.archival_status,
            "acquired_at": acquired_at.isoformat(),
            "raw_object_id": verified_source.raw_object_id,
            "retrieval_receipt_id": verified_source.retrieval_receipt_id,
        }
    )
    resource_root = tmp_path / "package"
    manifest_path = resource_root / "manifests" / "p2_taiwan_stock_pool_contract_v1.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(data_supply, "files", lambda _package: resource_root)

    with pytest.raises(ValueError, match="taiwan_stock_pool_source_archive_invalid"):
        load_taiwan_stock_pool_manifest()

    loaded = load_taiwan_stock_pool_manifest(repository)
    assert loaded.source_references[0] == verified_source

    Path(object_ref.uri).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="taiwan_stock_pool_source_archive_invalid"):
        load_taiwan_stock_pool_manifest(repository)


def test_taiwan_selection_evidence_rejects_a_mismatched_assertion_kind() -> None:
    manifest = load_taiwan_stock_pool_manifest()
    dividend = next(
        evidence
        for evidence in manifest.evidence
        if evidence.evidence_id == "twse-2330-cash-dividend"
    )

    with pytest.raises(ValueError, match="taiwan_stock_pool_evidence_assertion_invalid"):
        replace(dividend, evidence_kind="display_name_change")
