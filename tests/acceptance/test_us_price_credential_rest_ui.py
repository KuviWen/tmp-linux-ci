from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import Application, build_test_application
from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceEntitlement,
    SourcePolicyVersion,
)
from stock_forecasting.source_credentials import (
    CredentialNotReady,
    CredentialValidationResult,
    ManagedSourceCredentialResolver,
)


class LiteralCredentialValidator:
    def __init__(self, result: CredentialValidationResult) -> None:
        self.result = result
        self.calls: list[dict[str, str]] = []

    def validate(self, credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        self.calls.append(dict(credential_fields))
        return self.result


def _credential_application(
    tmp_path: Path,
    *,
    credential_validator: LiteralCredentialValidator | None = None,
) -> tuple[Application, TestClient, dict[str, str]]:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-source-administrator",
        environment="development",
        scopes={"source_credential.read", "source_credential.manage"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"restricted", "secret"},
    )
    actions = frozenset({"source_credential.read", "source_credential.manage"})
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-ticket-07-source-credential-v1",
                principal_id=identity.context.principal_id,
                actions=actions,  # type: ignore[arg-type]
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-ticket-07-source-credential-v1",
                dataset_id="source-credential-metadata",
                allowed_actions=actions,  # type: ignore[arg-type]
                purposes=frozenset({"source_administration"}),
                environments=frozenset({"development"}),
                data_protection_class="restricted",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-ticket-07-source-credential-v1",
                principal_id=identity.context.principal_id,
                dataset_id="source-credential-metadata",
                status="active",
                allowed_actions=actions,  # type: ignore[arg-type]
                purposes=frozenset({"source_administration"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
    )
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ticket-07.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_override=policy,
        source_credential_validators=(
            {"alpaca-market-data-basic": credential_validator}
            if credential_validator is not None
            else None
        ),
    )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    return application, client, {"Authorization": identity.credential.authorization_header()}


def test_alpaca_credential_readiness_is_visible_without_a_secret(tmp_path: Path) -> None:
    _, client, headers = _credential_application(tmp_path)

    response = client.get(
        "/api/v1/operations/source-credentials",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-missing"},
    )

    assert response.status_code == 200
    provider = response.json()["items"][0]
    assert {
        key: provider[key]
        for key in (
            "provider_id",
            "display_name",
            "credential_kind",
            "required_fields",
            "readiness",
            "reason_code",
            "secret_ref_id",
            "version",
            "configured_at",
            "last_validated_at",
            "registration_url",
            "key_management_url",
        )
    } == {
        "provider_id": "alpaca-market-data-basic",
        "display_name": "Alpaca Market Data Basic",
        "credential_kind": "api_key_pair",
        "required_fields": ["api_key_id", "api_secret_key"],
        "readiness": "missing",
        "reason_code": "source_credential_missing",
        "secret_ref_id": None,
        "version": None,
        "configured_at": None,
        "last_validated_at": None,
        "registration_url": "https://app.alpaca.markets/signup",
        "key_management_url": "https://app.alpaca.markets/paper/dashboard/overview",
    }
    assert provider["source_basis_id"] == "ALPACA-BASIC-US-MARKET-DATA-01"
    assert provider["plan_id"] == "basic-2026-08-15"
    assert provider["principal_classification"] == "individual_non_commercial"
    assert provider["terms_content_sha256"] == (
        "2dc774d4aeeafbe4c7f0565e7842d932bc8bc10488af805fce43b8734e7b9859"
    )
    assert provider["qualification_status"] == "candidate_terms_not_archived"
    assert provider["required_uses"] == [
        "backup_restore",
        "ingest",
        "internal_display",
        "model",
        "retain_observed_history",
        "transform",
    ]
    assert [member["dataset_id"] for member in provider["members"]] == [
        "alpaca-us-stock-bars-v2",
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-trading-calendar-v2",
        "nasdaq-current-symbol-directory",
    ]


