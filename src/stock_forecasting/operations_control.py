from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from stock_forecasting.authorization import (
    AuthorizationAction,
    AuthorizationDecision,
    AuthorizationPolicy,
    OperationIntent,
    PolicyDeniedOutcome,
    SecurityContext,
    authorization_audit_payload,
)
from stock_forecasting.data_supply import PRICE_RESEARCH_REQUIRED_USES
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.source_credentials import (
    AuditedSecretCheckout,
    AuditedSecretWrite,
    CredentialValidationEvidence,
    SecretCorruptError,
    SecretProvider,
    SecretRef,
    SecretUnavailableError,
    SecretUseContext,
    SourceContractAssessment,
    SourceCredentialValidator,
    pin_source_credential_lease,
    project_source_credential_readiness,
)
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest

_ALPACA_SOURCE_BASIS = load_us_stock_pool_manifest().source_basis.as_payload()
_SOURCE_CREDENTIAL_PROVIDERS: tuple[dict[str, object], ...] = (
    {
        "provider_id": "alpaca-market-data-basic",
        "display_name": "Alpaca Market Data Basic",
        "credential_kind": "api_key_pair",
        "source_basis": _ALPACA_SOURCE_BASIS,
        "required_uses": sorted(PRICE_RESEARCH_REQUIRED_USES),
        "required_fields": ["api_key_id", "api_secret_key"],
        "registration_url": "https://app.alpaca.markets/signup",
        "key_management_url": "https://app.alpaca.markets/paper/dashboard/overview",
    },
)
_SOURCE_CREDENTIAL_VALIDATION_TARGETS: dict[
    str,
    tuple[tuple[str, str, str], ...],
] = {
    "alpaca-market-data-basic": (
        (
            "alpaca-us-stock-bars",
            "alpaca-us-stock-bars-v2",
            "https://data.alpaca.markets/v2/stocks/bars",
        ),
        (
            "alpaca-us-corporate-actions-v1",
            "alpaca-us-corporate-actions-v1",
            "https://data.alpaca.markets/v1/corporate-actions",
        ),
        (
            "alpaca-us-trading-calendar-v2",
            "alpaca-us-trading-calendar-v2",
            "https://paper-api.alpaca.markets/v2/calendar",
        ),
    ),
}


