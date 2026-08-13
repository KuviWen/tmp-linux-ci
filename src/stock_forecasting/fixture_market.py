from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol, cast
from zoneinfo import ZoneInfo

from stock_forecasting.fixture_dataset import (
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
class FixtureCalendarSessionSpec:
    session_id: str
    session_kind: Literal["regular", "half_day"]
    open_at: str
    close_at: str

    def payload(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "session_kind": self.session_kind,
            "open_at": self.open_at,
            "close_at": self.close_at,
        }


@dataclass(frozen=True)
class FixtureCalendarClosureSpec:
    date: str
    session_status: str
    reason: str

    def payload(self) -> dict[str, str]:
        return {
            "date": self.date,
            "session_status": self.session_status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FixtureCalendarRevisionSpec:
    revision_id: str
    published_at: str
    effective_date: str
    change: str

    def payload(self) -> dict[str, str]:
        return {
            "revision_id": self.revision_id,
            "published_at": self.published_at,
            "effective_date": self.effective_date,
            "change": self.change,
        }


@dataclass(frozen=True)
class FixtureCalendarSpec:
    exchange: FixtureMarket
    timezone: str
    sessions: tuple[FixtureCalendarSessionSpec, ...]
    closures: tuple[FixtureCalendarClosureSpec, ...]
    revisions: tuple[FixtureCalendarRevisionSpec, ...]

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> FixtureCalendarSpec:
        exchange_text = str(payload["exchange"])
        if exchange_text not in ("XTAI", "XNAS"):
            raise ValueError("invalid_fixture_calendar_exchange")
        exchange = cast(FixtureMarket, exchange_text)
        facts = cast(list[dict[str, str]], payload["session_facts"])
        sessions: list[FixtureCalendarSessionSpec] = []
        for fact in facts:
            session_kind_text = fact["session_kind"]
            if session_kind_text not in ("regular", "half_day"):
                raise ValueError("invalid_fixture_session_kind")
            session_kind = cast(Literal["regular", "half_day"], session_kind_text)
            sessions.append(
                FixtureCalendarSessionSpec(
                    session_id=fact["session_id"],
                    session_kind=session_kind,
                    open_at=fact["open_at"],
                    close_at=fact["close_at"],
                )
            )
        closures = cast(list[dict[str, str]], payload["closures"])
        revisions = cast(list[dict[str, str]], payload["revisions"])
        return cls(
            exchange=exchange,
            timezone=str(payload["timezone"]),
            sessions=tuple(sessions),
            closures=tuple(
                FixtureCalendarClosureSpec(
                    date=closure["date"],
                    session_status=closure["session_status"],
                    reason=closure["reason"],
                )
                for closure in closures
            ),
            revisions=tuple(
                FixtureCalendarRevisionSpec(
                    revision_id=revision["revision_id"],
                    published_at=revision["published_at"],
                    effective_date=revision["effective_date"],
                    change=revision["change"],
                )
                for revision in revisions
            ),
        )

    def payload(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "timezone": self.timezone,
            "session_facts": [session.payload() for session in self.sessions],
            "closures": [closure.payload() for closure in self.closures],
            "revisions": [revision.payload() for revision in self.revisions],
        }

    def session_time_examples(self, session_ids: tuple[str, ...]) -> tuple[dict[str, str], ...]:
        by_id = {session.session_id: session for session in self.sessions}
        return tuple(
            {
                "session_id": session_id,
                "open_at": by_id[session_id].open_at,
                "close_at": by_id[session_id].close_at,
            }
            for session_id in session_ids
        )


@dataclass(frozen=True)
class FixtureCompanyActionSpec:
    kind: AdjustmentKind
    effective_session_id: str
    value: Decimal
    method: str
    currency: str | None = None

    def payload(self) -> dict[str, str]:
        payload = {
            "kind": self.kind,
            "effective_session_id": self.effective_session_id,
        }
        if self.kind == "cash_dividend":
            payload["cash_amount"] = f"{self.value:.2f}"
            if self.currency is not None:
                payload["currency"] = self.currency
        else:
            payload["split_ratio"] = f"{self.value:.2f}"
        return payload

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
    source_name: str
    health_scope: str
    normalized_schema_version: str
    committed_checkpoint: str
    ticker_assertions: tuple[TickerAssertionSpec, ...]
    external_identifier_assertions: tuple[ExternalIdentifierAssertionSpec, ...]
    selection: FixtureSelection
    calendar: FixtureCalendarSpec
    session_time_example_ids: tuple[str, ...]
    company_action: FixtureCompanyActionSpec

    def __post_init__(self) -> None:
        if self.calendar.exchange != self.market:
            raise ValueError("fixture_calendar_market_mismatch")
        calendar_session_ids = {session.session_id for session in self.calendar.sessions}
        if not set(self.selection.session_ids).issubset(calendar_session_ids):
            raise ValueError("fixture_selection_outside_calendar")
        if self.company_action.effective_session_id not in calendar_session_ids:
            raise ValueError("fixture_company_action_outside_calendar")

    @property
    def timezone(self) -> str:
        return self.calendar.timezone

    @property
    def session_fact_count(self) -> int:
        return len(self.calendar.sessions)

    @property
    def calendar_payload(self) -> dict[str, object]:
        return self.calendar.payload()

    @property
    def closure_dates(self) -> tuple[str, ...]:
        return tuple(closure.date for closure in self.calendar.closures)

    @property
    def calendar_revision_ids(self) -> tuple[str, ...]:
        return tuple(revision.revision_id for revision in self.calendar.revisions)

    @property
    def session_time_examples(self) -> tuple[dict[str, str], ...]:
        return self.calendar.session_time_examples(self.session_time_example_ids)

    @property
    def company_action_payload(self) -> dict[str, str]:
        return self.company_action.payload()

    @property
    def adjustment_rule(self) -> FixtureCompanyActionSpec:
        return self.company_action


class FixtureMarketAdapter(Protocol):
    def load(self, information_cutoff: datetime) -> FixtureMarketBatch: ...


class XtaiFixtureMarketAdapter:
    def __init__(self) -> None:
        self._dataset = XtaiFixtureDataset.load()

    def load(self, information_cutoff: datetime) -> FixtureMarketBatch:
        calendar = FixtureCalendarSpec.from_payload(self._dataset.calendar_payload())
        return FixtureMarketBatch(
            market="XTAI",
            market_date=information_cutoff.astimezone(ZoneInfo("Asia/Taipei")).date(),
            namespace="xtai",
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
            calendar=calendar,
            session_time_example_ids=("XTAI:2026-03-06", "XTAI:2026-03-09"),
            company_action=FixtureCompanyActionSpec(
                kind="cash_dividend",
                effective_session_id="XTAI:2026-06-15",
                value=Decimal("5.00"),
                method="fixture_cash_dividend_back_adjustment_v1",
                currency="TWD",
            ),
        )


class XnasFixtureMarketAdapter:
    def __init__(self) -> None:
        self._dataset = XnasFixtureDataset.load()

    def load(self, information_cutoff: datetime) -> FixtureMarketBatch:
        calendar = FixtureCalendarSpec.from_payload(self._dataset.calendar_payload())
        return FixtureMarketBatch(
            market="XNAS",
            market_date=information_cutoff.astimezone(ZoneInfo("America/New_York")).date(),
            namespace="xnas",
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
            calendar=calendar,
            session_time_example_ids=("XNAS:2026-03-06", "XNAS:2026-03-09"),
            company_action=FixtureCompanyActionSpec(
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
