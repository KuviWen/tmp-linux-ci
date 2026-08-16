from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from time import monotonic
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
    SourceAccessMode,
    SourceEntitlement,
    SourcePolicyVersion,
    build_us_zero_fee_engineering_authorization_policy,
)
from stock_forecasting.data_supply import (
    SourceBundleMemberRequest,
    SourceCredentialRequired,
    SourcePartitionRequest,
)
from stock_forecasting.operations_control import OperationsControl
from stock_forecasting.source_credentials import (
    CredentialNotReady,
    CredentialValidationEvidence,
    CredentialValidationResult,
    EncryptedFilesystemSecretProvider,
    InMemorySecretProvider,
    ManagedSourceCredentialResolver,
    SecretCorruptError,
    SecretLease,
    SecretProvider,
    SecretRef,
    SecretUnavailableError,
    SecretUseContext,
    SourceContractAssessment,
    SourceCredentialValidator,
)
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest


class LiteralCredentialValidator:
    source_access_mode: SourceAccessMode = "engineering_double"

    def __init__(self, result: CredentialValidationResult) -> None:
        self.result = result
        self.calls: list[dict[str, str]] = []

    def validate(self, credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        self.calls.append(dict(credential_fields))
        return self.result


class CallbackCredentialValidator:
    source_access_mode: SourceAccessMode = "engineering_double"

    def __init__(self) -> None:
        self.callback: Callable[[], None] | None = None

    def validate(self, credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        assert credential_fields["api_key_id"] == "PK-STALE-FIRST"
        assert self.callback is not None
        self.callback()
        return CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )


class SecretBearingExceptionValidator:
    source_access_mode: SourceAccessMode = "engineering_double"

    def validate(self, credential_fields: Mapping[str, str]) -> CredentialValidationResult:
        raise ValueError(f"provider rejected credential fields: {credential_fields}")


class ManualLeaseExpiryHandle:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualLeaseExpiryScheduler:
    def __init__(self) -> None:
        self.scheduled: list[tuple[float, ManualLeaseExpiryHandle]] = []

    def __call__(
        self,
        delay_seconds: float,
        callback: Callable[[], None],
    ) -> ManualLeaseExpiryHandle:
        handle = ManualLeaseExpiryHandle(callback)
        self.scheduled.append((delay_seconds, handle))
        return handle

    def fire_all(self) -> None:
        for _, handle in tuple(self.scheduled):
            if not handle.cancelled:
                handle.callback()


class RecordingSecretProvider:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        expiry_scheduler: ManualLeaseExpiryScheduler | None = None,
    ) -> None:
        self.delegate = InMemorySecretProvider(
            clock=clock or (lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC)),
            monotonic_clock=monotonic_clock,
            expiry_scheduler=expiry_scheduler,
        )
        self.refs: set[str] = set()
        self.fail_put = False
        self.fail_revoke = False
        self.fail_checkout = False
        self.checkout_exception: Exception | None = None
        self.checkout_calls: list[str] = []
        self.checkout_contexts: list[SecretUseContext] = []
        self.leases: list[SecretLease] = []

    def put(self, *, provider_id: str, credential_fields: Mapping[str, str]) -> SecretRef:
        if self.fail_put:
            raise OSError("secret provider write failed with SHOULD-NOT-BE-AUDITED")
        ref = self.delegate.put(provider_id=provider_id, credential_fields=credential_fields)
        self.refs.add(ref.secret_ref_id)
        return ref

    def checkout(self, secret_ref: SecretRef, use_context: SecretUseContext) -> SecretLease:
        self.checkout_calls.append(secret_ref.secret_ref_id)
        self.checkout_contexts.append(use_context)
        if self.fail_checkout:
            raise OSError("provider exception contained SHOULD-NOT-BE-AUDITED")
        if self.checkout_exception is not None:
            raise self.checkout_exception
        lease = self.delegate.checkout(secret_ref, use_context)
        self.leases.append(lease)
        return lease

    def revoke(self, secret_ref: SecretRef) -> None:
        if self.fail_revoke:
            raise OSError("injected_secret_delete_failure")
        self.delegate.revoke(secret_ref)
        self.refs.discard(secret_ref.secret_ref_id)


class ConcurrentCheckoutSecretProvider(RecordingSecretProvider):
    def __init__(self) -> None:
        super().__init__()
        self.synchronize_checkout = False
        self.concurrent_checkout_attempts = 0
        self.checkout_barrier = Barrier(2)

    def checkout(self, secret_ref: SecretRef, use_context: SecretUseContext) -> SecretLease:
        if self.synchronize_checkout:
            self.concurrent_checkout_attempts += 1
            with suppress(BrokenBarrierError):
                self.checkout_barrier.wait(timeout=0.5)
        return super().checkout(secret_ref, use_context)


