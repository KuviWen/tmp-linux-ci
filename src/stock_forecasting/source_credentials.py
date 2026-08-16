from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
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
class SecretLease:
    secret_ref_id: str
    _credential_fields: Mapping[str, str] = field(repr=False)

    def credential_fields(self) -> dict[str, str]:
        return dict(self._credential_fields)


class SecretUnavailableError(KeyError):
    pass


class SecretCorruptError(ValueError):
    pass


class SecretProvider(Protocol):
    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef: ...

    def checkout(self, secret_ref_id: str) -> SecretLease: ...

    def revoke(self, secret_ref_id: str) -> None: ...


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
    def resolve_valid(self, provider_id: str, *, trace_id: str) -> dict[str, str]: ...


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


class ManagedSourceCredentialResolver:
    def __init__(
        self,
        state_store: StateStore,
        secret_provider: SecretProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._state_store = state_store
        self._secret_provider = secret_provider
        self._clock = clock or (lambda: datetime.now(UTC))

    def resolve_valid(self, provider_id: str, *, trace_id: str) -> dict[str, str]:
        current = self._state_store.get_source_credential(provider_id=provider_id)
        if current is None:
            raise CredentialNotReady("source_credential_missing")
        if current["readiness"] != "valid":
            raise CredentialNotReady(str(current["reason_code"]))
        expires_at = current.get("expires_at")
        if (
            isinstance(expires_at, str)
            and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= self._clock()
        ):
            raise CredentialNotReady("source_credential_expired")
        self._state_store.record_security_event(
            event_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{trace_id}:{provider_id}:{current['version']}:secret-checkout",
                )
            ),
            action="source_credential.checkout",
            outcome="allowed",
            reason_code="source_credential_checkout_authorized",
            trace_id=trace_id,
            authorization={
                "provider_id": provider_id,
                "credential_version": current["version"],
                "secret_ref_id": current["secret_ref_id"],
            },
        )
        try:
            lease = self._secret_provider.checkout(str(current["secret_ref_id"]))
        except SecretUnavailableError as error:
            raise CredentialNotReady("source_credential_secret_unavailable") from error
        except SecretCorruptError as error:
            raise CredentialNotReady("source_credential_secret_corrupt") from error
        return lease.credential_fields()


class InMemorySecretProvider:
    def __init__(self) -> None:
        self._secrets: dict[str, dict[str, str]] = {}

    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef:
        secret_ref_id = f"secret-ref:{provider_id}:{uuid4()}"
        self._secrets[secret_ref_id] = dict(credential_fields)
        return SecretRef(secret_ref_id)

    def checkout(self, secret_ref_id: str) -> SecretLease:
        try:
            credential_fields = self._secrets[secret_ref_id]
        except KeyError as error:
            raise SecretUnavailableError("source_credential_secret_unavailable") from error
        return SecretLease(secret_ref_id, credential_fields)

    def revoke(self, secret_ref_id: str) -> None:
        self._secrets.pop(secret_ref_id, None)


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

    def checkout(self, secret_ref_id: str) -> SecretLease:
        try:
            encrypted_payload = self._secret_path(secret_ref_id).read_bytes()
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
        return SecretLease(secret_ref_id, decoded)

    def revoke(self, secret_ref_id: str) -> None:
        with suppress(FileNotFoundError):
            self._secret_path(secret_ref_id).unlink()

    def _secret_path(self, secret_ref_id: str) -> Path:
        digest = hashlib.sha256(secret_ref_id.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.secret"
