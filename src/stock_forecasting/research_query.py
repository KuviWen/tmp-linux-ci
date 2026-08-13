from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from stock_forecasting.authorization import (
    AuthorizationPolicy,
    OperationIntent,
    PolicyDeniedOutcome,
    SecurityContext,
    authorization_audit_payload,
    fixture_dataset_id,
)
from stock_forecasting.platform.state_store import StateStore


class ResearchQuery:
    def __init__(
        self,
        state_store: StateStore,
        *,
        security_context: SecurityContext,
        authorization_policy: AuthorizationPolicy,
        authorization_time: datetime | None,
    ) -> None:
        self._state_store = state_store
        self._security_context = security_context
        self._authorization_policy = authorization_policy
        self._authorization_time = authorization_time

    def _authorize_record(
        self,
        record: dict[str, Any],
        *,
        trace_id: str,
        security_context: SecurityContext,
    ) -> PolicyDeniedOutcome | None:
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="research_prediction.read",
                dataset_id=fixture_dataset_id(str(record["calendar"]["exchange"])),
                purpose="fixture_research",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=self._authorization_time or datetime.now(UTC),
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
        )
        self._state_store.record_authorization_decision(
            authorization=authorization_audit_payload(decision),
            outcome="allowed" if decision.allowed else "denied",
            trace_id=trace_id,
        )
        if not decision.allowed:
            return PolicyDeniedOutcome.from_decision(decision)
        return None

    def get_listing_research(
        self,
        *,
        listing_id: str,
        information_cutoff: datetime,
        fixture_scenario: str = "normal",
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> dict[str, Any] | PolicyDeniedOutcome:
        expected_cutoff = information_cutoff.isoformat().replace("+00:00", "Z")
        record = self._state_store.get_listing_research(
            listing_id=listing_id,
            information_cutoff=expected_cutoff,
            fixture_scenario=fixture_scenario,
        )
        if record is None:
            raise KeyError(listing_id)
        denial = self._authorize_record(
            record,
            trace_id=trace_id or f"trace-research-{uuid4()}",
            security_context=security_context or self._security_context,
        )
        if denial is not None:
            return denial
        return record

    def list_predictions(
        self,
        *,
        execution_purpose: str,
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> list[dict[str, Any]] | PolicyDeniedOutcome:
        records = self._state_store.list_research_records(execution_purpose=execution_purpose)
        resolved_trace_id = trace_id or f"trace-research-{uuid4()}"
        for record in records:
            denial = self._authorize_record(
                record,
                trace_id=resolved_trace_id,
                security_context=security_context or self._security_context,
            )
            if denial is not None:
                return denial
        return records

    def require_listing_research(
        self,
        *,
        listing_id: str,
        information_cutoff: datetime,
        fixture_scenario: str = "normal",
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> dict[str, Any]:
        outcome = self.get_listing_research(
            listing_id=listing_id,
            information_cutoff=information_cutoff,
            fixture_scenario=fixture_scenario,
            trace_id=trace_id,
            security_context=security_context,
        )
        if isinstance(outcome, PolicyDeniedOutcome):
            raise RuntimeError("policy_denied_outcome_requires_handling")
        return outcome

    def require_predictions(
        self,
        *,
        execution_purpose: str,
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> list[dict[str, Any]]:
        outcome = self.list_predictions(
            execution_purpose=execution_purpose,
            trace_id=trace_id,
            security_context=security_context,
        )
        if isinstance(outcome, PolicyDeniedOutcome):
            raise RuntimeError("policy_denied_outcome_requires_handling")
        return outcome