class SequenceProviderTransport:
    def __init__(self, responses: list[ProviderHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _direct_secret_context(*, credential_version: int) -> SecretUseContext:
    lease_not_before = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    return SecretUseContext(
        workload_principal_id="workload:ticket-07-test",
        environment="development",
        source_id="alpaca-us-stock-bars",
        destination="alpaca-market-data-basic",
        purpose="credential_contract_test",
        request_id=f"request-direct-secret-{credential_version}",
        work_id=f"work-direct-secret-{credential_version}",
        credential_version=credential_version,
        lease_duration=timedelta(minutes=5),
        lease_not_before=lease_not_before,
        lease_expires_at=lease_not_before + timedelta(minutes=5),
    )


def _credential_application(
    tmp_path: Path,
    *,
    credential_validator: SourceCredentialValidator | None = None,
    secret_provider: SecretProvider | None = None,
    source_adapter_identity: LocalApiKeyIdentity | None = None,
    install_source_adapter_policy: bool = True,
    source_adapter_enabled: bool = True,
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
    resolved_source_adapter_identity = (
        source_adapter_identity
        or LocalApiKeyIdentity.issue(
            owner="ticket-07-alpaca-source-adapter",
            environment="development",
            scopes={"market_data.collect"},
            issued_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
            data_protection_classes={"licensed", "secret"},
            principal_classification="individual_non_commercial",
        )
        if source_adapter_enabled
        else None
    )
    policy_set_id = "ticket-07-credential-acceptance-v1"
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'ticket-07.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_set_id=policy_set_id,
        authorization_policy_override=policy,
        source_credential_validators=(
            {"alpaca-market-data-basic": credential_validator}
            if credential_validator is not None
            else None
        ),
        secret_provider=secret_provider,
        source_adapter_security_context=(
            resolved_source_adapter_identity.context
            if resolved_source_adapter_identity is not None
            else None
        ),
    )
    if install_source_adapter_policy and resolved_source_adapter_identity is not None:
        application.authorization_policy_repository.install(
            policy_set_id,
            build_us_zero_fee_engineering_authorization_policy(
                resolved_source_adapter_identity.context
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
    assert "const credential = result.credential ?? result;" in response.text
    assert "`${credential.readiness}: ${credential.reason_code} (v${credential.version})`" in (
        response.text
    )


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


@pytest.mark.parametrize("operation", ["set", "rotate"])
def test_secret_write_failure_keeps_allow_and_terminal_audit(
    tmp_path: Path,
    operation: str,
) -> None:
    secret_provider = RecordingSecretProvider(clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC))
    application, client, headers = _credential_application(
        tmp_path,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    previous: dict[str, object] | None = None
    if operation == "rotate":
        previous = client.put(
            endpoint,
            headers={**headers, "X-Trace-Id": "trace-before-failed-rotation"},
            json={
                "credential_fields": {
                    "api_key_id": "PK-BEFORE-FAILED-ROTATION",
                    "api_secret_key": "before-failed-rotation-secret",
                }
            },
        ).json()
    secret_provider.fail_put = True
    trace_id = f"trace-secret-{operation}-failure"

    with pytest.raises(RuntimeError, match="source_credential_write_failed") as raised:
        client.request(
            "PUT" if operation == "set" else "POST",
            endpoint if operation == "set" else f"{endpoint}/rotations",
            headers={**headers, "X-Trace-Id": trace_id},
            json={
                "credential_fields": {
                    "api_key_id": f"PK-{operation.upper()}-FAILURE",
                    "api_secret_key": f"{operation}-failure-secret",
                }
            },
        )

    assert "SHOULD-NOT-BE-AUDITED" not in str(raised.value)
    assert f"{operation}-failure-secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None

    events = application.state_store.list_audit_events(trace_id=trace_id)
    assert events[0]["action"] == "source_credential.manage"
    assert events[0]["outcome"] == "allowed"
    write_events = [event for event in events if event["action"] == "source_credential.write"]
    assert [event["outcome"] for event in write_events] == ["attempted", "failed"]
    assert write_events[-1]["reason_code"] == "source_credential_write_failed"
    assert "SHOULD-NOT-BE-AUDITED" not in str(events)
    assert f"{operation}-failure-secret" not in str(events)
    current = application.state_store.get_source_credential(provider_id="alpaca-market-data-basic")
    if operation == "set":
        assert current is None
    else:
        assert current is not None
        assert current["secret_ref_id"] == previous["secret_ref_id"]  # type: ignore[index]


def test_secret_write_is_revoked_when_success_terminal_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_provider = RecordingSecretProvider()
    application, client, headers = _credential_application(
        tmp_path,
        secret_provider=secret_provider,
    )
    original_record = application.state_store.record_security_event

    def fail_write_success(**kwargs: object) -> None:
        if kwargs["action"] == "source_credential.write" and kwargs["outcome"] == "succeeded":
            raise OSError("secret_write_terminal_audit_unavailable")
        original_record(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(application.state_store, "record_security_event", fail_write_success)

    with pytest.raises(OSError, match="secret_write_terminal_audit_unavailable"):
        client.put(
            "/api/v1/operations/source-credentials/alpaca-market-data-basic",
            headers={**headers, "X-Trace-Id": "trace-secret-write-terminal-failure"},
            json={
                "credential_fields": {
                    "api_key_id": "PK-WRITE-TERMINAL-AUDIT",
                    "api_secret_key": "write-terminal-audit-secret",
                }
            },
        )

    assert secret_provider.refs == set()
    assert (
        application.state_store.get_source_credential(provider_id="alpaca-market-data-basic")
        is None
    )
    events = application.state_store.list_audit_events(
        trace_id="trace-secret-write-terminal-failure"
    )
    assert [event["outcome"] for event in events] == ["allowed", "attempted"]
    assert "write-terminal-audit-secret" not in str(events)


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


@pytest.mark.parametrize(
    "payload",
    [
        {
            "credential_fields": {
                "api_key_id": "PK-EXTRA-FIELD",
                "api_secret_key": "extra-field-secret",
            },
            "unexpected": "must-not-be-ignored",
        },
        {
            "credential_fields": {
                "K" * 129: "bounded-key-secret",
            }
        },
        {
            "credential_fields": {
                "api_key_id": "PK-BOUNDED-VALUE",
                "api_secret_key": "S" * 4097,
            }
        },
    ],
)
def test_credential_write_contract_rejects_unknown_or_unbounded_fields(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    _, client, headers = _credential_application(tmp_path)

    response = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_failed"
    listed = client.get("/api/v1/operations/source-credentials", headers=headers).json()["items"][0]
    assert listed["readiness"] == "missing"


def test_credential_request_body_over_16_kib_is_rejected_before_secret_storage(
    tmp_path: Path,
) -> None:
    _, client, headers = _credential_application(tmp_path)
    oversized_secret = "S" * (16 * 1024)

    response = client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-oversized-credential-body"},
        json={
            "credential_fields": {
                "api_key_id": "PK-OVERSIZED-BODY",
                "api_secret_key": oversized_secret,
            }
        },
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://example.invalid/problems/request-body-too-large",
        "title": "Request body too large",
        "status": 413,
        "detail": "The request body exceeds the 16384-byte limit.",
        "instance": "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        "trace_id": "trace-oversized-credential-body",
        "code": "request_body_too_large",
    }
    assert oversized_secret not in response.text
    listed = client.get("/api/v1/operations/source-credentials", headers=headers).json()["items"][0]
    assert listed["readiness"] == "missing"


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
        application.secret_provider.checkout(
            SecretRef(first["secret_ref_id"]),
            _direct_secret_context(credential_version=1),
        )
    lease = application.secret_provider.checkout(
        SecretRef(payload["secret_ref_id"]),
        _direct_secret_context(credential_version=2),
    )
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
        application.secret_provider.checkout(
            SecretRef(configured["secret_ref_id"]),
            _direct_secret_context(credential_version=2),
        )
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
    manifest = load_us_stock_pool_manifest()
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(
                authentication_status="passed",
            ),
            source_contract_assessment=SourceContractAssessment(
                contract_id="alpaca-ticket-07-live-v1",
                live_validation="passed",
                ticker_count=10,
                pagination_pages=2,
                datasets=(
                    "alpaca-us-stock-bars-v2",
                    "alpaca-us-corporate-actions-v1",
                    "alpaca-us-trading-calendar-v2",
                ),
                symbol_lifecycle_probe="passed",
                universe_manifest_id=manifest.manifest_id,
                reference_graph_version_id=manifest.selection_evidence_version,
                listing_ids=tuple(listing.listing_id for listing in manifest.listings),
            ),
        )
    )
    application, client, headers = _credential_application(
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
    payload = validated.json()
    assert payload["credential"] == {
        "provider_id": "alpaca-market-data-basic",
        "readiness": "valid",
        "reason_code": "source_credential_valid",
        "secret_ref_id": configured["secret_ref_id"],
        "version": 2,
        "configured_at": "2026-08-15T08:00:00Z",
        "last_validated_at": "2026-08-15T08:00:00Z",
        "validation_evidence": {"authentication_status": "passed"},
    }
    assert payload["source_contract_assessment"] == {
        "contract_id": "alpaca-ticket-07-live-v1",
        "live_validation": "passed",
        "ticker_count": 10,
        "pagination_pages": 2,
        "datasets": [
            "alpaca-us-stock-bars-v2",
            "alpaca-us-corporate-actions-v1",
            "alpaca-us-trading-calendar-v2",
        ],
        "symbol_lifecycle_probe": "passed",
        "universe_manifest_id": manifest.manifest_id,
        "reference_graph_version_id": manifest.selection_evidence_version,
        "listing_ids": [listing.listing_id for listing in manifest.listings],
        "source_contract_reason_code": None,
    }
    assert re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        payload["source_contract_assessment_artifact_id"],
    )
    trace = application.state_store.get_trace_evidence("trace-p2-credential-validation")
    assert payload["source_contract_assessment_artifact_id"] in trace["artifact_ids"]
    assessment_artifact = application.state_store.get_canonical_artifact(
        payload["source_contract_assessment_artifact_id"]
    )
    assert assessment_artifact == {
        "artifact_kind": "source_contract_assessment",
        "execution_purpose": "source_administration",
        "payload": {
            "provider_id": "alpaca-market-data-basic",
            "assessed_at": "2026-08-15T08:00:00Z",
            "credential_version": 2,
            "assessment": payload["source_contract_assessment"],
        },
    }
    listed = client.get(
        "/api/v1/operations/source-credentials",
        headers=headers,
    ).json()["items"][0]
    assert listed["validation_evidence"] == {"authentication_status": "passed"}
    assert "source_contract_assessment" not in listed
    assert validator.calls == [
        {
            "api_key_id": "PK-VALIDATE",
            "api_secret_key": "validate-secret-value",
        }
    ]
    assert "PK-VALIDATE" not in validated.text
    assert "validate-secret-value" not in validated.text


def test_validation_checkout_uses_an_injected_source_adapter_identity(tmp_path: Path) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    adapter_identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-alpaca-source-adapter",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    secret_provider = RecordingSecretProvider(clock=lambda: now)
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
        source_adapter_identity=adapter_identity,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-WORKLOAD-SEPARATION",
                "api_secret_key": "workload-separation-secret",
            }
        },
    )

    response = client.post(f"{endpoint}/validations", headers=headers)

    assert response.status_code == 200
    checkout_context = secret_provider.checkout_contexts[-1]
    assert checkout_context.workload_principal_id == adapter_identity.context.principal_id
    assert checkout_context.workload_principal_id != application.security_context.principal_id


