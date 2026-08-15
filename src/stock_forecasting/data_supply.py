from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from io import BytesIO
from typing import Any, Literal, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from stock_forecasting.authorization import (
    AuthorizationDecision,
    AuthorizationPolicy,
    OperationIntent,
    SecurityContext,
    SourceUseRight,
    authorization_audit_payload,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore

StockPoolCoverageCase = Literal[
    "ordinary_share",
    "ticker_change",
    "company_action",
    "suspension",
    "historical_delisting",
]
ManifestEvidenceStatus = Literal["qualification_candidate", "qualified"]
TaiwanPriceSourceMode = Literal["current", "historical"]
SourceRevisionKind = Literal["original", "late_arrival", "correction", "withdrawal"]
SourceQualityIssue = Literal["identity_ambiguous", "missing_company_action"]
HistoricalEvidenceLevel = Literal["platform_observed", "archive_attested", "published_current_only"]
_APPROVED_PRICE_SCHEMA_VERSIONS = frozenset({"taiwan-unadjusted-eod-v1"})


@dataclass(frozen=True)
class SourcePartitionRequest:
    request_id: str
    trace_id: str
    source_id: str
    mode: TaiwanPriceSourceMode
    listing_ids: tuple[str, ...]
    start_date: date
    end_date: date
    expected_checkpoint: str | None
    policy_decision_id: str | None = None
    historical_availability_claim_id: str | None = None


@dataclass(frozen=True)
class SourceCollectionCoverage:
    requested_start: date
    requested_end: date
    observed_start: date | None
    observed_end: date | None
    complete: bool


@dataclass(frozen=True)
class HistoricalAvailabilityClaim:
    source_id: str
    evidence_level: HistoricalEvidenceLevel
    evidence_status: ManifestEvidenceStatus
    observed_start: date
    observed_end: date
    schema_version: str
    exact_sessions_verified: bool
    integrity_verified: bool
    company_actions_verified: bool
    listing_lifecycle_verified: bool
    qualification_artifact_id: str | None

    def as_payload(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "evidence_level": self.evidence_level,
            "evidence_status": self.evidence_status,
            "observed_start": self.observed_start.isoformat(),
            "observed_end": self.observed_end.isoformat(),
            "schema_version": self.schema_version,
            "exact_sessions_verified": self.exact_sessions_verified,
            "integrity_verified": self.integrity_verified,
            "company_actions_verified": self.company_actions_verified,
            "listing_lifecycle_verified": self.listing_lifecycle_verified,
            "qualification_artifact_id": self.qualification_artifact_id,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> HistoricalAvailabilityClaim:
        evidence_level = payload.get("evidence_level")
        evidence_status = payload.get("evidence_status")
        if evidence_level not in {
            "platform_observed",
            "archive_attested",
            "published_current_only",
        } or evidence_status not in {"qualification_candidate", "qualified"}:
            raise ValueError("historical_availability_claim_invalid")
        qualification_artifact_id = payload.get("qualification_artifact_id")
        if qualification_artifact_id is not None and not isinstance(qualification_artifact_id, str):
            raise ValueError("historical_availability_claim_invalid")
        try:
            return cls(
                source_id=str(payload["source_id"]),
                evidence_level=cast(HistoricalEvidenceLevel, evidence_level),
                evidence_status=cast(ManifestEvidenceStatus, evidence_status),
                observed_start=date.fromisoformat(str(payload["observed_start"])),
                observed_end=date.fromisoformat(str(payload["observed_end"])),
                schema_version=str(payload["schema_version"]),
                exact_sessions_verified=payload["exact_sessions_verified"] is True,
                integrity_verified=payload["integrity_verified"] is True,
                company_actions_verified=payload["company_actions_verified"] is True,
                listing_lifecycle_verified=payload["listing_lifecycle_verified"] is True,
                qualification_artifact_id=qualification_artifact_id,
            )
        except (KeyError, ValueError) as error:
            raise ValueError("historical_availability_claim_invalid") from error


@dataclass(frozen=True)
class CollectedSourcePartition:
    request_id: str
    source_id: str
    acquired_at: datetime
    sanitized_source_uri: str
    media_type: str
    raw_payload: bytes
    checkpoint_before: str | None
    checkpoint_after: str | None
    coverage: SourceCollectionCoverage
    source_revision: str


@dataclass(frozen=True)
class CanonicalPriceRow:
    listing_id: str
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class CompanyActionRecord:
    listing_id: str
    effective_date: date
    kind: Literal["cash_dividend", "split"]
    value: Decimal
    currency: str | None
    source_action_id: str


@dataclass(frozen=True)
class ListingLifecycleRecord:
    listing_id: str
    effective_date: date
    status: Literal["active", "suspended", "delisted"]
    source_event_id: str


@dataclass(frozen=True)
class DecodedSourcePartition:
    source_id: str
    schema_version: str
    source_revision: str
    prices: tuple[CanonicalPriceRow, ...]
    company_actions: tuple[CompanyActionRecord, ...]
    listing_lifecycle: tuple[ListingLifecycleRecord, ...]
    adjusted_close_cross_checks: tuple[Decimal, ...]
    identity_assertion_ids: tuple[str, ...]
    parent_object_ids: tuple[str, ...]
    revision_kind: SourceRevisionKind = "original"
    quality_issues: tuple[SourceQualityIssue, ...] = ()


@dataclass(frozen=True)
class LoadedSourcePartition:
    collection: CollectedSourcePartition
    decoded: DecodedSourcePartition


class SourceCollector(Protocol):
    def collect(self, request: SourcePartitionRequest) -> CollectedSourcePartition: ...


class SourceDecoder(Protocol):
    def decode(self, collection: CollectedSourcePartition) -> DecodedSourcePartition: ...


class PriceSourceAdapter(Protocol):
    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition: ...


class SourceRateLimited(RuntimeError):
    def __init__(self, *, retry_after_seconds: int, rate_limit_policy_id: str) -> None:
        if retry_after_seconds < 0:
            raise ValueError("retry_after_seconds_must_be_non_negative")
        super().__init__("source_rate_limited")
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit_policy_id = rate_limit_policy_id


class TaiwanPriceSourceAdapter:
    def __init__(
        self,
        *,
        source_id: str,
        mode: TaiwanPriceSourceMode,
        adapter_version: str,
        rate_limit_policy_id: str,
        collector: SourceCollector,
        decoder: SourceDecoder,
    ) -> None:
        self.source_id = source_id
        self.mode = mode
        self.adapter_version = adapter_version
        self.rate_limit_policy_id = rate_limit_policy_id
        self._collector = collector
        self._decoder = decoder

    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition:
        if request.source_id != self.source_id or request.mode != self.mode:
            raise ValueError("source_adapter_request_mismatch")
        try:
            collection = self._collector.collect(request)
        except SourceRateLimited as error:
            if error.rate_limit_policy_id != self.rate_limit_policy_id:
                raise ValueError("source_rate_limit_policy_mismatch") from error
            raise
        if collection.request_id != request.request_id or collection.source_id != request.source_id:
            raise ValueError("source_collection_request_mismatch")
        if collection.checkpoint_before != request.expected_checkpoint:
            raise ValueError("source_checkpoint_mismatch")
        decoded = self._decoder.decode(collection)
        if (
            decoded.source_id != collection.source_id
            or decoded.source_revision != collection.source_revision
        ):
            raise ValueError("source_decoder_lineage_mismatch")
        return LoadedSourcePartition(collection=collection, decoded=decoded)


@dataclass(frozen=True)
class PriceMaterializationOutcome:
    status: Literal["policy_blocked", "published", "quarantined", "deferred"]
    reason_code: str
    policy_reason_code: str
    dependency_id: Literal["DEP-MKT-TW-01"]
    source_id: str
    source_mode: TaiwanPriceSourceMode
    listing_ids: tuple[str, ...]
    trace_id: str
    policy_decision_id: str
    policy_evaluation_id: str
    policy_correlation_id: str
    policy_valid_until: datetime
    evaluated_at: datetime
    raw_object_id: str | None = None
    retrieval_receipt_id: str | None = None
    normalized_object_id: str | None = None
    source_revision: str | None = None
    checkpoint: str | None = None
    coverage: SourceCollectionCoverage | None = None
    dataset_version_id: str | None = None
    adjustment_version_id: str | None = None
    historical_availability_claim_id: str | None = None
    rate_limit_policy_id: str | None = None
    retry_after_seconds: int | None = None

    def as_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "policy_reason_code": self.policy_reason_code,
            "dependency_id": self.dependency_id,
            "source_id": self.source_id,
            "source_mode": self.source_mode,
            "listing_ids": list(self.listing_ids),
            "trace_id": self.trace_id,
            "policy_decision_id": self.policy_decision_id,
            "policy_evaluation_id": self.policy_evaluation_id,
            "policy_correlation_id": self.policy_correlation_id,
            "policy_valid_until": _instant(self.policy_valid_until),
            "evaluated_at": _instant(self.evaluated_at),
            "raw_object_id": self.raw_object_id,
            "retrieval_receipt_id": self.retrieval_receipt_id,
            "normalized_object_id": self.normalized_object_id,
            "source_revision": self.source_revision,
            "checkpoint": self.checkpoint,
            "coverage": _coverage_payload(self.coverage) if self.coverage is not None else None,
            "dataset_version_id": self.dataset_version_id,
            "adjustment_version_id": self.adjustment_version_id,
            "historical_availability_claim_id": self.historical_availability_claim_id,
            "rate_limit_policy_id": self.rate_limit_policy_id,
            "retry_after_seconds": self.retry_after_seconds,
            "checks": _source_qualification_checks(self.status, self.reason_code),
        }


PRICE_RESEARCH_REQUIRED_USES: frozenset[SourceUseRight] = frozenset(
    {
        "ingest",
        "retain_7_years",
        "transform",
        "model",
        "internal_display",
        "backup_restore",
    }
)


class DataSupply:
    def __init__(
        self,
        *,
        authorization_policy: AuthorizationPolicy,
        security_context: SecurityContext,
        adapters: Mapping[str, PriceSourceAdapter],
        object_repository: FilesystemObjectRepository,
        state_store: StateStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._authorization_policy = authorization_policy
        self._security_context = security_context
        self._adapters = adapters
        self._object_repository = object_repository
        self._state_store = state_store
        self._clock = clock

    def materialize(self, request: SourcePartitionRequest) -> PriceMaterializationOutcome:
        if request.policy_decision_id is not None:
            raise ValueError("source_policy_decision_must_be_internal")
        evaluated_at = self._clock()
        decision = self._authorization_policy.evaluate(
            self._security_context,
            OperationIntent(
                action="market_data.collect",
                dataset_id=request.source_id,
                purpose="price_research",
                environment=self._security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=request.trace_id,
                correlation_id=request.request_id,
                required_uses=PRICE_RESEARCH_REQUIRED_USES,
            ),
        )
        if decision.allowed:
            return self._materialize_allowed(request, decision)
        outcome = PriceMaterializationOutcome(
            status="policy_blocked",
            reason_code=_public_policy_reason(decision.reason_code),
            policy_reason_code=decision.reason_code,
            dependency_id="DEP-MKT-TW-01",
            source_id=request.source_id,
            source_mode=request.mode,
            listing_ids=request.listing_ids,
            trace_id=request.trace_id,
            policy_decision_id=decision.decision_id,
            policy_evaluation_id=decision.evaluation_id,
            policy_correlation_id=decision.correlation_id,
            policy_valid_until=decision.valid_until,
            evaluated_at=decision.evaluated_at,
        )
        self._state_store.publish_price_research_evaluation(
            trace_id=request.trace_id,
            execution_purpose="price_research",
            artifacts=[],
            authorization=authorization_audit_payload(decision),
            authorization_outcome="denied",
            eligibility_records=_eligibility_records(outcome),
        )
        return outcome

    def _materialize_allowed(
        self,
        request: SourcePartitionRequest,
        decision: AuthorizationDecision,
    ) -> PriceMaterializationOutcome:
        adapter = self._adapters.get(request.source_id)
        if adapter is None:
            raise ValueError("qualified_source_adapter_unavailable")
        durable_checkpoint = self._state_store.get_price_source_checkpoint(
            source_id=request.source_id,
            source_mode=request.mode,
        )
        if request.expected_checkpoint != durable_checkpoint:
            raise ValueError("source_checkpoint_state_mismatch")
        authorized_request = replace(request, policy_decision_id=decision.decision_id)
        try:
            loaded = adapter.load(authorized_request)
        except SourceRateLimited as error:
            outcome = PriceMaterializationOutcome(
                status="deferred",
                reason_code="source_rate_limited",
                policy_reason_code=decision.reason_code,
                dependency_id="DEP-MKT-TW-01",
                source_id=request.source_id,
                source_mode=request.mode,
                listing_ids=request.listing_ids,
                trace_id=request.trace_id,
                policy_decision_id=decision.decision_id,
                policy_evaluation_id=decision.evaluation_id,
                policy_correlation_id=decision.correlation_id,
                policy_valid_until=decision.valid_until,
                evaluated_at=decision.evaluated_at,
                historical_availability_claim_id=request.historical_availability_claim_id,
                rate_limit_policy_id=error.rate_limit_policy_id,
                retry_after_seconds=error.retry_after_seconds,
            )
            self._state_store.publish_price_research_evaluation(
                trace_id=request.trace_id,
                execution_purpose="price_research",
                artifacts=[],
                authorization=authorization_audit_payload(decision),
                authorization_outcome="allowed",
                eligibility_records=_eligibility_records(outcome),
            )
            return outcome
        collection = loaded.collection
        decoded = loaded.decoded
        historical_claim = self._historical_claim(request)
        if (
            collection.request_id != request.request_id
            or collection.source_id != request.source_id
            or decoded.source_id != request.source_id
            or decoded.source_revision != collection.source_revision
        ):
            raise ValueError("source_materialization_lineage_mismatch")
        if collection.coverage.requested_start != request.start_date or (
            collection.coverage.requested_end != request.end_date
        ):
            raise ValueError("source_coverage_request_mismatch")
        raw_checksum = hashlib.sha256(collection.raw_payload).hexdigest()
        raw_object = self._object_repository.put_verified(
            BytesIO(collection.raw_payload),
            expected_checksum=raw_checksum,
            metadata={
                "media_type": collection.media_type,
                "object_kind": "raw_source_object",
            },
        )
        raw_artifact = _raw_source_artifact(raw_object.object_id)
        retrieval_receipt = _source_retrieval_receipt_artifact(
            request,
            collection,
            raw_object.object_id,
        )
        quarantine_reason = _quarantine_reason(request, collection, decoded, historical_claim)
        if quarantine_reason is not None:
            quarantine_payload: dict[str, object] = {
                "reason_code": quarantine_reason,
                "source_id": request.source_id,
                "source_revision": collection.source_revision,
                "revision_kind": decoded.revision_kind,
                "quality_issues": list(decoded.quality_issues),
                "raw_object_id": raw_object.object_id,
                "retrieval_receipt_id": retrieval_receipt["artifact_id"],
                "coverage": _coverage_payload(collection.coverage),
                "policy_decision_id": decision.decision_id,
                "historical_availability_claim_id": (request.historical_availability_claim_id),
            }
            quarantine_id = _artifact_id("quarantine_record", quarantine_payload)
            outcome = PriceMaterializationOutcome(
                status="quarantined",
                reason_code=quarantine_reason,
                policy_reason_code=decision.reason_code,
                dependency_id="DEP-MKT-TW-01",
                source_id=request.source_id,
                source_mode=request.mode,
                listing_ids=request.listing_ids,
                trace_id=request.trace_id,
                policy_decision_id=decision.decision_id,
                policy_evaluation_id=decision.evaluation_id,
                policy_correlation_id=decision.correlation_id,
                policy_valid_until=decision.valid_until,
                evaluated_at=decision.evaluated_at,
                raw_object_id=raw_object.object_id,
                retrieval_receipt_id=str(retrieval_receipt["artifact_id"]),
                source_revision=collection.source_revision,
                checkpoint=collection.checkpoint_after,
                coverage=collection.coverage,
                historical_availability_claim_id=(request.historical_availability_claim_id),
            )
            quarantine_artifacts: list[dict[str, Any]] = [
                raw_artifact,
                retrieval_receipt,
                {
                    "artifact_id": quarantine_id,
                    "artifact_kind": "quarantine_record",
                    "payload": quarantine_payload,
                },
            ]
            self._state_store.publish_price_research_evaluation(
                trace_id=request.trace_id,
                execution_purpose="price_research",
                artifacts=quarantine_artifacts,
                authorization=authorization_audit_payload(decision),
                authorization_outcome="allowed",
                eligibility_records=_eligibility_records(outcome),
            )
            return outcome
        normalized_payload = _normalized_partition_payload(decoded)
        normalized_bytes = _canonical_json_bytes(normalized_payload)
        normalized_checksum = hashlib.sha256(normalized_bytes).hexdigest()
        normalized_object = self._object_repository.put_verified(
            BytesIO(normalized_bytes),
            expected_checksum=normalized_checksum,
            metadata={
                "media_type": "application/json",
                "object_kind": "normalized_price_object",
            },
        )
        dataset_payload: dict[str, object] = {
            "source_id": request.source_id,
            "source_mode": request.mode,
            "source_revision": collection.source_revision,
            "revision_kind": decoded.revision_kind,
            "schema_version": decoded.schema_version,
            "price_semantics": "unadjusted",
            "raw_object_id": raw_object.object_id,
            "normalized_object_id": normalized_object.object_id,
            "coverage": _coverage_payload(collection.coverage),
            "identity_assertion_ids": list(decoded.identity_assertion_ids),
            "parent_object_ids": [raw_object.object_id, *decoded.parent_object_ids],
            "policy_decision_id": decision.decision_id,
            "historical_availability_claim_id": (request.historical_availability_claim_id),
            "integrity": {
                "raw_sha256": raw_object.checksum,
                "normalized_sha256": normalized_object.checksum,
            },
        }
        dataset_version_id = _artifact_id("dataset_version", dataset_payload)
        adjusted_closes = _derive_adjusted_closes(decoded)
        provider_cross_check = "matched" if decoded.adjusted_close_cross_checks else "not_provided"
        adjustment_payload: dict[str, object] = {
            "input_dataset_version_id": dataset_version_id,
            "method": "internal_total_return_adjustment_v1",
            "company_action_source": "canonical_company_actions",
            "adjusted_closes": adjusted_closes,
            "provider_cross_check": provider_cross_check,
        }
        adjustment_version_id = _artifact_id("adjustment_version", adjustment_payload)
        artifacts: list[dict[str, Any]] = [
            raw_artifact,
            retrieval_receipt,
            {
                "artifact_id": normalized_object.object_id,
                "artifact_kind": "normalized_price_object",
                "payload": {
                    "object_id": normalized_object.object_id,
                    "schema_version": decoded.schema_version,
                    "parent_object_id": raw_object.object_id,
                },
            },
            {
                "artifact_id": dataset_version_id,
                "artifact_kind": "dataset_version",
                "payload": dataset_payload,
            },
            {
                "artifact_id": adjustment_version_id,
                "artifact_kind": "adjustment_version",
                "payload": adjustment_payload,
            },
        ]
        outcome = PriceMaterializationOutcome(
            status="published",
            reason_code="qualified_price_materialized",
            policy_reason_code=decision.reason_code,
            dependency_id="DEP-MKT-TW-01",
            source_id=request.source_id,
            source_mode=request.mode,
            listing_ids=request.listing_ids,
            trace_id=request.trace_id,
            policy_decision_id=decision.decision_id,
            policy_evaluation_id=decision.evaluation_id,
            policy_correlation_id=decision.correlation_id,
            policy_valid_until=decision.valid_until,
            evaluated_at=decision.evaluated_at,
            raw_object_id=raw_object.object_id,
            retrieval_receipt_id=str(retrieval_receipt["artifact_id"]),
            normalized_object_id=normalized_object.object_id,
            source_revision=collection.source_revision,
            checkpoint=collection.checkpoint_after,
            coverage=collection.coverage,
            dataset_version_id=dataset_version_id,
            adjustment_version_id=adjustment_version_id,
            historical_availability_claim_id=(request.historical_availability_claim_id),
        )
        self._state_store.publish_price_research_evaluation(
            trace_id=request.trace_id,
            execution_purpose="price_research",
            artifacts=artifacts,
            authorization=authorization_audit_payload(decision),
            authorization_outcome="allowed",
            eligibility_records=_eligibility_records(outcome),
        )
        return outcome

    def _historical_claim(
        self,
        request: SourcePartitionRequest,
    ) -> HistoricalAvailabilityClaim | None:
        claim_id = request.historical_availability_claim_id
        if request.mode != "historical":
            if claim_id is not None:
                raise ValueError("historical_claim_not_allowed_for_current_source")
            return None
        if claim_id is None:
            return None
        try:
            payload = self._state_store.get_verified_governance_artifact(
                artifact_id=claim_id,
                artifact_kind="historical_availability_claim",
            )
            return HistoricalAvailabilityClaim.from_payload(payload)
        except (KeyError, ValueError):
            return None


def _public_policy_reason(policy_reason_code: str) -> str:
    if policy_reason_code in {"source_policy_unknown", "source_entitlement_missing"}:
        return "dependency_evidence_unverified"
    if policy_reason_code in {
        "source_policy_use_denied",
        "source_entitlement_use_denied",
        "source_policy_action_denied",
        "source_entitlement_action_denied",
        "source_policy_purpose_denied",
        "source_entitlement_purpose_denied",
    }:
        return "source_rights_insufficient"
    return "source_rights_not_effective"


def _source_qualification_checks(status: str, reason_code: str) -> dict[str, str]:
    if status == "published":
        return {
            "policy": "passed",
            "coverage": "passed",
            "schema": "passed",
            "integrity": "passed",
            "depth": "passed",
        }
    if status == "policy_blocked":
        return {
            "policy": "blocked",
            "coverage": "not_evaluated",
            "schema": "not_evaluated",
            "integrity": "not_evaluated",
            "depth": "not_evaluated",
        }
    if status == "deferred":
        return {
            "policy": "passed",
            "coverage": "not_evaluated",
            "schema": "not_evaluated",
            "integrity": "not_evaluated",
            "depth": "not_evaluated",
        }
    checks = {
        "policy": "passed",
        "coverage": "passed",
        "schema": "passed",
        "integrity": "passed",
        "depth": "passed",
    }
    if reason_code == "incomplete_coverage":
        checks["coverage"] = "blocked"
    elif reason_code == "schema_incompatible":
        checks["schema"] = "blocked"
    elif reason_code in {
        "insufficient_history_depth",
        "historical_evidence_unverified",
        "historical_evidence_reconstruction_only",
        "published_current_only",
    }:
        checks["depth"] = "blocked"
    else:
        checks["integrity"] = "blocked"
    return checks


def _eligibility_records(outcome: PriceMaterializationOutcome) -> list[dict[str, object]]:
    payload = outcome.as_payload()
    records: list[dict[str, object]] = []
    for listing_id in outcome.listing_ids:
        records.append(
            {
                "eligibility_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        "stock-forecasting/price-eligibility/"
                        f"{outcome.policy_evaluation_id}/{outcome.source_id}/"
                        f"{outcome.source_mode}/{listing_id}",
                    )
                ),
                "listing_id": listing_id,
                "source_id": outcome.source_id,
                "source_mode": outcome.source_mode,
                "evaluated_at": _instant(outcome.evaluated_at),
                "status": outcome.status,
                "reason_code": outcome.reason_code,
                "trace_id": outcome.trace_id,
                "payload": payload,
            }
        )
    return records


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _coverage_payload(coverage: SourceCollectionCoverage) -> dict[str, object]:
    return {
        "requested_start": coverage.requested_start.isoformat(),
        "requested_end": coverage.requested_end.isoformat(),
        "observed_start": (
            coverage.observed_start.isoformat() if coverage.observed_start is not None else None
        ),
        "observed_end": (
            coverage.observed_end.isoformat() if coverage.observed_end is not None else None
        ),
        "complete": coverage.complete,
    }


