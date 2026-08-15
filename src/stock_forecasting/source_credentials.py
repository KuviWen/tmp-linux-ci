from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from stock_forecasting.platform.state_store import StateStore


@dataclass(frozen=True)
class SecretRef:
    secret_ref_id: str


@dataclass(frozen=True)
class SecretLease:
    secret_ref_id: str
    _credential_fields: Mapping[str, str] = field(repr=False)

    def credential_fields(self) -> dict[str, str]:
        return dict(self._credential_fields)


class SecretProvider(Protocol):
    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef: ...

    def checkout(self, secret_ref_id: str) -> SecretLease: ...

    def revoke(self, secret_ref_id: str) -> None: ...


@dataclass(frozen=True)
class CredentialValidationResult:
    readiness: str
    reason_code: str
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.readiness not in {"valid", "validation_failed", "expired"}:
            raise ValueError("source_credential_validation_result_invalid")
        if not self.reason_code:
            raise ValueError("source_credential_validation_reason_required")


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
    def resolve_valid(self, provider_id: str) -> dict[str, str]: ...


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

    def resolve_valid(self, provider_id: str) -> dict[str, str]:
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
        try:
            lease = self._secret_provider.checkout(str(current["secret_ref_id"]))
        except KeyError as error:
            raise CredentialNotReady("source_credential_secret_unavailable") from error
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
            raise KeyError("source_credential_secret_unavailable") from error
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
            raise KeyError("source_credential_secret_unavailable") from error
        try:
            decoded = json.loads(self._fernet.decrypt(encrypted_payload))
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("source_credential_secret_corrupt") from error
        if not isinstance(decoded, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in decoded.items()
        ):
            raise ValueError("source_credential_secret_corrupt")
        return SecretLease(secret_ref_id, decoded)

    def revoke(self, secret_ref_id: str) -> None:
        with suppress(FileNotFoundError):
            self._secret_path(secret_ref_id).unlink()

    def _secret_path(self, secret_ref_id: str) -> Path:
        digest = hashlib.sha256(secret_ref_id.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.secret"
