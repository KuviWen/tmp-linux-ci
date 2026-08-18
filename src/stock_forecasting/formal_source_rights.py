from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from stock_forecasting.alpaca_provider_contract import (
    ALPACA_PROVIDER_DISTRIBUTIONS,
    ALPACA_PROVIDER_ID,
)
from stock_forecasting.authorization import (
    AuthorizationAction,
    AuthorizationPolicy,
    SecurityContext,
    SourceDistribution,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
    build_pending_rights_operator_authorization_policy,
)
from stock_forecasting.content_address import content_id, sha256_hex
from stock_forecasting.finmind_provider_contract import (
    FINMIND_PROVIDER_DISTRIBUTIONS,
    FINMIND_PROVIDER_ID,
)
from stock_forecasting.market_data_provider_contract import ProviderDistributionContract

FORMAL_SOURCE_RIGHTS_SCHEMA = "formal-source-rights/v1"
QUALIFIED_OPERATOR_POLICY_PREFIX = "ticket-09-owner-operator-qualified-"
REQUIRED_SOURCE_USES: frozenset[SourceUseRight] = frozenset(
    {
        "backup_restore",
        "ingest",
        "internal_display",
        "model",
        "retain_observed_history",
        "transform",
    }
)


@dataclass(frozen=True)
class _ProviderContract:
    provider_id: str
    plan_id: str
    credential_kind: str
    official_sender: str
    terms_url: str
    attribution: str
    distributions: tuple[ProviderDistributionContract, ...]


_PROVIDER_CONTRACTS = {
    FINMIND_PROVIDER_ID: _ProviderContract(
        provider_id=FINMIND_PROVIDER_ID,
        plan_id="Free",
        credential_kind="api_token",
        official_sender="finmind.tw@gmail.com",
        terms_url="https://finmind.github.io/PrivacyPolicy/",
        attribution="FinMind",
        distributions=FINMIND_PROVIDER_DISTRIBUTIONS,
    ),
    ALPACA_PROVIDER_ID: _ProviderContract(
        provider_id=ALPACA_PROVIDER_ID,
        plan_id="Basic",
        credential_kind="api_key_pair",
        official_sender="support@alpaca.markets",
        terms_url=("https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf"),
        attribution="Alpaca",
        distributions=ALPACA_PROVIDER_DISTRIBUTIONS,
    ),
}


@dataclass(frozen=True)
class FormalProviderRights:
    contract: _ProviderContract
    response_sha256: str
    terms_sha256: str


@dataclass(frozen=True)
class FormalSourceRightsManifest:
    sha256: str
    reviewed_at: datetime
    reviewer_owner: str
    providers: tuple[FormalProviderRights, ...]

    @property
    def policy_set_id(self) -> str:
        return f"{QUALIFIED_OPERATOR_POLICY_PREFIX}{self.sha256[:16]}"

    @classmethod
    def load(cls, path: Path, *, reviewer_owner: str) -> FormalSourceRightsManifest:
        content = path.read_bytes()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError("formal_source_rights_manifest_invalid") from error
        if not isinstance(payload, dict):
            raise ValueError("formal_source_rights_manifest_invalid")
        _expect_exact_keys(
            payload,
            {
                "schema_version",
                "reviewed_at",
                "reviewer_owner",
                "deployment_jurisdiction",
                "usage_scope",
                "decision",
                "providers",
            },
            "formal_source_rights_manifest_invalid",
        )
        if (
            payload["schema_version"] != FORMAL_SOURCE_RIGHTS_SCHEMA
            or payload["reviewer_owner"] != reviewer_owner
            or payload["deployment_jurisdiction"] != "Taiwan"
            or payload["usage_scope"] != "personal_noncommercial_single_user_local_only"
            or payload["decision"] != "qualified"
        ):
            raise ValueError("formal_source_rights_manifest_invalid")
        reviewed_at = _parse_instant(payload["reviewed_at"])
        providers_payload = payload["providers"]
        if not isinstance(providers_payload, list) or len(providers_payload) != len(
            _PROVIDER_CONTRACTS
        ):
            raise ValueError("formal_source_rights_manifest_invalid")
        providers = tuple(
            _load_provider_rights(path.parent, cast(dict[str, Any], provider))
            for provider in providers_payload
            if isinstance(provider, dict)
        )
        if {item.contract.provider_id for item in providers} != set(_PROVIDER_CONTRACTS):
            raise ValueError("formal_source_rights_manifest_invalid")
        return cls(
            sha256=sha256_hex(content),
            reviewed_at=reviewed_at,
            reviewer_owner=reviewer_owner,
            providers=providers,
        )


