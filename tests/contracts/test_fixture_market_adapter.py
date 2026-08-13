from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from stock_forecasting.application import build_test_application
from stock_forecasting.fixture_market import (
    FixtureMarket,
    FixtureMarketBatch,
    TickerAssertionSpec,
    XnasFixtureMarketAdapter,
    XtaiFixtureMarketAdapter,
    default_fixture_market_adapters,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand, FixtureEodWorkflow


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


def test_workflow_uses_the_adapter_market_date_for_identity_and_selection(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 8, 13, 0, 30, tzinfo=UTC)
    base_batch = XnasFixtureMarketAdapter().load(cutoff)
    batch = replace(
        base_batch,
        market_date=datetime(2026, 8, 12, tzinfo=UTC).date(),
        ticker_assertions=(
            TickerAssertionSpec(
                "USF1",
                datetime(2024, 1, 1, tzinfo=UTC).date(),
                datetime(2026, 8, 12, tzinfo=UTC).date(),
            ),
            TickerAssertionSpec("USF2", datetime(2026, 8, 13, tzinfo=UTC).date(), None),
        ),
    )

    class Adapter:
        def load(self, information_cutoff: datetime) -> FixtureMarketBatch:
            assert information_cutoff == cutoff
            return batch

    workflow = FixtureEodWorkflow(
        StateStore("sqlite+pysqlite:///:memory:", create_schema=True),
        observed_at=cutoff,
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        market_adapters={"XNAS": Adapter()},
    )

    outcome = workflow.execute(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-market-date",
            idempotency_key="market-date",
            market="XNAS",
        )
    )

    assert outcome.display_ticker == "USF1"


def test_explicit_empty_provider_registry_fails_closed(tmp_path: Path) -> None:
    workflow = FixtureEodWorkflow(
        StateStore("sqlite+pysqlite:///:memory:", create_schema=True),
        observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC),
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        market_adapters={},
    )

    with pytest.raises(KeyError, match="XNAS"):
        workflow.execute(
            FixtureEodCommand(
                information_cutoff=datetime(2026, 8, 12, 22, tzinfo=UTC),
                trace_id="trace-empty-provider-registry",
                idempotency_key="empty-provider-registry",
                market="XNAS",
            )
        )


def test_provider_market_mismatch_fails_closed(tmp_path: Path) -> None:
    workflow = FixtureEodWorkflow(
        StateStore("sqlite+pysqlite:///:memory:", create_schema=True),
        observed_at=datetime(2026, 8, 12, 21, 55, tzinfo=UTC),
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        market_adapters={"XNAS": XtaiFixtureMarketAdapter()},
    )

    with pytest.raises(ValueError, match="fixture_adapter_market_mismatch"):
        workflow.execute(
            FixtureEodCommand(
                information_cutoff=datetime(2026, 8, 12, 22, tzinfo=UTC),
                trace_id="trace-provider-market-mismatch",
                idempotency_key="provider-market-mismatch",
                market="XNAS",
            )
        )


def test_provider_batch_rejects_a_calendar_for_another_market() -> None:
    batch = XnasFixtureMarketAdapter().load(datetime(2026, 8, 12, 22, tzinfo=UTC))

    with pytest.raises(ValueError, match="fixture_calendar_market_mismatch"):
        replace(batch, calendar=replace(batch.calendar, exchange="XTAI"))


def test_company_action_payload_and_adjustment_share_one_typed_spec() -> None:
    batch = XnasFixtureMarketAdapter().load(datetime(2026, 8, 12, 22, tzinfo=UTC))
    changed = replace(
        batch,
        company_action=replace(batch.company_action, value=Decimal("3.00")),
    )

    assert changed.company_action_payload["split_ratio"] == "3.00"
    adjusted = changed.adjustment_rule.apply(
        [
            {"session_id": "XNAS:2026-01-30", "close": "300.00"},
            {"session_id": "XNAS:2026-02-02", "close": "100.00"},
        ]
    )
    assert adjusted == [
        {"session_id": "XNAS:2026-01-30", "adjusted_close": "100.00"},
        {"session_id": "XNAS:2026-02-02", "adjusted_close": "100.00"},
    ]


def test_provider_batch_rejects_selection_calendar_fact_mismatch() -> None:
    batch = XnasFixtureMarketAdapter().load(datetime(2026, 8, 12, 22, tzinfo=UTC))
    first, *remaining = batch.selection.sessions
    mismatched_selection = replace(
        batch.selection,
        sessions=(replace(first, session_kind="half_day"), *remaining),
    )

    with pytest.raises(ValueError, match="fixture_selection_calendar_fact_mismatch"):
        replace(batch, selection=mismatched_selection)
