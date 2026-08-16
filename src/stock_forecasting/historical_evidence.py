from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Literal, cast

from stock_forecasting.authorization import (
    AuthorizationPolicy,
    OperationIntent,
    SecurityContext,
    SourceAccessMode,
    SourcePolicyVersion,
    authorization_audit_payload,
)
from stock_forecasting.data_supply import (
    PRICE_RESEARCH_REQUIRED_USES,
    HistoricalAvailabilityClaim,
)
from stock_forecasting.data_supply import HistoricalEvidenceLevel as HistoricalEvidenceLevel
from stock_forecasting.platform.object_repository import (
    FilesystemObjectRepository,
    ObjectIntegrityError,
)
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_adjustment import (
    PriceAdjustmentAction,
    UnadjustedClose,
    derive_adjusted_closes,
)

HistoricalEvidenceAction = Literal["qualify", "supersede", "revoke", "expire"]
SubmittedHistoricalEvidenceLevel = Literal[
    "platform_observed",
    "archive_attested",
    "published_current_only",
    "unknown",
    "self_asserted",
]


@dataclass(frozen=True)
class HistoricalEvidenceCommand:
    action: HistoricalEvidenceAction
    listing_id: str
    market: str
    source_id: str
    trace_id: str
    attestation_id: str | None = None
    submitted_evidence_level: SubmittedHistoricalEvidenceLevel | None = None
    prior_claim_id: str | None = None


@dataclass(frozen=True)
class HistoricalEvidenceAttestationCommand:
    listing_id: str
    market: str
    source_id: str
    evidence_level: HistoricalEvidenceLevel
    evidence_object_id: str
    calendar_object_id: str
    reference_object_id: str
    trace_id: str


@dataclass(frozen=True)
class HistoricalEvidenceOutcome:
    status: Literal["qualified", "quarantined", "revoked", "expired"]
    reason_code: str
    claim_id: str | None
    use_scope: tuple[str, ...] = ()
    artifact_ids: dict[str, str] = field(default_factory=dict)


class HistoricalEvidenceAuthorizationError(RuntimeError):
    pass


class HistoricalEvidenceAttestationIssuer:
    """Binds provider evidence to an authorized collection decision, without approving it."""

    def __init__(
        self,
        state_store: StateStore,
        *,
        object_repository: FilesystemObjectRepository,
        authorization_policy: AuthorizationPolicy,
        security_context: SecurityContext,
        clock: Callable[[], datetime],
    ) -> None:
        self._state_store = state_store
        self._object_repository = object_repository
        self._authorization_policy = authorization_policy
        self._security_context = security_context
        self._clock = clock

    def issue(self, command: HistoricalEvidenceAttestationCommand) -> str:
        first_observed_at = self._clock()
        authorizations, policy = _authorize_historical_evidence(
            state_store=self._state_store,
            authorization_policy=self._authorization_policy,
            security_context=self._security_context,
            action="market_data.collect",
            source_id=command.source_id,
            trace_id=command.trace_id,
            evaluated_at=first_observed_at,
            rejection_operation="attest_historical_evidence",
            listing_id=command.listing_id,
            market=command.market,
        )
        evidence_bytes = self._object_repository.open_by_id(command.evidence_object_id).read()
        calendar_bytes = self._object_repository.open_by_id(command.calendar_object_id).read()
        reference_bytes = self._object_repository.open_by_id(command.reference_object_id).read()
        return self._state_store._publish_historical_evidence_attestation(
            payload={
                "attestation_schema_version": "historical-evidence-attestation/v1",
                "listing_id": command.listing_id,
                "market": command.market,
                "source_id": command.source_id,
                "evidence_level": command.evidence_level,
                "evidence_object_id": command.evidence_object_id,
                "evidence_checksum": hashlib.sha256(evidence_bytes).hexdigest(),
                "calendar_object_id": command.calendar_object_id,
                "calendar_checksum": hashlib.sha256(calendar_bytes).hexdigest(),
                "reference_object_id": command.reference_object_id,
                "reference_checksum": hashlib.sha256(reference_bytes).hexdigest(),
                "source_policy_version_id": policy.version_id,
                "source_basis_id": policy.source_basis_id,
                "source_access_basis": policy.access_basis,
                "collection_authorization_decision_ids": sorted(
                    str(authorization["decision_id"]) for authorization in authorizations
                ),
                "collector_principal_ids": sorted(
                    {str(authorization["principal_id"]) for authorization in authorizations}
                ),
                "distribution_bindings": sorted(
                    [
                        {
                            "distribution_id": str(authorization["distribution_id"]),
                            "distribution_url": str(authorization["distribution_url"]),
                        }
                        for authorization in authorizations
                        if isinstance(authorization.get("distribution_id"), str)
                        and isinstance(authorization.get("distribution_url"), str)
                    ],
                    key=lambda binding: (
                        binding["distribution_id"],
                        binding["distribution_url"],
                    ),
                ),
                "first_observed_at": first_observed_at.isoformat(),
                "attested_at": first_observed_at.isoformat(),
            },
            trace_id=command.trace_id,
            authorizations=authorizations,
        )


