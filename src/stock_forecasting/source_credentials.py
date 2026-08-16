from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, SupportsIndex
from uuid import NAMESPACE_URL, uuid4, uuid5

from cryptography.fernet import Fernet, InvalidToken

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
    issued_at: datetime
    lease_duration: timedelta

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
        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() is None:
            raise ValueError("secret_use_context_invalid")
        if not timedelta(0) < self.lease_duration <= timedelta(hours=1):
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
            "issued_at": self.issued_at.isoformat().replace("+00:00", "Z"),
            "lease_duration_seconds": int(self.lease_duration.total_seconds()),
        }


class SecretLease:
    __slots__ = (
        "_credential_fields",
        "credential_version",
        "expires_at",
        "issued_at",
        "purpose",
        "_revoked",
        "secret_ref_id",
    )

    def __init__(
        self,
        secret_ref: SecretRef,
        credential_fields: Mapping[str, str],
        use_context: SecretUseContext,
    ) -> None:
        self.secret_ref_id = secret_ref.secret_ref_id
        self.credential_version = use_context.credential_version
        self.issued_at = use_context.issued_at
        self.expires_at = use_context.issued_at + use_context.lease_duration
        self.purpose = use_context.purpose
        self._revoked = False
        self._credential_fields = MappingProxyType(dict(credential_fields))

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
        return self._revoked

    def __reduce_ex__(self, _protocol: SupportsIndex) -> str | tuple[Any, ...]:
        raise TypeError("secret_lease_not_serializable")

    def __getstate__(self) -> object:
        raise TypeError("secret_lease_not_serializable")

    def credential_fields(self, *, accessed_at: datetime) -> dict[str, str]:
        if self.revoked:
            raise SecretUnavailableError("source_credential_lease_revoked")
        if accessed_at.tzinfo is None or accessed_at.utcoffset() is None:
            raise ValueError("secret_lease_access_time_invalid")
        if accessed_at < self.issued_at:
            raise SecretUnavailableError("source_credential_lease_not_yet_valid")
        if accessed_at >= self.expires_at:
            raise SecretUnavailableError("source_credential_lease_expired")
        return dict(self._credential_fields)

    def revoke(self) -> None:
        self._revoked = True


class SecretUnavailableError(KeyError):
    pass


class SecretCorruptError(ValueError):
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
        try:
            lease = self._secret_provider.checkout(SecretRef(secret_ref_id), use_context)
        except (SecretUnavailableError, SecretCorruptError) as error:
            self._state_store.record_security_event(
                event_id=str(uuid4()),
                action="source_credential.checkout",
                outcome="failed",
                reason_code=str(error.args[0]),
                trace_id=trace_id,
                authorization=authorization,
            )
            raise
        except Exception:
            self._state_store.record_security_event(
                event_id=str(uuid4()),
                action="source_credential.checkout",
                outcome="failed",
                reason_code="source_credential_checkout_failed",
                trace_id=trace_id,
                authorization=authorization,
            )
            raise
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

    def resolve_valid(
        self,
        provider_id: str,
        *,
        trace_id: str,
        request_id: str,
        work_id: str,
        source_id: str,
    ) -> SecretLease:
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
        )
        expires_at = pinned.get("credential_expires_at")
        if (
            isinstance(expires_at, str)
            and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= self._clock()
        ):
            raise CredentialNotReady("source_credential_expired")
        credential_version = pinned.get("credential_version")
        secret_ref_id = pinned.get("secret_ref_id")
        if not isinstance(credential_version, int) or not isinstance(secret_ref_id, str):
            raise ValueError("source_credential_lease_pin_invalid")
        issued_at = self._clock()
        use_context = SecretUseContext(
            workload_principal_id=self._workload_principal_id,
            environment=self._environment,
            source_id=source_id,
            destination=provider_id,
            purpose="price_research_ingest",
            request_id=request_id,
            work_id=work_id,
            credential_version=credential_version,
            issued_at=issued_at,
            lease_duration=timedelta(minutes=5),
        )
        try:
            return self._secret_checkout.checkout(
                secret_ref_id=secret_ref_id,
                trace_id=trace_id,
                use_context=use_context,
            )
        except SecretUnavailableError as error:
            raise CredentialNotReady("source_credential_secret_unavailable") from error
        except SecretCorruptError as error:
            raise CredentialNotReady("source_credential_secret_corrupt") from error


class InMemorySecretProvider:
    def __init__(self) -> None:
        self._secrets: dict[str, dict[str, str]] = {}

    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef:
        secret_ref_id = f"secret-ref:{provider_id}:{uuid4()}"
        self._secrets[secret_ref_id] = dict(credential_fields)
        return SecretRef(secret_ref_id)

    def checkout(self, secret_ref: SecretRef, use_context: SecretUseContext) -> SecretLease:
        try:
            credential_fields = self._secrets[secret_ref.secret_ref_id]
        except KeyError as error:
            raise SecretUnavailableError("source_credential_secret_unavailable") from error
        return SecretLease(secret_ref, credential_fields, use_context)

    def revoke(self, secret_ref: SecretRef) -> None:
        self._secrets.pop(secret_ref.secret_ref_id, None)


class EncryptedFilesystemSecretProvider:
    _KEY_FILENAME = "master.key"

    def __init__(self, root: Path) -> None:
        self._root = root
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
        return SecretLease(secret_ref, decoded, use_context)

    def revoke(self, secret_ref: SecretRef) -> None:
        with suppress(FileNotFoundError):
            self._secret_path(secret_ref.secret_ref_id).unlink()

    def _secret_path(self, secret_ref_id: str) -> Path:
        digest = hashlib.sha256(secret_ref_id.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.secret"