def _normalized_partition_payload(decoded: DecodedSourcePartition) -> dict[str, object]:
    return {
        "schema_version": decoded.schema_version,
        "source_revision": decoded.source_revision,
        "revision_kind": decoded.revision_kind,
        "prices": [
            {
                "listing_id": price.listing_id,
                "session_date": price.session_date.isoformat(),
                "open": str(price.open),
                "high": str(price.high),
                "low": str(price.low),
                "close": str(price.close),
                "volume": price.volume,
            }
            for price in decoded.prices
        ],
        "company_actions": [
            {
                "listing_id": action.listing_id,
                "effective_date": action.effective_date.isoformat(),
                "kind": action.kind,
                "value": str(action.value),
                "currency": action.currency,
                "source_action_id": action.source_action_id,
            }
            for action in decoded.company_actions
        ],
        "listing_lifecycle": [
            {
                "listing_id": event.listing_id,
                "effective_date": event.effective_date.isoformat(),
                "status": event.status,
                "source_event_id": event.source_event_id,
            }
            for event in decoded.listing_lifecycle
        ],
    }


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _artifact_id(kind: str, payload: object) -> str:
    return f"sha256:{hashlib.sha256(kind.encode() + _canonical_json_bytes(payload)).hexdigest()}"


def _derive_adjusted_closes(decoded: DecodedSourcePartition) -> list[dict[str, str]]:
    adjusted_rows: list[dict[str, str]] = []
    for price in decoded.prices:
        adjusted = price.close
        for action in decoded.company_actions:
            if action.listing_id != price.listing_id or price.session_date >= action.effective_date:
                continue
            if action.kind == "cash_dividend":
                adjusted -= action.value
            else:
                adjusted /= action.value
        adjusted_rows.append(
            {
                "listing_id": price.listing_id,
                "session_date": price.session_date.isoformat(),
                "adjusted_close": str(adjusted),
            }
        )
    return adjusted_rows


