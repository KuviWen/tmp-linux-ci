from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.alpaca_market_data import ProviderHttpRequest, ProviderHttpResponse
from stock_forecasting.application import Application, build_test_application
from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    PolicyDeniedOutcome,
    SourceEntitlement,
    SourcePolicyVersion,
)
from stock_forecasting.data_supply import SourceBundleMemberRequest, SourcePartitionRequest
from stock_forecasting.operations_control import OperationsControl
from stock_forecasting.source_credentials import (
    CredentialNotReady,
    CredentialValidationEvidence,
    CredentialValidationResult,
    InMemorySecretProvider,
    ManagedSourceCredentialResolver,
    SecretLease,
    SecretProvider,
    SecretRef,
)


class LiteralCredentialValidator:
    def __init__(self, result: CredentialValidationResult) -> None:
        self.result = result
        self.calls: list[dict[str, str]] = []

    def validate(self, credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        self.calls.append(dict(credential_fields))
        return self.result


class CallbackCredentialValidator:
    def __init__(self) -> None:
        self.callback: Callable[[], None] | None = None

    def validate(self, credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        assert credential_fields["api_key_id"] == "PK-STALE-FIRST"
        assert self.callback is not None
        self.callback()
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
        )


class RecordingSecretProvider:
    def __init__(self) -> None:
        self.delegate = InMemorySecretProvider()
        self.refs: set[str] = set()
        self.fail_revoke = False

    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef:
        ref = self.delegate.put(provider_id=provider_id, credential_fields=credential_fields)
        self.refs.add(ref.secret_ref_id)
        return ref

    def checkout(self, secret_ref_id: str) -> SecretLease:
        return self.delegate.checkout(secret_ref_id)

    def revoke(self, secret_ref_id: str) -> None:
        if self.fail_revoke:
            raise OSError("injected_secret_delete_failure")
        self.delegate.revoke(secret_ref_id)
        self.refs.discard(secret_ref_id)


class SequenceProviderTransport:
    def __init__(self, responses: list[ProviderHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _credential_application(
    tmp_path: Path,
    *,
    credential_validator: LiteralCredentialValidator | None = None,
    secret_provider: SecretProvider | None = None,
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
        secret_provider=secret_provider,
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
    source_basis = provider["source_basis"]
    assert source_basis["source_basis_id"] == "ALPACA-BASIC-US-MARKET-DATA-01"
    assert source_basis["plan_id"] == "basic-2026-08-15"
    assert source_basis["principal_classification"] == "individual_non_commercial"
    assert source_basis["terms_content_sha256"] == (
        "2dc774d4aeeafbe4c7f0565e7842d932bc8bc10488af805fce43b8734e7b9859"
    )
    assert source_basis["qualification_status"] == "candidate_terms_not_archived"
    assert provider["required_uses"] == [
        "backup_restore",
        "ingest",
        "internal_display",
        "model",
        "retain_observed_history",
        "transform",
    ]
    assert [member["dataset_id"] for member in source_basis["members"]] == [
        "alpaca-us-stock-bars-v2",
        "alpaca-us-corporate-actions-v1",
        "alpaca-us-trading-calendar-v2",
    ]
    assert [member["dataset_id"] for member in source_basis["supplemental_references"]] == [
        "nasdaq-current-symbol-directory",
    ]
    assert all(member["allowed_uses"] == [] for member in source_basis["members"])
    assert all(member["rights_status"] == "unverified" for member in source_basis["members"])


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


def test_malformed_credential_body_never_echoes_rejected_secret(tmp_path: Path) -> None:
    _, client, headers = _credential_application(tmp_path)
    plaintext = "MALFORMED-PLAINTEXT-SECRET"

    response = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={"credential_fields": [plaintext]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_failed"
    assert plaintext not in response.text


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
            evidence=CredentialValidationEvidence(
                contract_id="alpaca-ticket-07-live-v1",
                live_validation="passed",
                ticker_count=10,
            ),
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
        "validation_evidence": {
            "contract_id": "alpaca-ticket-07-live-v1",
            "live_validation": "passed",
            "ticker_count": 10,
        },
    }
    assert validator.calls == [
        {
            "api_key_id": "PK-VALIDATE",
            "api_secret_key": "validate-secret-value",
        }
    ]
    assert "PK-VALIDATE" not in validated.text
    assert "validate-secret-value" not in validated.text


def test_credential_validation_rejects_arbitrary_secret_bearing_evidence() -> None:
    with pytest.raises(ValueError, match="source_credential_validation_evidence_invalid"):
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence={"api_secret_key": "must-never-be-persisted"},  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="source_credential_validation_evidence_invalid"):
        CredentialValidationEvidence(live_validation="invented")  # type: ignore[arg-type]


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


def test_rest_managed_credential_is_consumed_by_the_application_us_adapter(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(readiness="valid", reason_code="source_credential_valid")
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-SHARED-RUNTIME",
                "api_secret_key": "shared-runtime-secret",
            }
        },
    )
    client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )
    transport = SequenceProviderTransport(
        [
            ProviderHttpResponse(
                200,
                b'{"bars":{"AAPL":[{"t":"2024-01-03T05:00:00Z","o":184.22,"h":185.88,"l":183.43,"c":184.25,"v":58414460}]},"next_page_token":null}',
            ),
            ProviderHttpResponse(200, b'{"cash_dividends":[],"next_page_token":null}'),
            ProviderHttpResponse(
                200,
                b'[{"date":"2024-01-03","open":"09:30","close":"16:00"}]',
            ),
        ]
    )
    adapter = application.build_alpaca_price_adapter(
        transport=transport,
        source_access_mode="engineering_double",
    )

    loaded = adapter.load(
        SourcePartitionRequest(
            request_id="request-shared-runtime-credential",
            trace_id="trace-shared-runtime-credential",
            source_id="alpaca-us-stock-bars",
            mode="historical",
            listing_ids=("70000000-0000-4000-8000-000000000001",),
            start_date=datetime(2024, 1, 3, tzinfo=UTC).date(),
            end_date=datetime(2024, 1, 3, tzinfo=UTC).date(),
            expected_checkpoint=None,
            distribution_id="alpaca-us-stock-bars-v2",
            distribution_url="https://data.alpaca.markets/v2/stocks/bars",
            bundle_members=(
                SourceBundleMemberRequest(
                    dataset_id="alpaca-us-corporate-actions-v1",
                    distribution_id="alpaca-us-corporate-actions-v1",
                    distribution_url="https://data.alpaca.markets/v1/corporate-actions",
                    schema_version="alpaca-corporate-actions-v1",
                ),
                SourceBundleMemberRequest(
                    dataset_id="alpaca-us-trading-calendar-v2",
                    distribution_id="alpaca-us-trading-calendar-v2",
                    distribution_url="https://paper-api.alpaca.markets/v2/calendar",
                    schema_version="alpaca-trading-calendar-v2",
                ),
            ),
        )
    )

    assert loaded.collection.request_id == "request-shared-runtime-credential"
    assert len(transport.requests) == 3
    assert all(
        request.headers["APCA-API-KEY-ID"] == "PK-SHARED-RUNTIME"
        and request.headers["APCA-API-SECRET-KEY"] == "shared-runtime-secret"
        for request in transport.requests
    )
    assert all("shared-runtime-secret" not in repr(request) for request in transport.requests)


