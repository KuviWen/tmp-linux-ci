from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol, cast
from zoneinfo import ZoneInfo

from stock_forecasting.fixture_dataset import (
    CALENDAR_CLOSURES,
    CALENDAR_REVISION_ID,
    XNAS_CALENDAR_CLOSURES,
    XNAS_CALENDAR_REVISION_ID,
    FixtureSelection,
    XnasFixtureDataset,
    XtaiFixtureDataset,
)

FixtureMarket = Literal["XTAI", "XNAS"]
AdjustmentKind = Literal["cash_dividend", "split"]
IdentitySubjectKind = Literal["issuer", "security", "listing"]


@dataclass(frozen=True)
class TickerAssertionSpec:
    ticker: str
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class ExternalIdentifierAssertionSpec:
    subject_kind: IdentitySubjectKind
    identifier_type: str
    identifier_value: str
    source: str
    evidence: str
    trust_level: Literal["fixture_only"]
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class FixtureAdjustmentRule:
    kind: AdjustmentKind
    effective_session_id: str
    value: Decimal
    method: str

    def apply(self, records: list[dict[str, object]]) -> list[dict[str, str]]:
        adjusted: list[dict[str, str]] = []
        for record in records:
            if "close" not in record:
                continue
            close = Decimal(str(record["close"]))
            if str(record["session_id"]) < self.effective_session_id:
                close = close - self.value if self.kind == "cash_dividend" else close / self.value
            adjusted.append(
                {
                    "session_id": str(record["session_id"]),
                    "adjusted_close": str(close.quantize(Decimal("0.01"))),
                }
            )
        return adjusted


@dataclass(frozen=True)
class FixtureMarketBatch:
    market: FixtureMarket
    market_date: date
    namespace: str
    timezone: str
    source_name: str
    health_scope: str
    normalized_schema_version: str
    committed_checkpoint: str
    ticker_assertions: tuple[TickerAssertionSpec, ...]
    external_identifier_assertions: tuple[ExternalIdentifierAssertionSpec, ...]
    selection: FixtureSelection
    session_fact_count: int
    calendar_payload: dict[str, object]
    closure_dates: tuple[str, ...]
    calendar_revision_ids: tuple[str, ...]
    session_time_examples: tuple[dict[str, str], ...]
    company_action_payload: dict[str, str]
    adjustment_rule: FixtureAdjustmentRule


class FixtureMarketAdapter(Protocol):
    def load(self, information_cutoff: datetime) -> FixtureMarketBatch: ...


def _session_time_examples(
    calendar_payload: dict[str, object],
    session_ids: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    facts = cast(list[dict[str, str]], calendar_payload["session_facts"])
    by_id = {fact["session_id"]: fact for fact in facts}
    return tuple(
        {
            "session_id": session_id,
            "open_at": by_id[session_id]["open_at"],
            "close_at": by_id[session_id]["close_at"],
        }
        for session_id in session_ids
    )


class XtaiFixtureMarketAdapter:
    def __init__(self) -> None:
        self._dataset = XtaiFixtureDataset.load()

    def load(self, information_cutoff: datetime) -> FixtureMarketBatch:
        calendar_payload = self._dataset.calendar_payload()
        return FixtureMarketBatch(
            market="XTAI",
            market_date=information_cutoff.astimezone(ZoneInfo("Asia/Taipei")).date(),
            namespace="xtai",
            timezone="Asia/Taipei",
            source_name="xtai-fixture",
            health_scope="xtai_fixture_source",
            normalized_schema_version="fixture-eod-normalized-v1",
            committed_checkpoint="xtai-fixture-page:1",
            ticker_assertions=(
                TickerAssertionSpec("1234", date(2024, 1, 1), date(2025, 8, 12)),
                TickerAssertionSpec("2330", date(2025, 8, 13), None),
            ),
            external_identifier_assertions=(
                ExternalIdentifierAssertionSpec(
                    subject_kind="security",
                    identifier_type="fixture_source_security_id",
                    identifier_value="XTAI-FIXTURE-SECURITY-001",
                    source="synthetic_fixture_registry",
                    evidence="xtai-fixture-identity-manifest-v1",
                    trust_level="fixture_only",
                    valid_from=date(2024, 1, 1),
                    valid_to=None,
                ),
            ),
            selection=self._dataset.select(information_cutoff),
            session_fact_count=self._dataset.session_fact_count,
            calendar_payload=calendar_payload,
            closure_dates=CALENDAR_CLOSURES,
            calendar_revision_ids=(CALENDAR_REVISION_ID,),
            session_time_examples=_session_time_examples(
                calendar_payload,
                ("XTAI:2026-03-06", "XTAI:2026-03-09"),
            ),
            company_action_payload={
                "kind": "cash_dividend",
                "effective_session_id": "XTAI:2026-06-15",
                "cash_amount": "5.00",
                "currency": "TWD",
            },
            adjustment_rule=FixtureAdjustmentRule(
                kind="cash_dividend",
                effective_session_id="XTAI:2026-06-15",
                value=Decimal("5.00"),
                method="fixture_cash_dividend_back_adjustment_v1",
            ),
        )


class XnasFixtureMarketAdapter:
    def __init__(self) -> None:
        self._dataset = XnasFixtureDataset.load()

    def load(self, information_cutoff: datetime) -> FixtureMarketBatch:
        calendar_payload = self._dataset.calendar_payload()
        return FixtureMarketBatch(
            market="XNAS",
            market_date=information_cutoff.astimezone(ZoneInfo("America/New_York")).date(),
            namespace="xnas",
            timezone="America/New_York",
            source_name="xnas-fixture",
            health_scope="xnas_fixture_source",
            normalized_schema_version="fixture-eod-normalized-v1",
            committed_checkpoint="xnas-fixture-page:1",
            ticker_assertions=(
                TickerAssertionSpec("USF1", date(2024, 1, 1), date(2025, 12, 31)),
                TickerAssertionSpec("USF2", date(2026, 1, 1), None),
            ),
            external_identifier_assertions=(
                ExternalIdentifierAssertionSpec(
                    subject_kind="security",
                    identifier_type="fixture_source_security_id",
                    identifier_value="XNAS-FIXTURE-SECURITY-001",
                    source="synthetic_fixture_registry",
                    evidence="xnas-fixture-identity-manifest-v1",
                    trust_level="fixture_only",
                    valid_from=date(2024, 1, 1),
                    valid_to=None,
                ),
            ),
            selection=self._dataset.select(information_cutoff),
            session_fact_count=self._dataset.session_fact_count,
            calendar_payload=calendar_payload,
            closure_dates=XNAS_CALENDAR_CLOSURES,
            calendar_revision_ids=(XNAS_CALENDAR_REVISION_ID,),
            session_time_examples=_session_time_examples(
                calendar_payload,
                ("XNAS:2026-03-06", "XNAS:2026-03-09"),
            ),
            company_action_payload={
                "kind": "split",
                "effective_session_id": "XNAS:2026-02-02",
                "split_ratio": "2.00",
            },
            adjustment_rule=FixtureAdjustmentRule(
                kind="split",
                effective_session_id="XNAS:2026-02-02",
                value=Decimal("2.00"),
                method="fixture_split_back_adjustment_v1",
            ),
        )


def default_fixture_market_adapters() -> dict[FixtureMarket, FixtureMarketAdapter]:
    return {
        "XTAI": XtaiFixtureMarketAdapter(),
        "XNAS": XnasFixtureMarketAdapter(),
    }
