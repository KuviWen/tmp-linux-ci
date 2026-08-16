from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock, Timer
from time import monotonic
from types import MappingProxyType
from typing import Any, Literal, Protocol, SupportsIndex
from uuid import NAMESPACE_URL, uuid4, uuid5

from cryptography.fernet import Fernet, InvalidToken

from stock_forecasting.authorization import SourceAccessMode
from stock_forecasting.platform.state_store import StateStore

_CREDENTIAL_VALIDATION_REASON_CODES = frozenset(
    {
        "source_credential_authentication_failed",
        "source_credential_expired",
        "source_credential_fields_invalid",
        "source_credential_secret_corrupt",
        "source_credential_secret_unavailable",
        "source_credential_validation_inconclusive",
        "source_credential_validator_output_rejected",
        "source_credential_valid",
    }
)
_VALIDATION_CONTRACT_IDS = frozenset({"alpaca-credential-probe-v1", "alpaca-ticket-07-live-v1"})
_VALIDATION_DATASET_IDS = frozenset(
    {
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-stock-bars-v2",
        "alpaca-us-trading-calendar-v2",
    }
)
_SOURCE_CONTRACT_REASON_CODES = frozenset(
    {
        "source_contract_forbidden",
        "source_contract_probe_failed",
        "source_contract_rate_limited",
        "source_contract_schema_invalid",
        "source_contract_unavailable",
    }
)
_SECRET_CHECKOUT_UNAVAILABLE_REASONS = frozenset(
    {
        "source_credential_lease_expired",
        "source_credential_lease_not_yet_valid",
        "source_credential_lease_revoked",
        "source_credential_secret_unavailable",
    }
)
_SECRET_CHECKOUT_CORRUPT_REASONS = frozenset({"source_credential_secret_corrupt"})
_MAX_CACHED_SECRET_LEASES = 64


class LeaseExpiryHandle(Protocol):
    def cancel(self) -> None: ...


LeaseExpiryScheduler = Callable[[float, Callable[[], None]], LeaseExpiryHandle]


