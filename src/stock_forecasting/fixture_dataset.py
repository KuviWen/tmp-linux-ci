from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypedDict

FIXTURE_SOURCE = Path(__file__).parent / "fixtures" / "xtai_eod_v1.csv"
CALENDAR_CLOSURES = (
    "2025-10-06",
    "2025-10-10",
    "2026-01-01",
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    "2026-04-06",
    "2026-05-01",
    "2026-06-19",
)
CALENDAR_REVISION_ID = "xtai-fixture-calendar-revision-1"


class RawEodRecord(TypedDict):
    session_id: str
    open: str
    high: str
    low: str
    close: str
    volume: int


class CalendarSessionFact(TypedDict):
    session_id: str
    session_kind: Literal["regular", "half_day"]
    open_at: str
    close_at: str


@dataclass(frozen=True)
class FixtureSession:
    session_id: str
    session_kind: Literal["regular", "half_day"]
    open_at: str
    close_at: str
    open: str
    high: str
    low: str
    close: str
    volume: int

    def raw_record(self) -> RawEodRecord:
        return {
            "session_id": self.session_id,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    def calendar_fact(self) -> CalendarSessionFact:
        return {
            "session_id": self.session_id,
            "session_kind": self.session_kind,
            "open_at": self.open_at,
            "close_at": self.close_at,
        }


@dataclass(frozen=True)
class FixtureSelection:
    sessions: tuple[FixtureSession, ...]

    @property
    def records(self) -> list[RawEodRecord]:
        return [session.raw_record() for session in self.sessions]

    @property
    def session_ids(self) -> list[str]:
        return [session.session_id for session in self.sessions]


class XtaiFixtureDataset:
    def __init__(self, sessions: tuple[FixtureSession, ...]) -> None:
        if len(sessions) != 300:
            raise ValueError("fixture_calendar_must_have_300_session_facts")
        if len({session.session_id for session in sessions}) != len(sessions):
            raise ValueError("duplicate_fixture_session")
        self._sessions = sessions

    @classmethod
    def load(cls) -> XtaiFixtureDataset:
        sessions: list[FixtureSession] = []
        with FIXTURE_SOURCE.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                session_kind_text = row["session_kind"]
                if session_kind_text == "regular":
                    session_kind: Literal["regular", "half_day"] = "regular"
                elif session_kind_text == "half_day":
                    session_kind = "half_day"
                else:
                    raise ValueError("invalid_fixture_session_kind")
                for field in ("open", "high", "low", "close"):
                    Decimal(row[field])
                sessions.append(
                    FixtureSession(
                        session_id=row["session_id"],
                        session_kind=session_kind,
                        open_at=row["open_at"],
                        close_at=row["close_at"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=int(row["volume"]),
                    )
                )
        return cls(tuple(sessions))

    def select(self, information_cutoff: datetime, *, count: int = 253) -> FixtureSelection:
        cutoff_date = information_cutoff.date().isoformat()
        eligible = tuple(
            session
            for session in self._sessions
            if session.session_id.removeprefix("XTAI:") <= cutoff_date
        )
        if len(eligible) < count:
            raise ValueError("fixture_calendar_does_not_cover_cutoff")
        return FixtureSelection(eligible[-count:])

    @property
    def session_fact_count(self) -> int:
        return len(self._sessions)

    def calendar_payload(self) -> dict[str, object]:
        return {
            "exchange": "XTAI",
            "timezone": "Asia/Taipei",
            "session_facts": [session.calendar_fact() for session in self._sessions],
            "closures": [
                {
                    "date": closure_date,
                    "session_status": "closed",
                    "reason": "fixture_exchange_closure",
                }
                for closure_date in CALENDAR_CLOSURES
            ],
            "revisions": [
                {
                    "revision_id": CALENDAR_REVISION_ID,
                    "published_at": "2026-02-01T00:00:00Z",
                    "effective_date": "2026-02-20",
                    "change": "fixture_exchange_closure_confirmed",
                }
            ],
        }
