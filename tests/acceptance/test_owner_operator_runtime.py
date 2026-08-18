from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import (
    LocalApiKeyIdentity,
    SourceAccessMode,
    build_pending_rights_operator_authorization_policy,
)
from stock_forecasting.source_credentials import (
    CredentialValidationEvidence,
    CredentialValidationResult,
    EncryptedFilesystemSecretProvider,
)


class NeverCalledLiveValidator:
    source_access_mode: SourceAccessMode = "live_provider"

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def validate(self, credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        self.calls.append(dict(credential_fields))
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )


@pytest.mark.parametrize(
    ("provider_id", "credential_fields", "secret_canary"),
    [
        (
            "finmind-free-api",
            {"token": "finmind-owner-operator-secret"},
            "finmind-owner-operator-secret",
        ),
        (
            "alpaca-market-data-basic",
            {
                "api_key_id": "PK-OWNER-OPERATOR",
                "api_secret_key": "alpaca-owner-operator-secret",
            },
            "alpaca-owner-operator-secret",
        ),
    ],
)
def test_owner_can_configure_credentials_while_pending_rights_block_live_validation(
    tmp_path: Path,
    provider_id: str,
    credential_fields: dict[str, str],
    secret_canary: str,
) -> None:
    now = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
    owner = LocalApiKeyIdentity.issue(
        owner="owner-local",
        environment="local",
        scopes={
            "price_research_eligibility.read",
            "source_credential.read",
            "source_credential.manage",
            "model_governance.read",
            "model_governance.approve",
        },
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"internal", "licensed", "restricted", "secret"},
        principal_classification="individual_non_commercial",
    )
    source_adapter = LocalApiKeyIdentity.issue(
        owner="owner-local-source-adapter",
        environment="local",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    validator = NeverCalledLiveValidator()
    secret_root = tmp_path / "source-secrets"
    policy_set_id = "ticket-09-owner-operator-v1"
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'operator.db'}",
        object_root=tmp_path / "objects",
        authorization_time=now,
        local_identity=owner,
        authorization_policy_set_id=policy_set_id,
        authorization_policy_override=build_pending_rights_operator_authorization_policy(
            owner.context
        ),
        source_adapter_security_context=source_adapter.context,
        source_credential_validators={provider_id: validator},
        secret_provider=EncryptedFilesystemSecretProvider(secret_root, clock=lambda: now),
    )
    application.authorization_policy_repository.install(
        policy_set_id,
        build_pending_rights_operator_authorization_policy(source_adapter.context),
    )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {
        "Authorization": owner.credential.authorization_header(),
        "X-Trace-Id": f"trace-owner-operator-{provider_id}",
    }
    endpoint = f"/api/v1/operations/source-credentials/{provider_id}"

    configured = client.put(
        endpoint,
        headers=headers,
        json={"credential_fields": credential_fields},
    )
    validation = client.post(f"{endpoint}/validations", headers=headers)
    listing = client.get("/api/v1/operations/source-credentials", headers=headers)

    assert configured.status_code == 200
    assert configured.json()["readiness"] == "configured"
    assert configured.json()["reason_code"] == "source_credential_not_validated"
    assert validation.status_code == 403
    assert validation.json()["code"] == "authorization_denied"
    listed = next(item for item in listing.json()["items"] if item["provider_id"] == provider_id)
    assert listed["readiness"] == "configured"
    assert validator.calls == []
    assert secret_canary not in configured.text + validation.text + listing.text
    assert all(secret_canary.encode() not in path.read_bytes() for path in secret_root.iterdir())