def test_revoked_credential_can_be_reapplied_through_the_write_only_rest_seam(
    tmp_path: Path,
) -> None:
    application, client, headers = _credential_application(tmp_path)
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    first = client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-FIRST-APPLICATION",
                "api_secret_key": "first-application-secret",
            }
        },
    ).json()
    revoked = client.delete(endpoint, headers=headers).json()

    reapplied = client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-REAPPLIED",
                "api_secret_key": "reapplied-secret",
            }
        },
    )

    assert reapplied.status_code == 200
    assert reapplied.json()["readiness"] == "configured"
    assert reapplied.json()["version"] == revoked["version"] + 1
    assert reapplied.json()["secret_ref_id"] != first["secret_ref_id"]
    assert application.secret_provider.checkout(
        reapplied.json()["secret_ref_id"]
    ).credential_fields() == {
        "api_key_id": "PK-REAPPLIED",
        "api_secret_key": "reapplied-secret",
    }
    assert "PK-REAPPLIED" not in reapplied.text
    assert "reapplied-secret" not in reapplied.text


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


def test_operations_page_can_mutate_with_an_http_only_session_and_csrf_token(
    tmp_path: Path,
) -> None:
    _, client, headers = _credential_application(tmp_path)

    page = client.get("/operations/source-credentials", headers=headers)

    assert page.status_code == 200
    assert "HttpOnly" in page.headers["set-cookie"]
    assert 'name="expires_at"' in page.text
    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text)
    assert csrf_match is not None
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    without_csrf = client.put(
        endpoint,
        json={
            "credential_fields": {
                "api_key_id": "PK-BROWSER",
                "api_secret_key": "browser-secret",
            }
        },
    )
    assert without_csrf.status_code == 403

    configured = client.put(
        endpoint,
        headers={"X-CSRF-Token": csrf_match.group(1)},
        json={
            "credential_fields": {
                "api_key_id": "PK-BROWSER",
                "api_secret_key": "browser-secret",
            }
        },
    )

    assert configured.status_code == 200
    assert configured.json()["readiness"] == "configured"
    assert "PK-BROWSER" not in configured.text
    assert "browser-secret" not in configured.text