def _raw_source_artifact(raw_object_id: str) -> dict[str, Any]:
    return {
        "artifact_id": raw_object_id,
        "artifact_kind": "raw_source_object",
        "payload": {"object_id": raw_object_id},
    }


def _source_retrieval_receipt_artifact(
    request: SourcePartitionRequest,
    collection: CollectedSourcePartition,
    raw_object_id: str,
) -> dict[str, Any]:
    payload: dict[str, object] = {
        "object_id": raw_object_id,
        "request_id": request.request_id,
        "source_id": request.source_id,
        "source_mode": request.mode,
        "source_revision": collection.source_revision,
        "sanitized_source_uri": collection.sanitized_source_uri,
        "acquired_at": _instant(collection.acquired_at),
        "checkpoint_before": collection.checkpoint_before,
        "checkpoint_after": collection.checkpoint_after,
    }
    return {
        "artifact_id": _artifact_id("source_retrieval_receipt", payload),
        "artifact_kind": "source_retrieval_receipt",
        "payload": payload,
    }


def _quarantine_reason(
    request: SourcePartitionRequest,
    collection: CollectedSourcePartition,
    decoded: DecodedSourcePartition,
    historical_claim: HistoricalAvailabilityClaim | None,
) -> str | None:
    if decoded.schema_version not in _APPROVED_PRICE_SCHEMA_VERSIONS:
        return "schema_incompatible"
    if decoded.adjusted_close_cross_checks and (
        tuple(item["adjusted_close"] for item in _derive_adjusted_closes(decoded))
        != tuple(str(value) for value in decoded.adjusted_close_cross_checks)
    ):
        return "adjustment_cross_check_mismatch"
    if (
        not collection.coverage.complete
        or collection.coverage.observed_start is None
        or collection.coverage.observed_end is None
    ):
        return "incomplete_coverage"
    if request.mode == "historical":
        try:
            depth_boundary = request.end_date.replace(year=request.end_date.year - 7)
        except ValueError:
            depth_boundary = request.end_date.replace(
                year=request.end_date.year - 7,
                day=28,
            )
        if (
            request.start_date > depth_boundary
            or collection.coverage.observed_start > depth_boundary
            or collection.coverage.observed_end < request.end_date
        ):
            return "insufficient_history_depth"
        if historical_claim is None:
            return "historical_evidence_unverified"
        if historical_claim.source_id != request.source_id:
            return "historical_evidence_unverified"
        if historical_claim.evidence_level == "published_current_only":
            return "published_current_only"
        if (
            historical_claim.observed_start > depth_boundary
            or historical_claim.observed_end < request.end_date
            or historical_claim.schema_version != decoded.schema_version
            or not historical_claim.exact_sessions_verified
            or not historical_claim.integrity_verified
            or not historical_claim.company_actions_verified
            or not historical_claim.listing_lifecycle_verified
        ):
            return "historical_evidence_unverified"
    if decoded.revision_kind == "withdrawal":
        return "source_withdrawn"
    if "identity_ambiguous" in decoded.quality_issues or not decoded.identity_assertion_ids:
        return "identity_ambiguous"
    if "missing_company_action" in decoded.quality_issues:
        return "missing_company_action"
    observed_listing_ids = {
        item.listing_id
        for items in (decoded.prices, decoded.company_actions, decoded.listing_lifecycle)
        for item in items
    }
    requested_listing_ids = set(request.listing_ids)
    if not observed_listing_ids <= requested_listing_ids:
        return "identity_ambiguous"
    if observed_listing_ids != requested_listing_ids:
        return "incomplete_coverage"
    return None