@pytest.mark.parametrize(
    ("adapter_policy_installed", "adapter_expired", "expected_reason"),
    [
        (False, False, "action_grant_missing"),
        (True, True, "identity_expired"),
    ],
)
def test_validation_fails_closed_before_checkout_when_adapter_authorization_is_not_current(
    tmp_path: Path,
    adapter_policy_installed: bool,
    adapter_expired: bool,
    expected_reason: str,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    adapter_identity = LocalApiKeyIdentity.issue(
        owner="ticket-07-alpaca-source-adapter",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=2),
        expires_at=(now - timedelta(hours=1) if adapter_expired else now + timedelta(hours=1)),
        data_protection_classes={"licensed", "secret"},
        principal_classification="individual_non_commercial",
    )
    secret_provider = RecordingSecretProvider(clock=lambda: now)
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
        source_adapter_identity=adapter_identity,
        install_source_adapter_policy=adapter_policy_installed,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    configured = client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-ADAPTER-AUTHORIZATION",
                "api_secret_key": "adapter-authorization-secret",
            }
        },
    )
    assert configured.status_code == 200
    secret_provider.checkout_calls.clear()
    trace_id = f"trace-adapter-authorization-{expected_reason}"

    response = client.post(
        f"{endpoint}/validations",
        headers={**headers, "X-Trace-Id": trace_id},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "authorization_denied"
    assert secret_provider.checkout_calls == []
    assert validator.calls == []
    audit = application.state_store.list_audit_events(trace_id=trace_id)
    assert expected_reason in {event["reason_code"] for event in audit}
    assert "adapter-authorization-secret" not in str(audit)


def test_validation_uses_a_disabled_path_when_no_source_adapter_identity_exists(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    secret_provider = RecordingSecretProvider(clock=lambda: now)
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
        source_adapter_enabled=False,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-DISABLED-ADAPTER",
                "api_secret_key": "disabled-adapter-secret",
            }
        },
    )
    secret_provider.checkout_calls.clear()

    response = client.post(
        f"{endpoint}/validations",
        headers={**headers, "X-Trace-Id": "trace-disabled-source-adapter"},
    )

    assert application.source_adapter_security_context is None
    assert application.alpaca_price_adapter is None
    assert response.status_code == 409
    assert response.json()["detail"] == "source_adapter_identity_unavailable"
    assert secret_provider.checkout_calls == []
    assert validator.calls == []
    audit = application.state_store.list_audit_events(trace_id="trace-disabled-source-adapter")
    assert "disabled-adapter-secret" not in str(audit)


