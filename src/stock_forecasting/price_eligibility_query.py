from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from stock_forecasting.authorization import (
    AuthorizationDecision,
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
        source_authorization_policy: Callable[[str], AuthorizationPolicy] | None = None,
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._authorization_time = authorization_time
        self._source_authorization_policy = source_authorization_policy or (
            lambda _principal_id: authorization_policy
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
        )

    def _apply_current_source_rights(
        self,
        *,
        sources: list[dict[str, object]],
        evaluated_at: datetime,
        trace_id: str,
    ) -> list[dict[str, object]]:
        decisions: dict[str, AuthorizationDecision] = {}
        projected_sources: list[dict[str, object]] = []
        for source in sources:
            projected = dict(source)
            projected["current_policy_decision"] = None
            if source["status"] == "policy_blocked":
                projected_sources.append(projected)
                continue
            evaluation_id = str(source["policy_evaluation_id"])
            try:
                decision = decisions.get(evaluation_id)
                if decision is None:
                    prior_authorization = self._state_store.get_authorization_decision(
                        evaluation_id=evaluation_id
                    )
                    principal_id = prior_authorization.get("principal_id")
                    if not isinstance(principal_id, str):
                        raise ValueError("source_workload_principal_missing")
                    decision = self._source_authorization_policy(
                        principal_id
                    ).reevaluate_source_workload(
                        prior_authorization,
                        evaluated_at=evaluated_at,
                        trace_id=trace_id,
                        correlation_id=(
                            f"{trace_id}:{source['source_id']}:{source['source_mode']}:"
                            "current-source-rights"
                        ),
                        required_uses=PRICE_RESEARCH_REQUIRED_USES,
                    )
                    self._state_store.record_authorization_decision(
                        authorization=authorization_audit_payload(decision),
                        outcome="allowed" if decision.allowed else "denied",
                        trace_id=trace_id,
                    )
                    decisions[evaluation_id] = decision
                projected["current_policy_decision"] = _current_policy_decision_payload(decision)
                source_rights_allowed = decision.allowed
            except (KeyError, ValueError):
                source_rights_allowed = False
            source_checks = projected.get("checks")
            checks = dict(source_checks) if isinstance(source_checks, dict) else {}
            if not source_rights_allowed:
                checks["policy"] = "blocked"
                if source["status"] != "deferred":
                    projected["status"] = "policy_blocked"
                    projected["reason_code"] = "source_rights_not_effective"
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


def _current_policy_decision_payload(decision: AuthorizationDecision) -> dict[str, object]:
    authorization = authorization_audit_payload(decision)
    return {
        field: authorization[field]
        for field in (
            "evaluation_id",
            "decision_id",
            "reason_code",
            "evaluated_at",
            "valid_until",
            "grant_version_id",
            "source_policy_version_id",
            "source_entitlement_version_id",
        )
    }
