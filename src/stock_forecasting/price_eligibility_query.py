from __future__ import annotations

from datetime import UTC, datetime

from stock_forecasting.authorization import (
    AuthorizationPolicy,
    OperationIntent,
    PolicyDeniedOutcome,
    SecurityContext,
    authorization_audit_payload,
)
from stock_forecasting.platform.state_store import StateStore


class PriceEligibilityQuery:
    def __init__(
        self,
        state_store: StateStore,
        *,
        authorization_policy: AuthorizationPolicy,
        authorization_time: datetime | None,
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._authorization_time = authorization_time

    def get_listing(
        self,
        *,
        listing_id: str,
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        denied = self._authorize(trace_id=trace_id, security_context=security_context)
        if denied is not None:
            return denied
        sources = self._state_store.list_price_research_eligibility(listing_id=listing_id)
        if not sources:
            raise KeyError(listing_id)
        modes = {str(source["source_mode"]) for source in sources}
        required_modes_present = modes == {"current", "historical"}
        statuses = {str(source["status"]) for source in sources}
        if "policy_blocked" in statuses or not required_modes_present:
            status = "policy_blocked"
            reason_code = "dependency_evidence_unverified"
        elif "quarantined" in statuses:
            status = "quarantined"
            reason_code = next(
                str(source["reason_code"])
                for source in sources
                if source["status"] == "quarantined"
            )
        else:
            status = "qualified"
            reason_code = "qualified_price_materialized"
        checks = _qualification_checks(status)
        return {
            "listing_id": listing_id,
            "market": "XTAI",
            "status": status,
            "reason_code": reason_code,
            "dependency_id": "DEP-MKT-TW-01",
            "formally_qualified": status == "qualified",
            "checks": checks,
            "sources": sources,
        }

    def list_sources(
        self,
        *,
        trace_id: str,
        security_context: SecurityContext,
    ) -> list[dict[str, object]] | PolicyDeniedOutcome:
        denied = self._authorize(trace_id=trace_id, security_context=security_context)
        if denied is not None:
            return denied
        return self._state_store.list_price_research_eligibility()

    def _authorize(
        self,
        *,
        trace_id: str,
        security_context: SecurityContext,
    ) -> PolicyDeniedOutcome | None:
        evaluated_at = self._authorization_time or datetime.now(UTC)
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="price_research_eligibility.read",
                dataset_id="price-research-eligibility",
                purpose="price_research",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
        )
        self._state_store.record_authorization_decision(
            authorization=authorization_audit_payload(decision),
            outcome="allowed" if decision.allowed else "denied",
            trace_id=trace_id,
        )
        return None if decision.allowed else PolicyDeniedOutcome.from_decision(decision)


def _qualification_checks(status: str) -> dict[str, str]:
    if status == "qualified":
        return {
            "policy": "passed",
            "coverage": "passed",
            "schema": "passed",
            "integrity": "passed",
            "depth": "passed",
        }
    if status == "quarantined":
        return {
            "policy": "passed",
            "coverage": "blocked",
            "schema": "blocked",
            "integrity": "blocked",
            "depth": "blocked",
        }
    return {
        "policy": "blocked",
        "coverage": "not_evaluated",
        "schema": "not_evaluated",
        "integrity": "not_evaluated",
        "depth": "not_evaluated",
    }
