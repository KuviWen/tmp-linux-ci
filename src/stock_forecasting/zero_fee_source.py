from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

from stock_forecasting.authorization import SourceUseRight

CredentialKind = Literal["api_key_pair", "bearer_token"]


@dataclass(frozen=True)
class ZeroFeeSourceBundleMember:
    provider_id: str
    dataset_id: str
    distribution_url: str
    qualification_scope: str
    schema_version: str
    price_semantics: Literal["unadjusted"] | None
    qualification_status: Literal[
        "candidate_terms_not_archived",
        "candidate_scope_limited",
    ]
    materialization_role: Literal[
        "required_observation",
        "supplemental_qualification_reference",
    ]
    known_gaps: tuple[str, ...]
    allowed_uses: frozenset[SourceUseRight]
    rights_status: Literal["unverified"]
    attribution_requirement: Literal["unresolved"]
    retention_limit: Literal["unresolved"]
    deletion_requirement: Literal["unresolved"]
    effective_from: date | None
    effective_to: date | None

    def __post_init__(self) -> None:
        if (
            not self.provider_id
            or not self.dataset_id
            or not self.distribution_url.startswith("https://")
            or not self.qualification_scope
            or not self.schema_version
            or self.materialization_role
            not in {"required_observation", "supplemental_qualification_reference"}
            or not self.known_gaps
            or self.allowed_uses
            or self.rights_status != "unverified"
            or self.attribution_requirement != "unresolved"
            or self.retention_limit != "unresolved"
            or self.deletion_requirement != "unresolved"
            or (
                self.effective_from is not None
                and self.effective_to is not None
                and self.effective_from > self.effective_to
            )
        ):
            raise ValueError("zero_fee_source_bundle_member_invalid")

    def as_payload(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "dataset_id": self.dataset_id,
            "distribution_url": self.distribution_url,
            "qualification_scope": self.qualification_scope,
            "schema_version": self.schema_version,
            "price_semantics": self.price_semantics,
            "qualification_status": self.qualification_status,
            "materialization_role": self.materialization_role,
            "known_gaps": list(self.known_gaps),
            "allowed_uses": sorted(self.allowed_uses),
            "rights_status": self.rights_status,
            "attribution_requirement": self.attribution_requirement,
            "retention_limit": self.retention_limit,
            "deletion_requirement": self.deletion_requirement,
            "effective_from": (
                self.effective_from.isoformat() if self.effective_from is not None else None
            ),
            "effective_to": (
                self.effective_to.isoformat() if self.effective_to is not None else None
            ),
        }


@dataclass(frozen=True)
class ZeroFeeAuthenticatedSourceBasis:
    source_basis_id: str
    basis_type: Literal["zero_fee_plan"]
    provider_id: str
    plan_id: str
    principal_classification: str
    credential_kind: CredentialKind
    account_required: Literal[True]
    fee_required: Literal[False]
    terms_url: str
    terms_content_sha256: str | None
    qualification_status: Literal["candidate_terms_not_archived"]
    members: tuple[ZeroFeeSourceBundleMember, ...]
    supplemental_references: tuple[ZeroFeeSourceBundleMember, ...]

    def __post_init__(self) -> None:
        terms_digest_is_valid = self.terms_content_sha256 is None or (
            len(self.terms_content_sha256) == 64
            and all(character in "0123456789abcdef" for character in self.terms_content_sha256)
        )
        if (
            not self.source_basis_id
            or self.basis_type != "zero_fee_plan"
            or not self.provider_id
            or not self.plan_id
            or not self.principal_classification
            or self.credential_kind not in {"api_key_pair", "bearer_token"}
            or self.account_required is not True
            or self.fee_required is not False
            or not self.terms_url.startswith("https://")
            or not terms_digest_is_valid
            or self.qualification_status != "candidate_terms_not_archived"
            or not self.members
            or any(member.provider_id != self.provider_id for member in self.members)
            or len({member.dataset_id for member in self.members}) != len(self.members)
            or any(member.materialization_role != "required_observation" for member in self.members)
            or any(
                member.materialization_role != "supplemental_qualification_reference"
                for member in self.supplemental_references
            )
            or len({member.dataset_id for member in (*self.members, *self.supplemental_references)})
            != len(self.members) + len(self.supplemental_references)
        ):
            raise ValueError("zero_fee_authenticated_source_basis_invalid")

    def as_payload(self) -> dict[str, object]:
        return {
            "source_basis_id": self.source_basis_id,
            "basis_type": self.basis_type,
            "provider_id": self.provider_id,
            "plan_id": self.plan_id,
            "principal_classification": self.principal_classification,
            "credential_kind": self.credential_kind,
            "account_required": self.account_required,
            "fee_required": self.fee_required,
            "terms_url": self.terms_url,
            "terms_content_sha256": self.terms_content_sha256,
            "qualification_status": self.qualification_status,
            "members": [member.as_payload() for member in self.members],
            "supplemental_references": [
                member.as_payload() for member in self.supplemental_references
            ],
        }


def source_bundle_member_from_payload(
    member: dict[str, object],
) -> ZeroFeeSourceBundleMember:
    return ZeroFeeSourceBundleMember(
        provider_id=str(member["provider_id"]),
        dataset_id=str(member["dataset_id"]),
        distribution_url=str(member["distribution_url"]),
        qualification_scope=str(member["qualification_scope"]),
        schema_version=str(member["schema_version"]),
        price_semantics=cast(Literal["unadjusted"] | None, member["price_semantics"]),
        qualification_status=cast(
            Literal["candidate_terms_not_archived", "candidate_scope_limited"],
            member["qualification_status"],
        ),
        materialization_role=cast(
            Literal["required_observation", "supplemental_qualification_reference"],
            member["materialization_role"],
        ),
        known_gaps=tuple(cast(list[str], member["known_gaps"])),
        allowed_uses=frozenset(cast(list[SourceUseRight], member["allowed_uses"])),
        rights_status=cast(Literal["unverified"], member["rights_status"]),
        attribution_requirement=cast(Literal["unresolved"], member["attribution_requirement"]),
        retention_limit=cast(Literal["unresolved"], member["retention_limit"]),
        deletion_requirement=cast(Literal["unresolved"], member["deletion_requirement"]),
        effective_from=(
            date.fromisoformat(str(member["effective_from"]))
            if member["effective_from"] is not None
            else None
        ),
        effective_to=(
            date.fromisoformat(str(member["effective_to"]))
            if member["effective_to"] is not None
            else None
        ),
    )
