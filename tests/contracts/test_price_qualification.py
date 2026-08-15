from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from stock_forecasting.data_supply import (
    HistoricalAvailabilityClaim,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_qualification import TaiwanPriceQualificationWorkflow


def test_formal_gate_rejects_an_existing_artifact_with_the_wrong_evidence_contract() -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    workflow = TaiwanPriceQualificationWorkflow(state_store)
    manifest = load_taiwan_stock_pool_manifest()
    historical_claim_id = workflow.register_historical_availability_claim(
        HistoricalAvailabilityClaim(
            source_id=manifest.historical_source_id,
            evidence_level="archive_attested",
            evidence_status="qualification_candidate",
            observed_start=date(2019, 8, 14),
            observed_end=date(2026, 8, 14),
            schema_version="taiwan-unadjusted-eod-v1",
            exact_sessions_verified=True,
            integrity_verified=True,
            company_actions_verified=True,
            listing_lifecycle_verified=True,
            qualification_artifact_id=None,
        ),
        trace_id="trace-candidate-claim",
    )
    wrong_gate_id = state_store.publish_governance_artifact(
        artifact_kind="review_note",
        payload={"dependency_id": "DEP-MKT-TW-01", "evidence_status": "qualified"},
        trace_id="trace-wrong-formal-gate",
    )
    claimed_manifest = replace(
        manifest,
        evidence_status="qualified",
        formal_qualification_artifact_id=wrong_gate_id,
        historical_availability_claim_id=historical_claim_id,
    )
    sources: list[dict[str, object]] = [
        {
            "source_id": manifest.current_source_id,
            "source_mode": "current",
            "status": "published",
            "dataset_version_id": "sha256:current-dataset",
            "adjustment_version_id": "sha256:current-adjustment",
            "historical_availability_claim_id": None,
        },
        {
            "source_id": manifest.historical_source_id,
            "source_mode": "historical",
            "status": "published",
            "dataset_version_id": "sha256:historical-dataset",
            "adjustment_version_id": "sha256:historical-adjustment",
            "historical_availability_claim_id": historical_claim_id,
        },
    ]

    assert workflow.formal_qualification_available(claimed_manifest, sources) is False

    with pytest.raises(ValueError, match="formal_gate_requires_qualified_historical_claim"):
        workflow.register_formal_qualification_gate(
            manifest=manifest,
            historical_availability_claim_id=historical_claim_id,
            trace_id="trace-rejected-formal-gate",
        )
