from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
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

    def _authorize_dataset(
        self,
        dataset_id: str,
        *,
        purpose: Literal["fixture_research", "price_research"],
        trace_id: str,
        security_context: SecurityContext,
    ) -> PolicyDeniedOutcome | None:
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="research_prediction.read",
                dataset_id=dataset_id,
                purpose=purpose,
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
        execution_purpose: str = "fixture",
        fixture_scenario: str = "normal",
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> dict[str, Any] | PolicyDeniedOutcome:
        expected_cutoff = information_cutoff.isoformat().replace("+00:00", "Z")
        resolved_trace_id = trace_id or f"trace-research-{uuid4()}"
        resolved_security_context = security_context or self._security_context
        authorization_dataset_id = self._state_store.get_listing_authorization_dataset(
            listing_id=listing_id,
            information_cutoff=expected_cutoff,
            execution_purpose=execution_purpose,
            fixture_scenario=fixture_scenario,
        )
        datasets = (
            (authorization_dataset_id,)
            if authorization_dataset_id is not None
            else tuple(fixture_dataset_id(market) for market in ("XTAI", "XNAS"))
        )
        for dataset_id in datasets:
            denial = self._authorize_dataset(
                dataset_id,
                purpose=(
                    "price_research" if execution_purpose == "production" else "fixture_research"
                ),
                trace_id=resolved_trace_id,
                security_context=resolved_security_context,
            )
            if denial is not None:
                return denial
        record = self._state_store.get_listing_research(
            listing_id=listing_id,
            information_cutoff=expected_cutoff,
            execution_purpose=execution_purpose,
            fixture_scenario=fixture_scenario,
        )
        if record is None:
            raise KeyError(listing_id)
        return record

    def list_predictions(
        self,
        *,
        execution_purpose: str,
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> list[dict[str, Any]] | PolicyDeniedOutcome:
        resolved_trace_id = trace_id or f"trace-research-{uuid4()}"
        records = self._state_store.list_research_records(execution_purpose=execution_purpose)
        formal_records_present = execution_purpose == "production" and bool(records)
        datasets = (
            tuple(
                sorted({str(record["lineage"]["source_policy_manifest_id"]) for record in records})
            )
            if formal_records_present
            else tuple(fixture_dataset_id(market) for market in ("XTAI", "XNAS"))
        )
        for dataset_id in datasets:
            denial = self._authorize_dataset(
                dataset_id,
                purpose=("price_research" if formal_records_present else "fixture_research"),
                trace_id=resolved_trace_id,
                security_context=security_context or self._security_context,
            )
            if denial is not None:
                return denial
        return records

    def list_prediction_history(
        self,
        *,
        listing_id: str,
        execution_purpose: str,
        trace_id: str | None = None,
        security_context: SecurityContext | None = None,
    ) -> list[dict[str, Any]] | PolicyDeniedOutcome:
        resolved_trace_id = trace_id or f"trace-research-history-{uuid4()}"
        records = self._state_store.list_listing_research_history(
            listing_id=listing_id,
            execution_purpose=execution_purpose,
        )
        formal_records_present = execution_purpose == "production" and bool(records)
        datasets = (
            tuple(
                sorted({str(record["lineage"]["source_policy_manifest_id"]) for record in records})
            )
            if formal_records_present
            else tuple(fixture_dataset_id(market) for market in ("XTAI", "XNAS"))
        )
        for dataset_id in datasets:
            denial = self._authorize_dataset(
                dataset_id,
                purpose=("price_research" if formal_records_present else "fixture_research"),
                trace_id=resolved_trace_id,
                security_context=security_context or self._security_context,
            )
            if denial is not None:
                return denial
        return records