def _schedule_lease_expiry(
    delay_seconds: float,
    callback: Callable[[], None],
) -> LeaseExpiryHandle:
    timer = Timer(delay_seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


@dataclass(frozen=True)
class SecretRef:
    secret_ref_id: str


@dataclass(frozen=True)
class SecretUseContext:
    workload_principal_id: str
    environment: str
    source_id: str
    destination: str
    purpose: str
    request_id: str
    work_id: str
    credential_version: int
    lease_duration: timedelta
    lease_not_before: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        text_fields = (
            self.workload_principal_id,
            self.environment,
            self.source_id,
            self.destination,
            self.purpose,
            self.request_id,
            self.work_id,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("secret_use_context_invalid")
        if self.credential_version < 1:
            raise ValueError("secret_use_context_invalid")
        lease_times = (self.lease_not_before, self.lease_expires_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in lease_times):
            raise ValueError("secret_use_context_invalid")
        if not timedelta(0) < self.lease_duration <= timedelta(hours=1):
            raise ValueError("secret_use_context_invalid")
        if self.lease_expires_at <= self.lease_not_before:
            raise ValueError("secret_use_context_invalid")

    def as_audit_payload(self) -> dict[str, object]:
        return {
            "workload_principal_id": self.workload_principal_id,
            "environment": self.environment,
            "source_id": self.source_id,
            "destination": self.destination,
            "purpose": self.purpose,
            "request_id": self.request_id,
            "work_id": self.work_id,
            "credential_version": self.credential_version,
            "lease_not_before": self.lease_not_before.isoformat().replace("+00:00", "Z"),
            "lease_expires_at": self.lease_expires_at.isoformat().replace("+00:00", "Z"),
            "lease_duration_seconds": int(self.lease_duration.total_seconds()),
        }


class SecretLease:
    __slots__ = (
        "_credential_fields",
        "_credential_version",
        "_expires_at",
        "_issued_at",
        "_expiry_handle",
        "_lock",
        "_monotonic_clock",
        "_monotonic_deadline",
        "_purpose",
        "_revoked",
        "_secret_ref_id",
    )

    def __init__(
        self,
        secret_ref: SecretRef,
        credential_fields: Mapping[str, str],
        use_context: SecretUseContext,
        *,
        clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float],
        expiry_scheduler: LeaseExpiryScheduler = _schedule_lease_expiry,
    ) -> None:
        issued_at = clock()
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError("secret_provider_clock_invalid")
        if issued_at < use_context.lease_not_before:
            raise SecretUnavailableError("source_credential_lease_not_yet_valid")
        expires_at = min(
            issued_at + use_context.lease_duration,
            use_context.lease_expires_at,
        )
        if expires_at <= issued_at:
            raise SecretUnavailableError("source_credential_lease_expired")
        self._secret_ref_id = secret_ref.secret_ref_id
        self._credential_version = use_context.credential_version
        self._issued_at = issued_at
        self._expires_at = expires_at
        self._purpose = use_context.purpose
        self._revoked = False
        self._credential_fields = MappingProxyType(dict(credential_fields))
        self._lock = Lock()
        self._expiry_handle: LeaseExpiryHandle | None = None
        self._monotonic_clock = monotonic_clock
        lease_seconds = (expires_at - issued_at).total_seconds()
        self._monotonic_deadline = monotonic_clock() + lease_seconds
        expiry_handle = expiry_scheduler(lease_seconds, self.revoke)
        with self._lock:
            self._expiry_handle = expiry_handle
            if self._revoked:
                expiry_handle.cancel()

    def __repr__(self) -> str:
        return (
            "SecretLease(secret_ref_id=<redacted>, "
            f"credential_version={self.credential_version}, "
            f"issued_at={self.issued_at!r}, expires_at={self.expires_at!r}, "
            f"purpose={self.purpose!r}, revoked={self.revoked})"
        )

    __str__ = __repr__

    @property
    def revoked(self) -> bool:
        with self._lock:
            return self._revoked

    @property
    def secret_ref_id(self) -> str:
        return self._secret_ref_id

    @property
    def credential_version(self) -> int:
        return self._credential_version

    @property
    def issued_at(self) -> datetime:
        return self._issued_at

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    @property
    def purpose(self) -> str:
        return self._purpose

    def __reduce_ex__(self, _protocol: SupportsIndex) -> str | tuple[Any, ...]:
        raise TypeError("secret_lease_not_serializable")

    def __getstate__(self) -> object:
        raise TypeError("secret_lease_not_serializable")

    def credential_fields(self) -> dict[str, str]:
        with self._lock:
            if self._revoked:
                raise SecretUnavailableError("source_credential_lease_revoked")
            if self._monotonic_clock() >= self._monotonic_deadline:
                self._revoke_locked()
                raise SecretUnavailableError("source_credential_lease_expired")
            return dict(self._credential_fields)

    @property
    def active(self) -> bool:
        with self._lock:
            if self._revoked:
                return False
            if self._monotonic_clock() >= self._monotonic_deadline:
                self._revoke_locked()
                return False
            return True

    def revoke(self) -> None:
        with self._lock:
            self._revoke_locked()

    def _revoke_locked(self) -> None:
        self._revoked = True
        self._credential_fields = MappingProxyType({})
        if self._expiry_handle is not None:
            self._expiry_handle.cancel()


class SecretUnavailableError(KeyError):
    pass


class SecretCorruptError(ValueError):
    pass


class SecretWriteError(RuntimeError):
    pass


class SecretProvider(Protocol):
    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef: ...

    def checkout(
        self,
        secret_ref: SecretRef,
        use_context: SecretUseContext,
    ) -> SecretLease: ...

    def revoke(self, secret_ref: SecretRef) -> None: ...


@dataclass(frozen=True)
class CredentialValidationEvidence:
    authentication_status: Literal["not_run", "passed", "failed"] = "not_run"

    def __post_init__(self) -> None:
        if self.authentication_status not in {"not_run", "passed", "failed"}:
            raise ValueError("source_credential_validation_evidence_invalid")

    def as_payload(self) -> dict[str, object]:
        return {"authentication_status": self.authentication_status}


@dataclass(frozen=True)
class SourceContractAssessment:
    contract_id: str | None = None
    live_validation: Literal["not_run", "passed", "failed"] = "not_run"
    ticker_count: int | None = None
    pagination_pages: int | None = None
    datasets: tuple[str, ...] = ()
    symbol_lifecycle_probe: Literal["passed"] | None = None
    source_contract_reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.live_validation not in {"not_run", "passed", "failed"}:
            raise ValueError("source_contract_assessment_invalid")
        if self.contract_id is not None and self.contract_id not in _VALIDATION_CONTRACT_IDS:
            raise ValueError("source_contract_assessment_invalid")
        if self.ticker_count is not None and self.ticker_count < 0:
            raise ValueError("source_contract_assessment_invalid")
        if self.pagination_pages is not None and self.pagination_pages < 0:
            raise ValueError("source_contract_assessment_invalid")
        if any(dataset not in _VALIDATION_DATASET_IDS for dataset in self.datasets):
            raise ValueError("source_contract_assessment_invalid")
        if self.symbol_lifecycle_probe not in {None, "passed"}:
            raise ValueError("source_contract_assessment_invalid")
        if (
            self.source_contract_reason_code is not None
            and self.source_contract_reason_code not in _SOURCE_CONTRACT_REASON_CODES
        ):
            raise ValueError("source_contract_assessment_invalid")
        has_measurement = any(
            value is not None
            for value in (
                self.contract_id,
                self.ticker_count,
                self.pagination_pages,
                self.symbol_lifecycle_probe,
                self.source_contract_reason_code,
            )
        ) or bool(self.datasets)
        if self.live_validation == "not_run" and has_measurement:
            raise ValueError("source_contract_assessment_invalid")
        if self.live_validation == "passed" and (
            self.contract_id is None or self.source_contract_reason_code is not None
        ):
            raise ValueError("source_contract_assessment_invalid")
        if self.live_validation == "failed" and (
            self.contract_id is None or self.source_contract_reason_code is None
        ):
            raise ValueError("source_contract_assessment_invalid")

    def as_payload(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "live_validation": self.live_validation,
            "ticker_count": self.ticker_count,
            "pagination_pages": self.pagination_pages,
            "datasets": list(self.datasets),
            "symbol_lifecycle_probe": self.symbol_lifecycle_probe,
            "source_contract_reason_code": self.source_contract_reason_code,
        }


@dataclass(frozen=True)
class CredentialValidationResult:
    readiness: str
    reason_code: str
    evidence: CredentialValidationEvidence = field(default_factory=CredentialValidationEvidence)
    source_contract_assessment: SourceContractAssessment | None = None

    def __post_init__(self) -> None:
        if self.readiness not in {"configured", "valid", "validation_failed", "expired"}:
            raise ValueError("source_credential_validation_result_invalid")
        if self.reason_code not in _CREDENTIAL_VALIDATION_REASON_CODES:
            raise ValueError("source_credential_validation_reason_invalid")
        if not isinstance(self.evidence, CredentialValidationEvidence):
            raise ValueError("source_credential_validation_evidence_invalid")
        if self.source_contract_assessment is not None and not isinstance(
            self.source_contract_assessment,
            SourceContractAssessment,
        ):
            raise ValueError("source_contract_assessment_invalid")
        state = (self.readiness, self.reason_code, self.evidence.authentication_status)
        allowed_states = {
            ("valid", "source_credential_valid", "passed"),
            (
                "configured",
                "source_credential_validation_inconclusive",
                "not_run",
            ),
            (
                "validation_failed",
                "source_credential_authentication_failed",
                "failed",
            ),
            (
                "validation_failed",
                "source_credential_fields_invalid",
                "not_run",
            ),
            (
                "validation_failed",
                "source_credential_validator_output_rejected",
                "not_run",
            ),
            ("expired", "source_credential_expired", "not_run"),
        }
        if state not in allowed_states:
            raise ValueError("source_credential_validation_result_invalid")
        if self.readiness == "configured" and (
            self.source_contract_assessment is None
            or self.source_contract_assessment.live_validation != "failed"
        ):
            raise ValueError("source_credential_validation_result_invalid")
        if self.readiness in {"validation_failed", "expired"} and (
            self.source_contract_assessment is not None
        ):
            raise ValueError("source_credential_validation_result_invalid")


class SourceCredentialValidator(Protocol):
    source_access_mode: SourceAccessMode

    def validate(
        self,
        credential_fields: Mapping[str, str],
    ) -> CredentialValidationResult: ...


class CredentialNotReady(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        if not reason_code.startswith("source_credential_"):
            raise ValueError("source_credential_reason_invalid")
        super().__init__(reason_code)
        self.reason_code = reason_code


class SourceCredentialResolver(Protocol):
    def resolve_valid(
        self,
        provider_id: str,
        *,
        trace_id: str,
        request_id: str,
        work_id: str,
        source_id: str,
    ) -> SecretLease: ...


def project_source_credential_readiness(
    current: Mapping[str, object] | None,
    *,
    evaluated_at: datetime,
) -> dict[str, object]:
    if current is None:
        return {
            "readiness": "missing",
            "reason_code": "source_credential_missing",
            "secret_ref_id": None,
            "version": None,
            "configured_at": None,
            "last_validated_at": None,
        }
    projected = dict(current)
    expires_at = projected.get("expires_at")
    if (
        projected.get("readiness") != "revoked"
        and isinstance(expires_at, str)
        and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= evaluated_at
    ):
        projected["readiness"] = "expired"
        projected["reason_code"] = "source_credential_expired"
    return projected


def pin_source_credential_lease(
    state_store: StateStore,
    *,
    provider_id: str,
    current: Mapping[str, object],
    trace_id: str,
    workload_principal_id: str,
    environment: str,
    source_id: str,
    destination: str,
    purpose: str,
    request_id: str,
    work_id: str,
    lease_duration: timedelta,
    lease_issued_at: datetime,
) -> dict[str, object]:
    event_id = str(
        uuid5(
            NAMESPACE_URL,
            f"source-credential-lease-pin:{provider_id}:{work_id}",
        )
    )
    existing = state_store.get_security_event(event_id=event_id)
    expected_identity = {
        "provider_id": provider_id,
        "workload_principal_id": workload_principal_id,
        "environment": environment,
        "source_id": source_id,
        "destination": destination,
        "purpose": purpose,
        "request_id": request_id,
        "work_id": work_id,
        "lease_duration_seconds": int(lease_duration.total_seconds()),
    }
    if existing is not None:
        authorization = existing.get("authorization")
        if not isinstance(authorization, dict) or any(
            authorization.get(key) != value for key, value in expected_identity.items()
        ):
            raise ValueError("source_credential_lease_pin_context_mismatch")
        return dict(authorization)
    credential_version = current.get("version")
    secret_ref_id = current.get("secret_ref_id")
    if not isinstance(credential_version, int) or not isinstance(secret_ref_id, str):
        raise ValueError("source_credential_metadata_invalid")
    authorization = {
        **expected_identity,
        "credential_version": credential_version,
        "secret_ref_id": secret_ref_id,
        "credential_expires_at": current.get("expires_at"),
        "lease_not_before": lease_issued_at.isoformat().replace("+00:00", "Z"),
        "lease_expires_at": (lease_issued_at + lease_duration).isoformat().replace("+00:00", "Z"),
    }
    state_store.record_security_event(
        event_id=event_id,
        action="source_credential.lease_pin",
        outcome="allowed",
        reason_code="source_credential_lease_pinned",
        trace_id=trace_id,
        authorization=authorization,
    )
    return authorization


class AuditedSecretCheckout:
    def __init__(self, state_store: StateStore, secret_provider: SecretProvider) -> None:
        self._state_store = state_store
        self._secret_provider = secret_provider

    def checkout(
        self,
        *,
        secret_ref_id: str,
        trace_id: str,
        use_context: SecretUseContext,
    ) -> SecretLease:
        attempt_id = str(uuid4())
        authorization = {
            **use_context.as_audit_payload(),
            "provider_id": use_context.destination,
            "secret_ref_id": secret_ref_id,
            "checkout_attempt_id": attempt_id,
        }
        self._state_store.record_security_event(
            event_id=str(uuid4()),
            action="source_credential.checkout",
            outcome="attempted",
            reason_code="source_credential_checkout_attempted",
            trace_id=trace_id,
            authorization=authorization,
        )
        failure: tuple[type[SecretUnavailableError] | type[SecretCorruptError], str, str] | None = (
            None
        )
        try:
            lease = self._secret_provider.checkout(SecretRef(secret_ref_id), use_context)
        except SecretUnavailableError as error:
            supplied_reason = error.args[0] if error.args else None
            reason_code = (
                supplied_reason
                if type(supplied_reason) is str
                and supplied_reason in _SECRET_CHECKOUT_UNAVAILABLE_REASONS
                else "source_credential_secret_unavailable"
            )
            failure = (SecretUnavailableError, str(reason_code), str(reason_code))
        except SecretCorruptError as error:
            supplied_reason = error.args[0] if error.args else None
            reason_code = (
                supplied_reason
                if type(supplied_reason) is str
                and supplied_reason in _SECRET_CHECKOUT_CORRUPT_REASONS
                else "source_credential_secret_corrupt"
            )
            failure = (SecretCorruptError, str(reason_code), str(reason_code))
        except Exception:
            failure = (
                SecretUnavailableError,
                "source_credential_secret_unavailable",
                "source_credential_checkout_failed",
            )
        if failure is not None:
            error_type, reason_code, audit_reason_code = failure
            self._state_store.record_security_event(
                event_id=str(uuid4()),
                action="source_credential.checkout",
                outcome="failed",
                reason_code=audit_reason_code,
                trace_id=trace_id,
                authorization=authorization,
            )
            raise error_type(reason_code) from None
        try:
            self._state_store.record_security_event(
                event_id=str(uuid4()),
                action="source_credential.checkout",
                outcome="succeeded",
                reason_code="source_credential_checkout_succeeded",
                trace_id=trace_id,
                authorization=authorization,
            )
        except Exception:
            lease.revoke()
            raise
        return lease


class AuditedSecretWrite:
    def __init__(self, state_store: StateStore, secret_provider: SecretProvider) -> None:
        self._state_store = state_store
        self._secret_provider = secret_provider

    def put(
        self,
        *,
        provider_id: str,
        credential_fields: Mapping[str, str],
        operation: Literal["set", "rotate"],
        trace_id: str,
        authorization: Mapping[str, object],
        occurred_at: datetime,
    ) -> SecretRef:
        attempt_id = str(uuid4())
        audit_context = {
            "provider_id": provider_id,
            "operation": operation,
            "principal_id": authorization.get("principal_id"),
            "environment": authorization.get("environment"),
            "purpose": authorization.get("purpose"),
            "authorization_decision_id": authorization.get("decision_id"),
            "authorization_evaluation_id": authorization.get("evaluation_id"),
            "secret_write_attempt_id": attempt_id,
        }
        self._state_store.record_security_event(
            event_id=str(uuid4()),
            action="source_credential.write",
            outcome="attempted",
            reason_code="source_credential_write_attempted",
            trace_id=trace_id,
            authorization=audit_context,
        )
        write_failed = False
        try:
            secret_ref = self._secret_provider.put(
                provider_id=provider_id,
                credential_fields=credential_fields,
            )
        except Exception:
            write_failed = True
        if write_failed:
            self._state_store.record_security_event(
                event_id=str(uuid4()),
                action="source_credential.write",
                outcome="failed",
                reason_code="source_credential_write_failed",
                trace_id=trace_id,
                authorization=audit_context,
            )
            raise SecretWriteError("source_credential_write_failed") from None
        try:
            self._state_store.record_security_event(
                event_id=str(uuid4()),
                action="source_credential.write",
                outcome="succeeded",
                reason_code="source_credential_write_succeeded",
                trace_id=trace_id,
                authorization={
                    **audit_context,
                    "secret_ref_id": secret_ref.secret_ref_id,
                },
            )
        except Exception:
            try:
                self._secret_provider.revoke(secret_ref)
            except Exception:
                self._state_store.queue_source_secret_cleanup(
                    secret_ref_id=secret_ref.secret_ref_id,
                    provider_id=provider_id,
                    queued_at=occurred_at.isoformat().replace("+00:00", "Z"),
                )
            raise
        return secret_ref


class ManagedSourceCredentialResolver:
    def __init__(
        self,
        state_store: StateStore,
        secret_provider: SecretProvider,
        *,
        workload_principal_id: str,
        environment: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._state_store = state_store
        self._secret_checkout = AuditedSecretCheckout(state_store, secret_provider)
        self._workload_principal_id = workload_principal_id
        self._environment = environment
        self._clock = clock or (lambda: datetime.now(UTC))
        self._leases: OrderedDict[tuple[str, str], SecretLease] = OrderedDict()

    def resolve_valid(
        self,
        provider_id: str,
        *,
        trace_id: str,
        request_id: str,
        work_id: str,
        source_id: str,
    ) -> SecretLease:
        self._prune_leases()
        current = self._state_store.get_source_credential(provider_id=provider_id)
        if current is None:
            raise CredentialNotReady("source_credential_missing")
        pin_event_id = str(
            uuid5(
                NAMESPACE_URL,
                f"source-credential-lease-pin:{provider_id}:{work_id}",
            )
        )
        existing_pin = self._state_store.get_security_event(event_id=pin_event_id)
        if existing_pin is None and current["readiness"] != "valid":
            raise CredentialNotReady(str(current["reason_code"]))
        evaluated_at = self._clock()
        pinned = pin_source_credential_lease(
            self._state_store,
            provider_id=provider_id,
            current=current,
            trace_id=trace_id,
            workload_principal_id=self._workload_principal_id,
            environment=self._environment,
            source_id=source_id,
            destination=provider_id,
            purpose="price_research_ingest",
            request_id=request_id,
            work_id=work_id,
            lease_duration=timedelta(minutes=5),
            lease_issued_at=evaluated_at,
        )
        credential_expires_at = pinned.get("credential_expires_at")
        if (
            isinstance(credential_expires_at, str)
            and datetime.fromisoformat(credential_expires_at.replace("Z", "+00:00")) <= evaluated_at
        ):
            raise CredentialNotReady("source_credential_expired")
        credential_version = pinned.get("credential_version")
        secret_ref_id = pinned.get("secret_ref_id")
        lease_not_before = pinned.get("lease_not_before")
        lease_expires_at = pinned.get("lease_expires_at")
        if (
            not isinstance(credential_version, int)
            or not isinstance(secret_ref_id, str)
            or not isinstance(lease_not_before, str)
            or not isinstance(lease_expires_at, str)
        ):
            raise ValueError("source_credential_lease_pin_invalid")
        pinned_not_before = datetime.fromisoformat(lease_not_before.replace("Z", "+00:00"))
        pinned_expires_at = datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00"))
        if pinned_expires_at <= evaluated_at:
            raise CredentialNotReady("source_credential_lease_expired")
        cached_lease = self._leases.get((provider_id, work_id))
        if cached_lease is not None:
            self._leases.move_to_end((provider_id, work_id))
            return cached_lease
        use_context = SecretUseContext(
            workload_principal_id=self._workload_principal_id,
            environment=self._environment,
            source_id=source_id,
            destination=provider_id,
            purpose="price_research_ingest",
            request_id=request_id,
            work_id=work_id,
            credential_version=credential_version,
            lease_duration=timedelta(minutes=5),
            lease_not_before=pinned_not_before,
            lease_expires_at=pinned_expires_at,
        )
        try:
            lease = self._secret_checkout.checkout(
                secret_ref_id=secret_ref_id,
                trace_id=trace_id,
                use_context=use_context,
            )
        except SecretUnavailableError as error:
            reason_code = str(error.args[0])
            if reason_code.startswith("source_credential_lease_"):
                raise CredentialNotReady(reason_code) from error
            raise CredentialNotReady("source_credential_secret_unavailable") from error
        except SecretCorruptError as error:
            raise CredentialNotReady("source_credential_secret_corrupt") from error
        self._leases[(provider_id, work_id)] = lease
        while len(self._leases) > _MAX_CACHED_SECRET_LEASES:
            _, evicted = self._leases.popitem(last=False)
            evicted.revoke()
        return lease

    def _prune_leases(self) -> None:
        for cache_key, lease in tuple(self._leases.items()):
            if lease.active:
                continue
            lease.revoke()
            del self._leases[cache_key]


class InMemorySecretProvider:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        expiry_scheduler: LeaseExpiryScheduler | None = None,
    ) -> None:
        self._secrets: dict[str, dict[str, str]] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock
        self._expiry_scheduler = expiry_scheduler or _schedule_lease_expiry

    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef:
        secret_ref_id = f"secret-ref:{provider_id}:{uuid4()}"
        self._secrets[secret_ref_id] = dict(credential_fields)
        return SecretRef(secret_ref_id)

    def checkout(self, secret_ref: SecretRef, use_context: SecretUseContext) -> SecretLease:
        try:
            credential_fields = self._secrets[secret_ref.secret_ref_id]
        except KeyError as error:
            raise SecretUnavailableError("source_credential_secret_unavailable") from error
        return SecretLease(
            secret_ref,
            credential_fields,
            use_context,
            clock=self._clock,
            monotonic_clock=self._monotonic_clock,
            expiry_scheduler=self._expiry_scheduler,
        )

    def revoke(self, secret_ref: SecretRef) -> None:
        self._secrets.pop(secret_ref.secret_ref_id, None)


class EncryptedFilesystemSecretProvider:
    _KEY_FILENAME = "master.key"

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        expiry_scheduler: LeaseExpiryScheduler | None = None,
    ) -> None:
        self._root = root
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock
        self._expiry_scheduler = expiry_scheduler or _schedule_lease_expiry
        self._root.mkdir(parents=True, exist_ok=True)
        key_path = self._root / self._KEY_FILENAME
        try:
            with key_path.open("xb") as key_file:
                key_file.write(Fernet.generate_key())
            os.chmod(key_path, 0o600)
        except FileExistsError:
            pass
        self._fernet = Fernet(key_path.read_bytes())

    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef:
        if not provider_id or not credential_fields:
            raise ValueError("source_credential_fields_required")
        if any(
            not key or not isinstance(value, str) or not value
            for key, value in credential_fields.items()
        ):
            raise ValueError("source_credential_fields_required")
        secret_ref_id = f"secret-ref:{provider_id}:{uuid4()}"
        payload = json.dumps(dict(credential_fields), sort_keys=True).encode("utf-8")
        secret_path = self._secret_path(secret_ref_id)
        with secret_path.open("xb") as secret_file:
            secret_file.write(self._fernet.encrypt(payload))
        os.chmod(secret_path, 0o600)
        return SecretRef(secret_ref_id)

    def checkout(self, secret_ref: SecretRef, use_context: SecretUseContext) -> SecretLease:
        try:
            encrypted_payload = self._secret_path(secret_ref.secret_ref_id).read_bytes()
        except FileNotFoundError as error:
            raise SecretUnavailableError("source_credential_secret_unavailable") from error
        try:
            decoded = json.loads(self._fernet.decrypt(encrypted_payload))
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SecretCorruptError("source_credential_secret_corrupt") from error
        if not isinstance(decoded, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in decoded.items()
        ):
            raise SecretCorruptError("source_credential_secret_corrupt")
        return SecretLease(
            secret_ref,
            decoded,
            use_context,
            clock=self._clock,
            monotonic_clock=self._monotonic_clock,
            expiry_scheduler=self._expiry_scheduler,
        )

    def revoke(self, secret_ref: SecretRef) -> None:
        with suppress(FileNotFoundError):
            self._secret_path(secret_ref.secret_ref_id).unlink()

    def _secret_path(self, secret_ref_id: str) -> Path:
        digest = hashlib.sha256(secret_ref_id.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.secret"