def test_credential_validation_rejects_arbitrary_secret_bearing_evidence() -> None:
    with pytest.raises(ValueError, match="source_credential_validation_evidence_invalid"):
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence={"api_secret_key": "must-never-be-persisted"},  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="source_credential_validation_evidence_invalid"):
        CredentialValidationEvidence(
            authentication_status="invented"  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="source_credential_validation_reason_invalid"):
        CredentialValidationResult(
            readiness="valid",
            reason_code="super-secret-value",
        )

    with pytest.raises(ValueError, match="source_contract_assessment_invalid"):
        SourceContractAssessment(contract_id="super-secret-value")

    with pytest.raises(ValueError, match="source_contract_assessment_invalid"):
        SourceContractAssessment(datasets=("super-secret-value",))


@pytest.mark.parametrize(
    ("readiness", "reason_code", "authentication_status", "assessment"),
    [
        (
            "valid",
            "source_credential_authentication_failed",
            "failed",
            None,
        ),
        (
            "valid",
            "source_credential_valid",
            "not_run",
            None,
        ),
        (
            "validation_failed",
            "source_credential_authentication_failed",
            "passed",
            None,
        ),
        (
            "configured",
            "source_credential_validation_inconclusive",
            "not_run",
            None,
        ),
        (
            "expired",
            "source_credential_valid",
            "passed",
            None,
        ),
        (
            "validation_failed",
            "source_credential_fields_invalid",
            "not_run",
            SourceContractAssessment(
                contract_id="alpaca-credential-probe-v1",
                live_validation="failed",
                source_contract_reason_code="source_contract_schema_invalid",
            ),
        ),
    ],
)
def test_credential_validation_result_rejects_incoherent_state_combinations(
    readiness: str,
    reason_code: str,
    authentication_status: str,
    assessment: SourceContractAssessment | None,
) -> None:
    with pytest.raises(ValueError, match="source_credential_validation_result_invalid"):
        CredentialValidationResult(
            readiness=readiness,
            reason_code=reason_code,
            evidence=CredentialValidationEvidence(
                authentication_status=authentication_status,  # type: ignore[arg-type]
            ),
            source_contract_assessment=assessment,
        )


@pytest.mark.parametrize(
    "assessment_fields",
    [
        {
            "contract_id": "alpaca-credential-probe-v1",
            "live_validation": "passed",
            "source_contract_reason_code": "source_contract_unavailable",
        },
        {
            "contract_id": "alpaca-credential-probe-v1",
            "live_validation": "failed",
        },
        {"live_validation": "passed"},
        {"live_validation": "not_run", "ticker_count": 1},
    ],
)
def test_source_contract_assessment_rejects_incoherent_state_combinations(
    assessment_fields: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="source_contract_assessment_invalid"):
        SourceContractAssessment(**assessment_fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "assessment_fields",
    [
        {
            "contract_id": "alpaca-ticket-07-live-v1",
            "live_validation": "passed",
            "ticker_count": 0,
            "pagination_pages": 2,
            "datasets": (
                "alpaca-us-stock-bars-v2",
                "alpaca-us-corporate-actions-v1",
                "alpaca-us-trading-calendar-v2",
            ),
            "symbol_lifecycle_probe": "passed",
        },
        {
            "contract_id": "alpaca-ticket-07-live-v1",
            "live_validation": "passed",
            "ticker_count": 10,
            "pagination_pages": 1,
            "datasets": (
                "alpaca-us-stock-bars-v2",
                "alpaca-us-corporate-actions-v1",
                "alpaca-us-trading-calendar-v2",
            ),
            "symbol_lifecycle_probe": "passed",
        },
        {
            "contract_id": "alpaca-credential-probe-v1",
            "live_validation": "passed",
            "ticker_count": 1,
            "datasets": (),
        },
    ],
)
def test_passed_source_contract_assessment_requires_contract_specific_evidence(
    assessment_fields: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="source_contract_assessment_invalid"):
        SourceContractAssessment(**assessment_fields)  # type: ignore[arg-type]


def test_passed_live_source_contract_assessment_requires_versioned_universe_identity() -> None:
    with pytest.raises(ValueError, match="source_contract_assessment_invalid"):
        SourceContractAssessment(
            contract_id="alpaca-ticket-07-live-v1",
            live_validation="passed",
            ticker_count=10,
            pagination_pages=2,
            datasets=(
                "alpaca-us-stock-bars-v2",
                "alpaca-us-corporate-actions-v1",
                "alpaca-us-trading-calendar-v2",
            ),
            symbol_lifecycle_probe="passed",
        )


@pytest.mark.parametrize(
    "listing_id",
    [
        "not-a-uuid",
        "70000000000040008000000000000001",
        "{70000000-0000-4000-8000-000000000001}",
    ],
)
def test_source_contract_assessment_rejects_non_canonical_uuid_listing_evidence(
    listing_id: str,
) -> None:
    with pytest.raises(ValueError, match="source_contract_assessment_invalid"):
        SourceContractAssessment(
            contract_id="alpaca-ticket-07-live-v1",
            live_validation="failed",
            listing_ids=(listing_id,),
            source_contract_reason_code="source_contract_schema_invalid",
        )


def test_operations_rejects_validator_output_that_echoes_a_credential_value(
    tmp_path: Path,
) -> None:
    secret_value = "alpaca-ticket-07-live-v1"
    manifest = load_us_stock_pool_manifest()
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
            source_contract_assessment=SourceContractAssessment(
                contract_id=secret_value,
                live_validation="passed",
                ticker_count=10,
                pagination_pages=2,
                datasets=(
                    "alpaca-us-stock-bars-v2",
                    "alpaca-us-corporate-actions-v1",
                    "alpaca-us-trading-calendar-v2",
                ),
                symbol_lifecycle_probe="passed",
                universe_manifest_id=manifest.manifest_id,
                reference_graph_version_id=manifest.selection_evidence_version,
                listing_ids=tuple(listing.listing_id for listing in manifest.listings),
            ),
        )
    )
    _, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-SECRET-ECHO",
                "api_secret_key": secret_value,
            }
        },
    )

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )

    assert validated.status_code == 200
    assert validated.json()["credential"]["readiness"] == "validation_failed"
    assert validated.json()["credential"]["reason_code"] == (
        "source_credential_validator_output_rejected"
    )
    assert "validation_evidence" not in validated.json()["credential"]
    assert validated.json()["source_contract_assessment"] is None
    assert secret_value not in validated.text


