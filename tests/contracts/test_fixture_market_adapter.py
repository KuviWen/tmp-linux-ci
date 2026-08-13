from __future__ import annotations

import json
from datetime import UTC, datetime

from stock_forecasting.application import build_test_application
from stock_forecasting.fixture_market import FixtureMarket, default_fixture_market_adapters
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand


def test_xtai_and_xnas_adapters_share_the_provider_and_module_contract() -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    adapters = default_fixture_market_adapters()
    assert set(adapters) == {"XTAI", "XNAS"}

    provider_shapes: set[tuple[str, ...]] = set()
    normalized_schema_versions: set[str] = set()
    for market in ("XTAI", "XNAS"):
        batch = adapters[market].load(cutoff)
        assert batch.market == market
        assert len(batch.selection.sessions) == 253
        assert batch.session_fact_count == 300
        assert all(
            session.session_id.startswith(f"{market}:") for session in batch.selection.sessions
        )
        assert set(batch.calendar_payload) == {
            "exchange",
            "timezone",
            "session_facts",
            "closures",
            "revisions",
        }
        provider_shapes.add(tuple(sorted(batch.selection.records[0])))
        normalized_schema_versions.add(batch.normalized_schema_version)
    assert provider_shapes == {("close", "high", "low", "open", "session_id", "volume")}
    assert normalized_schema_versions == {"fixture-eod-normalized-v1"}

    application = build_test_application(observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC))
    research_shapes: set[tuple[str, ...]] = set()
    source_evidence_shapes: set[tuple[str, ...]] = set()
    prediction_shapes: set[tuple[str, ...]] = set()
    artifact_kind_sets: set[frozenset[str]] = set()
    market_cases: tuple[tuple[FixtureMarket, str], ...] = (("XTAI", "tw"), ("XNAS", "us"))
    for market, suffix in market_cases:
        trace_id = f"trace-ticket-02-contract-{suffix}"
        outcome = application.run_fixture_eod(
            FixtureEodCommand(
                information_cutoff=cutoff,
                trace_id=trace_id,
                idempotency_key=f"ticket-02-contract-{suffix}",
                market=market,
            )
        )
        research = application.research_query.get_listing_research(
            listing_id=outcome.listing_id,
            information_cutoff=cutoff,
        )
        raw_payload = json.loads(application.object_repository.open(outcome.raw_object_ref).read())
        evidence = application.operations_control.get_trace_evidence(trace_id)

        assert outcome.market == market
        assert raw_payload["exchange"] == market
        assert research["calendar"]["exchange"] == market
        assert research["identity"]["listing_id"] == outcome.listing_id
        assert research["identity"]["listing_id"] != research["identity"]["display_ticker"]
        assert application.security_audit.list_events(trace_id=trace_id) == [
            {
                "action": "fixture_eod_publication",
                "outcome": "allowed",
                "reason_code": "fixture_policy_active",
                "trace_id": trace_id,
            }
        ]
        research_shapes.add(tuple(sorted(research)))
        source_evidence_shapes.add(tuple(sorted(research["source_evidence"])))
        prediction_shapes.update(
            tuple(sorted(prediction)) for prediction in research["predictions"]
        )
        artifact_kind_sets.add(frozenset(evidence["artifact_kinds"]))

    assert len(research_shapes) == 1
    assert len(source_evidence_shapes) == 1
    assert prediction_shapes == {
        (
            "confidence_score",
            "data_support",
            "horizon_sessions",
            "prediction_status",
            "probabilities",
        )
    }
    assert len(artifact_kind_sets) == 1