def build_qualified_operator_authorization_policy(
    context: SecurityContext,
    manifest: FormalSourceRightsManifest,
) -> AuthorizationPolicy:
    if context.principal_classification != "individual_non_commercial":
        raise ValueError("formal_source_rights_principal_classification_invalid")
    pending = build_pending_rights_operator_authorization_policy(context)
    collect_action: frozenset[AuthorizationAction] = frozenset({"market_data.collect"})
    source_basis_id = f"sha256:{manifest.sha256}"
    policies: list[SourcePolicyVersion] = []
    entitlements: list[SourceEntitlement] = []
    for provider in manifest.providers:
        for distribution in provider.contract.distributions:
            policy_payload: dict[str, object] = {
                "principal_id": context.principal_id,
                "provider_id": provider.contract.provider_id,
                "plan_id": provider.contract.plan_id,
                "dataset_id": distribution.policy_dataset_id,
                "distribution_id": distribution.distribution_id,
                "distribution_url": distribution.distribution_url,
                "allowed_uses": sorted(REQUIRED_SOURCE_USES),
                "source_basis_id": source_basis_id,
                "response_sha256": provider.response_sha256,
                "terms_sha256": provider.terms_sha256,
                "valid_from": context.issued_at.isoformat(),
                "valid_to": context.expires_at.isoformat(),
            }
            policies.append(
                SourcePolicyVersion(
                    version_id=content_id("formal-source-policy/v1", policy_payload),
                    dataset_id=distribution.policy_dataset_id,
                    allowed_actions=collect_action,
                    purposes=frozenset({"price_research"}),
                    environments=frozenset({context.environment}),
                    data_protection_class="licensed",
                    resource_states=frozenset({"active"}),
                    valid_from=context.issued_at,
                    valid_to=context.expires_at,
                    allowed_uses=REQUIRED_SOURCE_USES,
                    access_basis="zero_fee_plan",
                    source_basis_id=source_basis_id,
                    license_id=f"sha256:{provider.response_sha256}",
                    terms_url=provider.contract.terms_url,
                    terms_content_sha256=provider.terms_sha256,
                    attribution=provider.contract.attribution,
                    distributions=(
                        SourceDistribution(
                            dataset_id=distribution.distribution_id,
                            distribution_url=distribution.distribution_url,
                        ),
                    ),
                    provider_id=provider.contract.provider_id,
                    plan_id=provider.contract.plan_id,
                    principal_classification="individual_non_commercial",
                    credential_kind=provider.contract.credential_kind,
                    account_required=True,
                    fee_required=False,
                )
            )
            if "market_data.collect" in context.scopes:
                entitlements.append(
                    SourceEntitlement(
                        version_id=content_id(
                            "formal-source-entitlement/v1",
                            {
                                **policy_payload,
                                "allowed_actions": ["market_data.collect"],
                            },
                        ),
                        principal_id=context.principal_id,
                        dataset_id=distribution.policy_dataset_id,
                        status="active",
                        allowed_actions=collect_action,
                        purposes=frozenset({"price_research"}),
                        environments=frozenset({context.environment}),
                        valid_from=context.issued_at,
                        valid_to=context.expires_at,
                        allowed_uses=REQUIRED_SOURCE_USES,
                    )
                )
    return AuthorizationPolicy(
        action_grants=pending.action_grants,
        source_policies=(*pending.source_policies, *policies),
        source_entitlements=(*pending.source_entitlements, *entitlements),
    )


