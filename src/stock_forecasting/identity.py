from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class TickerAssertion:
    listing_id: str
    ticker: str
    valid_from: date
    valid_to: date | None

    def contains(self, valid_at: date) -> bool:
        return self.valid_from <= valid_at and (self.valid_to is None or valid_at <= self.valid_to)

    def as_payload(self) -> dict[str, str | None]:
        return {
            "listing_id": self.listing_id,
            "ticker": self.ticker,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


@dataclass(frozen=True)
class ListingIdentity:
    listing_id: str
    ticker_assertions: tuple[TickerAssertion, ...]

    def __post_init__(self) -> None:
        ordered = sorted(self.ticker_assertions, key=lambda assertion: assertion.valid_from)
        if not ordered or any(assertion.listing_id != self.listing_id for assertion in ordered):
            raise ValueError("ticker_assertion_listing_mismatch")
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.valid_to is None or previous.valid_to >= current.valid_from:
                raise ValueError("ticker_assertion_validity_overlap")

    def ticker_at(self, valid_at: date) -> str:
        matches = [
            assertion.ticker for assertion in self.ticker_assertions if assertion.contains(valid_at)
        ]
        if len(matches) != 1:
            raise KeyError(f"ticker_not_resolved:{valid_at.isoformat()}")
        return matches[0]

    def assertions_payload(self) -> list[dict[str, str | None]]:
        return [assertion.as_payload() for assertion in self.ticker_assertions]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ListingIdentity:
        assertions = tuple(
            TickerAssertion(
                listing_id=str(assertion["listing_id"]),
                ticker=str(assertion["ticker"]),
                valid_from=date.fromisoformat(str(assertion["valid_from"])),
                valid_to=(
                    date.fromisoformat(str(assertion["valid_to"]))
                    if assertion["valid_to"] is not None
                    else None
                ),
            )
            for assertion in payload["ticker_assertions"]
        )
        return cls(listing_id=str(payload["listing_id"]), ticker_assertions=assertions)
