from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from io import BytesIO
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
from stock_forecasting.historical_evidence import (
    QualifiedHistoricalAvailabilityClaimVerifier,
)
from stock_forecasting.platform.object_repository import (
    FilesystemObjectRepository,
    ObjectIntegrityError,
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
        object_repository: FilesystemObjectRepository | None = None,
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._security_context = security_context
        self._clock = clock
        self._object_repository = object_repository

    def register_open_data_source_basis_evidence(
        self,
        *,
        manifest: TaiwanStockPoolManifest,
        source_id: str,
        terms_content: bytes,
        trace_id: str,
    ) -> str:
        authorizations = self._authorize_sources(
            (source_id,),
            trace_id=trace_id,
            operation="register_open_data_source_basis_evidence",
        )
        try:
            authorization = authorizations[0]
            policy_version_id = authorization.get("source_policy_version_id")
            policies = (
                tuple(
                    policy
                    for policy in self._authorization_policy.source_policies
                    if policy.version_id == policy_version_id and policy.dataset_id == source_id
                )
                if self._authorization_policy is not None
                else ()
            )
            if (
                len(policies) != 1
                or policies[0].access_basis != "open_data_terms"
                or not isinstance(policies[0].license_id, str)
                or not isinstance(policies[0].terms_url, str)
                or not isinstance(policies[0].terms_content_sha256, str)
                or not isinstance(policies[0].attribution, str)
                or self._object_repository is None
                or not terms_content
                or source_id not in {manifest.current_source_id, manifest.historical_source_id}
                or {
                    (distribution.dataset_id, distribution.distribution_url)
                    for distribution in policies[0].distributions
                }
                != {
                    (dataset.dataset_id, dataset.distribution_url)
                    for dataset in manifest.source_basis.datasets
                }
            ):
                raise ValueError("open_data_source_basis_evidence_invalid")
            policy = policies[0]
            license_id = cast(str, policy.license_id)
            terms_url = cast(str, policy.terms_url)
            expected_terms_sha256 = cast(str, policy.terms_content_sha256)
            attribution = cast(str, policy.attribution)
            terms_sha256 = hashlib.sha256(terms_content).hexdigest()
            if terms_sha256 != expected_terms_sha256:
                raise ValueError("source_basis_terms_content_mismatch")
            terms_object = self._object_repository.put_verified(
                BytesIO(terms_content),
                expected_checksum=terms_sha256,
                metadata={
                    "object_kind": "open_data_terms",
                    "source_id": source_id,
                    "terms_url": terms_url,
                },
            )
        except ValueError as error:
            self._state_store._publish_governance_rejection(
                payload={
                    "operation": "register_open_data_source_basis_evidence",
                    "reason_code": str(error),
                },
                trace_id=trace_id,
                authorizations=authorizations,
            )
            raise
        return self._state_store._publish_authorized_governance_artifact(
            artifact_kind="open_data_source_basis_evidence",
            payload={
                "source_basis_id": "TWSE-OGDL-OPEN-DATA-01",
                "source_id": source_id,
                "verification_status": "verified",
                "license_id": license_id,
                "terms_url": terms_url,
                "terms_content_sha256": terms_sha256,
                "terms_object_id": terms_object.object_id,
                "attribution": attribution,
                "source_policy_version_id": policy.version_id,
                "distributions": [
                    {
                        "dataset_id": dataset.dataset_id,
                        "distribution_url": dataset.distribution_url,
                    }
                    for dataset in manifest.source_basis.datasets
                ],
            },
            trace_id=trace_id,
            authorizations=authorizations,
        )

    def register_zero_fee_source_basis_evidence(
        self,
        *,
        manifest: TaiwanStockPoolManifest,
        source_id: str,
        terms_content: bytes,
        trace_id: str,
    ) -> str:
        """Archive and bind the exact terms for an authenticated zero-fee candidate."""
        from stock_forecasting.finmind_provider_contract import (
            FINMIND_PROVIDER_DISTRIBUTIONS,
        )

        policy_source_ids = tuple(
            distribution.policy_dataset_id for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
        )
        authorizations = self._authorize_sources(
            policy_source_ids,
            trace_id=trace_id,
            operation="register_zero_fee_source_basis_evidence",
        )
        basis = manifest.authenticated_source_basis
        try:
            policies = (
                tuple(
                    policy
                    for policy in self._authorization_policy.source_policies
                    if policy.version_id
                    in {
                        authorization.get("source_policy_version_id")
                        for authorization in authorizations
                    }
                )
                if self._authorization_policy is not None
                else ()
            )
            expected_distributions = {
                (member.dataset_id, member.distribution_url) for member in basis.members
            }
            if (
                len(policies) != len(policy_source_ids)
                or {policy.dataset_id for policy in policies} != set(policy_source_ids)
                or any(
                    policy.access_basis != "zero_fee_plan"
                    or policy.source_basis_id != basis.source_basis_id
                    or policy.provider_id != basis.provider_id
                    or policy.plan_id != basis.plan_id
                    or policy.principal_classification != basis.principal_classification
                    or policy.credential_kind != basis.credential_kind
                    or policy.account_required is not True
                    or policy.fee_required is not False
                    or policy.terms_url != basis.terms_url
                    or policy.terms_content_sha256 != basis.terms_content_sha256
                    or not isinstance(policy.license_id, str)
                    or not isinstance(policy.attribution, str)
                    or not isinstance(policy.terms_content_sha256, str)
                    for policy in policies
                )
                or {
                    (item.dataset_id, item.distribution_url)
                    for policy in policies
                    for item in policy.distributions
                }
                != expected_distributions
                or self._object_repository is None
                or not terms_content
                or source_id not in {manifest.current_source_id, manifest.historical_source_id}
            ):
                raise ValueError("zero_fee_source_basis_evidence_invalid")
            policy = policies[0]
            expected_terms_sha256 = cast(str, policy.terms_content_sha256)
            terms_sha256 = hashlib.sha256(terms_content).hexdigest()
            if terms_sha256 != expected_terms_sha256:
                raise ValueError("source_basis_terms_content_mismatch")
            terms_object = self._object_repository.put_verified(
                BytesIO(terms_content),
                expected_checksum=terms_sha256,
                metadata={
                    "object_kind": "zero_fee_plan_terms",
                    "source_id": source_id,
                    "terms_url": basis.terms_url,
                },
            )
        except ValueError as error:
            self._state_store._publish_governance_rejection(
                payload={
                    "operation": "register_zero_fee_source_basis_evidence",
                    "reason_code": str(error),
                },
                trace_id=trace_id,
                authorizations=authorizations,
            )
            raise
        return self._state_store._publish_authorized_governance_artifact(
            artifact_kind="zero_fee_source_basis_evidence",
            payload={
                "source_basis_id": basis.source_basis_id,
                "source_id": source_id,
                "verification_status": "verified",
                "license_id": policy.license_id,
                "terms_url": basis.terms_url,
                "terms_content_sha256": terms_sha256,
                "terms_object_id": terms_object.object_id,
                "attribution": policy.attribution,
                "source_policy_version_ids": sorted(policy.version_id for policy in policies),
                "provider_id": basis.provider_id,
                "plan_id": basis.plan_id,
                "principal_classification": basis.principal_classification,
                "credential_kind": basis.credential_kind,
                "account_required": basis.account_required,
                "fee_required": basis.fee_required,
                "distributions": [
                    {
                        "dataset_id": member.dataset_id,
                        "distribution_url": member.distribution_url,
                    }
                    for member in basis.members
                ],
            },
            trace_id=trace_id,
            authorizations=authorizations,
        )

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
                self._validate_historical_evidence(claim)
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

    def _validate_historical_evidence(
        self,
        claim: HistoricalAvailabilityClaim,
        *,
        claim_id: str | None = None,
    ) -> None:
        if (
            claim_id is None
            or self._clock is None
            or not QualifiedHistoricalAvailabilityClaimVerifier(
                self._state_store,
                evaluated_at=self._clock(),
            ).is_usable(claim_id=claim_id, claim=claim)
        ):
            raise ValueError("qualified_claim_requires_historical_evidence")

    def _validate_source_basis_evidence(
        self,
        *,
        source_id: str,
        evidence_id: str,
        manifest: TaiwanStockPoolManifest | None = None,
        authorization: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, object] | None = None
        artifact_kind: str | None = None
        for candidate_kind in (
            "open_data_source_basis_evidence",
            "zero_fee_source_basis_evidence",
        ):
            try:
                payload = self._state_store.get_verified_governance_artifact(
                    artifact_id=evidence_id,
                    artifact_kind=candidate_kind,
                )
                artifact_kind = candidate_kind
                break
            except KeyError:
                continue
        if payload is None or artifact_kind is None:
            raise ValueError("formal_gate_requires_verified_source_basis")
        evidence = cast(dict[str, Any], payload)
        distributions = evidence.get("distributions")
        evidence_fields = (
            "license_id",
            "terms_url",
            "terms_content_sha256",
            "terms_object_id",
            "attribution",
        )
        if (
            evidence.get("source_id") != source_id
            or evidence.get("verification_status") != "verified"
            or len(str(evidence.get("terms_content_sha256", ""))) != 64
            or not isinstance(distributions, list)
            or not distributions
            or any(
                not isinstance(evidence.get(field), str) or not evidence[field]
                for field in evidence_fields
            )
            or (
                artifact_kind == "open_data_source_basis_evidence"
                and not isinstance(evidence.get("source_policy_version_id"), str)
            )
            or (
                artifact_kind == "zero_fee_source_basis_evidence"
                and (
                    not isinstance(evidence.get("source_policy_version_ids"), list)
                    or not evidence["source_policy_version_ids"]
                    or not all(
                        isinstance(item, str) and item
                        for item in evidence["source_policy_version_ids"]
                    )
                )
            )
            or any(
                not isinstance(distribution, dict)
                or set(distribution) != {"dataset_id", "distribution_url"}
                or not isinstance(distribution.get("dataset_id"), str)
                or not isinstance(distribution.get("distribution_url"), str)
                for distribution in distributions
            )
        ):
            raise ValueError("formal_gate_requires_verified_source_basis")
        if artifact_kind == "open_data_source_basis_evidence":
            if (
                (
                    evidence.get("source_basis_id") != manifest.source_basis.source_basis_id
                    if manifest is not None
                    else evidence.get("source_basis_id") != "TWSE-OGDL-OPEN-DATA-01"
                )
                or evidence.get("license_id") != "OGDL-1.0"
                or evidence.get("terms_url") != "https://data.gov.tw/license"
            ):
                raise ValueError("formal_gate_requires_verified_source_basis")
        else:
            zero_fee_fields = (
                "provider_id",
                "plan_id",
                "principal_classification",
                "credential_kind",
            )
            if (
                manifest is None
                or evidence.get("source_basis_id")
                != manifest.authenticated_source_basis.source_basis_id
                or any(
                    evidence.get(field) != getattr(manifest.authenticated_source_basis, field)
                    for field in zero_fee_fields
                )
                or evidence.get("account_required") is not True
                or evidence.get("fee_required") is not False
                or evidence.get("terms_url") != manifest.authenticated_source_basis.terms_url
            ):
                raise ValueError("formal_gate_requires_verified_source_basis")
        try:
            if self._object_repository is None:
                raise ValueError
            terms_content = self._object_repository.open_by_id(
                str(evidence["terms_object_id"])
            ).read()
            if hashlib.sha256(terms_content).hexdigest() != evidence["terms_content_sha256"]:
                raise ValueError
        except (KeyError, OSError, ObjectIntegrityError, ValueError):
            raise ValueError("formal_gate_requires_verified_source_basis") from None
        if manifest is not None:
            basis_members = (
                manifest.source_basis.datasets
                if artifact_kind == "open_data_source_basis_evidence"
                else manifest.authenticated_source_basis.members
            )
            expected_distributions = [
                {
                    "dataset_id": item.dataset_id,
                    "distribution_url": item.distribution_url,
                }
                for item in basis_members
            ]
            if distributions != expected_distributions:
                raise ValueError("formal_gate_requires_verified_source_basis")
        if authorization is not None:
            policy_version_id = authorization.get("source_policy_version_id")
            raw_policy_version_ids = (
                [evidence.get("source_policy_version_id")]
                if artifact_kind == "open_data_source_basis_evidence"
                else evidence.get("source_policy_version_ids")
            )
            if not isinstance(raw_policy_version_ids, list) or not all(
                isinstance(item, str) for item in raw_policy_version_ids
            ):
                raise ValueError("formal_gate_requires_verified_source_basis")
            evidence_policy_version_ids = cast(list[str], raw_policy_version_ids)
            policies = (
                tuple(
                    policy
                    for policy in self._authorization_policy.source_policies
                    if policy.version_id in evidence_policy_version_ids
                )
                if self._authorization_policy is not None
                else ()
            )
            authorized_policy = next(
                (
                    policy
                    for policy in policies
                    if policy.version_id == policy_version_id and policy.dataset_id == source_id
                ),
                None,
            )
            if (
                authorized_policy is None
                or any(
                    policy.access_basis
                    != (
                        "open_data_terms"
                        if artifact_kind == "open_data_source_basis_evidence"
                        else "zero_fee_plan"
                    )
                    for policy in policies
                )
                or policy_version_id not in evidence_policy_version_ids
                or evidence["license_id"] != authorized_policy.license_id
                or evidence["terms_url"] != authorized_policy.terms_url
                or evidence["terms_content_sha256"] != authorized_policy.terms_content_sha256
                or evidence["attribution"] != authorized_policy.attribution
                or {(item["dataset_id"], item["distribution_url"]) for item in distributions}
                != {
                    (item.dataset_id, item.distribution_url)
                    for policy in policies
                    for item in policy.distributions
                }
            ):
                raise ValueError("formal_gate_requires_verified_source_basis")
            if artifact_kind == "open_data_source_basis_evidence":
                if (
                    len(policies) != 1
                    or authorization.get("source_entitlement_version_id") is not None
                ):
                    raise ValueError("formal_gate_requires_verified_source_basis")
            elif authorization.get("source_entitlement_version_id") is None or any(
                evidence["source_basis_id"] != policy.source_basis_id
                or evidence["provider_id"] != policy.provider_id
                or evidence["plan_id"] != policy.plan_id
                or evidence["principal_classification"] != policy.principal_classification
                or evidence["credential_kind"] != policy.credential_kind
                or evidence["account_required"] != policy.account_required
                or evidence["fee_required"] != policy.fee_required
                or evidence["license_id"] != policy.license_id
                or evidence["terms_url"] != policy.terms_url
                or evidence["terms_content_sha256"] != policy.terms_content_sha256
                or evidence["attribution"] != policy.attribution
                for policy in policies
            ):
                raise ValueError("formal_gate_requires_verified_source_basis")
        return evidence

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
            source_archive_bindings = manifest.verified_source_archive_bindings(
                self._object_repository
            )
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
            self._validate_historical_evidence(claim, claim_id=claim_id)
            gate_payload = self._state_store.get_verified_governance_artifact(
                artifact_id=gate_id,
                artifact_kind="taiwan_price_qualification_gate",
            )
            source_basis_evidence_id = gate_payload.get("source_basis_evidence_id")
            if not isinstance(source_basis_evidence_id, str):
                return False
            source_basis_evidence = self._validate_source_basis_evidence(
                source_id=manifest.historical_source_id,
                evidence_id=source_basis_evidence_id,
                manifest=manifest,
            )
        except (KeyError, ValueError):
            return False
        return gate_payload == {
            "source_basis_id": source_basis_evidence["source_basis_id"],
            "manifest_id": manifest.manifest_id,
            "source_path_id": manifest.source_path_id,
            "current_source_id": manifest.current_source_id,
            "historical_source_id": manifest.historical_source_id,
            "historical_availability_claim_id": claim_id,
            "historical_qualification_artifact_id": claim.qualification_artifact_id,
            "source_basis_evidence_id": source_basis_evidence_id,
            "selection_source_archive_bindings": source_archive_bindings,
            "evidence_status": "qualified",
            "permitted_use": "historical_training_backtest_and_internal_research",
        }

    def register_formal_qualification_gate(
        self,
        *,
        manifest: TaiwanStockPoolManifest,
        historical_availability_claim_id: str,
        source_basis_evidence_id: str | None = None,
        trace_id: str,
    ) -> str:
        authorizations = self._authorize_sources(
            (manifest.current_source_id, manifest.historical_source_id),
            trace_id=trace_id,
            operation="register_formal_qualification_gate",
        )
        try:
            source_archive_bindings = manifest.verified_source_archive_bindings(
                self._object_repository
            )
        except ValueError as error:
            reason_code = "formal_gate_requires_verified_source_archive"
            self._state_store._publish_governance_rejection(
                payload={
                    "operation": "register_formal_qualification_gate",
                    "reason_code": reason_code,
                },
                trace_id=trace_id,
                authorizations=authorizations,
            )
            raise ValueError(reason_code) from error
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
            self._validate_historical_evidence(
                claim,
                claim_id=historical_availability_claim_id,
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
        try:
            if source_basis_evidence_id is None:
                raise ValueError("formal_gate_requires_verified_source_basis")
            historical_authorization = next(
                authorization
                for authorization in authorizations
                if authorization["dataset_id"] == manifest.historical_source_id
            )
            source_basis_evidence = self._validate_source_basis_evidence(
                source_id=manifest.historical_source_id,
                evidence_id=source_basis_evidence_id,
                manifest=manifest,
                authorization=historical_authorization,
            )
        except (KeyError, ValueError) as error:
            self._state_store._publish_governance_rejection(
                payload={
                    "operation": "register_formal_qualification_gate",
                    "reason_code": "formal_gate_requires_verified_source_basis",
                },
                trace_id=trace_id,
                authorizations=authorizations,
            )
            raise ValueError("formal_gate_requires_verified_source_basis") from error
        return self._state_store._publish_authorized_governance_artifact(
            artifact_kind="taiwan_price_qualification_gate",
            payload={
                "source_basis_id": source_basis_evidence["source_basis_id"],
                "manifest_id": manifest.manifest_id,
                "source_path_id": manifest.source_path_id,
                "current_source_id": manifest.current_source_id,
                "historical_source_id": manifest.historical_source_id,
                "historical_availability_claim_id": historical_availability_claim_id,
                "historical_qualification_artifact_id": claim.qualification_artifact_id,
                "source_basis_evidence_id": source_basis_evidence_id,
                "selection_source_archive_bindings": source_archive_bindings,
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
        for source_id in dict.fromkeys(source_ids):
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
