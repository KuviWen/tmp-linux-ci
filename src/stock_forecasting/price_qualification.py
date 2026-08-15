from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from stock_forecasting.authorization import (
    AuthorizationPolicy,
    OperationIntent,
    SecurityContext,
    authorization_audit_payload,
)
from stock_forecasting.data_supply import (
    PRICE_RESEARCH_REQUIRED_USES,
    HistoricalAvailabilityClaim,
    TaiwanStockPoolManifest,
)
from stock_forecasting.platform.state_store import StateStore


class QualificationAuthorizationError(RuntimeError):
    """Raised when qualification governance is not explicitly authorized."""


class TaiwanPriceQualificationWorkflow:
    """Mints authoritative, immutable qualification records for price research."""

    def __init__(
        self,
        state_store: StateStore,
        *,
        authorization_policy: AuthorizationPolicy | None = None,
        security_context: SecurityContext | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._security_context = security_context
        self._clock = clock

    def register_historical_availability_claim(
        self,
        claim: HistoricalAvailabilityClaim,
        *,
        trace_id: str,
    ) -> str:
        authorizations = self._authorize_sources(
            (claim.source_id,),
            trace_id=trace_id,
            operation="register_historical_availability_claim",
        )
        try:
            if claim.evidence_status == "qualification_candidate":
                if claim.qualification_artifact_id is not None:
                    raise ValueError("candidate_claim_cannot_assert_qualification_evidence")
            else:
                self._validate_dependency_evidence(claim, authorization=authorizations[0])
        except ValueError as error:
            self._state_store._publish_governance_rejection(
                payload={
                    "operation": "register_historical_availability_claim",
                    "reason_code": str(error),
                },
                trace_id=trace_id,
                authorizations=authorizations,
            )
            raise
        return self._state_store._publish_authorized_governance_artifact(
            artifact_kind="historical_availability_claim",
            payload=claim.as_payload(),
            trace_id=trace_id,
            authorizations=authorizations,
        )

    def _validate_dependency_evidence(
        self,
        claim: HistoricalAvailabilityClaim,
        *,
        authorization: Mapping[str, object] | None = None,
    ) -> None:
        evidence_id = claim.qualification_artifact_id
        if evidence_id is None:
            raise ValueError("qualified_claim_requires_dependency_evidence")
        try:
            payload = self._state_store.get_verified_governance_artifact(
                artifact_id=evidence_id,
                artifact_kind="dependency_qualification_evidence",
            )
        except KeyError as error:
            raise ValueError("qualified_claim_requires_dependency_evidence") from error
        evidence = cast(dict[str, Any], payload)
        approval_fields = (
            "rights_contract_id",
            "approval_id",
            "approved_by_principal_id",
            "approved_at",
            "source_policy_version_id",
            "source_entitlement_version_id",
        )
        if (
            evidence.get("dependency_id") != "DEP-MKT-TW-01"
            or evidence.get("source_id") != claim.source_id
            or evidence.get("evidence_level") != claim.evidence_level
            or evidence.get("evidence_status") != "qualified"
            or any(
                not isinstance(evidence.get(field), str) or not evidence[field]
                for field in approval_fields
            )
        ):
            raise ValueError("qualified_claim_requires_dependency_evidence")
        try:
            approved_at = datetime.fromisoformat(
                str(evidence["approved_at"]).replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ValueError("qualified_claim_requires_dependency_evidence") from error
        if approved_at.tzinfo is None:
            raise ValueError("qualified_claim_requires_dependency_evidence")
        if authorization is not None and (
            evidence["source_policy_version_id"] != authorization.get("source_policy_version_id")
            or evidence["source_entitlement_version_id"]
            != authorization.get("source_entitlement_version_id")
        ):
            raise ValueError("qualified_claim_requires_dependency_evidence")

    def formal_qualification_available(
        self,
        manifest: TaiwanStockPoolManifest,
        sources: Sequence[Mapping[str, object]],
    ) -> bool:
        gate_id = manifest.formal_qualification_artifact_id
        claim_id = manifest.historical_availability_claim_id
        if (
            not manifest.formally_qualified
            or not manifest.matches_formal_source_lineage(sources)
            or gate_id is None
            or claim_id is None
        ):
            return False
        try:
            claim_payload = self._state_store.get_verified_governance_artifact(
                artifact_id=claim_id,
                artifact_kind="historical_availability_claim",
            )
            claim = HistoricalAvailabilityClaim.from_payload(claim_payload)
            if (
                claim.evidence_status != "qualified"
                or claim.source_id != manifest.historical_source_id
            ):
                return False
            self._validate_dependency_evidence(claim)
            gate_payload = self._state_store.get_verified_governance_artifact(
                artifact_id=gate_id,
                artifact_kind="taiwan_price_qualification_gate",
            )
        except (KeyError, ValueError):
            return False
        return gate_payload == {
            "dependency_id": "DEP-MKT-TW-01",
            "manifest_id": manifest.manifest_id,
            "current_source_id": manifest.current_source_id,
            "historical_source_id": manifest.historical_source_id,
            "historical_availability_claim_id": claim_id,
            "dependency_qualification_artifact_id": claim.qualification_artifact_id,
            "evidence_status": "qualified",
            "permitted_use": "historical_training_backtest_and_internal_research",
        }

    def register_formal_qualification_gate(
        self,
        *,
        manifest: TaiwanStockPoolManifest,
        historical_availability_claim_id: str,
        trace_id: str,
    ) -> str:
        authorizations = self._authorize_sources(
            (manifest.current_source_id, manifest.historical_source_id),
            trace_id=trace_id,
            operation="register_formal_qualification_gate",
        )
        try:
            claim_payload = self._state_store.get_verified_governance_artifact(
                artifact_id=historical_availability_claim_id,
                artifact_kind="historical_availability_claim",
            )
            claim = HistoricalAvailabilityClaim.from_payload(claim_payload)
            if (
                claim.evidence_status != "qualified"
                or claim.source_id != manifest.historical_source_id
            ):
                raise ValueError("formal_gate_requires_qualified_historical_claim")
            historical_authorization = next(
                authorization
                for authorization in authorizations
                if authorization["dataset_id"] == manifest.historical_source_id
            )
            self._validate_dependency_evidence(
                claim,
                authorization=historical_authorization,
            )
        except (KeyError, ValueError) as error:
            self._state_store._publish_governance_rejection(
                payload={
                    "operation": "register_formal_qualification_gate",
                    "reason_code": "formal_gate_requires_qualified_historical_claim",
                },
                trace_id=trace_id,
                authorizations=authorizations,
            )
            raise ValueError("formal_gate_requires_qualified_historical_claim") from error
        return self._state_store._publish_authorized_governance_artifact(
            artifact_kind="taiwan_price_qualification_gate",
            payload={
                "dependency_id": "DEP-MKT-TW-01",
                "manifest_id": manifest.manifest_id,
                "current_source_id": manifest.current_source_id,
                "historical_source_id": manifest.historical_source_id,
                "historical_availability_claim_id": historical_availability_claim_id,
                "dependency_qualification_artifact_id": claim.qualification_artifact_id,
                "evidence_status": "qualified",
                "permitted_use": "historical_training_backtest_and_internal_research",
            },
            trace_id=trace_id,
            authorizations=authorizations,
        )

    def _authorize_sources(
        self,
        source_ids: Sequence[str],
        *,
        trace_id: str,
        operation: str,
    ) -> list[dict[str, object]]:
        if (
            self._authorization_policy is None
            or self._security_context is None
            or self._clock is None
        ):
            raise QualificationAuthorizationError("qualification_authorization_not_configured")
        evaluated_at = self._clock()
        authorizations: list[dict[str, object]] = []
        for source_id in source_ids:
            decision = self._authorization_policy.evaluate(
                self._security_context,
                OperationIntent(
                    action="price_qualification.govern",
                    dataset_id=source_id,
                    purpose="price_research",
                    environment=self._security_context.environment,
                    resource_state="active",
                    evaluated_at=evaluated_at,
                    trace_id=trace_id,
                    correlation_id=f"{trace_id}:{source_id}",
                    required_uses=PRICE_RESEARCH_REQUIRED_USES,
                ),
            )
            authorization = authorization_audit_payload(decision)
            authorizations.append(authorization)
        denied = next(
            (
                authorization
                for authorization in authorizations
                if authorization["reason_code"] != "authorized"
            ),
            None,
        )
        if denied is not None:
            reason_code = str(denied["reason_code"])
            self._state_store._publish_governance_rejection(
                payload={"operation": operation, "reason_code": reason_code},
                trace_id=trace_id,
                authorizations=authorizations,
            )
            raise QualificationAuthorizationError(reason_code)
        return authorizations