def test_operations_sanitizes_a_secret_bearing_validator_exception(tmp_path: Path) -> None:
    validator = SecretBearingExceptionValidator()
    _, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    secret_value = "exception-secret-canary"
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-EXCEPTION-CANARY",
                "api_secret_key": secret_value,
            }
        },
    )

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )

    assert validated.status_code == 200
    assert validated.json()["credential"]["readiness"] == "validation_failed"
    assert validated.json()["credential"]["reason_code"] == (
        "source_credential_validator_output_rejected"
    )
    assert "validation_evidence" not in validated.json()["credential"]
    assert validated.json()["source_contract_assessment"] is None
    assert "PK-EXCEPTION-CANARY" not in validated.text
    assert secret_value not in validated.text


@pytest.mark.parametrize("validator_raises", [False, True])
def test_validation_releases_its_secret_lease_immediately(
    tmp_path: Path,
    validator_raises: bool,
) -> None:
    secret_provider = RecordingSecretProvider()
    validator: SourceCredentialValidator = (
        SecretBearingExceptionValidator()
        if validator_raises
        else LiteralCredentialValidator(
            CredentialValidationResult(
                readiness="valid",
                reason_code="source_credential_valid",
                evidence=CredentialValidationEvidence(authentication_status="passed"),
            )
        )
    )
    _, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    configured = client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-RELEASE-AFTER-VALIDATION",
                "api_secret_key": "release-after-validation-secret",
            }
        },
    )
    assert configured.status_code == 200

    validated = client.post(f"{endpoint}/validations", headers=headers)

    assert validated.status_code == 200
    lease = secret_provider.leases[-1]
    assert lease.revoked is True
    with pytest.raises(SecretUnavailableError, match="source_credential_lease_revoked"):
        lease.credential_fields()


def test_only_a_valid_managed_credential_can_be_resolved_for_provider_use(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )

    with pytest.raises(CredentialNotReady, match="source_credential_missing"):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-direct-resolver",
            request_id="request-direct-resolver",
            work_id="work-direct-resolver",
            source_id="alpaca-us-stock-bars",
        )

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
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-direct-resolver",
            request_id="request-direct-resolver",
            work_id="work-direct-resolver",
            source_id="alpaca-us-stock-bars",
        )

    client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-resolver-validate"},
    )
    lease = resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-direct-resolver",
        request_id="request-direct-resolver",
        work_id="work-direct-resolver",
        source_id="alpaca-us-stock-bars",
    )
    assert lease.credential_fields() == {
        "api_key_id": "PK-RESOLVER",
        "api_secret_key": "resolver-secret-value",
    }
    assert lease.credential_version == 2
    assert lease.purpose == "price_research_ingest"
    assert lease.expires_at == datetime(2026, 8, 15, 8, 5, tzinfo=UTC)
    checkout_audit = application.state_store.list_audit_events(trace_id="trace-direct-resolver")
    assert [event["action"] for event in checkout_audit] == [
        "source_credential.lease_pin",
        "source_credential.checkout",
        "source_credential.checkout",
    ]
    assert [event["outcome"] for event in checkout_audit] == [
        "allowed",
        "attempted",
        "succeeded",
    ]
    assert checkout_audit[0]["reason_code"] == "source_credential_lease_pinned"
    assert checkout_audit[1]["reason_code"] == "source_credential_checkout_attempted"
    assert checkout_audit[2]["reason_code"] == "source_credential_checkout_succeeded"
    assert all(event["provider_id"] == "alpaca-market-data-basic" for event in checkout_audit)
    assert all(event["credential_version"] == 2 for event in checkout_audit)
    assert "resolver-secret-value" not in str(checkout_audit)

    client.delete(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers={**headers, "X-Trace-Id": "trace-p2-credential-resolver-revoke"},
    )
    with pytest.raises(CredentialNotReady, match="source_credential_revoked"):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-direct-resolver-after-revoke",
            request_id="request-direct-resolver-after-revoke",
            work_id="work-direct-resolver-after-revoke",
            source_id="alpaca-us-stock-bars",
        )


@pytest.mark.parametrize(
    ("terminal_transition", "expected_reason"),
    [
        ("revoke", "source_credential_revoked"),
        ("validation_failed", "source_credential_authentication_failed"),
    ],
)
def test_terminal_credential_state_revokes_an_existing_work_lease(
    tmp_path: Path,
    terminal_transition: str,
    expected_reason: str,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-TERMINAL-LEASE",
                "api_secret_key": "terminal-lease-secret",
            }
        },
    )
    client.post(f"{endpoint}/validations", headers=headers)
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )
    resolve_arguments = {
        "trace_id": "trace-terminal-work-lease",
        "request_id": "request-terminal-work-lease",
        "work_id": "work-terminal-work-lease",
        "source_id": "alpaca-us-stock-bars",
    }
    lease = resolver.resolve_valid("alpaca-market-data-basic", **resolve_arguments)
    assert lease.active is True

    if terminal_transition == "revoke":
        client.delete(endpoint, headers=headers)
    else:
        validator.result = CredentialValidationResult(
            readiness="validation_failed",
            reason_code="source_credential_authentication_failed",
            evidence=CredentialValidationEvidence(authentication_status="failed"),
        )
        client.post(f"{endpoint}/validations", headers=headers)

    with pytest.raises(CredentialNotReady, match=expected_reason):
        resolver.resolve_valid("alpaca-market-data-basic", **resolve_arguments)
    assert lease.revoked is True
    with pytest.raises(SecretUnavailableError, match="source_credential_lease_revoked"):
        lease.credential_fields()


