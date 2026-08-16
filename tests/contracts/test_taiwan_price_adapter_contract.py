from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from stock_forecasting.data_supply import (
    CanonicalPriceRow,
    CollectedSourcePartition,
    DecodedSourcePartition,
    SourceCollectionCoverage,
    SourcePartitionRequest,
    TaiwanPriceSourceAdapter,
)


class LiteralCollector:
    def __init__(self, collected: CollectedSourcePartition) -> None:
        self.collected = collected
        self.requests: list[SourcePartitionRequest] = []

    def collect(self, request: SourcePartitionRequest) -> CollectedSourcePartition:
        self.requests.append(request)
        return self.collected


class LiteralDecoder:
    def __init__(self, decoded: DecodedSourcePartition) -> None:
        self.decoded = decoded
        self.collections: list[CollectedSourcePartition] = []

    def decode(self, collection: CollectedSourcePartition) -> DecodedSourcePartition:
        self.collections.append(collection)
        return self.decoded


@pytest.mark.parametrize("mode", ["current", "historical"])
def test_current_and_historical_sources_use_the_same_collector_decoder_contract(
    mode: str,
) -> None:
    request = SourcePartitionRequest(
        request_id=f"request-{mode}",
        trace_id="trace-p2-trace-tw-01",
        source_id=f"tw-price-{mode}",
        mode=mode,  # type: ignore[arg-type]
        listing_ids=("10000000-0000-4000-8000-000000000001",),
        start_date=date(2026, 8, 14),
        end_date=date(2026, 8, 14),
        expected_checkpoint="page:40",
        policy_decision_id="decision-allowed-001",
    )
    coverage = SourceCollectionCoverage(
        requested_start=date(2026, 8, 14),
        requested_end=date(2026, 8, 14),
        observed_start=date(2026, 8, 14),
        observed_end=date(2026, 8, 14),
        complete=True,
    )
    collection = CollectedSourcePartition(
        request_id=request.request_id,
        source_id=request.source_id,
        acquired_at=datetime(2026, 8, 15, 1, 0, tzinfo=UTC),
        sanitized_source_uri="provider://daily-prices?date=2026-08-14",
        media_type="application/json",
        raw_payload=b'{"provider_symbol":"TW-001","adjustedClose":"999.00"}',
        checkpoint_before="page:40",
        checkpoint_after="page:41",
        coverage=coverage,
        source_revision="revision-2026-08-15T01:00:00Z",
    )
    decoded = DecodedSourcePartition(
        source_id=request.source_id,
        schema_version="taiwan-unadjusted-eod-v1",
        source_revision=collection.source_revision,
        prices=(
            CanonicalPriceRow(
                listing_id=request.listing_ids[0],
                session_date=date(2026, 8, 14),
                open=Decimal("998.00"),
                high=Decimal("1002.00"),
                low=Decimal("997.00"),
                close=Decimal("1000.00"),
                volume=1000000,
            ),
        ),
        company_actions=(),
        listing_lifecycle=(),
        adjusted_close_cross_checks=(Decimal("999.00"),),
        identity_assertion_ids=("identity-assertion-001",),
        parent_object_ids=("raw-object-001",),
    )
    collector = LiteralCollector(collection)
    decoder = LiteralDecoder(decoded)
    adapter = TaiwanPriceSourceAdapter(
        source_id=request.source_id,
        mode=request.mode,
        adapter_version="taiwan-price-adapter-v1",
        rate_limit_policy_id="provider-rate-limit-v1",
        source_access_mode="engineering_double",
        collector=collector,
        decoder=decoder,
    )

    result = adapter.load(request)

    assert result.collection == collection
    assert result.decoded == decoded
    assert collector.requests == [request]
    assert decoder.collections == [collection]
    assert adapter.rate_limit_policy_id == "provider-rate-limit-v1"
    assert collection.checkpoint_after == "page:41"
    assert collection.coverage == coverage
    assert result.decoded.source_revision == "revision-2026-08-15T01:00:00Z"
    assert result.decoded.identity_assertion_ids == ("identity-assertion-001",)
    assert result.decoded.parent_object_ids == ("raw-object-001",)


def test_adapter_rejects_a_checkpoint_mismatch_without_decoding() -> None:
    request = SourcePartitionRequest(
        request_id="request-current",
        trace_id="trace-p2-trace-tw-01",
        source_id="tw-price-current",
        mode="current",
        listing_ids=("10000000-0000-4000-8000-000000000001",),
        start_date=date(2026, 8, 14),
        end_date=date(2026, 8, 14),
        expected_checkpoint="page:40",
        policy_decision_id="decision-allowed-001",
    )
    collection = CollectedSourcePartition(
        request_id=request.request_id,
        source_id=request.source_id,
        acquired_at=datetime(2026, 8, 15, 1, 0, tzinfo=UTC),
        sanitized_source_uri="provider://daily-prices?date=2026-08-14",
        media_type="application/json",
        raw_payload=b"{}",
        checkpoint_before="page:39",
        checkpoint_after="page:40",
        coverage=SourceCollectionCoverage(
            requested_start=date(2026, 8, 14),
            requested_end=date(2026, 8, 14),
            observed_start=date(2026, 8, 14),
            observed_end=date(2026, 8, 14),
            complete=True,
        ),
        source_revision="revision-1",
    )
    collector = LiteralCollector(collection)
    decoder = LiteralDecoder(
        DecodedSourcePartition(
            source_id=request.source_id,
            schema_version="taiwan-unadjusted-eod-v1",
            source_revision="revision-1",
            prices=(),
            company_actions=(),
            listing_lifecycle=(),
            adjusted_close_cross_checks=(),
            identity_assertion_ids=(),
            parent_object_ids=(),
        )
    )
    adapter = TaiwanPriceSourceAdapter(
        source_id=request.source_id,
        mode="current",
        adapter_version="taiwan-price-adapter-v1",
        rate_limit_policy_id="provider-rate-limit-v1",
        source_access_mode="engineering_double",
        collector=collector,
        decoder=decoder,
    )

    with pytest.raises(ValueError, match="source_checkpoint_mismatch"):
        adapter.load(request)

    assert decoder.collections == []
