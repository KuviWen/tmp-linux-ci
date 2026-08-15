from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from stock_forecasting.data_supply import (
    HistoricalAvailabilityClaim,
    TaiwanStockPoolManifest,
)
from stock_forecasting.platform.state_store import StateStore


class TaiwanPriceQualificationWorkflow:
    """Mints authoritative, immutable qualification records for price research."""

    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store

    def register_historical_availability_claim(
        self,
        claim: HistoricalAvailabilityClaim,
        *,
        trace_id: str,
    ) -> str:
        if claim.evidence_status == "qualification_candidate":
            if claim.qualification_artifact_id is not None:
                raise ValueError("candidate_claim_cannot_assert_qualification_evidence")
        else:
            self._validate_dependency_evidence(claim)
        return self._state_store.publish_governance_artifact(
            artifact_kind="historical_availability_claim",
            payload=claim.as_payload(),
            trace_id=trace_id,
        )

    def _validate_dependency_evidence(self, claim: HistoricalAvailabilityClaim) -> None:
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
        if (
            evidence.get("dependency_id") != "DEP-MKT-TW-01"
            or evidence.get("source_id") != claim.source_id
            or evidence.get("evidence_level") != claim.evidence_level
            or evidence.get("evidence_status") != "qualified"
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
        try:
            claim_payload = self._state_store.get_verified_governance_artifact(
                artifact_id=historical_availability_claim_id,
                artifact_kind="historical_availability_claim",
            )
            claim = HistoricalAvailabilityClaim.from_payload(claim_payload)
        except (KeyError, ValueError) as error:
            raise ValueError("formal_gate_requires_qualified_historical_claim") from error
        if claim.evidence_status != "qualified" or claim.source_id != manifest.historical_source_id:
            raise ValueError("formal_gate_requires_qualified_historical_claim")
        self._validate_dependency_evidence(claim)
        return self._state_store.publish_governance_artifact(
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
        )