def test_secret_checkout_audit_failure_prevents_secret_egress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_provider = RecordingSecretProvider()
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-AUDIT-FAIL-CLOSED",
                "api_secret_key": "audit-fail-closed-secret",
            }
        },
    )
    client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )
    secret_provider.checkout_calls.clear()
    monkeypatch.setattr(
        application.state_store,
        "record_security_event",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("audit_store_unavailable")),
    )

    with pytest.raises(OSError, match="audit_store_unavailable"):
        ManagedSourceCredentialResolver(
            application.state_store,
            secret_provider,
            workload_principal_id="workload:alpaca-source-adapter",
            environment="development",
            clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
        ).resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-ticket-07-checkout-audit-failure",
            request_id="request-ticket-07-checkout-audit-failure",
            work_id="work-ticket-07-checkout-audit-failure",
            source_id="alpaca-us-stock-bars",
        )

    assert secret_provider.checkout_calls == []


def test_one_work_id_stays_pinned_to_one_credential_version_after_rotation(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    first = client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-PINNED-FIRST",
                "api_secret_key": "pinned-first-secret",
            }
        },
    ).json()
    client.post(f"{endpoint}/validations", headers=headers)
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )
    first_lease = resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-pinned-first",
        request_id="request-pinned",
        work_id="work-pinned",
        source_id="alpaca-us-stock-bars",
    )
    assert first_lease.secret_ref_id == first["secret_ref_id"]

    second = client.post(
        f"{endpoint}/rotations",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-PINNED-SECOND",
                "api_secret_key": "pinned-second-secret",
            }
        },
    ).json()
    client.post(f"{endpoint}/validations", headers=headers)

    pinned_retry = resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-pinned-retry",
        request_id="request-pinned",
        work_id="work-pinned",
        source_id="alpaca-us-stock-bars",
    )
    assert pinned_retry is first_lease
    assert pinned_retry.credential_fields()["api_key_id"] == "PK-PINNED-FIRST"
    new_work_lease = resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-pinned-new-work",
        request_id="request-pinned-new-work",
        work_id="work-pinned-new-work",
        source_id="alpaca-us-stock-bars",
    )
    assert new_work_lease.secret_ref_id == second["secret_ref_id"]
    assert new_work_lease.credential_fields()["api_key_id"] == "PK-PINNED-SECOND"


def test_concurrent_resolution_returns_one_lease_for_one_work_id(tmp_path: Path) -> None:
    secret_provider = ConcurrentCheckoutSecretProvider()
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-CONCURRENT-LEASE",
                "api_secret_key": "concurrent-lease-secret",
            }
        },
    )
    client.post(f"{endpoint}/validations", headers=headers)
    resolve_arguments = {
        "trace_id": "trace-concurrent-work-lease",
        "request_id": "request-concurrent-work-lease",
        "work_id": "work-concurrent-work-lease",
        "source_id": "alpaca-us-stock-bars",
    }
    ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    ).resolve_valid("alpaca-market-data-basic", **resolve_arguments)
    secret_provider.checkout_calls.clear()
    secret_provider.leases.clear()
    secret_provider.synchronize_checkout = True
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                resolver.resolve_valid,
                "alpaca-market-data-basic",
                **resolve_arguments,
            )
            for _ in range(2)
        ]
        leases = [future.result() for future in futures]

    assert leases[0] is leases[1]
    assert secret_provider.concurrent_checkout_attempts == 1
    assert len(secret_provider.checkout_calls) == 1
    assert len(secret_provider.leases) == 1


def test_one_work_id_cannot_renew_its_pinned_lease_window(tmp_path: Path) -> None:
    wall_time = [datetime(2026, 8, 15, 8, 0, tzinfo=UTC)]
    monotonic_time = [100.0]
    secret_provider = RecordingSecretProvider(
        clock=lambda: wall_time[0],
        monotonic_clock=lambda: monotonic_time[0],
    )
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-NONRENEWABLE-LEASE",
                "api_secret_key": "nonrenewable-lease-secret",
            }
        },
    )
    client.post(f"{endpoint}/validations", headers=headers)
    secret_provider.checkout_calls.clear()
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: wall_time[0],
    )
    resolve_arguments = {
        "trace_id": "trace-nonrenewable-lease",
        "request_id": "request-nonrenewable-lease",
        "work_id": "work-nonrenewable-lease",
        "source_id": "alpaca-us-stock-bars",
    }

    first = resolver.resolve_valid("alpaca-market-data-basic", **resolve_arguments)
    repeated = resolver.resolve_valid("alpaca-market-data-basic", **resolve_arguments)
    assert repeated is first
    assert first.issued_at == datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    assert first.expires_at == datetime(2026, 8, 15, 8, 5, tzinfo=UTC)
    assert len(secret_provider.checkout_calls) == 1

    wall_time[0] += timedelta(minutes=4)
    monotonic_time[0] += 240
    restarted_resolver = ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: wall_time[0],
    )
    restarted = restarted_resolver.resolve_valid("alpaca-market-data-basic", **resolve_arguments)
    assert restarted.issued_at == datetime(2026, 8, 15, 8, 4, tzinfo=UTC)
    assert restarted.expires_at == datetime(2026, 8, 15, 8, 5, tzinfo=UTC)

    monotonic_time[0] += 60
    with pytest.raises(KeyError, match="source_credential_lease_expired"):
        restarted.credential_fields()


def test_resolver_revokes_expired_and_capacity_evicted_plaintext_leases(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    monotonic_time = [100.0]
    secret_provider = RecordingSecretProvider(
        clock=lambda: now,
        monotonic_clock=lambda: monotonic_time[0],
    )
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-BOUNDED-LEASE-CACHE",
                "api_secret_key": "bounded-lease-cache-secret",
            }
        },
    )
    client.post(f"{endpoint}/validations", headers=headers)
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: now,
    )

    expired = resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-expiring-cache-lease",
        request_id="request-expiring-cache-lease",
        work_id="work-expiring-cache-lease",
        source_id="alpaca-us-stock-bars",
    )
    monotonic_time[0] += 301
    resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-prune-expired-cache-lease",
        request_id="request-prune-expired-cache-lease",
        work_id="work-prune-expired-cache-lease",
        source_id="alpaca-us-stock-bars",
    )
    assert expired.revoked is True

    bounded_first = resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-bounded-cache-lease-0",
        request_id="request-bounded-cache-lease-0",
        work_id="work-bounded-cache-lease-0",
        source_id="alpaca-us-stock-bars",
    )
    for index in range(1, 65):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id=f"trace-bounded-cache-lease-{index}",
            request_id=f"request-bounded-cache-lease-{index}",
            work_id=f"work-bounded-cache-lease-{index}",
            source_id="alpaca-us-stock-bars",
        )
    assert bounded_first.revoked is True


