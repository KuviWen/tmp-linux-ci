from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from stock_forecasting.authorization import (
    AuthorizationPolicy,
    CurrentSourcePrincipalAttributes,
    OperationIntent,
    PolicyDeniedOutcome,
    SecurityContext,
    SourceRightsDecision,
    SourceRightsEvidenceError,
    authorization_audit_payload,
    source_rights_resolution_failure,
)
from stock_forecasting.data_supply import (
    PRICE_RESEARCH_REQUIRED_USES,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.finmind_provider_contract import FINMIND_PROVIDER_DISTRIBUTIONS
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_qualification import TaiwanPriceQualificationWorkflow
from stock_forecasting.source_credentials import project_source_credential_readiness
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest


class PriceEligibilityQuery:
    def __init__(
        self,
        state_store: StateStore,
        *,
        authorization_policy: AuthorizationPolicy,
        authorization_time: datetime | None,
        source_authorization_policy: Callable[[str], AuthorizationPolicy] | None = None,
        source_principal_attributes: (
            Callable[[str], CurrentSourcePrincipalAttributes] | None
        ) = None,
        object_repository: FilesystemObjectRepository | None = None,
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._authorization_time = authorization_time
        self._source_authorization_policy = source_authorization_policy or (
            lambda _principal_id: authorization_policy
        )
        self._source_principal_attributes = source_principal_attributes
        self._object_repository = object_repository

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
        us_manifest = load_us_stock_pool_manifest()
        us_listing = next(
            (candidate for candidate in us_manifest.listings if candidate.listing_id == listing_id),
            None,
        )
        if us_listing is not None:
            market: str = us_listing.market
            source_basis = us_manifest.source_basis.as_payload()
            formal_evidence_available = us_manifest.formally_qualified
            credential = project_source_credential_readiness(
                self._state_store.get_source_credential(
                    provider_id=us_manifest.source_basis.provider_id
                ),
                evaluated_at=evaluated_at,
            )
            credential_reason = (
                None if credential["readiness"] == "valid" else str(credential["reason_code"])
            )
        else:
            manifest = load_taiwan_stock_pool_manifest(self._object_repository)
            market = "XTAI"
            finmind_source_ids = {
                distribution.policy_dataset_id for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
            }
            finmind_selected = any(
                str(source["source_id"]) in finmind_source_ids for source in sources
            )
            selected_basis = (
                manifest.authenticated_source_basis if finmind_selected else manifest.source_basis
            )
            qualification_manifest = (
                manifest.for_authenticated_source_path() if finmind_selected else manifest
            )
            persisted_gate = self._state_store.find_latest_price_qualification_gate(
                manifest_id=qualification_manifest.manifest_id,
                source_path_id=qualification_manifest.source_path_id,
            )
            if persisted_gate is not None:
                gate_id, gate_payload = persisted_gate
                with suppress(ValueError):
                    qualification_manifest = qualification_manifest.with_formal_qualification_gate(
                        artifact_id=gate_id,
                        payload=gate_payload,
                    )
            source_basis = selected_basis.as_payload()
            try:
                formal_evidence_available = TaiwanPriceQualificationWorkflow(
                    self._state_store,
                    object_repository=self._object_repository,
                ).formal_qualification_available(
                    qualification_manifest,
                    sources,
                )
            except ValueError:
                formal_evidence_available = False
            if finmind_selected:
                credential = project_source_credential_readiness(
                    self._state_store.get_source_credential(
                        provider_id=manifest.authenticated_source_basis.provider_id
                    ),
                    evaluated_at=evaluated_at,
                )
                credential_reason = (
                    None if credential["readiness"] == "valid" else str(credential["reason_code"])
                )
            else:
                credential_reason = None
        current_source_rights_denied = any(
            isinstance((current := source.get("current_policy_decision")), dict)
            and current.get("outcome") == "denied"
            for source in sources
        )
        if current_source_rights_denied:
            status = "policy_blocked"
            reason_code = "source_rights_not_effective"
        elif "policy_blocked" in statuses:
            status = "policy_blocked"
            reason_code = (
                "source_rights_not_effective"
                if any(source["reason_code"] == "source_rights_not_effective" for source in sources)
                else "source_basis_unverified"
            )
        elif credential_reason is not None:
            status = "credential_required"
            reason_code = credential_reason
        elif "credential_required" in statuses:
            status = "credential_required"
            reason_code = next(
                str(source["reason_code"])
                for source in sources
                if source["status"] == "credential_required"
            )
        elif "unavailable" in statuses:
            status = "unavailable"
            reason_code = next(
                str(source["reason_code"])
                for source in sources
                if source["status"] == "unavailable"
            )
        elif "deferred" in statuses:
            status = "deferred"
            reason_code = "source_collection_deferred"
        elif not required_modes_present:
            status = "policy_blocked"
            reason_code = "source_basis_unverified"
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
        downstream_state = "ready" if status == "qualified" else status
        return {
            "listing_id": listing_id,
            "market": market,
            "status": status,
            "reason_code": reason_code,
            "source_basis_id": str(source_basis["source_basis_id"]),
            "source_basis": source_basis,
            "formally_qualified": (formal_evidence_available and not current_source_rights_denied),
            "downstream_readiness": {
                "new_collection": downstream_state,
                "feature_materialization": downstream_state,
                "training": downstream_state,
                "research_display": downstream_state,
            },
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
        decisions: dict[tuple[str, ...], tuple[SourceRightsDecision, str]] = {}
        projected_sources: list[dict[str, object]] = []
        for source in sources:
            projected = dict(source)
            projected["current_policy_decision"] = None
            if source["status"] == "policy_blocked":
                projected_sources.append(projected)
                continue
            evaluation_id = str(source["policy_evaluation_id"])
            decision_id = str(source["policy_decision_id"])
            source_id = str(source["source_id"])
            source_trace_id = str(source["trace_id"])
            source_correlation_id = str(source.get("policy_correlation_id", ""))
            decision_key = (
                evaluation_id,
                decision_id,
                source_id,
                source_trace_id,
                source_correlation_id,
            )
            decision_and_artifact = decisions.get(decision_key)
            failure_reason = "source_rights_prior_evidence_missing"
            current_subject: CurrentSourcePrincipalAttributes | None = None
            try:
                if decision_and_artifact is None:
                    prior_authorization = self._state_store.get_authorization_decision(
                        evaluation_id=evaluation_id
                    )
                    principal_id = prior_authorization.get("principal_id")
                    if not isinstance(principal_id, str):
                        raise SourceRightsEvidenceError("source_rights_prior_evidence_invalid")
                    failure_reason = "source_rights_policy_unavailable"
                    policy = self._source_authorization_policy(principal_id)
                    failure_reason = "source_rights_subject_attributes_unavailable"
                    current_subject = self._resolve_current_source_principal_attributes(
                        principal_id=principal_id,
                        security_context=security_context,
                    )
                    failure_reason = "source_rights_prior_evidence_invalid"
                    decision = policy.evaluate_current_source_rights(
                        prior_authorization,
                        expected_dataset_id=source_id,
                        expected_evaluation_id=evaluation_id,
                        expected_decision_id=decision_id,
                        expected_trace_id=source_trace_id,
                        expected_correlation_id=source_correlation_id,
                        current_runtime_environment=security_context.environment,
                        current_subject=current_subject,
                        evaluated_at=evaluated_at,
                        trace_id=trace_id,
                        correlation_id=(
                            f"{trace_id}:{source_id}:{source['source_mode']}:current-source-rights"
                        ),
                        required_uses=PRICE_RESEARCH_REQUIRED_USES,
                    )
                    payload = decision.as_payload()
                    evidence_artifact_id = (
                        self._state_store.publish_current_source_rights_resolution(
                            payload=payload,
                            trace_id=trace_id,
                        )
                    )
                    decision_and_artifact = (decision, evidence_artifact_id)
                    decisions[decision_key] = decision_and_artifact
            except SourceRightsEvidenceError as error:
                failure_reason = error.reason_code
            except (KeyError, ValueError):
                pass
            if decision_and_artifact is None:
                decision = source_rights_resolution_failure(
                    dataset_id=source_id,
                    prior_evaluation_id=evaluation_id,
                    prior_decision_id=decision_id or None,
                    prior_trace_id=source_trace_id or None,
                    prior_correlation_id=source_correlation_id or None,
                    evaluated_at=evaluated_at,
                    trace_id=trace_id,
                    reason_code=failure_reason,
                    runtime_environment=security_context.environment,
                    current_subject=current_subject,
                )
                payload = decision.as_payload()
                evidence_artifact_id = self._state_store.publish_current_source_rights_resolution(
                    payload=payload,
                    trace_id=trace_id,
                )
                decision_and_artifact = (decision, evidence_artifact_id)
                decisions[decision_key] = decision_and_artifact
            decision, evidence_artifact_id = decision_and_artifact
            projected["current_policy_decision"] = _current_policy_decision_payload(
                decision,
                evidence_artifact_id=evidence_artifact_id,
            )
            source_checks = projected.get("checks")
            checks = dict(source_checks) if isinstance(source_checks, dict) else {}
            if not decision.allowed:
                checks["policy"] = "blocked"
                if source["status"] != "deferred":
                    projected["status"] = "policy_blocked"
                    projected["reason_code"] = "source_rights_not_effective"
            projected["checks"] = checks
            projected_sources.append(projected)
        return projected_sources

    def _resolve_current_source_principal_attributes(
        self,
        *,
        principal_id: str,
        security_context: SecurityContext,
    ) -> CurrentSourcePrincipalAttributes:
        if self._source_principal_attributes is not None:
            attributes = self._source_principal_attributes(principal_id)
            if not isinstance(attributes, CurrentSourcePrincipalAttributes):
                raise SourceRightsEvidenceError("source_rights_subject_attributes_invalid")
            return attributes
        if principal_id == security_context.principal_id:
            return CurrentSourcePrincipalAttributes.from_verified_security_context(security_context)
        raise SourceRightsEvidenceError("source_rights_subject_attributes_unavailable")

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


def _current_policy_decision_payload(
    decision: SourceRightsDecision,
    *,
    evidence_artifact_id: str,
) -> dict[str, object]:
    return {**decision.as_payload(), "evidence_artifact_id": evidence_artifact_id}