def test_validation_authorizes_before_reading_restricted_credential_state(
    tmp_path: Path,
) -> None:
    application, _, _ = _credential_application(tmp_path)
    unauthorized = LocalApiKeyIdentity.issue(
        owner="ticket-07-read-only-caller",
        environment="development",
        scopes={"source_credential.read"},
        issued_at=datetime(2026, 8, 15, 7, 59, tzinfo=UTC),
        expires_at=datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        data_protection_classes={"restricted"},
    )

    outcome = application.operations_control.validate_source_credential(
        provider_id="alpaca-market-data-basic",
        trace_id="trace-p2-credential-denied-before-read",
        security_context=unauthorized.context,
    )

    assert isinstance(outcome, PolicyDeniedOutcome)


def test_expired_credential_is_fail_closed_without_contacting_provider(tmp_path: Path) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(readiness="valid", reason_code="source_credential_valid")
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    configured = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-EXPIRED-BY-METADATA",
                "api_secret_key": "expired-by-metadata-secret",
            },
            "expires_at": "2026-08-15T07:59:00Z",
        },
    )
    assert configured.status_code == 200
    listed = client.get("/api/v1/operations/source-credentials", headers=headers).json()["items"][0]
    assert listed["readiness"] == "expired"
    assert listed["reason_code"] == "source_credential_expired"

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )

    assert validated.status_code == 200
    assert validated.json()["readiness"] == "expired"
    assert validated.json()["reason_code"] == "source_credential_expired"
    assert validator.calls == []
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
    )
    with pytest.raises(CredentialNotReady, match="source_credential_expired"):
        resolver.resolve_valid("alpaca-market-data-basic")


def test_a_previously_valid_credential_fails_closed_after_its_expiry(tmp_path: Path) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(readiness="valid", reason_code="source_credential_valid")
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-VALID-THEN-EXPIRED",
                "api_secret_key": "valid-then-expired-secret",
            },
            "expires_at": "2026-08-15T08:01:00Z",
        },
    )
    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )
    assert validated.json()["readiness"] == "valid"

    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        clock=lambda: datetime(2026, 8, 15, 8, 2, tzinfo=UTC),
    )

    with pytest.raises(CredentialNotReady, match="source_credential_expired"):
        resolver.resolve_valid("alpaca-market-data-basic")


def test_validation_result_cannot_be_applied_to_a_concurrently_rotated_secret(
    tmp_path: Path,
) -> None:
    validator = CallbackCredentialValidator()
    application, client, headers = _credential_application(tmp_path)
    application.operations_control = OperationsControl(
        application.state_store,
        authorization_policy=application.authorization_policy,
        secret_provider=application.secret_provider,
        source_credential_validators={"alpaca-market-data-basic": validator},
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-STALE-FIRST",
                "api_secret_key": "stale-first-secret",
            }
        },
    )

    def rotate_during_validation() -> None:
        outcome = application.operations_control.rotate_source_credential(
            provider_id="alpaca-market-data-basic",
            credential_fields={
                "api_key_id": "PK-STALE-SECOND",
                "api_secret_key": "stale-second-secret",
            },
            expires_at=None,
            trace_id="trace-p2-credential-race-rotate",
            security_context=application.security_context,
        )
        assert not isinstance(outcome, PolicyDeniedOutcome)

    validator.callback = rotate_during_validation

    response = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "source_credential_validation_stale"
    current = application.state_store.get_source_credential(provider_id="alpaca-market-data-basic")
    assert current is not None
    assert current["readiness"] == "configured"
    assert current["version"] == 2