def test_secret_lease_expiry_clears_plaintext_without_another_resolver_call(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)
    expiry_scheduler = ManualLeaseExpiryScheduler()
    secret_provider = RecordingSecretProvider(
        clock=lambda: now,
        expiry_scheduler=expiry_scheduler,
    )
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-IDLE-LEASE",
                "api_secret_key": "idle-lease-secret",
            }
        },
    )
    client.post(f"{endpoint}/validations", headers=headers)
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: now,
    )
    lease = resolver.resolve_valid(
        "alpaca-market-data-basic",
        trace_id="trace-idle-lease-expiry",
        request_id="request-idle-lease-expiry",
        work_id="work-idle-lease-expiry",
        source_id="alpaca-us-stock-bars",
    )
    assert lease.credential_fields()["api_secret_key"] == "idle-lease-secret"
    assert {delay for delay, _ in expiry_scheduler.scheduled} == {300.0}

    expiry_scheduler.fire_all()

    assert lease.revoked is True
    with pytest.raises(SecretUnavailableError, match="source_credential_lease_revoked"):
        lease.credential_fields()


def test_each_failed_secret_checkout_has_a_distinct_terminal_audit(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    configured = client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-MISSING-CHECKOUT",
                "api_secret_key": "missing-checkout-secret",
            }
        },
    ).json()
    client.post(f"{endpoint}/validations", headers=headers)
    application.secret_provider.revoke(SecretRef(configured["secret_ref_id"]))
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )

    for _ in range(2):
        with pytest.raises(CredentialNotReady, match="source_credential_secret_unavailable"):
            resolver.resolve_valid(
                "alpaca-market-data-basic",
                trace_id="trace-repeated-checkout-failure",
                request_id="request-repeated-checkout-failure",
                work_id="work-repeated-checkout-failure",
                source_id="alpaca-us-stock-bars",
            )

    events = application.state_store.list_audit_events(trace_id="trace-repeated-checkout-failure")
    checkout_events = [event for event in events if event["action"] == "source_credential.checkout"]
    assert [event["outcome"] for event in checkout_events] == [
        "attempted",
        "failed",
        "attempted",
        "failed",
    ]
    assert all(
        event["reason_code"]
        in {"source_credential_checkout_attempted", "source_credential_secret_unavailable"}
        for event in checkout_events
    )


def test_successful_checkout_is_not_returned_when_terminal_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_provider = RecordingSecretProvider()
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-TERMINAL-AUDIT",
                "api_secret_key": "terminal-audit-secret",
            }
        },
    )
    client.post(f"{endpoint}/validations", headers=headers)
    secret_provider.checkout_calls.clear()
    original_record = application.state_store.record_security_event

    def fail_success_terminal(**kwargs: object) -> None:
        if kwargs["outcome"] == "succeeded":
            raise OSError("terminal_audit_store_unavailable")
        original_record(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(application.state_store, "record_security_event", fail_success_terminal)
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )

    with pytest.raises(OSError, match="terminal_audit_store_unavailable"):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-terminal-audit-failure",
            request_id="request-terminal-audit-failure",
            work_id="work-terminal-audit-failure",
            source_id="alpaca-us-stock-bars",
        )

    assert len(secret_provider.checkout_calls) == 1


@pytest.mark.parametrize(
    ("provider_error", "public_reason", "audit_reason"),
    [
        (
            OSError("provider exception contained SHOULD-NOT-BE-AUDITED"),
            "source_credential_secret_unavailable",
            "source_credential_checkout_failed",
        ),
        (
            SecretUnavailableError("SHOULD-NOT-BE-AUDITED"),
            "source_credential_secret_unavailable",
            "source_credential_secret_unavailable",
        ),
        (
            SecretCorruptError("SHOULD-NOT-BE-AUDITED"),
            "source_credential_secret_corrupt",
            "source_credential_secret_corrupt",
        ),
        (
            SecretUnavailableError({"api_secret_key": "unexpected-checkout-secret"}),
            "source_credential_secret_unavailable",
            "source_credential_secret_unavailable",
        ),
        (
            SecretCorruptError(["unexpected-checkout-secret"]),
            "source_credential_secret_corrupt",
            "source_credential_secret_corrupt",
        ),
    ],
)
def test_unexpected_secret_provider_failure_has_a_redacted_terminal_audit(
    tmp_path: Path,
    provider_error: Exception,
    public_reason: str,
    audit_reason: str,
) -> None:
    secret_provider = RecordingSecretProvider()
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    client.put(
        endpoint,
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-UNEXPECTED-CHECKOUT",
                "api_secret_key": "unexpected-checkout-secret",
            }
        },
    )
    client.post(f"{endpoint}/validations", headers=headers)
    secret_provider.checkout_exception = provider_error
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )

    with pytest.raises(CredentialNotReady, match=public_reason) as raised:
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-unexpected-checkout-failure",
            request_id="request-unexpected-checkout-failure",
            work_id="work-unexpected-checkout-failure",
            source_id="alpaca-us-stock-bars",
        )

    assert "SHOULD-NOT-BE-AUDITED" not in str(raised.value)
    assert "unexpected-checkout-secret" not in str(raised.value)
    exception_tree: list[BaseException] = []
    pending: list[BaseException] = [raised.value]
    while pending:
        current = pending.pop()
        exception_tree.append(current)
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if linked is not None and linked not in exception_tree
        )
    assert "SHOULD-NOT-BE-AUDITED" not in repr(exception_tree)
    assert "unexpected-checkout-secret" not in repr(exception_tree)

    events = application.state_store.list_audit_events(trace_id="trace-unexpected-checkout-failure")
    checkout_events = [event for event in events if event["action"] == "source_credential.checkout"]
    assert [event["outcome"] for event in checkout_events] == ["attempted", "failed"]
    assert checkout_events[-1]["reason_code"] == audit_reason
    assert "SHOULD-NOT-BE-AUDITED" not in str(events)
    assert "unexpected-checkout-secret" not in str(events)