def is_owner_operator_policy_set(policy_set_id: str) -> bool:
    from stock_forecasting.authorization_repository import TICKET_09_OWNER_OPERATOR_POLICY_SET

    return policy_set_id == TICKET_09_OWNER_OPERATOR_POLICY_SET or policy_set_id.startswith(
        QUALIFIED_OPERATOR_POLICY_PREFIX
    )


def _load_provider_rights(root: Path, payload: dict[str, Any]) -> FormalProviderRights:
    _expect_exact_keys(
        payload,
        {
            "provider_id",
            "plan_id",
            "credential_kind",
            "official_sender",
            "response_file",
            "response_sha256",
            "distribution_ids",
            "allowed_uses",
            "terms_url",
            "terms_file",
            "terms_sha256",
        },
        "formal_source_rights_provider_invalid",
    )
    provider_id = payload["provider_id"]
    if not isinstance(provider_id, str) or provider_id not in _PROVIDER_CONTRACTS:
        raise ValueError("formal_source_rights_provider_invalid")
    contract = _PROVIDER_CONTRACTS[provider_id]
    expected_distributions = {
        distribution.distribution_id for distribution in contract.distributions
    }
    if (
        payload["plan_id"] != contract.plan_id
        or payload["credential_kind"] != contract.credential_kind
        or str(payload["official_sender"]).lower() != contract.official_sender
        or payload["terms_url"] != contract.terms_url
        or set(_string_list(payload["distribution_ids"])) != expected_distributions
        or set(_string_list(payload["allowed_uses"])) != set(REQUIRED_SOURCE_USES)
    ):
        raise ValueError("formal_source_rights_provider_invalid")
    response_sha256 = _validated_evidence(
        root,
        payload["response_file"],
        payload["response_sha256"],
        reason="formal_source_rights_response_invalid",
    )
    response_path = _resolved_evidence_path(root, payload["response_file"])
    response_text = response_path.read_text(encoding="utf-8")
    headers = {
        name.strip().lower(): value.strip()
        for line in response_text.splitlines()
        if ":" in line
        for name, value in [line.split(":", 1)]
    }
    if (
        contract.official_sender not in headers.get("from", "").lower()
        or not headers.get("sent at")
        or not headers.get("recipient", "").startswith("[redacted]@")
        or not headers.get("redactions")
    ):
        raise ValueError("formal_source_rights_response_invalid")
    terms_sha256 = _validated_evidence(
        root,
        payload["terms_file"],
        payload["terms_sha256"],
        reason="formal_source_rights_terms_invalid",
    )
    return FormalProviderRights(
        contract=contract,
        response_sha256=response_sha256,
        terms_sha256=terms_sha256,
    )


def _validated_evidence(root: Path, relative_path: object, checksum: object, *, reason: str) -> str:
    if (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or any(character not in "0123456789abcdef" for character in checksum)
    ):
        raise ValueError(reason)
    path = _resolved_evidence_path(root, relative_path)
    if not path.is_file() or sha256_hex(path.read_bytes()) != checksum:
        raise ValueError(reason)
    return checksum


def _resolved_evidence_path(root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("formal_source_rights_evidence_path_invalid")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("formal_source_rights_evidence_path_invalid")
    return resolved


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("formal_source_rights_reviewed_at_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("formal_source_rights_reviewed_at_invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("formal_source_rights_reviewed_at_invalid")
    return parsed


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("formal_source_rights_provider_invalid")
    return cast(list[str], value)


def _expect_exact_keys(payload: dict[str, Any], expected: set[str], reason: str) -> None:
    if set(payload) != expected:
        raise ValueError(reason)