@dataclass(frozen=True)
class StockPoolListing:
    listing_id: str
    market: Literal["XTAI"]
    security_kind: Literal["ordinary_share"]
    coverage_cases: frozenset[StockPoolCoverageCase]

    def __post_init__(self) -> None:
        UUID(self.listing_id)


@dataclass(frozen=True)
class TaiwanStockPoolManifest:
    manifest_id: str
    taiwan_target: int
    united_states_target: int
    listings: tuple[StockPoolListing, ...]
    market_calendar_cases: frozenset[Literal["half_day_session"]]
    current_source_id: str
    historical_source_id: str
    formal_qualification_artifact_id: str | None
    historical_availability_claim_id: str | None
    evidence_status: ManifestEvidenceStatus

    def __post_init__(self) -> None:
        if len(self.listings) != self.taiwan_target:
            raise ValueError("taiwan_stock_pool_target_mismatch")
        if len({listing.listing_id for listing in self.listings}) != len(self.listings):
            raise ValueError("taiwan_stock_pool_listing_id_reused")

    @property
    def market_targets(self) -> dict[str, int]:
        return {"XTAI": self.taiwan_target, "US": self.united_states_target}

    @property
    def formally_qualified(self) -> bool:
        return (
            self.evidence_status == "qualified"
            and self.formal_qualification_artifact_id is not None
            and self.historical_availability_claim_id is not None
        )

    def matches_formal_source_lineage(
        self,
        sources: Sequence[Mapping[str, object]],
    ) -> bool:
        by_mode = {str(source["source_mode"]): source for source in sources}
        current = by_mode.get("current")
        historical = by_mode.get("historical")
        return (
            current is not None
            and historical is not None
            and current["source_id"] == self.current_source_id
            and historical["source_id"] == self.historical_source_id
            and historical["historical_availability_claim_id"]
            == self.historical_availability_claim_id
            and all(
                source["status"] == "published"
                and source["dataset_version_id"] is not None
                and source["adjustment_version_id"] is not None
                for source in (current, historical)
            )
        )