def test_operations_page_exposes_write_only_credential_controls_and_provider_links(
    tmp_path: Path,
) -> None:
    _, client, headers = _credential_application(tmp_path)

    response = client.get(
        "/operations/source-credentials",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-page"},
    )

    assert response.status_code == 200
    assert "來源憑證管理" in response.text
    assert "Alpaca Market Data Basic" in response.text
    assert "source_credential_missing" in response.text
    assert 'href="https://app.alpaca.markets/signup"' in response.text
    assert 'href="https://app.alpaca.markets/paper/dashboard/overview"' in response.text
    assert 'name="api_key_id"' in response.text
    assert 'name="api_secret_key"' in response.text
    assert response.text.count('type="password"') == 2
    assert 'data-operation="set"' in response.text
    assert 'data-operation="rotate"' in response.text
    assert 'data-operation="validate"' in response.text
    assert 'data-operation="revoke"' in response.text
    assert "/api/v1/operations/source-credentials/alpaca-market-data-basic" in response.text
    assert "程式不會自動建立帳號、處理 CAPTCHA、email、MFA 或接受條款" in response.text


def test_alpaca_credential_can_be_set_without_ever_being_returned(tmp_path: Path) -> None:
    _, client, headers = _credential_application(tmp_path)
    api_key_id = "PK-TICKET-07-KEY"
    api_secret_key = "ticket-07-super-secret-value"

    configured = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-set"},
        json={
            "credential_fields": {
                "api_key_id": api_key_id,
                "api_secret_key": api_secret_key,
            }
        },
    )

    assert configured.status_code == 200
    configured_payload = configured.json()
    assert configured_payload == {
        "provider_id": "alpaca-market-data-basic",
        "readiness": "configured",
        "reason_code": "source_credential_not_validated",
        "secret_ref_id": configured_payload["secret_ref_id"],
        "version": 1,
        "configured_at": "2026-08-15T08:00:00Z",
        "last_validated_at": None,
    }
    assert configured_payload["secret_ref_id"].startswith("secret-ref:")
    listed = client.get(
        "/api/v1/operations/source-credentials",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-list-after-set"},
    )
    serialized = configured.text + listed.text
    assert api_key_id not in serialized
    assert api_secret_key not in serialized
    database_bytes = (tmp_path / "ticket-07.db").read_bytes()
    assert api_key_id.encode() not in database_bytes
    assert api_secret_key.encode() not in database_bytes
    listed_provider = listed.json()["items"][0]
    assert {
        key: listed_provider[key]
        for key in (
            "provider_id",
            "display_name",
            "credential_kind",
            "required_fields",
            "readiness",
            "reason_code",
            "secret_ref_id",
            "version",
            "configured_at",
            "last_validated_at",
            "registration_url",
            "key_management_url",
        )
    } == {
        "provider_id": "alpaca-market-data-basic",
        "display_name": "Alpaca Market Data Basic",
        "credential_kind": "api_key_pair",
        "required_fields": ["api_key_id", "api_secret_key"],
        "readiness": "configured",
        "reason_code": "source_credential_not_validated",
        "secret_ref_id": configured_payload["secret_ref_id"],
        "version": 1,
        "configured_at": "2026-08-15T08:00:00Z",
        "last_validated_at": None,
        "registration_url": "https://app.alpaca.markets/signup",
        "key_management_url": "https://app.alpaca.markets/paper/dashboard/overview",
    }


def test_alpaca_credential_rotation_replaces_the_old_secret_lease(tmp_path: Path) -> None:
    application, client, headers = _credential_application(tmp_path)
    first = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-first"},
        json={
            "credential_fields": {
                "api_key_id": "PK-FIRST",
                "api_secret_key": "first-secret-value",
            }
        },
    ).json()

    rotated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/rotations",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-rotate"},
        json={
            "credential_fields": {
                "api_key_id": "PK-SECOND",
                "api_secret_key": "second-secret-value",
            }
        },
    )

    assert rotated.status_code == 200
    payload = rotated.json()
    assert payload == {
        "provider_id": "alpaca-market-data-basic",
        "readiness": "configured",
        "reason_code": "source_credential_not_validated",
        "secret_ref_id": payload["secret_ref_id"],
        "version": 2,
        "configured_at": "2026-08-15T08:00:00Z",
        "last_validated_at": None,
    }
    assert payload["secret_ref_id"] != first["secret_ref_id"]
    with pytest.raises(KeyError, match="source_credential_secret_unavailable"):
        application.secret_provider.checkout(first["secret_ref_id"])
    lease = application.secret_provider.checkout(payload["secret_ref_id"])
    assert lease.credential_fields() == {
        "api_key_id": "PK-SECOND",
        "api_secret_key": "second-secret-value",
    }
    assert "PK-SECOND" not in rotated.text
    assert "second-secret-value" not in rotated.text


