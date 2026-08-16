from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, cast

from stock_forecasting.alpaca_market_data import (
    AlpacaLiveContractValidator,
    UrllibProviderHttpTransport,
)
from stock_forecasting.application import Application, build_application
from stock_forecasting.authorization import (
    LocalApiKeyIdentity,
    RuntimeEnvironment,
)
from stock_forecasting.outbox import RelayFault
from stock_forecasting.source_credentials import EncryptedFilesystemSecretProvider


@dataclass(frozen=True)
class RuntimeSettings:
    database_url: str
    object_root: Path
    source_secret_root: Path
    fixture_information_cutoff: datetime
    fixture_collection_observed_at: datetime
    runtime_environment: RuntimeEnvironment
    public_bind_host: str
    local_api_key_mode: Literal["disabled", "enabled"]
    local_api_key_file: Path | None
    source_adapter_api_key_file: Path | None
    authorization_policy_set_id: str

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        database_url = os.environ.get("DATABASE_URL")
        object_root = os.environ.get("OBJECT_ROOT")
        source_secret_root = os.environ.get("SOURCE_SECRET_ROOT")
        cutoff_text = os.environ.get("FIXTURE_INFORMATION_CUTOFF")
        observed_at_text = os.environ.get("FIXTURE_COLLECTION_OBSERVED_AT")
        environment_text = os.environ.get("RUNTIME_ENVIRONMENT", "development")
        public_bind_host = os.environ.get("PUBLIC_BIND_HOST", "127.0.0.1")
        local_api_key_mode_text = os.environ.get("LOCAL_API_KEY_MODE", "disabled")
        local_api_key_file_text = os.environ.get("LOCAL_API_KEY_FILE")
        source_adapter_api_key_file_text = os.environ.get("SOURCE_ADAPTER_API_KEY_FILE")
        authorization_policy_set_id = os.environ.get("AUTHORIZATION_POLICY_SET_ID")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        if not object_root:
            raise RuntimeError("OBJECT_ROOT is required")
        if not cutoff_text:
            raise RuntimeError("FIXTURE_INFORMATION_CUTOFF is required")
        if not observed_at_text:
            raise RuntimeError("FIXTURE_COLLECTION_OBSERVED_AT is required")
        if not authorization_policy_set_id:
            raise RuntimeError("AUTHORIZATION_POLICY_SET_ID is required")
        cutoff = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
        observed_at = datetime.fromisoformat(observed_at_text.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise RuntimeError("FIXTURE_INFORMATION_CUTOFF must include a timezone")
        if observed_at.tzinfo is None:
            raise RuntimeError("FIXTURE_COLLECTION_OBSERVED_AT must include a timezone")
        if environment_text not in {"local", "development", "test", "staging", "production"}:
            raise RuntimeError("RUNTIME_ENVIRONMENT is invalid")
        if local_api_key_mode_text not in {"disabled", "enabled"}:
            raise RuntimeError("LOCAL_API_KEY_MODE is invalid")
        if local_api_key_mode_text == "enabled":
            if environment_text not in {"local", "development"}:
                raise RuntimeError("local_api_key_environment_forbidden")
            try:
                public_is_loopback = ip_address(public_bind_host).is_loopback
            except ValueError as error:
                raise RuntimeError("local_api_key_loopback_required") from error
            if not public_is_loopback:
                raise RuntimeError("local_api_key_loopback_required")
            if not local_api_key_file_text:
                raise RuntimeError("LOCAL_API_KEY_FILE is required")
            if not Path(local_api_key_file_text).is_file():
                raise RuntimeError("LOCAL_API_KEY_FILE is unavailable")
            if (
                source_adapter_api_key_file_text
                and not Path(source_adapter_api_key_file_text).is_file()
            ):
                raise RuntimeError("SOURCE_ADAPTER_API_KEY_FILE is unavailable")
        return cls(
            database_url=database_url,
            object_root=Path(object_root),
            source_secret_root=(
                Path(source_secret_root)
                if source_secret_root is not None
                else Path(object_root).parent / "source-secrets"
            ),
            fixture_information_cutoff=cutoff.astimezone(UTC),
            fixture_collection_observed_at=observed_at.astimezone(UTC),
            runtime_environment=cast(RuntimeEnvironment, environment_text),
            public_bind_host=public_bind_host,
            local_api_key_mode=cast(Literal["disabled", "enabled"], local_api_key_mode_text),
            local_api_key_file=(
                Path(local_api_key_file_text) if local_api_key_file_text is not None else None
            ),
            source_adapter_api_key_file=(
                Path(source_adapter_api_key_file_text)
                if source_adapter_api_key_file_text is not None
                else None
            ),
            authorization_policy_set_id=authorization_policy_set_id,
        )

    def build_application(self, *, relay_fault: RelayFault | None = None) -> Application:
        if self.local_api_key_mode != "enabled":
            raise RuntimeError("trusted_identity_provider_required")
        if self.local_api_key_file is None:
            raise RuntimeError("local_api_key_file_required")
        local_identity = LocalApiKeyIdentity.load(self.local_api_key_file)
        if local_identity.context.environment != self.runtime_environment:
            raise RuntimeError("local_api_key_environment_mismatch")
        source_adapter_security_context = None
        if self.source_adapter_api_key_file is not None:
            source_adapter_identity = LocalApiKeyIdentity.load(self.source_adapter_api_key_file)
            if source_adapter_identity.context.principal_id == local_identity.context.principal_id:
                raise RuntimeError("source_adapter_identity_must_be_distinct")
            if source_adapter_identity.context.environment != self.runtime_environment:
                raise RuntimeError("source_adapter_identity_environment_mismatch")
            if source_adapter_identity.context.scopes != frozenset({"market_data.collect"}):
                raise RuntimeError("source_adapter_identity_scope_invalid")
            source_adapter_security_context = source_adapter_identity.context
        return build_application(
            database_url=self.database_url,
            object_root=self.object_root,
            observed_at=self.fixture_collection_observed_at,
            relay_fault=relay_fault,
            local_identity=local_identity,
            authorization_policy_set_id=self.authorization_policy_set_id,
            source_adapter_security_context=source_adapter_security_context,
            secret_provider=EncryptedFilesystemSecretProvider(self.source_secret_root),
            source_credential_validators={
                "alpaca-market-data-basic": AlpacaLiveContractValidator(
                    UrllibProviderHttpTransport()
                )
            },
        )