class OperationsControl:
    def __init__(
        self,
        state_store: StateStore,
        *,
        authorization_policy: AuthorizationPolicy,
        secret_provider: SecretProvider,
        source_credential_validators: Mapping[str, SourceCredentialValidator],
        clock: Callable[[], datetime],
        source_adapter_security_context: SecurityContext | None,
        source_adapter_authorization_policy: Callable[[], AuthorizationPolicy] | None,
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._secret_provider = secret_provider
        self._secret_checkout = AuditedSecretCheckout(state_store, secret_provider)
        self._secret_write = AuditedSecretWrite(state_store, secret_provider)
        self._source_credential_validators = source_credential_validators
        self._clock = clock
        self._source_adapter_security_context = source_adapter_security_context
        self._source_adapter_authorization_policy = source_adapter_authorization_policy

    def list_source_credentials(
        self,
        *,
        trace_id: str,
        security_context: SecurityContext,
    ) -> list[dict[str, object]] | PolicyDeniedOutcome:
        decision = self._authorize(
            action="source_credential.read",
            trace_id=trace_id,
            security_context=security_context,
        )
        self._state_store.record_authorization_decision(
            authorization=authorization_audit_payload(decision),
            outcome="allowed" if decision.allowed else "denied",
            trace_id=trace_id,
        )
        if not decision.allowed:
            return PolicyDeniedOutcome.from_decision(decision)
        results: list[dict[str, object]] = []
        for provider in _SOURCE_CREDENTIAL_PROVIDERS:
            provider_id = str(provider["provider_id"])
            readiness = self._state_store.get_source_credential(provider_id=provider_id)
            result = {
                **provider,
                **(
                    project_source_credential_readiness(
                        readiness,
                        evaluated_at=self._clock(),
                    )
                ),
            }
            if self._state_store.list_pending_source_secret_cleanup(provider_id=provider_id):
                result["secret_cleanup_pending"] = True
            results.append(result)
        return results

    def set_source_credential(
        self,
        *,
        provider_id: str,
        credential_fields: dict[str, str],
        expires_at: str | None = None,
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        decision = self._authorize(
            action="source_credential.manage",
            trace_id=trace_id,
            security_context=security_context,
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        self._state_store.record_authorization_decision(
            authorization=authorization,
            outcome="allowed",
            trace_id=trace_id,
        )
        provider = self._provider(provider_id)
        self._drain_secret_cleanup(provider_id=provider_id)
        self._validate_fields(provider, credential_fields)
        canonical_expires_at = self._canonical_expiry(expires_at)
        evaluated_at = self._clock()
        secret_ref = self._secret_write.put(
            provider_id=provider_id,
            credential_fields=credential_fields,
            operation="set",
            trace_id=trace_id,
            authorization=authorization,
            occurred_at=evaluated_at,
        )
        try:
            outcome = self._state_store.publish_source_credential(
                provider_id=provider_id,
                secret_ref_id=secret_ref.secret_ref_id,
                readiness="configured",
                reason_code="source_credential_not_validated",
                configured_at=self._instant(evaluated_at),
                expires_at=canonical_expires_at,
                authorization=authorization,
                trace_id=trace_id,
            )
        except Exception:
            try:
                self._secret_provider.revoke(secret_ref)
            except Exception:
                self._state_store.queue_source_secret_cleanup(
                    secret_ref_id=secret_ref.secret_ref_id,
                    provider_id=provider_id,
                    queued_at=self._instant(evaluated_at),
                )
            raise
        if self._state_store.list_pending_source_secret_cleanup(provider_id=provider_id):
            outcome["secret_cleanup_pending"] = True
        return outcome

    def rotate_source_credential(
        self,
        *,
        provider_id: str,
        credential_fields: dict[str, str],
        expires_at: str | None = None,
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        decision = self._authorize(
            action="source_credential.manage",
            trace_id=trace_id,
            security_context=security_context,
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        self._state_store.record_authorization_decision(
            authorization=authorization,
            outcome="allowed",
            trace_id=trace_id,
        )
        provider = self._provider(provider_id)
        self._drain_secret_cleanup(provider_id=provider_id)
        self._validate_fields(provider, credential_fields)
        canonical_expires_at = self._canonical_expiry(expires_at)
        evaluated_at = self._clock()
        secret_ref = self._secret_write.put(
            provider_id=provider_id,
            credential_fields=credential_fields,
            operation="rotate",
            trace_id=trace_id,
            authorization=authorization,
            occurred_at=evaluated_at,
        )
        try:
            outcome, _ = self._state_store.rotate_source_credential(
                provider_id=provider_id,
                secret_ref_id=secret_ref.secret_ref_id,
                readiness="configured",
                reason_code="source_credential_not_validated",
                configured_at=self._instant(evaluated_at),
                expires_at=canonical_expires_at,
                authorization=authorization,
                trace_id=trace_id,
            )
        except Exception:
            try:
                self._secret_provider.revoke(secret_ref)
            except Exception:
                self._state_store.queue_source_secret_cleanup(
                    secret_ref_id=secret_ref.secret_ref_id,
                    provider_id=provider_id,
                    queued_at=self._instant(evaluated_at),
                )
            raise
        if self._drain_secret_cleanup(provider_id=provider_id):
            outcome["secret_cleanup_pending"] = True
        return outcome

    def revoke_source_credential(
        self,
        *,
        provider_id: str,
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        decision = self._authorize(
            action="source_credential.manage",
            trace_id=trace_id,
            security_context=security_context,
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        self._provider(provider_id)
        self._drain_secret_cleanup(provider_id=provider_id)
        evaluated_at = self._clock()
        current = self._state_store.get_source_credential(provider_id=provider_id)
        if current is None or current["readiness"] == "revoked":
            raise ValueError("source_credential_not_configured")
        outcome = self._state_store.revoke_source_credential(
            provider_id=provider_id,
            revoked_at=self._instant(evaluated_at),
            authorization=authorization,
            trace_id=trace_id,
        )
        if self._drain_secret_cleanup(provider_id=provider_id):
            outcome["secret_cleanup_pending"] = True
        return outcome

    def validate_source_credential(
        self,
        *,
        provider_id: str,
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        decision = self._authorize(
            action="source_credential.manage",
            trace_id=trace_id,
            security_context=security_context,
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        self._provider(provider_id)
        self._drain_secret_cleanup(provider_id=provider_id)
        current = self._state_store.get_source_credential(provider_id=provider_id)
        if current is None or current["readiness"] == "revoked":
            raise ValueError("source_credential_not_configured")
        validator = self._source_credential_validators.get(provider_id)
        if validator is None:
            raise ValueError("source_credential_validator_unavailable")
        evaluated_at = self._clock()
        expires_at = current.get("expires_at")
        validation_evidence: CredentialValidationEvidence | None
        source_contract_assessment: SourceContractAssessment | None = None
        expected_version = current["version"]
        expected_secret_ref_id = str(current["secret_ref_id"])
        if (
            isinstance(expires_at, str)
            and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= evaluated_at
        ):
            validation_readiness = "expired"
            validation_reason = "source_credential_expired"
            validation_evidence = CredentialValidationEvidence(
                authentication_status="not_run",
            )
        else:
            if (
                self._source_adapter_security_context is None
                or self._source_adapter_authorization_policy is None
            ):
                self._state_store.record_authorization_decision(
                    authorization=authorization,
                    outcome="allowed",
                    trace_id=trace_id,
                )
                raise ValueError("source_adapter_identity_unavailable")
            adapter_denial = self._authorize_source_adapter_validation(
                provider_id=provider_id,
                validator=validator,
                trace_id=trace_id,
                evaluated_at=evaluated_at,
            )
            if adapter_denial is not None:
                self._state_store.record_authorization_decision(
                    authorization=authorization,
                    outcome="allowed",
                    trace_id=trace_id,
                )
                return adapter_denial
            pinned = pin_source_credential_lease(
                self._state_store,
                provider_id=provider_id,
                current=current,
                trace_id=trace_id,
                workload_principal_id=self._source_adapter_security_context.principal_id,
                environment=self._source_adapter_security_context.environment,
                source_id=provider_id,
                destination=provider_id,
                purpose="source_credential_validation",
                request_id=trace_id,
                work_id=trace_id,
                lease_duration=timedelta(minutes=5),
                lease_issued_at=evaluated_at,
            )
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
            expected_version = credential_version
            expected_secret_ref_id = secret_ref_id
            use_context = SecretUseContext(
                workload_principal_id=self._source_adapter_security_context.principal_id,
                environment=self._source_adapter_security_context.environment,
                source_id=provider_id,
                destination=provider_id,
                purpose="source_credential_validation",
                request_id=trace_id,
                work_id=trace_id,
                credential_version=credential_version,
                lease_duration=timedelta(minutes=5),
                lease_not_before=datetime.fromisoformat(lease_not_before.replace("Z", "+00:00")),
                lease_expires_at=datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00")),
            )
            try:
                lease = self._secret_checkout.checkout(
                    secret_ref_id=secret_ref_id,
                    trace_id=trace_id,
                    use_context=use_context,
                )
            except (SecretUnavailableError, SecretCorruptError) as error:
                validation_readiness = "validation_failed"
                validation_reason = str(error.args[0])
                validation_evidence = CredentialValidationEvidence(
                    authentication_status="not_run",
                )
            else:
                credential_fields = lease.credential_fields()
                try:
                    validation = validator.validate(credential_fields)
                    serialized_validation = json.dumps(
                        {
                            "reason_code": validation.reason_code,
                            "evidence": validation.evidence.as_payload(),
                            "source_contract_assessment": (
                                validation.source_contract_assessment.as_payload()
                                if validation.source_contract_assessment is not None
                                else None
                            ),
                        },
                        sort_keys=True,
                    )
                    output_contains_secret = any(
                        credential_value in serialized_validation
                        for credential_value in credential_fields.values()
                    )
                except Exception:
                    validation_readiness = "validation_failed"
                    validation_reason = "source_credential_validator_output_rejected"
                    validation_evidence = None
                    source_contract_assessment = None
                else:
                    if output_contains_secret:
                        validation_readiness = "validation_failed"
                        validation_reason = "source_credential_validator_output_rejected"
                        validation_evidence = None
                        source_contract_assessment = None
                    else:
                        validation_readiness = validation.readiness
                        validation_reason = validation.reason_code
                        validation_evidence = validation.evidence
                        source_contract_assessment = validation.source_contract_assessment
        if not isinstance(expected_version, int):
            raise ValueError("source_credential_version_invalid")
        return self._state_store.record_source_credential_validation(
            provider_id=provider_id,
            readiness=validation_readiness,
            reason_code=validation_reason,
            validated_at=self._instant(evaluated_at),
            expected_version=expected_version,
            expected_secret_ref_id=expected_secret_ref_id,
            validation_evidence=(
                validation_evidence.as_payload() if validation_evidence is not None else {}
            ),
            source_contract_assessment=(
                source_contract_assessment.as_payload()
                if source_contract_assessment is not None
                else None
            ),
            authorization=authorization,
            trace_id=trace_id,
        )

    def _authorize_source_adapter_validation(
        self,
        *,
        provider_id: str,
        validator: SourceCredentialValidator,
        trace_id: str,
        evaluated_at: datetime,
    ) -> PolicyDeniedOutcome | None:
        context = self._source_adapter_security_context
        policy_loader = self._source_adapter_authorization_policy
        if context is None or policy_loader is None:
            raise ValueError("source_adapter_identity_unavailable")
        try:
            policy = policy_loader()
        except KeyError:
            policy = AuthorizationPolicy(
                action_grants=(),
                source_policies=(),
                source_entitlements=(),
            )
        for dataset_id, distribution_id, distribution_url in _SOURCE_CREDENTIAL_VALIDATION_TARGETS[
            provider_id
        ]:
            decision = policy.evaluate(
                context,
                OperationIntent(
                    action="market_data.collect",
                    dataset_id=dataset_id,
                    purpose="price_research",
                    environment=context.environment,
                    resource_state="active",
                    evaluated_at=evaluated_at,
                    trace_id=trace_id,
                    correlation_id=trace_id,
                    required_uses=PRICE_RESEARCH_REQUIRED_USES,
                    distribution_id=distribution_id,
                    distribution_url=distribution_url,
                    source_access_mode=validator.source_access_mode,
                ),
            )
            self._state_store.record_authorization_decision(
                authorization=authorization_audit_payload(decision),
                outcome="allowed" if decision.allowed else "denied",
                trace_id=trace_id,
            )
            if not decision.allowed:
                return PolicyDeniedOutcome.from_decision(decision)
        return None

    def _authorize(
        self,
        *,
        action: AuthorizationAction,
        trace_id: str,
        security_context: SecurityContext,
    ) -> AuthorizationDecision:
        return self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action=action,
                dataset_id="source-credential-metadata",
                purpose="source_administration",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=self._clock(),
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
        )

    @staticmethod
    def _provider(provider_id: str) -> dict[str, object]:
        provider = next(
            (item for item in _SOURCE_CREDENTIAL_PROVIDERS if item["provider_id"] == provider_id),
            None,
        )
        if provider is None:
            raise KeyError(provider_id)
        return provider

    @staticmethod
    def _validate_fields(provider: Mapping[str, object], credential_fields: dict[str, str]) -> None:
        required_fields = provider["required_fields"]
        if (
            not isinstance(required_fields, list)
            or set(credential_fields) != set(required_fields)
            or any(not value for value in credential_fields.values())
        ):
            raise ValueError("source_credential_fields_invalid")

    @staticmethod
    def _canonical_expiry(expires_at: str | None) -> str | None:
        if expires_at is None:
            return None
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("source_credential_expiry_invalid") from error
        if parsed.tzinfo is None:
            raise ValueError("source_credential_expiry_invalid")
        return OperationsControl._instant(parsed)

    def _drain_secret_cleanup(self, *, provider_id: str) -> bool:
        for secret_ref_id in self._state_store.list_pending_source_secret_cleanup(
            provider_id=provider_id
        ):
            try:
                self._secret_provider.revoke(SecretRef(secret_ref_id))
            except Exception:
                continue
            self._state_store.complete_source_secret_cleanup(
                secret_ref_id=secret_ref_id,
                completed_at=self._instant(self._clock()),
            )
        return bool(self._state_store.list_pending_source_secret_cleanup(provider_id=provider_id))

    @staticmethod
    def _instant(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    def list_health(self, *, scope: str) -> list[dict[str, object]]:
        return self._state_store.list_health(scope=scope)

    def get_work(self, work_id: str) -> dict[str, Any] | None:
        return self._state_store.get_work(work_id)

    def get_trace_evidence(self, trace_id: str) -> dict[str, Any]:
        return self._state_store.get_trace_evidence(trace_id)

    def get_outbox_event(self, event_id: str) -> dict[str, Any]:
        return self._state_store.get_outbox_event(event_id)

    def list_prediction_records(self, *, trace_id: str) -> list[dict[str, Any]]:
        return self._state_store.list_prediction_records(trace_id=trace_id)

    def list_prediction_record_evidence(self, *, trace_id: str) -> list[dict[str, str]]:
        return self._state_store.list_prediction_record_evidence(trace_id=trace_id)

    def get_outbox_recovery(self, event_id: str) -> dict[str, Any]:
        return self._state_store.get_outbox_recovery(event_id)

    def list_outbox_incidents(self, *, aggregate_id: str) -> list[dict[str, Any]]:
        return self._state_store.list_outbox_incidents(aggregate_id=aggregate_id)