class HistoricalEvidenceWorkflow:
    def __init__(
        self,
        state_store: StateStore,
        *,
        object_repository: FilesystemObjectRepository,
        observed_at: datetime,
        authorization_policy: AuthorizationPolicy,
        security_context: SecurityContext,
    ) -> None:
        self._state_store = state_store
        self._object_repository = object_repository
        self._observed_at = observed_at
        self._authorization_policy = authorization_policy
        self._security_context = security_context

    def execute(self, command: HistoricalEvidenceCommand) -> HistoricalEvidenceOutcome:
        authorizations, source_policy = _authorize_historical_evidence(
            state_store=self._state_store,
            authorization_policy=self._authorization_policy,
            security_context=self._security_context,
            action="price_qualification.govern",
            source_id=command.source_id,
            trace_id=command.trace_id,
            evaluated_at=self._observed_at,
            rejection_operation="qualify_historical_evidence",
            listing_id=command.listing_id,
            market=command.market,
        )
        rejected_levels = {
            "self_asserted": "historical_evidence_self_asserted",
            "published_current_only": "historical_evidence_current_only",
            "unknown": "historical_evidence_unknown",
        }
        submitted_level = command.submitted_evidence_level
        reason_code = rejected_levels.get(submitted_level) if submitted_level is not None else None
        if reason_code is not None:
            return self._quarantine(
                command,
                reason_code=reason_code,
                authorizations=authorizations,
                source_policy=source_policy,
                evidence_level=command.submitted_evidence_level,
            )
        if command.action in {"qualify", "supersede"}:
            return self._qualify(
                command,
                authorizations=authorizations,
                source_policy=source_policy,
            )
        if command.action in {"revoke", "expire"}:
            return self._record_claim_status(
                command,
                authorizations=authorizations,
                source_policy=source_policy,
            )
        raise NotImplementedError(command.action)

    def _qualify(
        self,
        command: HistoricalEvidenceCommand,
        *,
        authorizations: list[dict[str, object]],
        source_policy: SourcePolicyVersion,
    ) -> HistoricalEvidenceOutcome:
        prior_claim: dict[str, object] | None = None
        if command.action == "supersede":
            if command.prior_claim_id is None:
                raise ValueError("prior_claim_required")
            prior_claim = self._state_store.get_verified_governance_artifact(
                artifact_id=command.prior_claim_id,
                artifact_kind="historical_availability_claim",
            )
            if (
                prior_claim.get("listing_id") != command.listing_id
                or prior_claim.get("source_id") != command.source_id
            ):
                raise ValueError("prior_claim_scope_mismatch")
        try:
            attestation, evidence, listing, coverage, validity, evidence_level = (
                self._validated_evidence(
                    command=command,
                    source_policy=source_policy,
                )
            )
        except (KeyError, ObjectIntegrityError, TypeError, ValueError) as error:
            reason_code = (
                "historical_evidence_object_invalid"
                if isinstance(error, ObjectIntegrityError)
                else str(error) or "historical_evidence_invalid"
            )
            if reason_code.startswith("sha256:"):
                reason_code = "historical_evidence_object_invalid"
            return self._quarantine(
                command,
                reason_code=reason_code,
                authorizations=authorizations,
                source_policy=source_policy,
                evidence_level=command.submitted_evidence_level or "unknown",
            )
        evidence_object_id = str(attestation["evidence_object_id"])
        reconstruction_ready = self._can_build_reconstruction(evidence, listing)
        if evidence_level == "archive_attested" and not reconstruction_ready:
            return self._quarantine(
                command,
                reason_code="historical_reconstruction_contract_incomplete",
                authorizations=authorizations,
                source_policy=source_policy,
                evidence_level=evidence_level,
            )
        use_scope = (
            (
                ("production", "historical_reconstruction")
                if reconstruction_ready
                else ("production",)
            )
            if evidence_level == "platform_observed"
            else ("historical_reconstruction",)
        )
        verification_payload: dict[str, object] = {
            "verification_schema_version": "historical-evidence-verification/v1",
            "listing_id": command.listing_id,
            "market": command.market,
            "source_id": command.source_id,
            "evidence_level": evidence_level,
            "attestation_id": command.attestation_id,
            "evidence_object_id": evidence_object_id,
            "evidence_checksum": attestation["evidence_checksum"],
            "calendar_object_id": attestation["calendar_object_id"],
            "reference_object_id": attestation["reference_object_id"],
            "evidence_version": evidence["evidence_version"],
            "evidence_revision": evidence["revision"],
            "observation_kind": evidence["observation_kind"],
            "observation_reference": evidence["observation_reference"],
            "evidence_observed_at": evidence["observed_at"],
            "first_observed_at": attestation["first_observed_at"],
            "observed_start": coverage["start"],
            "observed_end": coverage["end"],
            "source_policy_id": source_policy.version_id,
            "source_basis_id": source_policy.source_basis_id,
            "public_terms_url": evidence["public_terms_url"],
            "valid_from": validity["valid_from"],
            "valid_until": validity["valid_until"],
            "verified_at": self._observed_at.isoformat(),
            "checks": {
                "exact_sessions": "passed",
                "integrity": "passed",
                "company_actions": "passed",
                "listing_lifecycle": "passed",
            },
            "use_scope": list(use_scope),
        }
        verification_id = self._publish(
            artifact_kind="historical_evidence_verification",
            payload=verification_payload,
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        claim_payload: dict[str, object] = {
            "claim_schema_version": "historical-availability-claim/v1",
            "schema_version": evidence["price_schema_version"],
            "listing_id": command.listing_id,
            "market": command.market,
            "source_id": command.source_id,
            "evidence_level": evidence_level,
            "evidence_status": "qualified",
            "attestation_id": command.attestation_id,
            "evidence_object_id": evidence_object_id,
            "evidence_checksum": attestation["evidence_checksum"],
            "evidence_version": evidence["evidence_version"],
            "evidence_revision": evidence["revision"],
            "observation_kind": evidence["observation_kind"],
            "observation_reference": evidence["observation_reference"],
            "evidence_observed_at": evidence["observed_at"],
            "first_observed_at": attestation["first_observed_at"],
            "observed_start": coverage["start"],
            "observed_end": coverage["end"],
            "source_policy_id": source_policy.version_id,
            "source_basis_id": source_policy.source_basis_id,
            "public_terms_url": evidence["public_terms_url"],
            "valid_from": validity["valid_from"],
            "valid_until": validity["valid_until"],
            "qualified_at": self._observed_at.isoformat(),
            "status": "qualified",
            "exact_sessions_verified": True,
            "integrity_verified": True,
            "company_actions_verified": True,
            "listing_lifecycle_verified": True,
            "qualification_artifact_id": verification_id,
            "supersedes_claim_id": command.prior_claim_id,
        }
        claim_id = self._publish(
            artifact_kind="historical_availability_claim",
            payload=claim_payload,
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        artifact_ids = {"claim": claim_id, "verification": verification_id}
        if reconstruction_ready:
            artifact_ids.update(
                self._build_reconstruction(
                    command=command,
                    evidence=evidence,
                    listing=listing,
                    claim_id=claim_id,
                    evidence_level=evidence_level,
                    source_policy_id=source_policy.version_id,
                    evidence_object_id=evidence_object_id,
                    authorizations=authorizations,
                )
            )
        if prior_claim is not None:
            impact_id = self._publish(
                artifact_kind="historical_claim_impact",
                payload={
                    "impact_schema_version": "historical-claim-impact/v1",
                    "event": "superseded",
                    "prior_claim_id": command.prior_claim_id,
                    "replacement_claim_id": claim_id,
                    "listing_id": command.listing_id,
                    "source_id": command.source_id,
                    "recorded_at": self._observed_at.isoformat(),
                    "affected_artifact_ids": self._affected_artifact_ids(
                        cast(str, command.prior_claim_id)
                    ),
                },
                trace_id=command.trace_id,
                authorizations=authorizations,
            )
            artifact_ids["impact"] = impact_id
        return HistoricalEvidenceOutcome(
            status="qualified",
            reason_code="historical_evidence_qualified",
            claim_id=claim_id,
            use_scope=use_scope,
            artifact_ids=artifact_ids,
        )

    def _record_claim_status(
        self,
        command: HistoricalEvidenceCommand,
        *,
        authorizations: list[dict[str, object]],
        source_policy: SourcePolicyVersion,
    ) -> HistoricalEvidenceOutcome:
        if command.prior_claim_id is None:
            raise ValueError("prior_claim_required")
        prior_claim = self._state_store.get_verified_governance_artifact(
            artifact_id=command.prior_claim_id,
            artifact_kind="historical_availability_claim",
        )
        if (
            prior_claim.get("listing_id") != command.listing_id
            or prior_claim.get("source_id") != command.source_id
            or prior_claim.get("source_policy_id") != source_policy.version_id
        ):
            raise ValueError("prior_claim_scope_mismatch")
        event = "revoked" if command.action == "revoke" else "expired"
        impact_id = self._publish(
            artifact_kind="historical_claim_impact",
            payload={
                "impact_schema_version": "historical-claim-impact/v1",
                "event": event,
                "prior_claim_id": command.prior_claim_id,
                "replacement_claim_id": None,
                "listing_id": command.listing_id,
                "source_id": command.source_id,
                "recorded_at": self._observed_at.isoformat(),
                "affected_artifact_ids": self._affected_artifact_ids(command.prior_claim_id),
            },
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        return HistoricalEvidenceOutcome(
            status=cast(Literal["revoked", "expired"], event),
            reason_code=f"historical_evidence_{event}",
            claim_id=command.prior_claim_id,
            artifact_ids={"claim": command.prior_claim_id, "impact": impact_id},
        )

    def _affected_artifact_ids(self, claim_id: str) -> list[str]:
        for report in self._state_store.list_historical_qualification_reports():
            if report.get("historical_availability_claim_id") != claim_id:
                continue
            dataset_ids = report.get("dataset_version_ids")
            affected = [str(item) for item in dataset_ids] if isinstance(dataset_ids, list) else []
            for field_name in (
                "qualification_report_id",
                "adjustment_version_id",
                "mature_labels_id",
                "feature_snapshot_id",
                "fold_manifest_id",
            ):
                artifact_id = report.get(field_name)
                if isinstance(artifact_id, str):
                    affected.append(artifact_id)
            return sorted(set(affected))
        return []

    def _quarantine(
        self,
        command: HistoricalEvidenceCommand,
        *,
        reason_code: str,
        authorizations: list[dict[str, object]],
        source_policy: SourcePolicyVersion,
        evidence_level: object,
    ) -> HistoricalEvidenceOutcome:
        report_id = self._publish(
            artifact_kind="historical_qualification_report",
            payload={
                "qualification_report_schema_version": "historical-qualification-report/v1",
                "listing_id": command.listing_id,
                "market": command.market,
                "source_id": command.source_id,
                "status": "quarantined",
                "reason_code": reason_code,
                "historical_availability_claim_id": None,
                "evidence_level": evidence_level,
                "source_policy_id": source_policy.version_id,
                "display_mode": "historical_reconstruction",
                "production_prediction": False,
                "exclusion_reasons": [reason_code],
                "created_at": self._observed_at.isoformat(),
            },
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        return HistoricalEvidenceOutcome(
            status="quarantined",
            reason_code=reason_code,
            claim_id=None,
            artifact_ids={"qualification_report": report_id},
        )

    def _validated_evidence(
        self,
        *,
        command: HistoricalEvidenceCommand,
        source_policy: SourcePolicyVersion,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        dict[str, object],
        dict[str, object],
        dict[str, object],
        HistoricalEvidenceLevel,
    ]:
        if command.attestation_id is None:
            raise ValueError("historical_evidence_attestation_required")
        attestation = self._state_store.get_verified_governance_artifact(
            artifact_id=command.attestation_id,
            artifact_kind="historical_evidence_attestation",
        )
        evidence_level = attestation.get("evidence_level")
        if evidence_level == "published_current_only":
            raise ValueError("historical_evidence_current_only")
        if evidence_level not in {"platform_observed", "archive_attested"}:
            raise ValueError("historical_evidence_attestation_invalid")
        if (
            attestation.get("listing_id") != command.listing_id
            or attestation.get("market") != command.market
            or attestation.get("source_id") != command.source_id
            or attestation.get("source_policy_version_id") != source_policy.version_id
        ):
            raise ValueError("historical_evidence_attestation_scope_mismatch")
        collector_principal_ids = attestation.get("collector_principal_ids")
        if not isinstance(collector_principal_ids, list) or (
            self._security_context.principal_id in collector_principal_ids
        ):
            raise ValueError("historical_evidence_separation_of_duties_required")
        evidence_bytes = self._verified_attested_object(attestation, "evidence")
        calendar_bytes = self._verified_attested_object(attestation, "calendar")
        reference_bytes = self._verified_attested_object(attestation, "reference")
        evidence = _json_object(evidence_bytes)
        calendar = _json_object(calendar_bytes)
        reference = _json_object(reference_bytes)
        listings = evidence.get("listings")
        if not isinstance(listings, list):
            raise ValueError("historical_evidence_invalid")
        listing = next(
            (
                item
                for item in listings
                if isinstance(item, dict)
                and item.get("listing_id") == command.listing_id
                and item.get("market") == command.market
            ),
            None,
        )
        expected_observation_kind = {
            "platform_observed": "platform_observation",
            "archive_attested": "official_archive",
        }[cast(Literal["platform_observed", "archive_attested"], evidence_level)]
        required_evidence_strings = (
            "evidence_version",
            "revision",
            "observation_reference",
            "observed_at",
            "public_terms_url",
            "calendar_version",
        )
        if (
            evidence.get("schema_version") != "historical-reconstruction-evidence/v1"
            or evidence.get("price_schema_version")
            not in {"taiwan-unadjusted-eod-v1", "us-unadjusted-eod-v1"}
            or evidence.get("observation_kind") != expected_observation_kind
            or any(not isinstance(evidence.get(field), str) for field in required_evidence_strings)
            or listing is None
        ):
            raise ValueError("historical_evidence_invalid")
        if isinstance(source_policy.terms_url, str) and (
            evidence.get("public_terms_url") != source_policy.terms_url
        ):
            raise ValueError("historical_evidence_source_policy_mismatch")
        distribution_bindings = attestation.get("distribution_bindings")
        if (
            not isinstance(distribution_bindings, list)
            or not distribution_bindings
            or not all(
                isinstance(binding, dict)
                and set(binding) == {"distribution_id", "distribution_url"}
                and isinstance(binding.get("distribution_id"), str)
                and isinstance(binding.get("distribution_url"), str)
                for binding in distribution_bindings
            )
        ):
            raise ValueError("historical_evidence_attestation_invalid")
        authorized_distribution_urls = {
            str(cast(dict[str, object], binding)["distribution_url"])
            for binding in distribution_bindings
        }
        if (
            evidence_level == "archive_attested"
            and evidence.get("observation_reference") not in authorized_distribution_urls
        ) or (
            evidence_level == "platform_observed"
            and not str(evidence.get("observation_reference")).startswith("platform://")
        ):
            raise ValueError("historical_evidence_distribution_mismatch")
        if (
            calendar.get("source_reference") not in authorized_distribution_urls
            or reference.get("source_reference") not in authorized_distribution_urls
        ):
            raise ValueError("historical_evidence_distribution_mismatch")
        coverage = evidence.get("coverage")
        validity = evidence.get("validity")
        if not isinstance(coverage, dict) or not isinstance(validity, dict):
            raise ValueError("historical_evidence_invalid")
        try:
            valid_from = datetime.fromisoformat(str(validity["valid_from"]))
            valid_until = datetime.fromisoformat(str(validity["valid_until"]))
            observed_at = datetime.fromisoformat(str(evidence["observed_at"]))
            first_observed_at = datetime.fromisoformat(str(attestation["first_observed_at"]))
            attested_at = datetime.fromisoformat(str(attestation["attested_at"]))
        except (KeyError, ValueError) as error:
            raise ValueError("historical_evidence_validity_invalid") from error
        if (
            valid_from.tzinfo is None
            or valid_until.tzinfo is None
            or observed_at.tzinfo is None
            or first_observed_at.tzinfo is None
            or attested_at.tzinfo is None
            or valid_from > observed_at
            or valid_until <= valid_from
        ):
            raise ValueError("historical_evidence_validity_invalid")
        if (
            observed_at > first_observed_at
            or first_observed_at != attested_at
            or first_observed_at > self._observed_at
        ):
            raise ValueError("historical_evidence_observation_chronology_invalid")
        self._validate_listing_evidence(
            command=command,
            evidence=evidence,
            listing=listing,
            calendar=calendar,
            reference=reference,
            coverage=coverage,
        )
        return (
            attestation,
            evidence,
            listing,
            cast(dict[str, object], coverage),
            cast(dict[str, object], validity),
            cast(HistoricalEvidenceLevel, evidence_level),
        )

    def _verified_attested_object(
        self,
        attestation: dict[str, object],
        object_kind: Literal["evidence", "calendar", "reference"],
    ) -> bytes:
        object_id = attestation.get(f"{object_kind}_object_id")
        expected_checksum = attestation.get(f"{object_kind}_checksum")
        if not isinstance(object_id, str) or not isinstance(expected_checksum, str):
            raise ValueError("historical_evidence_attestation_invalid")
        content = self._object_repository.open_by_id(object_id).read()
        if hashlib.sha256(content).hexdigest() != expected_checksum:
            raise ValueError("historical_evidence_attestation_integrity_mismatch")
        return content

    @staticmethod
    def _validate_listing_evidence(
        *,
        command: HistoricalEvidenceCommand,
        evidence: dict[str, object],
        listing: dict[str, object],
        calendar: dict[str, object],
        reference: dict[str, object],
        coverage: dict[str, object],
    ) -> None:
        sessions = listing.get("sessions")
        prices = listing.get("unadjusted_prices")
        actions = listing.get("company_actions")
        lifecycle = listing.get("lifecycle")
        symbols = listing.get("symbols")
        if (
            not isinstance(sessions, list)
            or not sessions
            or not all(isinstance(session, str) for session in sessions)
            or sessions != sorted(set(cast(list[str], sessions)))
            or not isinstance(prices, list)
            or not prices
            or not isinstance(actions, list)
            or not isinstance(lifecycle, list)
            or not lifecycle
            or not isinstance(symbols, list)
            or not symbols
            or not isinstance(listing.get("security_id"), str)
        ):
            raise ValueError("historical_evidence_invalid")
        if listing.get("company_actions_status") != "complete":
            raise ValueError("historical_evidence_company_actions_incomplete")
        if (
            calendar.get("schema_version") != "historical-realized-calendar/v1"
            or calendar.get("market") != command.market
            or calendar.get("version") != evidence["calendar_version"]
            or calendar.get("sessions") != sessions
        ):
            raise ValueError("historical_evidence_calendar_mismatch")
        reference_listing = reference.get("listing")
        if (
            reference.get("schema_version") != "historical-listing-reference/v1"
            or not isinstance(reference_listing, dict)
            or reference_listing.get("listing_id") != command.listing_id
            or reference_listing.get("market") != command.market
            or reference_listing.get("security_id") != listing["security_id"]
            or reference_listing.get("symbols") != symbols
            or reference_listing.get("lifecycle") != lifecycle
            or reference_listing.get("company_actions") != actions
        ):
            raise ValueError("historical_evidence_reference_mismatch")
        parsed_sessions = [date.fromisoformat(session) for session in cast(list[str], sessions)]
        if coverage.get("start") != sessions[0] or coverage.get("end") != sessions[-1]:
            raise ValueError("historical_evidence_session_mismatch")
        price_sessions: list[str] = []
        for row in cast(list[object], prices):
            if not isinstance(row, dict) or set(row) != {"session_date", "close"}:
                raise ValueError("historical_evidence_price_invalid")
            session = row.get("session_date")
            try:
                close = Decimal(str(row["close"]))
            except (KeyError, ValueError) as error:
                raise ValueError("historical_evidence_price_invalid") from error
            if not isinstance(session, str) or session not in sessions or close <= 0:
                raise ValueError("historical_evidence_price_invalid")
            price_sessions.append(session)
        if price_sessions != [session for session in sessions if session in set(price_sessions)]:
            raise ValueError("historical_evidence_session_mismatch")
        parsed_lifecycle: list[tuple[date, str]] = []
        for event in cast(list[object], lifecycle):
            if not isinstance(event, dict) or event.get("status") not in {
                "active",
                "suspended",
                "delisted",
            }:
                raise ValueError("historical_evidence_lifecycle_invalid")
            if not isinstance(event.get("source_event_id"), str):
                raise ValueError("historical_evidence_lifecycle_invalid")
            parsed_lifecycle.append(
                (date.fromisoformat(str(event["effective_date"])), str(event["status"]))
            )
        if parsed_lifecycle != sorted(parsed_lifecycle) or any(
            next(
                (
                    status
                    for effective, status in reversed(parsed_lifecycle)
                    if effective <= session
                ),
                None,
            )
            != "active"
            for session in parsed_sessions
        ):
            raise ValueError("historical_evidence_lifecycle_mismatch")
        symbol_validity: list[tuple[date, date | None]] = []
        for symbol in cast(list[object], symbols):
            if not isinstance(symbol, dict) or not isinstance(symbol.get("symbol"), str):
                raise ValueError("historical_evidence_symbol_invalid")
            valid_from = date.fromisoformat(str(symbol["valid_from"]))
            valid_to_value = symbol.get("valid_to")
            valid_to = (
                date.fromisoformat(str(valid_to_value)) if valid_to_value is not None else None
            )
            if valid_to is not None and valid_to < valid_from:
                raise ValueError("historical_evidence_symbol_invalid")
            symbol_validity.append((valid_from, valid_to))
        if any(
            not any(
                valid_from <= session and (valid_to is None or session <= valid_to)
                for valid_from, valid_to in symbol_validity
            )
            for session in parsed_sessions
        ):
            raise ValueError("historical_evidence_symbol_mismatch")
        for action in cast(list[object], actions):
            if not isinstance(action, dict) or action.get("kind") not in {
                "cash_dividend",
                "split",
            }:
                raise ValueError("historical_evidence_company_action_invalid")
            try:
                PriceAdjustmentAction(
                    listing_id=command.listing_id,
                    effective_date=date.fromisoformat(str(action["effective_date"])),
                    kind=cast(Literal["cash_dividend", "split"], action["kind"]),
                    value=Decimal(str(action["value"])),
                    source_action_id=str(action["source_action_id"]),
                )
            except (KeyError, ValueError) as error:
                raise ValueError("historical_evidence_company_action_invalid") from error

    @staticmethod
    def _can_build_reconstruction(evidence: dict[str, object], listing: dict[str, object]) -> bool:
        return listing.get("company_actions_status") == "complete" and all(
            isinstance(evidence.get(field_name), str)
            for field_name in (
                "calendar_version",
                "adjustment_rule_version",
                "label_rule_version",
                "code_provenance",
            )
        )

    def _build_reconstruction(
        self,
        *,
        command: HistoricalEvidenceCommand,
        evidence: dict[str, object],
        listing: dict[str, object],
        claim_id: str,
        evidence_level: HistoricalEvidenceLevel,
        source_policy_id: str,
        evidence_object_id: str,
        authorizations: list[dict[str, object]],
    ) -> dict[str, str]:
        common_lineage: dict[str, object] = {
            "listing_id": command.listing_id,
            "security_id": listing["security_id"],
            "market": command.market,
            "source_id": command.source_id,
            "historical_availability_claim_id": claim_id,
            "evidence_level": evidence_level,
            "evidence_object_id": evidence_object_id,
            "calendar_version": evidence["calendar_version"],
            "label_rule_version": evidence["label_rule_version"],
            "source_policy_id": source_policy_id,
            "code_provenance": evidence["code_provenance"],
            "created_at": self._observed_at.isoformat(),
        }
        dataset_object_id = self._store_json(
            {
                "dataset_schema_version": "historical-reconstruction-data/v1",
                "listing_id": command.listing_id,
                "security_id": listing["security_id"],
                "sessions": listing["sessions"],
                "symbols": listing["symbols"],
                "lifecycle": listing["lifecycle"],
                "unadjusted_prices": listing["unadjusted_prices"],
                "company_actions": listing["company_actions"],
            },
            object_kind="historical_reconstruction_dataset",
        )
        dataset_payload: dict[str, object] = {
            **common_lineage,
            "dataset_schema_version": "historical-reconstruction-dataset/v1",
            "evidence_version": evidence["evidence_version"],
            "evidence_revision": evidence["revision"],
            "dataset_object_id": dataset_object_id,
            "session_count": len(cast(list[object], listing["sessions"])),
        }
        dataset_id = self._publish(
            artifact_kind="historical_reconstruction_dataset",
            payload=dataset_payload,
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        adjusted_prices = self._adjusted_prices(listing)
        adjustment_object_id = self._store_json(
            {
                "adjustment_data_schema_version": "historical-adjustment-data/v1",
                "adjusted_prices": adjusted_prices,
                "company_action_ids": [
                    action.get("source_action_id")
                    for action in cast(list[dict[str, object]], listing["company_actions"])
                ],
            },
            object_kind="historical_adjustment_version",
        )
        adjustment_payload: dict[str, object] = {
            **common_lineage,
            "adjustment_schema_version": "historical-adjustment-version/v1",
            "dataset_version_id": dataset_id,
            "adjustment_rule_version": evidence["adjustment_rule_version"],
            "adjustment_object_id": adjustment_object_id,
        }
        adjustment_version_id = self._publish(
            artifact_kind="historical_adjustment_version",
            payload=adjustment_payload,
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        labels_payload = self._mature_labels(
            command=command,
            evidence=evidence,
            listing=listing,
            claim_id=claim_id,
            dataset_id=dataset_id,
            adjustment_version_id=adjustment_version_id,
            adjusted_prices=adjusted_prices,
            source_policy_id=source_policy_id,
        )
        labels_object_id = self._store_json(
            labels_payload,
            object_kind="historical_mature_labels",
        )
        labels_id = self._publish(
            artifact_kind="historical_mature_labels",
            payload={
                **common_lineage,
                "mature_labels_schema_version": "historical-mature-labels-artifact/v1",
                "dataset_version_id": dataset_id,
                "adjustment_version_id": adjustment_version_id,
                "labels_object_id": labels_object_id,
            },
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        labels = cast(list[dict[str, object]], labels_payload["labels"])
        exclusions = [
            f"{label['status']}:horizon_{label['horizon_sessions']}"
            for label in labels
            if label["status"] != "mature"
        ]
        derived_lineage = {
            **common_lineage,
            "dataset_version_ids": [dataset_id],
            "adjustment_version_id": adjustment_version_id,
            "mature_labels_id": labels_id,
        }
        feature_snapshot_id = self._publish(
            artifact_kind="historical_feature_snapshot",
            payload={
                **derived_lineage,
                "feature_snapshot_schema_version": "historical-feature-snapshot/v1",
                "information_cutoff": cast(list[str], listing["sessions"])[
                    min(20, len(cast(list[object], listing["sessions"])) - 1)
                ],
                "execution_purpose": "historical_reconstruction",
            },
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        fold_manifest_id = self._publish(
            artifact_kind="historical_fold_manifest",
            payload={
                **derived_lineage,
                "fold_manifest_schema_version": "historical-fold-manifest/v1",
                "feature_snapshot_id": feature_snapshot_id,
                "execution_purpose": "historical_reconstruction",
            },
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        report_id = self._publish(
            artifact_kind="historical_qualification_report",
            payload={
                **derived_lineage,
                "qualification_report_schema_version": "historical-qualification-report/v1",
                "status": "qualified",
                "display_mode": "historical_reconstruction",
                "production_prediction": False,
                "evidence_level": evidence_level,
                "evidence_revision": evidence["revision"],
                "exact_session_count": len(cast(list[object], listing["sessions"])),
                "unadjusted_prices_verified": True,
                "company_actions_verified": True,
                "listing_lifecycle_verified": True,
                "exact_endpoints_verified": not exclusions,
                "exclusion_reasons": exclusions,
                "feature_snapshot_id": feature_snapshot_id,
                "fold_manifest_id": fold_manifest_id,
            },
            trace_id=command.trace_id,
            authorizations=authorizations,
        )
        return {
            "qualification_report": report_id,
            "dataset": dataset_id,
            "adjustment_version": adjustment_version_id,
            "mature_labels": labels_id,
            "feature_snapshot": feature_snapshot_id,
            "fold_manifest": fold_manifest_id,
        }

    def _mature_labels(
        self,
        *,
        command: HistoricalEvidenceCommand,
        evidence: dict[str, object],
        listing: dict[str, object],
        claim_id: str,
        dataset_id: str,
        adjustment_version_id: str,
        adjusted_prices: list[dict[str, str]],
        source_policy_id: str,
    ) -> dict[str, object]:
        sessions = cast(list[str], listing["sessions"])
        prices = {row["session_date"]: Decimal(row["adjusted_close"]) for row in adjusted_prices}
        anchor_index = min(20, len(sessions) - 1)
        anchor_session = sessions[anchor_index]
        if anchor_index < 20 or any(
            session not in prices for session in sessions[: anchor_index + 1]
        ):
            return {
                "mature_labels_schema_version": "historical-mature-labels/v1",
                "listing_id": command.listing_id,
                "anchor_session_id": anchor_session,
                "labels": [
                    {
                        "horizon_sessions": horizon,
                        "target_session_id": None,
                        "status": "invalid_history",
                        "reason_code": "insufficient_20_session_history",
                        "future_return": None,
                        "sigma20": None,
                        "threshold": None,
                        "label": None,
                    }
                    for horizon in (1, 5, 20)
                ],
                "label_rule_version": evidence["label_rule_version"],
                "realized_calendar_version": evidence["calendar_version"],
                "adjustment_version_id": adjustment_version_id,
                "dataset_version_id": dataset_id,
                "historical_availability_claim_id": claim_id,
                "source_policy_id": source_policy_id,
                "code_provenance": evidence["code_provenance"],
                "created_at": self._observed_at.isoformat(),
            }
        returns = [
            (prices[sessions[index]] / prices[sessions[index - 1]]).ln()
            for index in range(1, anchor_index + 1)
        ]
        mean_return = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = sum(
            ((realized_return - mean_return) ** 2 for realized_return in returns),
            Decimal(0),
        ) / Decimal(len(returns) - 1)
        sigma20 = variance.sqrt()
        market_floor = Decimal("0.006") if command.market == "XTAI" else Decimal("0.0025")
        labels: list[dict[str, object]] = []
        for horizon in (1, 5, 20):
            threshold = max(
                market_floor,
                Decimal("0.35") * sigma20 * Decimal(horizon).sqrt(),
            )
            if anchor_index + horizon >= len(sessions):
                labels.append(
                    {
                        "horizon_sessions": horizon,
                        "target_session_id": None,
                        "status": "invalid_endpoint",
                        "reason_code": "exact_target_session_missing",
                        "future_return": None,
                        "sigma20": _decimal_text(sigma20),
                        "threshold": _decimal_text(threshold),
                        "label": None,
                    }
                )
                continue
            target_session = sessions[anchor_index + horizon]
            if target_session not in prices:
                labels.append(
                    {
                        "horizon_sessions": horizon,
                        "target_session_id": target_session,
                        "status": "invalid_endpoint",
                        "reason_code": "exact_target_price_missing",
                        "future_return": None,
                        "sigma20": _decimal_text(sigma20),
                        "threshold": _decimal_text(threshold),
                        "label": None,
                    }
                )
                continue
            future_return = prices[target_session] / prices[anchor_session] - Decimal(1)
            label = (
                "up"
                if future_return > threshold
                else "down"
                if future_return < -threshold
                else "flat"
            )
            labels.append(
                {
                    "horizon_sessions": horizon,
                    "target_session_id": target_session,
                    "status": "mature",
                    "reason_code": None,
                    "future_return": _decimal_text(future_return),
                    "sigma20": _decimal_text(sigma20),
                    "threshold": _decimal_text(threshold),
                    "label": label,
                }
            )
        return {
            "mature_labels_schema_version": "historical-mature-labels/v1",
            "listing_id": command.listing_id,
            "anchor_session_id": anchor_session,
            "labels": labels,
            "label_rule_version": evidence["label_rule_version"],
            "realized_calendar_version": evidence["calendar_version"],
            "adjustment_version_id": adjustment_version_id,
            "dataset_version_id": dataset_id,
            "historical_availability_claim_id": claim_id,
            "source_policy_id": source_policy_id,
            "code_provenance": evidence["code_provenance"],
            "created_at": self._observed_at.isoformat(),
        }

    @staticmethod
    def _adjusted_prices(listing: dict[str, object]) -> list[dict[str, str]]:
        listing_id = str(listing["listing_id"])
        adjusted = derive_adjusted_closes(
            tuple(
                UnadjustedClose(
                    listing_id=listing_id,
                    session_date=date.fromisoformat(str(price["session_date"])),
                    close=Decimal(str(price["close"])),
                )
                for price in cast(list[dict[str, object]], listing["unadjusted_prices"])
            ),
            tuple(
                PriceAdjustmentAction(
                    listing_id=listing_id,
                    effective_date=date.fromisoformat(str(action["effective_date"])),
                    kind=cast(Literal["cash_dividend", "split"], action["kind"]),
                    value=Decimal(str(action["value"])),
                    source_action_id=str(action["source_action_id"]),
                )
                for action in cast(list[dict[str, object]], listing["company_actions"])
            ),
        )
        return [
            {
                "session_date": row.session_date.isoformat(),
                "adjusted_close": str(row.adjusted_close),
            }
            for row in adjusted
        ]

    def _store_json(
        self,
        payload: dict[str, object],
        *,
        object_kind: str,
    ) -> str:
        content = _canonical_json_bytes(payload)
        return self._object_repository.put_verified(
            BytesIO(content),
            expected_checksum=hashlib.sha256(content).hexdigest(),
            metadata={
                "content_type": "application/json",
                "object_kind": object_kind,
            },
        ).object_id

    def _publish(
        self,
        *,
        artifact_kind: str,
        payload: dict[str, object],
        trace_id: str,
        authorizations: list[dict[str, object]],
    ) -> str:
        return self._state_store._publish_historical_evidence_artifact(
            artifact_kind=artifact_kind,
            payload=payload,
            trace_id=trace_id,
            authorizations=authorizations,
        )


class QualifiedHistoricalAvailabilityClaimVerifier:
    def __init__(self, state_store: StateStore, *, evaluated_at: datetime) -> None:
        self._state_store = state_store
        self._evaluated_at = evaluated_at

    def is_usable(
        self,
        *,
        claim_id: str,
        claim: HistoricalAvailabilityClaim,
    ) -> bool:
        if claim.evidence_status != "qualified" or claim.qualification_artifact_id is None:
            return False
        try:
            stored_payload = self._state_store.get_verified_governance_artifact(
                artifact_id=claim_id,
                artifact_kind="historical_availability_claim",
            )
            if HistoricalAvailabilityClaim.from_payload(stored_payload) != claim:
                return False
            verification = self._state_store.get_verified_governance_artifact(
                artifact_id=claim.qualification_artifact_id,
                artifact_kind="historical_evidence_verification",
            )
            valid_from = datetime.fromisoformat(str(verification["valid_from"]))
            valid_until = datetime.fromisoformat(str(verification["valid_until"]))
        except (KeyError, ValueError):
            return False
        checks = verification.get("checks")
        use_scope = verification.get("use_scope")
        if (
            not isinstance(checks, dict)
            or set(checks.values()) != {"passed"}
            or not isinstance(use_scope, list)
            or "historical_reconstruction" not in use_scope
            or verification.get("source_id") != claim.source_id
            or verification.get("evidence_level") != claim.evidence_level
            or not (valid_from <= self._evaluated_at < valid_until)
        ):
            return False
        impacts = self._state_store.list_historical_claim_impacts(claim_id=claim_id)
        return not any(
            impact.get("event") in {"superseded", "revoked", "expired"} for impact in impacts
        )

    def is_formally_reconstructable(
        self,
        *,
        claim_id: str,
        claim: HistoricalAvailabilityClaim,
    ) -> bool:
        if not self.is_usable(claim_id=claim_id, claim=claim):
            return False
        reports = [
            report
            for report in self._state_store.list_historical_qualification_reports()
            if report.get("historical_availability_claim_id") == claim_id
        ]
        if len(reports) != 1:
            return False
        report = reports[0]
        dataset_ids = report.get("dataset_version_ids")
        if (
            report.get("status") != "qualified"
            or report.get("evidence_level") != claim.evidence_level
            or report.get("exact_endpoints_verified") is not True
            or report.get("exclusion_reasons") != []
            or report.get("production_prediction") is not False
            or not isinstance(dataset_ids, list)
            or len(dataset_ids) != 1
            or not all(isinstance(item, str) for item in dataset_ids)
        ):
            return False
        expected_artifacts = {
            str(dataset_ids[0]): "historical_reconstruction_dataset",
            report.get("adjustment_version_id"): "historical_adjustment_version",
            report.get("mature_labels_id"): "historical_mature_labels",
            report.get("feature_snapshot_id"): "historical_feature_snapshot",
            report.get("fold_manifest_id"): "historical_fold_manifest",
        }
        if any(not isinstance(artifact_id, str) for artifact_id in expected_artifacts):
            return False
        try:
            return all(
                self._state_store.get_canonical_artifact(cast(str, artifact_id)).get(
                    "artifact_kind"
                )
                == artifact_kind
                for artifact_id, artifact_kind in expected_artifacts.items()
            )
        except KeyError:
            return False


def historical_qualification_projections(
    state_store: StateStore,
    *,
    evaluated_at: datetime,
    listing_id: str | None = None,
) -> list[dict[str, object]]:
    reports = state_store.list_historical_qualification_reports(listing_id=listing_id)
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for report in reports:
        key = (str(report["listing_id"]), str(report["source_id"]))
        if key in latest:
            continue
        if report.get("status") in {"quarantined", "policy_blocked"}:
            latest[key] = {
                "listing_id": report["listing_id"],
                "market": report["market"],
                "source_id": report["source_id"],
                "source_mode": "historical_reconstruction",
                "status": report["status"],
                "reason_code": report["reason_code"],
                "historical_availability_claim_id": None,
                "claim_id": None,
                "evidence_level": report["evidence_level"],
                "source_policy_id": report["source_policy_id"],
                "qualification_report_id": report["qualification_report_id"],
                "exclusion_reasons": report["exclusion_reasons"],
                "display_mode": "historical_reconstruction",
                "production_prediction": False,
            }
            continue
        claim_id = str(report["historical_availability_claim_id"])
        claim = state_store.get_verified_governance_artifact(
            artifact_id=claim_id,
            artifact_kind="historical_availability_claim",
        )
        status = "qualified"
        reason_code = "historical_reconstruction_qualified"
        exclusions = report.get("exclusion_reasons")
        if isinstance(exclusions, list) and exclusions:
            status = (
                "invalid_endpoint"
                if any(str(reason).startswith("invalid_endpoint:") for reason in exclusions)
                else "quarantined"
            )
            reason_code = str(exclusions[0])
        impacts = state_store.list_historical_claim_impacts(claim_id=claim_id)
        if impacts:
            event = str(impacts[-1]["event"])
            status = "expired" if event == "superseded" else event
            reason_code = f"historical_claim_{event}"
        elif evaluated_at >= datetime.fromisoformat(str(claim["valid_until"])):
            status = "expired"
            reason_code = "historical_claim_validity_expired"
        latest[key] = {
            "listing_id": report["listing_id"],
            "market": report["market"],
            "source_id": report["source_id"],
            "source_mode": "historical_reconstruction",
            "status": status,
            "reason_code": reason_code,
            "historical_availability_claim_id": claim_id,
            "claim_id": claim_id,
            "evidence_level": report["evidence_level"],
            "evidence_revision": report["evidence_revision"],
            "source_policy_id": report["source_policy_id"],
            "qualification_report_id": report["qualification_report_id"],
            "dataset_version_ids": report["dataset_version_ids"],
            "adjustment_version_id": report["adjustment_version_id"],
            "mature_labels_id": report["mature_labels_id"],
            "feature_snapshot_id": report["feature_snapshot_id"],
            "fold_manifest_id": report["fold_manifest_id"],
            "exclusion_reasons": report["exclusion_reasons"],
            "display_mode": "historical_reconstruction",
            "production_prediction": False,
        }
    return list(latest.values())


def _authorize_historical_evidence(
    *,
    state_store: StateStore,
    authorization_policy: AuthorizationPolicy,
    security_context: SecurityContext,
    action: Literal["market_data.collect", "price_qualification.govern"],
    source_id: str,
    trace_id: str,
    evaluated_at: datetime,
    rejection_operation: str,
    listing_id: str,
    market: str,
) -> tuple[list[dict[str, object]], SourcePolicyVersion]:
    candidate_policies = [
        policy for policy in authorization_policy.source_policies if policy.dataset_id == source_id
    ]
    if len(candidate_policies) != 1:
        decision = authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action=action,
                dataset_id=source_id,
                purpose="price_research",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=f"{trace_id}:{source_id}:{action}:source-policy",
                required_uses=PRICE_RESEARCH_REQUIRED_USES,
                source_access_mode="live_provider",
            ),
        )
        authorizations = [authorization_audit_payload(decision)]
        _publish_historical_authorization_denial(
            state_store=state_store,
            rejection_operation=rejection_operation,
            listing_id=listing_id,
            market=market,
            source_id=source_id,
            trace_id=trace_id,
            evaluated_at=evaluated_at,
            reason_code=decision.reason_code,
            authorizations=authorizations,
        )
        raise HistoricalEvidenceAuthorizationError(decision.reason_code)
    source_policy = candidate_policies[0]
    source_access_mode: SourceAccessMode = (
        "engineering_double"
        if source_policy.access_basis == "engineering_contract"
        else "live_provider"
    )
    distributions = source_policy.distributions or (None,)
    decisions = [
        authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action=action,
                dataset_id=source_id,
                purpose="price_research",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=(
                    f"{trace_id}:{source_id}:{action}:"
                    f"{distribution.dataset_id if distribution is not None else 'source'}"
                ),
                required_uses=PRICE_RESEARCH_REQUIRED_USES,
                source_access_mode=source_access_mode,
                distribution_id=(distribution.dataset_id if distribution is not None else None),
                distribution_url=(
                    distribution.distribution_url if distribution is not None else None
                ),
            ),
        )
        for distribution in distributions
    ]
    authorizations = [authorization_audit_payload(decision) for decision in decisions]
    if any(not decision.allowed for decision in decisions):
        denied = next(decision for decision in decisions if not decision.allowed)
        _publish_historical_authorization_denial(
            state_store=state_store,
            rejection_operation=rejection_operation,
            listing_id=listing_id,
            market=market,
            source_id=source_id,
            trace_id=trace_id,
            evaluated_at=evaluated_at,
            reason_code=denied.reason_code,
            authorizations=authorizations,
        )
        raise HistoricalEvidenceAuthorizationError(denied.reason_code)
    if any(
        authorization.get("source_policy_version_id") != source_policy.version_id
        for authorization in authorizations
    ):
        raise HistoricalEvidenceAuthorizationError("historical_source_policy_unavailable")
    return authorizations, source_policy


def _publish_historical_authorization_denial(
    *,
    state_store: StateStore,
    rejection_operation: str,
    listing_id: str,
    market: str,
    source_id: str,
    trace_id: str,
    evaluated_at: datetime,
    reason_code: str,
    authorizations: list[dict[str, object]],
) -> None:
    state_store._publish_governance_rejection(
        payload={
            "operation": rejection_operation,
            "reason_code": reason_code,
        },
        trace_id=trace_id,
        authorizations=authorizations,
    )
    if rejection_operation != "qualify_historical_evidence":
        return
    source_policy_ids = {
        str(authorization["source_policy_version_id"])
        for authorization in authorizations
        if isinstance(authorization.get("source_policy_version_id"), str)
    }
    state_store._publish_historical_policy_blocked(
        payload={
            "qualification_report_schema_version": "historical-qualification-report/v1",
            "listing_id": listing_id,
            "market": market,
            "source_id": source_id,
            "status": "policy_blocked",
            "reason_code": reason_code,
            "historical_availability_claim_id": None,
            "evidence_level": "unknown",
            "source_policy_id": (
                next(iter(source_policy_ids)) if len(source_policy_ids) == 1 else None
            ),
            "display_mode": "historical_reconstruction",
            "production_prediction": False,
            "exclusion_reasons": [reason_code],
            "created_at": evaluated_at.isoformat(),
        },
        trace_id=trace_id,
        authorizations=authorizations,
    )


def _json_object(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("historical_evidence_json_invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("historical_evidence_json_invalid")
    return cast(dict[str, object], payload)


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0.0"
    return format(value.normalize(), "f")