def load_taiwan_stock_pool_manifest() -> TaiwanStockPoolManifest:
    manifest_path = files("stock_forecasting").joinpath(
        "manifests/p2_taiwan_stock_pool_contract_v1.json"
    )
    payload = cast(dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8")))
    targets = cast(dict[str, int], payload["market_targets"])
    source_ids = cast(dict[str, str], payload["taiwan_sources"])
    listing_payloads = cast(list[dict[str, object]], payload["taiwan_listings"])
    market_calendar_cases = cast(
        list[Literal["half_day_session"]], payload["market_calendar_cases"]
    )
    return TaiwanStockPoolManifest(
        manifest_id=str(payload["manifest_id"]),
        taiwan_target=targets["XTAI"],
        united_states_target=targets["US"],
        listings=tuple(
            StockPoolListing(
                listing_id=str(listing["listing_id"]),
                market="XTAI",
                security_kind="ordinary_share",
                coverage_cases=frozenset(
                    cast(list[StockPoolCoverageCase], listing["coverage_cases"])
                ),
            )
            for listing in listing_payloads
        ),
        market_calendar_cases=frozenset(market_calendar_cases),
        current_source_id=source_ids["current"],
        historical_source_id=source_ids["historical"],
        formal_qualification_artifact_id=cast(
            str | None, payload["formal_qualification_artifact_id"]
        ),
        historical_availability_claim_id=cast(
            str | None, payload["historical_availability_claim_id"]
        ),
        evidence_status=cast(ManifestEvidenceStatus, payload["evidence_status"]),
    )
