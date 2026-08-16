from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class UnadjustedClose:
    listing_id: str
    session_date: date
    close: Decimal


@dataclass(frozen=True)
class PriceAdjustmentAction:
    listing_id: str
    effective_date: date
    kind: Literal["cash_dividend", "split"]
    value: Decimal
    source_action_id: str

    def __post_init__(self) -> None:
        if self.value <= 0 or not self.source_action_id:
            raise ValueError("price_adjustment_action_invalid")


@dataclass(frozen=True)
class AdjustedClose:
    listing_id: str
    session_date: date
    adjusted_close: Decimal


def derive_adjusted_closes(
    prices: tuple[UnadjustedClose, ...],
    actions: tuple[PriceAdjustmentAction, ...],
) -> tuple[AdjustedClose, ...]:
    adjusted_rows: list[AdjustedClose] = []
    for price in prices:
        adjusted = price.close
        for action in actions:
            if action.listing_id != price.listing_id or price.session_date >= action.effective_date:
                continue
            adjusted = (
                adjusted - action.value
                if action.kind == "cash_dividend"
                else adjusted / action.value
            )
        adjusted_rows.append(
            AdjustedClose(
                listing_id=price.listing_id,
                session_date=price.session_date,
                adjusted_close=adjusted,
            )
        )
    return tuple(adjusted_rows)
