from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

RuntimeEnvironment = Literal["local", "development", "test", "staging", "production"]
EntitlementStatus = Literal["draft", "under_review", "active", "suspended", "expired", "revoked"]
DataProtectionClass = Literal["public_source", "internal", "licensed", "restricted", "secret"]
AuthorizationAction = Literal[
    "fixture_pipeline.execute",
    "research_prediction.read",
    "market_data.collect",
    "price_research_eligibility.read",
    "price_qualification.govern",
]
AuthorizationPurpose = Literal["fixture_research", "price_research"]
AuthorizationResourceState = Literal["active"]
SourceUseRight = Literal[
    "ingest",
    "retain_7_years",
    "transform",
    "model",
    "internal_display",
    "backup_restore",
]

_LOCAL_KEY_ENVIRONMENTS = frozenset({"local", "development"})
_CONTEXT_ISSUER = object()
_MIN_INSTANT = datetime.min.replace(tzinfo=UTC)
_MAX_INSTANT = datetime.max.replace(tzinfo=UTC)


class IdentityVerificationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LocalApiKeyCredential:
    key_id: str
    _secret: str = field(repr=False)

    def authorization_header(self) -> str:
        return f"ApiKey {self.key_id}.{self._secret}"

    def __repr__(self) -> str:
        return f"LocalApiKeyCredential(key_id={self.key_id!r}, secret=<redacted>)"


@dataclass(frozen=True, init=False)
class SecurityContext:
    principal_id: str
    credential_id: str
    owner: str
    environment: RuntimeEnvironment
    scopes: frozenset[AuthorizationAction]
    data_protection_classes: frozenset[DataProtectionClass]
    issued_at: datetime
    expires_at: datetime
    authentication_method: Literal["local_api_key"]

    def __init__(
        self,
        *,
        principal_id: str,
        credential_id: str,
        owner: str,
        environment: RuntimeEnvironment,
        scopes: frozenset[AuthorizationAction],
        data_protection_classes: frozenset[DataProtectionClass],
        issued_at: datetime,
        expires_at: datetime,
        authentication_method: Literal["local_api_key"],
        _issuer: object | None = None,
    ) -> None:
        if _issuer is not _CONTEXT_ISSUER:
            raise TypeError("trusted_security_context_factory_required")
        object.__setattr__(self, "principal_id", principal_id)
        object.__setattr__(self, "credential_id", credential_id)
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(self, "data_protection_classes", data_protection_classes)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "authentication_method", authentication_method)

    @property
    def trusted(self) -> bool:
        return True


@dataclass(frozen=True)
class CurrentSourcePrincipalAttributes:
    principal_id: str
    evidence_id: str
    environment: RuntimeEnvironment
    data_protection_classes: frozenset[DataProtectionClass]
    valid_from: datetime
    valid_to: datetime

    def __post_init__(self) -> None:
        if not self.principal_id or not self.evidence_id:
            raise ValueError("source_principal_attributes_identity_required")
        if self.valid_from.tzinfo is None or self.valid_to.tzinfo is None:
            raise ValueError("source_principal_attributes_times_require_timezone")
        if self.valid_to <= self.valid_from:
            raise ValueError("source_principal_attributes_validity_invalid")

    @classmethod
    def from_verified_security_context(
        cls,
        context: SecurityContext,
    ) -> CurrentSourcePrincipalAttributes:
        evidence_digest = hashlib.sha256(context.credential_id.encode("utf-8")).hexdigest()
        return cls(
            principal_id=context.principal_id,
            evidence_id=f"verified-security-context-sha256:{evidence_digest}",
            environment=context.environment,
            data_protection_classes=context.data_protection_classes,
            valid_from=context.issued_at,
            valid_to=context.expires_at,
        )


