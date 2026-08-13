from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stock_forecasting.application import Application
from stock_forecasting.authorization import PolicyDeniedOutcome, SecurityContext
from stock_forecasting.research_query import ResearchQuery
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand, FixtureEodOutcome


@dataclass(frozen=True)
class _SuccessfulResearchQuery:
    query: ResearchQuery

    def get_listing_research(
        self,
        *,
        listing_id: str,
        information_cutoff: datetime,
        fixture_scenario: str = "normal",
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> dict[str, Any]:
        outcome = self.query.get_listing_research(
            listing_id=listing_id,
            information_cutoff=information_cutoff,
            fixture_scenario=fixture_scenario,
            trace_id=trace_id,
            security_context=security_context,
        )
        assert not isinstance(outcome, PolicyDeniedOutcome)
        return outcome

    def list_predictions(
        self,
        *,
        execution_purpose: str,
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> list[dict[str, Any]]:
        outcome = self.query.list_predictions(
            execution_purpose=execution_purpose,
            trace_id=trace_id,
            security_context=security_context,
        )
        assert not isinstance(outcome, PolicyDeniedOutcome)
        return outcome


@dataclass(frozen=True)
class _SuccessfulApplication:
    application: Application

    @property
    def research_query(self) -> _SuccessfulResearchQuery:
        return _SuccessfulResearchQuery(self.application.research_query)

    def run_fixture_eod(self, command: FixtureEodCommand) -> FixtureEodOutcome:
        outcome = self.application.run_fixture_eod(command)
        assert isinstance(outcome, FixtureEodOutcome)
        return outcome


def assert_success(application: Application) -> _SuccessfulApplication:
    """Narrow public outcome unions inside tests that are exercising success paths."""
    return _SuccessfulApplication(application)
