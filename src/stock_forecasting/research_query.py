from __future__ import annotations

from datetime import datetime
from typing import Any

from stock_forecasting.platform.state_store import StateStore


class ResearchQuery:
    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store

    def get_listing_research(
        self,
        *,
        listing_id: str,
        information_cutoff: datetime,
        fixture_scenario: str = "normal",
    ) -> dict[str, Any]:
        expected_cutoff = information_cutoff.isoformat().replace("+00:00", "Z")
        record = self._state_store.get_listing_research(
            listing_id=listing_id,
            information_cutoff=expected_cutoff,
            fixture_scenario=fixture_scenario,
        )
        if record is None:
            raise KeyError(listing_id)
        return record

    def list_predictions(self, *, execution_purpose: str) -> list[dict[str, Any]]:
        return self._state_store.list_research_records(execution_purpose=execution_purpose)