@dataclass(frozen=True)
class LocalApiKeyVerifier:
    key_id: str
    principal_id: str
    owner: str
    environment: RuntimeEnvironment
    scopes: frozenset[AuthorizationAction]
    data_protection_classes: frozenset[DataProtectionClass]
    issued_at: datetime
    expires_at: datetime
    revoked: bool
    _pepper: bytes = field(repr=False)
    _secret_digest: bytes = field(repr=False)

    @classmethod
    def issue(
        cls,
        *,
        owner: str,
        environment: RuntimeEnvironment,
        scopes: set[AuthorizationAction],
        issued_at: datetime,
        expires_at: datetime,
        data_protection_classes: set[DataProtectionClass] | None = None,
    ) -> tuple[LocalApiKeyCredential, LocalApiKeyVerifier]:
        if not owner:
            raise ValueError("local_api_key_owner_required")
        if environment not in _LOCAL_KEY_ENVIRONMENTS:
            raise ValueError("local_api_key_environment_forbidden")
        if not scopes:
            raise ValueError("local_api_key_scopes_required")
        if issued_at.tzinfo is None or expires_at.tzinfo is None:
            raise ValueError("local_api_key_times_require_timezone")
        if expires_at <= issued_at:
            raise ValueError("local_api_key_expiry_invalid")
        if expires_at - issued_at > timedelta(days=30):
            raise ValueError("local_api_key_lifetime_exceeded")
        key_id = str(uuid4())
        principal_id = str(uuid4())
        secret = secrets.token_urlsafe(32)
        pepper = secrets.token_bytes(32)
        digest = hmac.new(pepper, secret.encode("utf-8"), hashlib.sha256).digest()
        credential = LocalApiKeyCredential(key_id=key_id, _secret=secret)
        verifier = cls(
            key_id=key_id,
            principal_id=principal_id,
            owner=owner,
            environment=environment,
            scopes=frozenset(scopes),
            data_protection_classes=frozenset(data_protection_classes or {"internal"}),
            issued_at=issued_at,
            expires_at=expires_at,
            revoked=False,
            _pepper=pepper,
            _secret_digest=digest,
        )
        return credential, verifier

    def authenticate(
        self,
        authorization_header: str,
        *,
        client_host: str,
        environment: RuntimeEnvironment,
        authenticated_at: datetime,
    ) -> SecurityContext:
        try:
            scheme, presentation = authorization_header.split(" ", 1)
            key_id, secret = presentation.split(".", 1)
        except ValueError as error:
            raise IdentityVerificationError("local_api_key_malformed") from error
        if scheme != "ApiKey" or key_id != self.key_id:
            raise IdentityVerificationError("local_api_key_invalid")
        try:
            is_loopback = ipaddress.ip_address(client_host).is_loopback
        except ValueError as error:
            raise IdentityVerificationError("local_api_key_loopback_required") from error
        if not is_loopback:
            raise IdentityVerificationError("local_api_key_loopback_required")
        if environment not in _LOCAL_KEY_ENVIRONMENTS or environment != self.environment:
            raise IdentityVerificationError("local_api_key_environment_forbidden")
        if self.revoked:
            raise IdentityVerificationError("local_api_key_revoked")
        if authenticated_at.tzinfo is None:
            raise IdentityVerificationError("authentication_time_requires_timezone")
        if authenticated_at < self.issued_at or authenticated_at >= self.expires_at:
            raise IdentityVerificationError("local_api_key_expired")
        presented_digest = hmac.new(
            self._pepper,
            secret.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(presented_digest, self._secret_digest):
            raise IdentityVerificationError("local_api_key_invalid")
        return SecurityContext(
            principal_id=self.principal_id,
            credential_id=self.key_id,
            owner=self.owner,
            environment=self.environment,
            scopes=self.scopes,
            data_protection_classes=self.data_protection_classes,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            authentication_method="local_api_key",
            _issuer=_CONTEXT_ISSUER,
        )


@dataclass(frozen=True)
class LocalApiKeyIdentity:
    credential: LocalApiKeyCredential
    verifier: LocalApiKeyVerifier
    context: SecurityContext

    @classmethod
    def issue(
        cls,
        *,
        owner: str,
        environment: RuntimeEnvironment,
        scopes: set[AuthorizationAction],
        issued_at: datetime,
        expires_at: datetime,
        data_protection_classes: set[DataProtectionClass] | None = None,
    ) -> LocalApiKeyIdentity:
        credential, verifier = LocalApiKeyVerifier.issue(
            owner=owner,
            environment=environment,
            scopes=scopes,
            issued_at=issued_at,
            expires_at=expires_at,
            data_protection_classes=data_protection_classes,
        )
        context = verifier.authenticate(
            credential.authorization_header(),
            client_host="127.0.0.1",
            environment=environment,
            authenticated_at=issued_at,
        )
        return cls(credential=credential, verifier=verifier, context=context)

    def save(self, path: Path) -> None:
        payload = {
            "version": 2,
            "key_id": self.credential.key_id,
            "principal_id": self.verifier.principal_id,
            "secret": self.credential._secret,
            "owner": self.verifier.owner,
            "environment": self.verifier.environment,
            "scopes": sorted(self.verifier.scopes),
            "data_protection_classes": sorted(self.verifier.data_protection_classes),
            "issued_at": self.verifier.issued_at.isoformat(),
            "expires_at": self.verifier.expires_at.isoformat(),
            "revoked": self.verifier.revoked,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")

    @classmethod
    def load(cls, path: Path) -> LocalApiKeyIdentity:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 2:
                raise ValueError
            key_id = payload["key_id"]
            principal_id = payload["principal_id"]
            secret = payload["secret"]
            owner = payload["owner"]
            environment = payload["environment"]
            scopes = payload["scopes"]
            data_protection_classes = payload["data_protection_classes"]
            issued_at = datetime.fromisoformat(payload["issued_at"])
            expires_at = datetime.fromisoformat(payload["expires_at"])
            revoked = payload["revoked"]
            if (
                not isinstance(key_id, str)
                or not isinstance(principal_id, str)
                or not principal_id
                or not isinstance(secret, str)
                or not isinstance(owner, str)
                or environment not in _LOCAL_KEY_ENVIRONMENTS
                or not isinstance(scopes, list)
                or not scopes
                or not all(
                    scope
                    in {
                        "fixture_pipeline.execute",
                        "research_prediction.read",
                        "market_data.collect",
                        "price_research_eligibility.read",
                        "price_qualification.govern",
                    }
                    for scope in scopes
                )
                or not isinstance(data_protection_classes, list)
                or not data_protection_classes
                or not all(
                    protection_class
                    in {"public_source", "internal", "licensed", "restricted", "secret"}
                    for protection_class in data_protection_classes
                )
                or issued_at.tzinfo is None
                or expires_at.tzinfo is None
                or expires_at <= issued_at
                or expires_at - issued_at > timedelta(days=30)
                or not isinstance(revoked, bool)
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("local_api_key_file_invalid") from error
        credential = LocalApiKeyCredential(key_id=key_id, _secret=secret)
        pepper = secrets.token_bytes(32)
        verifier = LocalApiKeyVerifier(
            key_id=key_id,
            principal_id=principal_id,
            owner=owner,
            environment=cast(RuntimeEnvironment, environment),
            scopes=frozenset(cast(list[AuthorizationAction], scopes)),
            data_protection_classes=frozenset(
                cast(list[DataProtectionClass], data_protection_classes)
            ),
            issued_at=issued_at,
            expires_at=expires_at,
            revoked=revoked,
            _pepper=pepper,
            _secret_digest=hmac.new(
                pepper,
                secret.encode("utf-8"),
                hashlib.sha256,
            ).digest(),
        )
        context = verifier.authenticate(
            credential.authorization_header(),
            client_host="127.0.0.1",
            environment=verifier.environment,
            authenticated_at=issued_at,
        )
        return cls(credential=credential, verifier=verifier, context=context)


@dataclass(frozen=True)
class ActionGrant:
    version_id: str
    principal_id: str
    actions: frozenset[AuthorizationAction]
    environment: RuntimeEnvironment
    valid_from: datetime
    valid_to: datetime


@dataclass(frozen=True)
class SourcePolicyVersion:
    version_id: str
    dataset_id: str
    allowed_actions: frozenset[AuthorizationAction]
    purposes: frozenset[AuthorizationPurpose]
    environments: frozenset[RuntimeEnvironment]
    data_protection_class: DataProtectionClass
    resource_states: frozenset[AuthorizationResourceState]
    valid_from: datetime = _MIN_INSTANT
    valid_to: datetime = _MAX_INSTANT
    allowed_uses: frozenset[SourceUseRight] = frozenset()


@dataclass(frozen=True)
class SourceEntitlement:
    version_id: str
    principal_id: str
    dataset_id: str
    status: EntitlementStatus
    allowed_actions: frozenset[AuthorizationAction]
    purposes: frozenset[AuthorizationPurpose]
    environments: frozenset[RuntimeEnvironment]
    valid_from: datetime
    valid_to: datetime
    allowed_uses: frozenset[SourceUseRight] = frozenset()


@dataclass(frozen=True)
class OperationIntent:
    action: AuthorizationAction
    dataset_id: str
    purpose: AuthorizationPurpose
    environment: RuntimeEnvironment
    resource_state: AuthorizationResourceState
    evaluated_at: datetime
    trace_id: str
    correlation_id: str
    required_uses: frozenset[SourceUseRight] = frozenset()


@dataclass(frozen=True)
class AuthorizationDecision:
    evaluation_id: str
    decision_id: str
    allowed: bool
    reason_code: str
    principal_id: str
    credential_id: str
    authentication_method: Literal["local_api_key"]
    action: AuthorizationAction
    dataset_id: str
    purpose: AuthorizationPurpose
    environment: RuntimeEnvironment
    required_uses: frozenset[SourceUseRight]
    grant_version_id: str | None
    source_policy_version_id: str | None
    source_entitlement_version_id: str | None
    data_protection_class: DataProtectionClass | None
    trace_id: str
    correlation_id: str
    evaluated_at: datetime
    valid_until: datetime


@dataclass(frozen=True)
class SourceRightsDecision:
    evaluation_id: str
    decision_id: str
    allowed: bool
    reason_code: str
    subject_principal_id: str | None
    runtime_environment: RuntimeEnvironment
    subject_attributes_evidence_id: str | None
    subject_attributes_valid_until: datetime | None
    subject_data_protection_classes: frozenset[DataProtectionClass] | None
    dataset_id: str
    prior_evaluation_id: str
    prior_decision_id: str | None
    prior_trace_id: str | None
    prior_correlation_id: str | None
    evaluated_at: datetime
    valid_until: datetime
    grant_version_id: str | None
    source_policy_version_id: str | None
    source_entitlement_version_id: str | None

    def as_payload(self) -> dict[str, object]:
        return {
            "evaluation_id": self.evaluation_id,
            "decision_id": self.decision_id,
            "outcome": "allowed" if self.allowed else "denied",
            "reason_code": self.reason_code,
            "subject_principal_id": self.subject_principal_id,
            "runtime_environment": self.runtime_environment,
            "subject_attributes_evidence_id": self.subject_attributes_evidence_id,
            "subject_attributes_valid_until": (
                _instant(self.subject_attributes_valid_until)
                if self.subject_attributes_valid_until is not None
                else None
            ),
            "subject_data_protection_classes": (
                sorted(self.subject_data_protection_classes)
                if self.subject_data_protection_classes is not None
                else None
            ),
            "dataset_id": self.dataset_id,
            "prior_evaluation_id": self.prior_evaluation_id,
            "prior_decision_id": self.prior_decision_id,
            "prior_trace_id": self.prior_trace_id,
            "prior_correlation_id": self.prior_correlation_id,
            "evaluated_at": _instant(self.evaluated_at),
            "valid_until": _instant(self.valid_until),
            "grant_version_id": self.grant_version_id,
            "source_policy_version_id": self.source_policy_version_id,
            "source_entitlement_version_id": self.source_entitlement_version_id,
        }


@dataclass(frozen=True)
class _PolicyRightsResolution:
    reason_code: str
    grant: ActionGrant | None
    source_policy: SourcePolicyVersion | None
    source_entitlement: SourceEntitlement | None


class SourceRightsEvidenceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PolicyDeniedOutcome:
    decision_id: str
    correlation_id: str
    status: Literal["policy_denied"] = field(default="policy_denied", init=False)
    code: Literal["authorization_denied"] = field(default="authorization_denied", init=False)

    @classmethod
    def from_decision(cls, decision: AuthorizationDecision) -> PolicyDeniedOutcome:
        return cls(
            decision_id=decision.decision_id,
            correlation_id=decision.correlation_id,
        )


def _instant(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("authorization_instant_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("authorization_instant_invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("authorization_instant_invalid")
    return parsed


def action_grant_version_payload(grant: ActionGrant) -> dict[str, object]:
    return {
        "version_id": grant.version_id,
        "principal_id": grant.principal_id,
        "actions": sorted(grant.actions),
        "environment": grant.environment,
        "valid_from": _instant(grant.valid_from),
        "valid_to": _instant(grant.valid_to),
    }


def source_policy_version_payload(policy: SourcePolicyVersion) -> dict[str, object]:
    payload: dict[str, object] = {
        "version_id": policy.version_id,
        "dataset_id": policy.dataset_id,
        "allowed_actions": sorted(policy.allowed_actions),
        "purposes": sorted(policy.purposes),
        "environments": sorted(policy.environments),
        "data_protection_class": policy.data_protection_class,
        "resource_states": sorted(policy.resource_states),
        "valid_from": _instant(policy.valid_from),
        "valid_to": _instant(policy.valid_to),
    }
    if policy.allowed_uses:
        payload["allowed_uses"] = sorted(policy.allowed_uses)
    return payload


def source_entitlement_version_payload(
    entitlement: SourceEntitlement,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version_id": entitlement.version_id,
        "principal_id": entitlement.principal_id,
        "dataset_id": entitlement.dataset_id,
        "status": entitlement.status,
        "allowed_actions": sorted(entitlement.allowed_actions),
        "purposes": sorted(entitlement.purposes),
        "environments": sorted(entitlement.environments),
        "valid_from": _instant(entitlement.valid_from),
        "valid_to": _instant(entitlement.valid_to),
    }
    if entitlement.allowed_uses:
        payload["allowed_uses"] = sorted(entitlement.allowed_uses)
    return payload


def authorization_audit_payload(decision: AuthorizationDecision) -> dict[str, object]:
    payload: dict[str, object] = {
        "evaluation_id": decision.evaluation_id,
        "decision_id": decision.decision_id,
        "correlation_id": decision.correlation_id,
        "principal_id": decision.principal_id,
        "credential_id": decision.credential_id,
        "authentication_method": decision.authentication_method,
        "dataset_id": decision.dataset_id,
        "purpose": decision.purpose,
        "environment": decision.environment,
        "grant_version_id": decision.grant_version_id,
        "source_policy_version_id": decision.source_policy_version_id,
        "source_entitlement_version_id": decision.source_entitlement_version_id,
        "data_protection_class": decision.data_protection_class,
        "action": decision.action,
        "reason_code": decision.reason_code,
        "evaluated_at": _instant(decision.evaluated_at),
        "valid_until": _instant(decision.valid_until),
    }
    if decision.required_uses:
        payload["required_uses"] = sorted(decision.required_uses)
    return payload


def source_rights_resolution_failure(
    *,
    dataset_id: str,
    prior_evaluation_id: str,
    prior_decision_id: str | None,
    prior_trace_id: str | None,
    prior_correlation_id: str | None,
    evaluated_at: datetime,
    trace_id: str,
    reason_code: str,
    runtime_environment: RuntimeEnvironment,
    current_subject: CurrentSourcePrincipalAttributes | None,
) -> SourceRightsDecision:
    identity = "/".join(
        (
            dataset_id,
            prior_evaluation_id,
            prior_decision_id or "no-prior-decision",
            prior_trace_id or "no-prior-trace",
            prior_correlation_id or "no-prior-correlation",
            reason_code,
            runtime_environment,
            (
                current_subject.evidence_id
                if current_subject is not None
                else "no-subject-attributes"
            ),
            (
                f"classes:{','.join(sorted(current_subject.data_protection_classes))}"
                if current_subject is not None
                else "no-subject-classes"
            ),
            trace_id,
        )
    )
    return SourceRightsDecision(
        evaluation_id=str(uuid4()),
        decision_id=str(
            uuid5(NAMESPACE_URL, f"stock-forecasting/source-rights-failure/{identity}")
        ),
        allowed=False,
        reason_code=reason_code,
        subject_principal_id=(
            current_subject.principal_id if current_subject is not None else None
        ),
        runtime_environment=runtime_environment,
        subject_attributes_evidence_id=(
            current_subject.evidence_id if current_subject is not None else None
        ),
        subject_attributes_valid_until=(
            current_subject.valid_to if current_subject is not None else None
        ),
        subject_data_protection_classes=(
            current_subject.data_protection_classes if current_subject is not None else None
        ),
        dataset_id=dataset_id,
        prior_evaluation_id=prior_evaluation_id,
        prior_decision_id=prior_decision_id,
        prior_trace_id=prior_trace_id,
        prior_correlation_id=prior_correlation_id,
        evaluated_at=evaluated_at,
        valid_until=evaluated_at,
        grant_version_id=None,
        source_policy_version_id=None,
        source_entitlement_version_id=None,
    )


@dataclass(frozen=True)
class AuthorizationPolicy:
    action_grants: tuple[ActionGrant, ...]
    source_policies: tuple[SourcePolicyVersion, ...]
    source_entitlements: tuple[SourceEntitlement, ...]

    def publication_version_evidence(
        self,
        decision: AuthorizationDecision,
    ) -> dict[str, dict[str, object]]:
        if (
            not decision.allowed
            or decision.grant_version_id is None
            or decision.source_policy_version_id is None
            or decision.source_entitlement_version_id is None
        ):
            raise ValueError("allowed_authorization_versions_required")
        grants = tuple(
            grant for grant in self.action_grants if grant.version_id == decision.grant_version_id
        )
        policies = tuple(
            policy
            for policy in self.source_policies
            if policy.version_id == decision.source_policy_version_id
        )
        entitlements = tuple(
            entitlement
            for entitlement in self.source_entitlements
            if entitlement.version_id == decision.source_entitlement_version_id
        )
        if len(grants) != 1 or len(policies) != 1 or len(entitlements) != 1:
            raise ValueError("authorization_version_evidence_inconsistent")
        return {
            "action_grant": action_grant_version_payload(grants[0]),
            "source_policy": source_policy_version_payload(policies[0]),
            "source_entitlement": source_entitlement_version_payload(entitlements[0]),
        }

    def evaluate_current_source_rights(
        self,
        prior_authorization: Mapping[str, object],
        *,
        expected_dataset_id: str,
        expected_evaluation_id: str,
        expected_decision_id: str,
        expected_trace_id: str,
        expected_correlation_id: str,
        current_runtime_environment: RuntimeEnvironment,
        current_subject: CurrentSourcePrincipalAttributes,
        evaluated_at: datetime,
        trace_id: str,
        correlation_id: str,
        required_uses: frozenset[SourceUseRight],
    ) -> SourceRightsDecision:
        prior_required_uses = prior_authorization.get("required_uses")
        protection_class = prior_authorization.get("data_protection_class")
        environment = prior_authorization.get("environment")
        principal_id = prior_authorization.get("principal_id")
        if (
            prior_authorization.get("outcome") != "allowed"
            or prior_authorization.get("reason_code") != "authorized"
            or prior_authorization.get("action") != "market_data.collect"
            or prior_authorization.get("purpose") != "price_research"
            or prior_authorization.get("evaluation_id") != expected_evaluation_id
            or prior_authorization.get("decision_id") != expected_decision_id
            or prior_authorization.get("dataset_id") != expected_dataset_id
            or prior_authorization.get("trace_id") != expected_trace_id
            or prior_authorization.get("correlation_id") != expected_correlation_id
            or not isinstance(principal_id, str)
            or environment not in {"local", "development", "test", "staging", "production"}
            or protection_class
            not in {"public_source", "internal", "licensed", "restricted", "secret"}
            or not isinstance(prior_required_uses, list)
            or required_uses != set(prior_required_uses)
        ):
            raise SourceRightsEvidenceError("source_rights_prior_evidence_mismatch")
        try:
            prior_evaluated_at = _parse_instant(prior_authorization.get("evaluated_at"))
            prior_valid_until = _parse_instant(prior_authorization.get("valid_until"))
        except ValueError as error:
            raise SourceRightsEvidenceError("source_rights_prior_evidence_invalid") from error
        if prior_valid_until <= prior_evaluated_at or prior_evaluated_at > evaluated_at:
            raise SourceRightsEvidenceError("source_rights_prior_evidence_invalid")
        if (
            current_subject.principal_id != principal_id
            or current_subject.environment != current_runtime_environment
            or not current_subject.evidence_id
            or not current_subject.valid_from <= evaluated_at < current_subject.valid_to
        ):
            raise SourceRightsEvidenceError("source_rights_subject_attributes_invalid")
        intent = OperationIntent(
            action="market_data.collect",
            dataset_id=expected_dataset_id,
            purpose="price_research",
            environment=current_runtime_environment,
            resource_state="active",
            evaluated_at=evaluated_at,
            trace_id=trace_id,
            correlation_id=correlation_id,
            required_uses=required_uses,
        )
        rights = self._resolve_rights(
            principal_id=principal_id,
            intent=intent,
            data_protection_classes=current_subject.data_protection_classes,
        )
        reason_code = rights.reason_code
        if evaluated_at >= prior_valid_until:
            reason_code = "source_workload_evidence_expired"
        allowed = reason_code == "authorized"
        valid_until = evaluated_at
        if (
            allowed
            and rights.grant is not None
            and rights.source_policy is not None
            and rights.source_entitlement is not None
        ):
            valid_until = min(
                prior_valid_until,
                current_subject.valid_to,
                rights.grant.valid_to,
                rights.source_policy.valid_to,
                rights.source_entitlement.valid_to,
            )
        decision_identity = "/".join(
            (
                principal_id,
                expected_dataset_id,
                current_runtime_environment,
                current_subject.evidence_id,
                rights.grant.version_id if rights.grant is not None else "no-grant",
                rights.source_policy.version_id
                if rights.source_policy is not None
                else "no-policy",
                rights.source_entitlement.version_id
                if rights.source_entitlement is not None
                else "no-entitlement",
                reason_code,
                trace_id,
                correlation_id,
                f"classes:{','.join(sorted(current_subject.data_protection_classes))}",
                f"uses:{','.join(sorted(required_uses))}",
            )
        )
        return SourceRightsDecision(
            evaluation_id=str(uuid4()),
            decision_id=str(
                uuid5(NAMESPACE_URL, f"stock-forecasting/source-rights/{decision_identity}")
            ),
            allowed=allowed,
            reason_code=reason_code,
            subject_principal_id=principal_id,
            runtime_environment=current_runtime_environment,
            subject_attributes_evidence_id=current_subject.evidence_id,
            subject_attributes_valid_until=current_subject.valid_to,
            subject_data_protection_classes=current_subject.data_protection_classes,
            dataset_id=expected_dataset_id,
            prior_evaluation_id=expected_evaluation_id,
            prior_decision_id=expected_decision_id,
            prior_trace_id=expected_trace_id,
            prior_correlation_id=expected_correlation_id,
            evaluated_at=evaluated_at,
            valid_until=valid_until,
            grant_version_id=rights.grant.version_id if rights.grant is not None else None,
            source_policy_version_id=(
                rights.source_policy.version_id if rights.source_policy is not None else None
            ),
            source_entitlement_version_id=(
                rights.source_entitlement.version_id
                if rights.source_entitlement is not None
                else None
            ),
        )

    def _resolve_rights(
        self,
        *,
        principal_id: str,
        intent: OperationIntent,
        data_protection_classes: frozenset[DataProtectionClass],
    ) -> _PolicyRightsResolution:
        grant_history = tuple(
            candidate for candidate in self.action_grants if candidate.principal_id == principal_id
        )
        grant_candidates = tuple(
            candidate
            for candidate in grant_history
            if candidate.valid_from <= intent.evaluated_at < candidate.valid_to
        )
        source_policy_history = tuple(
            candidate
            for candidate in self.source_policies
            if candidate.dataset_id == intent.dataset_id
        )
        source_policy_candidates = tuple(
            candidate
            for candidate in source_policy_history
            if candidate.valid_from <= intent.evaluated_at < candidate.valid_to
        )
        entitlement_history = tuple(
            candidate
            for candidate in self.source_entitlements
            if candidate.principal_id == principal_id and candidate.dataset_id == intent.dataset_id
        )
        entitlement_candidates = tuple(
            candidate
            for candidate in entitlement_history
            if candidate.valid_from <= intent.evaluated_at < candidate.valid_to
        )
        grant = grant_candidates[0] if len(grant_candidates) == 1 else None
        source_policy = source_policy_candidates[0] if len(source_policy_candidates) == 1 else None
        entitlement = entitlement_candidates[0] if len(entitlement_candidates) == 1 else None
        reason_code = "authorized"
        if len(grant_candidates) > 1:
            reason_code = "action_grant_conflict"
        elif grant is None and grant_history:
            reason_code = "action_grant_expired"
        elif grant is None or intent.action not in grant.actions:
            reason_code = "action_grant_missing"
        elif grant.environment != intent.environment:
            reason_code = "action_grant_environment_denied"
        elif len(source_policy_candidates) > 1:
            reason_code = "source_policy_conflict"
        elif source_policy is None and source_policy_history:
            reason_code = "source_policy_expired"
        elif source_policy is None:
            reason_code = "source_policy_unknown"
        elif intent.action not in source_policy.allowed_actions:
            reason_code = "source_policy_action_denied"
        elif intent.purpose not in source_policy.purposes:
            reason_code = "source_policy_purpose_denied"
        elif intent.environment not in source_policy.environments:
            reason_code = "source_policy_environment_denied"
        elif intent.resource_state not in source_policy.resource_states:
            reason_code = "source_policy_resource_state_denied"
        elif not intent.required_uses <= source_policy.allowed_uses:
            reason_code = "source_policy_use_denied"
        elif source_policy.data_protection_class not in data_protection_classes:
            reason_code = "data_protection_class_denied"
        elif len(entitlement_candidates) > 1:
            reason_code = "source_entitlement_conflict"
        elif entitlement is None and entitlement_history:
            reason_code = "source_entitlement_expired"
        elif entitlement is None:
            reason_code = "source_entitlement_missing"
        elif entitlement.status != "active":
            reason_code = f"source_entitlement_{entitlement.status}"
        elif intent.action not in entitlement.allowed_actions:
            reason_code = "source_entitlement_action_denied"
        elif intent.purpose not in entitlement.purposes:
            reason_code = "source_entitlement_purpose_denied"
        elif intent.environment not in entitlement.environments:
            reason_code = "source_entitlement_environment_denied"
        elif not intent.required_uses <= entitlement.allowed_uses:
            reason_code = "source_entitlement_use_denied"
        return _PolicyRightsResolution(
            reason_code=reason_code,
            grant=grant,
            source_policy=source_policy,
            source_entitlement=entitlement,
        )

    def evaluate(
        self,
        context: SecurityContext,
        intent: OperationIntent,
    ) -> AuthorizationDecision:
        rights = self._resolve_rights(
            principal_id=context.principal_id,
            intent=intent,
            data_protection_classes=context.data_protection_classes,
        )
        grant = rights.grant
        source_policy = rights.source_policy
        entitlement = rights.source_entitlement
        reason_code = "authorized"
        if not context.trusted:
            reason_code = "identity_untrusted"
        elif not (context.issued_at <= intent.evaluated_at < context.expires_at):
            reason_code = "identity_expired"
        elif context.environment != intent.environment:
            reason_code = "identity_environment_mismatch"
        elif intent.action not in context.scopes:
            reason_code = "identity_scope_missing"
        else:
            reason_code = rights.reason_code
        allowed = reason_code == "authorized"
        decision_identity_parts = [
            context.principal_id,
            intent.action,
            intent.dataset_id,
            intent.purpose,
            intent.environment,
            intent.resource_state,
            grant.version_id if grant is not None else "no-grant",
            source_policy.version_id if source_policy is not None else "no-policy",
            entitlement.version_id if entitlement is not None else "no-entitlement",
            reason_code,
            intent.trace_id,
            intent.correlation_id,
        ]
        if intent.required_uses:
            decision_identity_parts.append(f"uses:{','.join(sorted(intent.required_uses))}")
        decision_identity = "/".join(decision_identity_parts)
        valid_until = intent.evaluated_at
        if allowed and grant is not None and source_policy is not None and entitlement is not None:
            valid_until = min(
                context.expires_at,
                grant.valid_to,
                source_policy.valid_to,
                entitlement.valid_to,
            )
        return AuthorizationDecision(
            evaluation_id=str(uuid4()),
            decision_id=str(uuid5(NAMESPACE_URL, f"stock-forecasting/authz/{decision_identity}")),
            allowed=allowed,
            reason_code=reason_code,
            principal_id=context.principal_id,
            credential_id=context.credential_id,
            authentication_method=context.authentication_method,
            action=intent.action,
            dataset_id=intent.dataset_id,
            purpose=intent.purpose,
            environment=intent.environment,
            required_uses=intent.required_uses,
            grant_version_id=grant.version_id if grant is not None else None,
            source_policy_version_id=(
                source_policy.version_id if source_policy is not None else None
            ),
            source_entitlement_version_id=(
                entitlement.version_id if entitlement is not None else None
            ),
            data_protection_class=(
                source_policy.data_protection_class if source_policy is not None else None
            ),
            trace_id=intent.trace_id,
            correlation_id=intent.correlation_id,
            evaluated_at=intent.evaluated_at,
            valid_until=valid_until,
        )


def fixture_dataset_id(market: str) -> str:
    datasets = {
        "XTAI": "xtai-fixture-eod",
        "XNAS": "xnas-fixture-eod",
    }
    try:
        return datasets[market]
    except KeyError as error:
        raise ValueError("unknown_fixture_market") from error


def _contract_version_id(kind: str, payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, f"stock-forecasting/{kind}/{canonical}"))


def build_fixture_authorization_policy(
    context: SecurityContext,
    *,
    entitlement_states: Mapping[str, EntitlementStatus] | None = None,
    entitlement_purposes: Mapping[str, frozenset[str]] | None = None,
    grant_actions: frozenset[str] | None = None,
    policy_markets: frozenset[str] | None = None,
) -> AuthorizationPolicy:
    states = entitlement_states or {}
    purposes = entitlement_purposes or {}
    known_markets = policy_markets or frozenset({"XTAI", "XNAS"})
    actions: frozenset[AuthorizationAction] = frozenset(
        {"fixture_pipeline.execute", "research_prediction.read"}
    )
    if grant_actions is not None and not grant_actions <= actions:
        raise ValueError("unknown_authorization_action")
    resolved_grant_actions = (
        actions
        if grant_actions is None
        else frozenset(cast(frozenset[AuthorizationAction], grant_actions))
    )
    grant_payload: dict[str, object] = {
        "principal_id": context.principal_id,
        "actions": sorted(resolved_grant_actions),
        "environment": context.environment,
        "valid_from": _instant(context.issued_at),
        "valid_to": _instant(context.expires_at),
    }
    grant = ActionGrant(
        version_id=_contract_version_id("action-grant", grant_payload),
        principal_id=context.principal_id,
        actions=resolved_grant_actions,
        environment=context.environment,
        valid_from=context.issued_at,
        valid_to=context.expires_at,
    )
    source_policies: list[SourcePolicyVersion] = []
    source_entitlements: list[SourceEntitlement] = []
    for market in ("XTAI", "XNAS"):
        dataset_id = fixture_dataset_id(market)
        namespace = market.lower()
        if market in known_markets:
            policy_payload: dict[str, object] = {
                "dataset_id": dataset_id,
                "allowed_actions": sorted(actions),
                "purposes": ["fixture_research"],
                "environments": [context.environment],
                "data_protection_class": "internal",
                "resource_states": ["active"],
                "valid_from": _instant(context.issued_at),
                "valid_to": _instant(context.expires_at),
            }
            source_policies.append(
                SourcePolicyVersion(
                    version_id=_contract_version_id(f"{namespace}/source-policy", policy_payload),
                    dataset_id=dataset_id,
                    allowed_actions=actions,
                    purposes=frozenset({"fixture_research"}),
                    environments=frozenset({context.environment}),
                    data_protection_class="internal",
                    resource_states=frozenset({"active"}),
                    valid_from=context.issued_at,
                    valid_to=context.expires_at,
                )
            )
        state = states.get(market, "active")
        configured_purposes = purposes.get(market, frozenset({"fixture_research"}))
        if not configured_purposes <= {"fixture_research"}:
            raise ValueError("unknown_authorization_purpose")
        allowed_purposes = frozenset(cast(frozenset[AuthorizationPurpose], configured_purposes))
        entitlement_payload: dict[str, object] = {
            "principal_id": context.principal_id,
            "dataset_id": dataset_id,
            "status": state,
            "allowed_actions": sorted(actions),
            "purposes": sorted(allowed_purposes),
            "environments": [context.environment],
            "valid_from": _instant(context.issued_at),
            "valid_to": _instant(context.expires_at),
        }
        source_entitlements.append(
            SourceEntitlement(
                version_id=_contract_version_id(
                    f"{namespace}/source-entitlement", entitlement_payload
                ),
                principal_id=context.principal_id,
                dataset_id=dataset_id,
                status=state,
                allowed_actions=actions,
                purposes=allowed_purposes,
                environments=frozenset({context.environment}),
                valid_from=context.issued_at,
                valid_to=context.expires_at,
            )
        )
    return AuthorizationPolicy(
        action_grants=(grant,),
        source_policies=tuple(source_policies),
        source_entitlements=tuple(source_entitlements),
    )


def build_taiwan_price_blocked_authorization_policy(
    context: SecurityContext,
) -> AuthorizationPolicy:
    actions: frozenset[AuthorizationAction] = frozenset(
        {"market_data.collect", "price_research_eligibility.read"}
    )
    grant_payload: dict[str, object] = {
        "principal_id": context.principal_id,
        "actions": sorted(actions),
        "environment": context.environment,
        "valid_from": _instant(context.issued_at),
        "valid_to": _instant(context.expires_at),
    }
    grant = ActionGrant(
        version_id=_contract_version_id("ticket-06/action-grant", grant_payload),
        principal_id=context.principal_id,
        actions=actions,
        environment=context.environment,
        valid_from=context.issued_at,
        valid_to=context.expires_at,
    )
    policy_payload: dict[str, object] = {
        "dataset_id": "price-research-eligibility",
        "allowed_actions": ["price_research_eligibility.read"],
        "purposes": ["price_research"],
        "environments": [context.environment],
        "data_protection_class": "internal",
        "resource_states": ["active"],
        "valid_from": _instant(context.issued_at),
        "valid_to": _instant(context.expires_at),
    }
    source_policy = SourcePolicyVersion(
        version_id=_contract_version_id("ticket-06/source-policy", policy_payload),
        dataset_id="price-research-eligibility",
        allowed_actions=frozenset({"price_research_eligibility.read"}),
        purposes=frozenset({"price_research"}),
        environments=frozenset({context.environment}),
        data_protection_class="internal",
        resource_states=frozenset({"active"}),
        valid_from=context.issued_at,
        valid_to=context.expires_at,
    )
    entitlement_payload: dict[str, object] = {
        "principal_id": context.principal_id,
        "dataset_id": "price-research-eligibility",
        "status": "active",
        "allowed_actions": ["price_research_eligibility.read"],
        "purposes": ["price_research"],
        "environments": [context.environment],
        "valid_from": _instant(context.issued_at),
        "valid_to": _instant(context.expires_at),
    }
    entitlement = SourceEntitlement(
        version_id=_contract_version_id("ticket-06/source-entitlement", entitlement_payload),
        principal_id=context.principal_id,
        dataset_id="price-research-eligibility",
        status="active",
        allowed_actions=frozenset({"price_research_eligibility.read"}),
        purposes=frozenset({"price_research"}),
        environments=frozenset({context.environment}),
        valid_from=context.issued_at,
        valid_to=context.expires_at,
    )
    return AuthorizationPolicy(
        action_grants=(grant,),
        source_policies=(source_policy,),
        source_entitlements=(entitlement,),
    )