def test_alpaca_credential_can_be_revoked_without_erasing_prior_metadata(
    tmp_path: Path,
) -> None:
    application, client, headers = _credential_application(tmp_path)
    configured = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-before-revoke"},
        json={
            "credential_fields": {
                "api_key_id": "PK-REVOKE",
                "api_secret_key": "revoke-secret-value",
            }
        },
    ).json()

    revoked = client.delete(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-revoke"},
    )

    assert revoked.status_code == 200
    assert revoked.json() == {
        "provider_id": "alpaca-market-data-basic",
        "readiness": "revoked",
        "reason_code": "source_credential_revoked",
        "secret_ref_id": configured["secret_ref_id"],
        "version": 2,
        "configured_at": "2026-08-15T08:00:00Z",
        "last_validated_at": None,
        "revoked_at": "2026-08-15T08:00:00Z",
    }
    with pytest.raises(KeyError, match="source_credential_secret_unavailable"):
        application.secret_provider.checkout(configured["secret_ref_id"])
    listed = client.get(
        "/api/v1/operations/source-credentials",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-list-revoked"},
    ).json()["items"][0]
    assert listed["readiness"] == "revoked"
    assert listed["reason_code"] == "source_credential_revoked"
    assert listed["revoked_at"] == "2026-08-15T08:00:00Z"


def test_alpaca_credential_validation_uses_the_secret_without_returning_it(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
        )
    )
    _, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    configured = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-before-validation"},
        json={
            "credential_fields": {
                "api_key_id": "PK-VALIDATE",
                "api_secret_key": "validate-secret-value",
            }
        },
    ).json()

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-validation"},
    )

    assert validated.status_code == 200
    assert validated.json() == {
        "provider_id": "alpaca-market-data-basic",
        "readiness": "valid",
        "reason_code": "source_credential_valid",
        "secret_ref_id": configured["secret_ref_id"],
        "version": 2,
        "configured_at": "2026-08-15T08:00:00Z",
        "last_validated_at": "2026-08-15T08:00:00Z",
    }
    assert validator.calls == [
        {
            "api_key_id": "PK-VALIDATE",
            "api_secret_key": "validate-secret-value",
        }
    ]
    assert "PK-VALIDATE" not in validated.text
    assert "validate-secret-value" not in validated.text


def test_only_a_valid_managed_credential_can_be_resolved_for_provider_use(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
    )

    with pytest.raises(CredentialNotReady, match="source_credential_missing"):
        resolver.resolve_valid("alpaca-market-data-basic")

    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-resolver-set"},
        json={
            "credential_fields": {
                "api_key_id": "PK-RESOLVER",
                "api_secret_key": "resolver-secret-value",
            }
        },
    )
    with pytest.raises(CredentialNotReady, match="source_credential_not_validated"):
        resolver.resolve_valid("alpaca-market-data-basic")

    client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-resolver-validate"},
    )
    assert resolver.resolve_valid("alpaca-market-data-basic") == {
        "api_key_id": "PK-RESOLVER",
        "api_secret_key": "resolver-secret-value",
    }

    client.delete(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-resolver-revoke"},
    )
    with pytest.raises(CredentialNotReady, match="source_credential_revoked"):
        resolver.resolve_valid("alpaca-market-data-basic")


def test_failed_validation_keeps_provider_use_fail_closed(tmp_path: Path) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="validation_failed",
            reason_code="source_credential_authentication_failed",
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-invalid-set"},
        json={
            "credential_fields": {
                "api_key_id": "PK-EXPIRED",
                "api_secret_key": "expired-secret-value",
            }
        },
    )

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-invalid-validation"},
    )

    assert validated.status_code == 200
    assert validated.json()["readiness"] == "validation_failed"
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
    )
    with pytest.raises(CredentialNotReady, match="source_credential_authentication_failed"):
        resolver.resolve_valid("alpaca-market-data-basic")
