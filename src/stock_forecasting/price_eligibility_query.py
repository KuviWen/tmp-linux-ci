from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from stock_forecasting.authorization import (
    AuthorizationPolicy,
    OperationIntent,
    PolicyDeniedOutcome,
    SecurityContext,
    authorization_audit_payload,
)
from stock_forecasting.data_supply import (
    PRICE_RESEARCH_REQUIRED_USES,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_qualification import TaiwanPriceQualificationWorkflow


class PriceEligibilityQuery:
    def __init__(
        self,
        state_store: StateStore,
        *,
        authorization_policy: AuthorizationPolicy,
        authorization_time: datetime | None,
        source_authorization_policy: Callable[[], AuthorizationPolicy] | None = None,
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._authorization_time = authorization_time
        self._source_authorization_policy = source_authorization_policy or (
            lambda: authorization_policy
        )

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
        evaluated_at = self._authorization_time or datetime.now(UTC)
        sources = self._apply_current_source_rights(
            sources=sources,
            evaluated_at=evaluated_at,
            trace_id=trace_id,
            security_context=security_context,
        )
        modes = {str(source["source_mode"]) for source in sources}
        required_modes_present = modes == {"current", "historical"}
        statuses = {str(source["status"]) for source in sources}
        manifest = load_taiwan_stock_pool_manifest()
        formal_evidence_available = TaiwanPriceQualificationWorkflow(
            self._state_store
        ).formal_qualification_available(
            manifest,
            sources,
        )
        if "policy_blocked" in statuses:
            status = "policy_blocked"
            reason_code = (
                "source_rights_not_effective"
                if any(source["reason_code"] == "source_rights_not_effective" for source in sources)
                else "dependency_evidence_unverified"
            )
        elif "deferred" in statuses:
            status = "deferred"
            reason_code = "source_collection_deferred"
        elif not required_modes_present:
            status = "policy_blocked"
            reason_code = "dependency_evidence_unverified"
        elif "quarantined" in statuses:
            status = "quarantined"
            reason_code = next(
                str(source["reason_code"])
                for source in sources
                if source["status"] == "quarantined"
            )
        elif not formal_evidence_available:
            status = "policy_blocked"
            reason_code = "qualification_evidence_unverified"
        else:
            status = "qualified"
            reason_code = "qualified_price_materialized"
        checks = _aggregate_qualification_checks(sources)
        if status == "policy_blocked":
            checks["policy"] = "blocked"
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
        return self._apply_current_source_rights(
            sources=self._state_store.list_price_research_eligibility(),
            evaluated_at=self._authorization_time or datetime.now(UTC),
            trace_id=trace_id,
            security_context=security_context,
        )

    def _apply_current_source_rights(
        self,
        *,
        sources: list[dict[str, object]],
        evaluated_at: datetime,
        trace_id: str,
        security_context: SecurityContext,
    ) -> list[dict[str, object]]:
        source_ids = sorted(
            {str(source["source_id"]) for source in sources if source["status"] == "published"}
        )
        if not source_ids:
            return sources
        policy = self._source_authorization_policy()
        source_allowed: dict[str, bool] = {}
        for source_id in source_ids:
            decision = policy.evaluate(
                security_context,
                OperationIntent(
                    action="price_research_eligibility.read",
                    dataset_id=source_id,
                    purpose="price_research",
                    environment=security_context.environment,
                    resource_state="active",
                    evaluated_at=evaluated_at,
                    trace_id=trace_id,
                    correlation_id=f"{trace_id}:{source_id}:current-source-rights",
                    required_uses=PRICE_RESEARCH_REQUIRED_USES,
                ),
            )
            self._state_store.record_authorization_decision(
                authorization=authorization_audit_payload(decision),
                outcome="allowed" if decision.allowed else "denied",
                trace_id=trace_id,
            )
            source_allowed[source_id] = decision.allowed
        projected_sources: list[dict[str, object]] = []
        for source in sources:
            source_id = str(source["source_id"])
            policy_expired = (
                source["status"] == "published"
                and _parse_instant(str(source["policy_valid_until"])) <= evaluated_at
            )
            if source_allowed.get(source_id, True) and not policy_expired:
                projected_sources.append(source)
                continue
            projected = dict(source)
            projected["status"] = "policy_blocked"
            projected["reason_code"] = "source_rights_not_effective"
            source_checks = projected.get("checks")
            checks = dict(source_checks) if isinstance(source_checks, dict) else {}
            checks["policy"] = "blocked"
            projected["checks"] = checks
            projected_sources.append(projected)
        return projected_sources

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


def _aggregate_qualification_checks(sources: list[dict[str, object]]) -> dict[str, str]:
    aggregated: dict[str, str] = {}
    for check_name in ("policy", "coverage", "schema", "integrity", "depth"):
        values = {
            str(source_checks[check_name])
            for source in sources
            if isinstance((source_checks := source.get("checks")), dict)
        }
        if "blocked" in values:
            aggregated[check_name] = "blocked"
        elif "not_evaluated" in values or not values:
            aggregated[check_name] = "not_evaluated"
        else:
            aggregated[check_name] = "passed"
    return aggregated


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("price_eligibility_instant_timezone_required")
    return parsed