def test_rest_managed_credential_is_consumed_by_the_application_us_adapter(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
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
        SecretRef(reapplied.json()["secret_ref_id"]),
        _direct_secret_context(credential_version=reapplied.json()["version"]),
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
            evidence=CredentialValidationEvidence(authentication_status="failed"),
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
    assert validated.json()["credential"]["readiness"] == "validation_failed"
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
    )
    with pytest.raises(CredentialNotReady, match="source_credential_authentication_failed"):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-direct-resolver",
            request_id="request-direct-resolver-invalid",
            work_id="work-direct-resolver-invalid",
            source_id="alpaca-us-stock-bars",
        )


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


def test_operations_page_bounds_browser_sessions_and_evicts_the_oldest(
    tmp_path: Path,
) -> None:
    _, client, headers = _credential_application(tmp_path)
    first_page = client.get("/operations/source-credentials", headers=headers)
    first_csrf = re.search(r'<meta name="csrf-token" content="([^"]+)">', first_page.text)
    assert first_csrf is not None
    session_cookie_name = "stock_forecasting_operations_session"
    first_session_id = first_page.cookies.get(session_cookie_name)
    assert first_session_id is not None

    latest_page = first_page
    for _ in range(256):
        latest_page = client.get("/operations/source-credentials", headers=headers)
    latest_csrf = re.search(r'<meta name="csrf-token" content="([^"]+)">', latest_page.text)
    assert latest_csrf is not None

    evicted_client = TestClient(client.app, client=("127.0.0.1", 50001))
    evicted_client.cookies.set(session_cookie_name, first_session_id)
    endpoint = "/api/v1/operations/source-credentials/alpaca-market-data-basic"
    evicted = evicted_client.put(
        endpoint,
        headers={"X-CSRF-Token": first_csrf.group(1)},
        json={
            "credential_fields": {
                "api_key_id": "PK-EVICTED",
                "api_secret_key": "evicted-secret",
            }
        },
    )
    newest = client.put(
        endpoint,
        headers={"X-CSRF-Token": latest_csrf.group(1)},
        json={
            "credential_fields": {
                "api_key_id": "PK-NEWEST",
                "api_secret_key": "newest-secret",
            }
        },
    )

    assert evicted.status_code == 401
    assert newest.status_code == 200


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
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
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
    assert validated.json()["credential"]["readiness"] == "expired"
    assert validated.json()["credential"]["reason_code"] == "source_credential_expired"
    assert validator.calls == []
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
    )
    with pytest.raises(CredentialNotReady, match="source_credential_expired"):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-direct-resolver",
            request_id="request-direct-resolver-expired",
            work_id="work-direct-resolver-expired",
            source_id="alpaca-us-stock-bars",
        )


def test_a_previously_valid_credential_fails_closed_after_its_expiry(tmp_path: Path) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
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
    assert validated.json()["credential"]["readiness"] == "valid"

    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        clock=lambda: datetime(2026, 8, 15, 8, 2, tzinfo=UTC),
    )

    with pytest.raises(CredentialNotReady, match="source_credential_expired"):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-direct-resolver",
            request_id="request-direct-resolver-expired-after-validation",
            work_id="work-direct-resolver-expired-after-validation",
            source_id="alpaca-us-stock-bars",
        )


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
        source_adapter_security_context=application.source_adapter_security_context,
        source_adapter_authorization_policy=lambda: (
            build_us_zero_fee_engineering_authorization_policy(
                application.source_adapter_security_context
            )
            if application.source_adapter_security_context is not None
            else AuthorizationPolicy((), (), ())
        ),
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
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
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
    application.secret_provider.revoke(SecretRef(configured["secret_ref_id"]))

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )

    assert validated.status_code == 200
    assert validated.json()["credential"]["readiness"] == "validation_failed"
    assert validated.json()["credential"]["reason_code"] == ("source_credential_secret_unavailable")
    assert validator.calls == []


def test_validation_projects_a_corrupt_encrypted_secret_as_fail_closed(
    tmp_path: Path,
) -> None:
    validator = LiteralCredentialValidator(
        CredentialValidationResult(
            readiness="valid",
            reason_code="source_credential_valid",
            evidence=CredentialValidationEvidence(authentication_status="passed"),
        )
    )
    secret_root = tmp_path / "encrypted-source-secrets"
    secret_provider = EncryptedFilesystemSecretProvider(
        secret_root,
        clock=lambda: datetime(2026, 8, 15, 8, 0, tzinfo=UTC),
    )
    application, client, headers = _credential_application(
        tmp_path,
        credential_validator=validator,
        secret_provider=secret_provider,
    )
    client.put(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic",
        headers=headers,
        json={
            "credential_fields": {
                "api_key_id": "PK-CORRUPT-LEASE",
                "api_secret_key": "corrupt-lease-secret",
            }
        },
    )
    initially_valid = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )
    assert initially_valid.json()["credential"]["readiness"] == "valid"
    secret_file = next(secret_root.glob("*.secret"))
    secret_file.write_bytes(b"not-a-valid-encrypted-secret")
    transport = SequenceProviderTransport([])
    adapter = application.build_alpaca_price_adapter(
        transport=transport,
        source_access_mode="engineering_double",
    )

    with pytest.raises(SourceCredentialRequired, match="source_credential_secret_corrupt"):
        adapter.load(
            SourcePartitionRequest(
                request_id="request-corrupt-runtime-credential",
                trace_id="trace-corrupt-runtime-credential",
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
    assert transport.requests == []

    validated = client.post(
        "/api/v1/operations/source-credentials/alpaca-market-data-basic/validations",
        headers=headers,
    )

    assert validated.status_code == 200
    assert validated.json()["credential"]["readiness"] == "validation_failed"
    assert validated.json()["credential"]["reason_code"] == ("source_credential_secret_corrupt")
    assert len(validator.calls) == 1
    resolver = ManagedSourceCredentialResolver(
        application.state_store,
        application.secret_provider,
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
    )
    with pytest.raises(CredentialNotReady, match="source_credential_secret_corrupt"):
        resolver.resolve_valid(
            "alpaca-market-data-basic",
            trace_id="trace-direct-resolver",
            request_id="request-direct-resolver-corrupt",
            work_id="work-direct-resolver-corrupt",
            source_id="alpaca-us-stock-bars",
        )


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