def test_secret_write_is_compensated_when_metadata_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_provider = RecordingSecretProvider()
    application, _, _ = _credential_application(tmp_path, secret_provider=secret_provider)

    def fail_publish(**_: Any) -> dict[str, object]:
        raise RuntimeError("injected_metadata_commit_failure")

    monkeypatch.setattr(application.state_store, "publish_source_credential", fail_publish)

    with pytest.raises(RuntimeError, match="injected_metadata_commit_failure"):
        application.operations_control.set_source_credential(
            provider_id="alpaca-market-data-basic",
            credential_fields={
                "api_key_id": "PK-COMPENSATE",
                "api_secret_key": "compensate-secret",
            },
            expires_at=None,
            trace_id="trace-p2-credential-compensate",
            security_context=application.security_context,
        )

    assert secret_provider.refs == set()


def test_failed_write_compensation_is_added_to_the_durable_cleanup_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_provider = RecordingSecretProvider()
    secret_provider.fail_revoke = True
    application, _, _ = _credential_application(tmp_path, secret_provider=secret_provider)

    def fail_publish(**_: Any) -> dict[str, object]:
        raise RuntimeError("injected_metadata_commit_failure")

    monkeypatch.setattr(application.state_store, "publish_source_credential", fail_publish)

    with pytest.raises(RuntimeError, match="injected_metadata_commit_failure"):
        application.operations_control.set_source_credential(
            provider_id="alpaca-market-data-basic",
            credential_fields={
                "api_key_id": "PK-COMPENSATE-PENDING",
                "api_secret_key": "compensate-pending-secret",
            },
            trace_id="trace-p2-credential-compensate-pending",
            security_context=application.security_context,
        )

    assert application.state_store.list_pending_source_secret_cleanup(
        provider_id="alpaca-market-data-basic"
    ) == list(secret_provider.refs)


def test_validation_projects_a_missing_secret_as_not_ready(tmp_path: Path) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(readiness="valid", reason_code="source_credential_valid")
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    configured = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-MISSING-LEASE",
                "api_secret_key": "missing-lease-secret",
            }
        },
    ).json()
    application.secret_provider.revoke(configured["secret_ref_id"])

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )

    assert validated.status_code == 200
    assert validated.json()["readiness"] == "validation_failed"
    assert validated.json()["reason_code"] == "source_credential_secret_unavailable"
    assert validator.calls == []


def test_failed_secret_cleanup_is_durable_and_retried_from_operations(
    tmp_path: Path,
) -> None:
    secret_provider = RecordingSecretProvider()
    application, client, headers = _credential_application(
        tmp_path,
        secret_provider=secret_provider,
    )
    first = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-CLEANUP-FIRST",
                "api_secret_key": "cleanup-first-secret",
            }
        },
    ).json()
    secret_provider.fail_revoke = True

    rotated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/rotations",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-CLEANUP-SECOND",
                "api_secret_key": "cleanup-second-secret",
            }
        },
    )

    assert rotated.status_code == 200
    assert rotated.json()["secret_cleanup_pending"] is True
    assert first["secret_ref_id"] in secret_provider.refs
    secret_provider.fail_revoke = False

    listed = client.get("/api/v1/operations/source-credentials", headers=headers)

    assert listed.status_code == 200
    assert first["secret_ref_id"] in secret_provider.refs
    assert listed.json()["items"][0]["secret_cleanup_pending"] is True

    third = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/rotations",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-CLEANUP-THIRD",
                "api_secret_key": "cleanup-third-secret",
            }
        },
    )

    assert third.status_code == 200
    assert first["secret_ref_id"] not in secret_provider.refs
    assert "secret_cleanup_pending" not in third.json()
