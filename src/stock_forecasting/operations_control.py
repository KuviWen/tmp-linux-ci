from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from stock_forecasting.authorization import (
    AuthorizationPolicy,
    OperationIntent,
    PolicyDeniedOutcome,
    SecurityContext,
    authorization_audit_payload,
)
from stock_forecasting.data_supply import PRICE_RESEARCH_REQUIRED_USES
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.source_credentials import SecretProvider, SourceCredentialValidator
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest

_ALPACA_SOURCE_BASIS = load_us_stock_pool_manifest().source_basis.as_payload()
_SOURCE_CREDENTIAL_PROVIDERS: tuple[dict[str, object], ...] = (
    {
        **_ALPACA_SOURCE_BASIS,
        "provider_id": "alpaca-market-data-basic",
        "display_name": "Alpaca Market Data Basic",
        "credential_kind": "api_key_pair",
        "required_uses": sorted(PRICE_RESEARCH_REQUIRED_USES),
        "required_fields": ["api_key_id", "api_secret_key"],
        "registration_url": "https://app.alpaca.markets/signup",
        "key_management_url": "https://app.alpaca.markets/paper/dashboard/overview",
    },
)


class OperationsControl:
    def __init__(
        self,
        state_store: StateStore,
        *,
        authorization_policy: AuthorizationPolicy,
        secret_provider: SecretProvider,
        source_credential_validators: Mapping[str, SourceCredentialValidator],
        clock: Callable[[], datetime],
    ) -> None:
        self._state_store = state_store
        self._authorization_policy = authorization_policy
        self._secret_provider = secret_provider
        self._source_credential_validators = source_credential_validators
        self._clock = clock

    def list_source_credentials(
        self,
        *,
        trace_id: str,
        security_context: SecurityContext,
    ) -> list[dict[str, object]] | PolicyDeniedOutcome:
        evaluated_at = self._clock()
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="source_credential.read",
                dataset_id="source-credential-metadata",
                purpose="source_administration",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
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
            readiness = self._state_store.get_source_credential(
                provider_id=str(provider["provider_id"])
            )
            results.append(
                {
                    **provider,
                    **(
                        readiness
                        if readiness is not None
                        else {
                            "readiness": "missing",
                            "reason_code": "source_credential_missing",
                            "secret_ref_id": None,
                            "version": None,
                            "configured_at": None,
                            "last_validated_at": None,
                        }
                    ),
                }
            )
        return results

    def set_source_credential(
        self,
        *,
        provider_id: str,
        credential_fields: dict[str, str],
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        provider = next(
            (item for item in _SOURCE_CREDENTIAL_PROVIDERS if item["provider_id"] == provider_id),
            None,
        )
        if provider is None:
            raise KeyError(provider_id)
        required_fields = provider["required_fields"]
        if (
            not isinstance(required_fields, list)
            or set(credential_fields) != set(required_fields)
            or any(not value for value in credential_fields.values())
        ):
            raise ValueError("source_credential_fields_invalid")
        evaluated_at = self._clock()
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="source_credential.manage",
                dataset_id="source-credential-metadata",
                purpose="source_administration",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        secret_ref = self._secret_provider.put(
            provider_id=provider_id,
            credential_fields=credential_fields,
        )
        return self._state_store.publish_source_credential(
            provider_id=provider_id,
            secret_ref_id=secret_ref.secret_ref_id,
            readiness="configured",
            reason_code="source_credential_not_validated",
            configured_at=evaluated_at.isoformat().replace("+00:00", "Z"),
            authorization=authorization,
            trace_id=trace_id,
        )

    def rotate_source_credential(
        self,
        *,
        provider_id: str,
        credential_fields: dict[str, str],
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        provider = next(
            (item for item in _SOURCE_CREDENTIAL_PROVIDERS if item["provider_id"] == provider_id),
            None,
        )
        if provider is None:
            raise KeyError(provider_id)
        required_fields = provider["required_fields"]
        if (
            not isinstance(required_fields, list)
            or set(credential_fields) != set(required_fields)
            or any(not value for value in credential_fields.values())
        ):
            raise ValueError("source_credential_fields_invalid")
        evaluated_at = self._clock()
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="source_credential.manage",
                dataset_id="source-credential-metadata",
                purpose="source_administration",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        secret_ref = self._secret_provider.put(
            provider_id=provider_id,
            credential_fields=credential_fields,
        )
        outcome, prior_secret_ref_id = self._state_store.rotate_source_credential(
            provider_id=provider_id,
            secret_ref_id=secret_ref.secret_ref_id,
            readiness="configured",
            reason_code="source_credential_not_validated",
            configured_at=evaluated_at.isoformat().replace("+00:00", "Z"),
            authorization=authorization,
            trace_id=trace_id,
        )
        self._secret_provider.revoke(prior_secret_ref_id)
        return outcome

    def revoke_source_credential(
        self,
        *,
        provider_id: str,
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        if not any(
            provider["provider_id"] == provider_id for provider in _SOURCE_CREDENTIAL_PROVIDERS
        ):
            raise KeyError(provider_id)
        evaluated_at = self._clock()
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="source_credential.manage",
                dataset_id="source-credential-metadata",
                purpose="source_administration",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        current = self._state_store.get_source_credential(provider_id=provider_id)
        if current is None or current["readiness"] == "revoked":
            raise ValueError("source_credential_not_configured")
        outcome = self._state_store.revoke_source_credential(
            provider_id=provider_id,
            revoked_at=evaluated_at.isoformat().replace("+00:00", "Z"),
            authorization=authorization,
            trace_id=trace_id,
        )
        self._secret_provider.revoke(str(current["secret_ref_id"]))
        return outcome

    def validate_source_credential(
        self,
        *,
        provider_id: str,
        trace_id: str,
        security_context: SecurityContext,
    ) -> dict[str, object] | PolicyDeniedOutcome:
        if not any(
            provider["provider_id"] == provider_id for provider in _SOURCE_CREDENTIAL_PROVIDERS
        ):
            raise KeyError(provider_id)
        current = self._state_store.get_source_credential(provider_id=provider_id)
        if current is None or current["readiness"] == "revoked":
            raise ValueError("source_credential_not_configured")
        validator = self._source_credential_validators.get(provider_id)
        if validator is None:
            raise ValueError("source_credential_validator_unavailable")
        evaluated_at = self._clock()
        decision = self._authorization_policy.evaluate(
            security_context,
            OperationIntent(
                action="source_credential.manage",
                dataset_id="source-credential-metadata",
                purpose="source_administration",
                environment=security_context.environment,
                resource_state="active",
                evaluated_at=evaluated_at,
                trace_id=trace_id,
                correlation_id=trace_id,
            ),
        )
        authorization = authorization_audit_payload(decision)
        if not decision.allowed:
            self._state_store.record_authorization_decision(
                authorization=authorization,
                outcome="denied",
                trace_id=trace_id,
            )
            return PolicyDeniedOutcome.from_decision(decision)
        lease = self._secret_provider.checkout(str(current["secret_ref_id"]))
        validation = validator.validate(lease.credential_fields())
        return self._state_store.record_source_credential_validation(
            provider_id=provider_id,
            readiness=validation.readiness,
            reason_code=validation.reason_code,
            validated_at=evaluated_at.isoformat().replace("+00:00", "Z"),
            authorization=authorization,
            trace_id=trace_id,
        )

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
