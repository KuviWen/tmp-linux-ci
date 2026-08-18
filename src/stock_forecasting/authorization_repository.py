from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationAction,
    AuthorizationPolicy,
    AuthorizationPurpose,
    AuthorizationResourceState,
    DataProtectionClass,
    EntitlementStatus,
    RuntimeEnvironment,
    SecurityContext,
    SourceAccessBasis,
    SourceDistribution,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
    action_grant_version_payload,
    build_fixture_authorization_policy,
    source_entitlement_version_payload,
    source_policy_version_payload,
)
from stock_forecasting.platform.state_store import StateStore

FIXTURE_ACTIVE_POLICY_SET = "fixture-active-v1"
FIXTURE_SUSPENDED_POLICY_SET = "fixture-suspended-v1"
FIXTURE_EXPIRED_POLICY_SET = "fixture-expired-v1"
FIXTURE_REVOKED_POLICY_SET = "fixture-revoked-v1"
FIXTURE_PURPOSE_REMOVED_POLICY_SET = "fixture-purpose-removed-v1"
FIXTURE_GRANT_MISSING_POLICY_SET = "fixture-grant-missing-v1"
FIXTURE_POLICY_UNKNOWN_SET = "fixture-policy-unknown-v1"
TICKET_06_POLICY_BLOCKED_SET = "ticket-06-taiwan-policy-blocked-v1"
TICKET_06_FINMIND_ENGINEERING_POLICY_SET = "ticket-06-finmind-zero-fee-engineering-v1"
TICKET_07_ENGINEERING_POLICY_SET = "ticket-07-us-zero-fee-engineering-v1"
TICKET_08_ENGINEERING_POLICY_SET = "ticket-08-historical-reconstruction-engineering-v1"
TICKET_09_ENGINEERING_POLICY_SET = "ticket-09-bootstrap-governance-engineering-v1"
TICKET_09_OWNER_OPERATOR_POLICY_SET = "ticket-09-owner-operator-v1"


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("authorization_policy_instant_invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("authorization_policy_instant_timezone_required")
    return parsed


def _policy_payload(policy: AuthorizationPolicy) -> dict[str, Any]:
    return {
        "action_grants": [action_grant_version_payload(grant) for grant in policy.action_grants],
        "source_policies": [
            source_policy_version_payload(source_policy) for source_policy in policy.source_policies
        ],
        "source_entitlements": [
            source_entitlement_version_payload(entitlement)
            for entitlement in policy.source_entitlements
        ],
    }


def _string_list(value: object, *, reason: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(reason)
    return cast(list[str], value)


def _policy_from_payload(payload: dict[str, Any]) -> AuthorizationPolicy:
    try:
        grants_payload = cast(list[dict[str, Any]], payload["action_grants"])
        policies_payload = cast(list[dict[str, Any]], payload["source_policies"])
        entitlements_payload = cast(list[dict[str, Any]], payload["source_entitlements"])
        return AuthorizationPolicy(
            action_grants=tuple(
                ActionGrant(
                    version_id=str(item["version_id"]),
                    principal_id=str(item["principal_id"]),
                    actions=frozenset(
                        cast(
                            list[AuthorizationAction],
                            _string_list(item["actions"], reason="action_grant_actions_invalid"),
                        )
                    ),
                    environment=cast(RuntimeEnvironment, item["environment"]),
                    valid_from=_parse_instant(item["valid_from"]),
                    valid_to=_parse_instant(item["valid_to"]),
                )
                for item in grants_payload
            ),
            source_policies=tuple(
                SourcePolicyVersion(
                    version_id=str(item["version_id"]),
                    dataset_id=str(item["dataset_id"]),
                    allowed_actions=frozenset(
                        cast(
                            list[AuthorizationAction],
                            _string_list(
                                item["allowed_actions"],
                                reason="source_policy_actions_invalid",
                            ),
                        )
                    ),
                    purposes=frozenset(
                        cast(
                            list[AuthorizationPurpose],
                            _string_list(item["purposes"], reason="policy_purposes_invalid"),
                        )
                    ),
                    environments=frozenset(
                        cast(
                            list[RuntimeEnvironment],
                            _string_list(
                                item["environments"], reason="policy_environments_invalid"
                            ),
                        )
                    ),
                    data_protection_class=cast(DataProtectionClass, item["data_protection_class"]),
                    resource_states=frozenset(
                        cast(
                            list[AuthorizationResourceState],
                            _string_list(item["resource_states"], reason="resource_states_invalid"),
                        )
                    ),
                    valid_from=_parse_instant(item["valid_from"]),
                    valid_to=_parse_instant(item["valid_to"]),
                    allowed_uses=frozenset(
                        cast(
                            list[SourceUseRight],
                            _string_list(
                                item.get("allowed_uses", []),
                                reason="source_policy_allowed_uses_invalid",
                            ),
                        )
                    ),
                    access_basis=cast(
                        SourceAccessBasis,
                        item.get("access_basis", "principal_entitlement"),
                    ),
                    source_basis_id=cast(str | None, item.get("source_basis_id")),
                    license_id=cast(str | None, item.get("license_id")),
                    terms_url=cast(str | None, item.get("terms_url")),
                    terms_content_sha256=cast(
                        str | None,
                        item.get("terms_content_sha256"),
                    ),
                    attribution=cast(str | None, item.get("attribution")),
                    distributions=tuple(
                        SourceDistribution(
                            dataset_id=str(distribution["dataset_id"]),
                            distribution_url=str(distribution["distribution_url"]),
                        )
                        for distribution in cast(
                            list[dict[str, object]],
                            item.get("distributions", []),
                        )
                    ),
                    provider_id=cast(str | None, item.get("provider_id")),
                    plan_id=cast(str | None, item.get("plan_id")),
                    principal_classification=cast(
                        str | None,
                        item.get("principal_classification"),
                    ),
                    credential_kind=cast(str | None, item.get("credential_kind")),
                    account_required=cast(bool | None, item.get("account_required")),
                    fee_required=cast(bool | None, item.get("fee_required")),
                )
                for item in policies_payload
            ),
            source_entitlements=tuple(
                SourceEntitlement(
                    version_id=str(item["version_id"]),
                    principal_id=str(item["principal_id"]),
                    dataset_id=str(item["dataset_id"]),
                    status=cast(EntitlementStatus, item["status"]),
                    allowed_actions=frozenset(
                        cast(
                            list[AuthorizationAction],
                            _string_list(
                                item["allowed_actions"],
                                reason="source_entitlement_actions_invalid",
                            ),
                        )
                    ),
                    purposes=frozenset(
                        cast(
                            list[AuthorizationPurpose],
                            _string_list(item["purposes"], reason="entitlement_purposes_invalid"),
                        )
                    ),
                    environments=frozenset(
                        cast(
                            list[RuntimeEnvironment],
                            _string_list(
                                item["environments"], reason="entitlement_environments_invalid"
                            ),
                        )
                    ),
                    valid_from=_parse_instant(item["valid_from"]),
                    valid_to=_parse_instant(item["valid_to"]),
                    allowed_uses=frozenset(
                        cast(
                            list[SourceUseRight],
                            _string_list(
                                item.get("allowed_uses", []),
                                reason="source_entitlement_allowed_uses_invalid",
                            ),
                        )
                    ),
                )
                for item in entitlements_payload
            ),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("authorization_policy_payload_invalid") from error


class AuthorizationPolicyRepository:
    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store

    def install(self, policy_set_id: str, policy: AuthorizationPolicy) -> None:
        principals = {grant.principal_id for grant in policy.action_grants} | {
            entitlement.principal_id for entitlement in policy.source_entitlements
        }
        if len(principals) != 1:
            raise ValueError("authorization_policy_principal_required")
        self._state_store.install_authorization_policy_set(
            policy_set_id=policy_set_id,
            principal_id=principals.pop(),
            payload=_policy_payload(policy),
        )

    def get(self, policy_set_id: str, *, principal_id: str) -> AuthorizationPolicy:
        return _policy_from_payload(
            self._state_store.get_authorization_policy_set(
                policy_set_id=policy_set_id,
                principal_id=principal_id,
            )
        )


def fixture_authorization_policy_catalog(
    identity_context: SecurityContext,
) -> dict[str, AuthorizationPolicy]:
    return {
        FIXTURE_ACTIVE_POLICY_SET: build_fixture_authorization_policy(identity_context),
        FIXTURE_SUSPENDED_POLICY_SET: build_fixture_authorization_policy(
            identity_context,
            entitlement_states={"XTAI": "suspended"},
        ),
        FIXTURE_EXPIRED_POLICY_SET: build_fixture_authorization_policy(
            identity_context,
            entitlement_states={"XTAI": "expired"},
        ),
        FIXTURE_REVOKED_POLICY_SET: build_fixture_authorization_policy(
            identity_context,
            entitlement_states={"XTAI": "revoked"},
        ),
        FIXTURE_PURPOSE_REMOVED_POLICY_SET: build_fixture_authorization_policy(
            identity_context,
            entitlement_purposes={"XTAI": frozenset()},
        ),
        FIXTURE_GRANT_MISSING_POLICY_SET: build_fixture_authorization_policy(
            identity_context,
            grant_actions=frozenset(),
        ),
        FIXTURE_POLICY_UNKNOWN_SET: build_fixture_authorization_policy(
            identity_context,
            policy_markets=frozenset({"XNAS"}),
        ),
    }
