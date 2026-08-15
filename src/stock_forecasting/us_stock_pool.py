from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from typing import Literal, cast
from uuid import UUID

from stock_forecasting.data_supply import ExternalSecurityAlias

USCoverageCase = Literal[
    "ordinary_share",
    "share_class",
    "adr",
    "ticker_change",
    "company_action",
    "suspension",
    "historical_delisting",
]


@dataclass(frozen=True)
class USStockPoolListing:
    listing_id: str
    issuer_id: str
    security_id: str
    market: Literal["XNAS", "XNYS"]
    security_kind: Literal["ordinary_share", "adr"]
    external_security_code: str
    external_aliases: tuple[ExternalSecurityAlias, ...]
    coverage_cases: frozenset[USCoverageCase]
    selection_evidence_urls: tuple[str, ...]

    def __post_init__(self) -> None:
        UUID(self.listing_id)
        UUID(self.issuer_id)
        UUID(self.security_id)
        alias_codes = {alias.security_code for alias in self.external_aliases}
        if (
            self.market not in {"XNAS", "XNYS"}
            or self.security_kind not in {"ordinary_share", "adr"}
            or not self.external_aliases
            or self.external_security_code not in alias_codes
            or (len(alias_codes) > 1) != ("ticker_change" in self.coverage_cases)
            or not self.selection_evidence_urls
            or any(not url.startswith("https://") for url in self.selection_evidence_urls)
        ):
            raise ValueError("us_stock_pool_listing_invalid")
        ordered_aliases = sorted(
            self.external_aliases,
            key=lambda alias: alias.valid_from or date.min,
        )
        if any(
            (previous.valid_to or date.max) >= (following.valid_from or date.min)
            for previous, following in zip(ordered_aliases, ordered_aliases[1:], strict=False)
        ):
            raise ValueError("us_stock_pool_external_alias_ambiguous")


@dataclass(frozen=True)
class USMarketCalendarEvidence:
    session_date: date
    open_time: str
    close_time: str
    source_url: str

    def __post_init__(self) -> None:
        if (
            not self.source_url.startswith("https://")
            or self.open_time != "09:30"
            or self.close_time >= "16:00"
        ):
            raise ValueError("us_stock_pool_calendar_evidence_invalid")


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
        ):
            raise ValueError("us_source_bundle_member_invalid")

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
        }


@dataclass(frozen=True)
class ZeroFeeAuthenticatedSourceBasis:
    source_basis_id: str
    basis_type: Literal["zero_fee_plan"]
    provider_id: str
    plan_id: str
    principal_classification: str
    credential_kind: Literal["api_key_pair"]
    account_required: Literal[True]
    fee_required: Literal[False]
    terms_url: str
    terms_content_sha256: str | None
    qualification_status: Literal["candidate_terms_not_archived"]
    members: tuple[ZeroFeeSourceBundleMember, ...]

    def __post_init__(self) -> None:
        if (
            not self.source_basis_id
            or self.basis_type != "zero_fee_plan"
            or not self.provider_id
            or not self.plan_id
            or not self.principal_classification
            or self.credential_kind != "api_key_pair"
            or self.account_required is not True
            or self.fee_required is not False
            or not self.terms_url.startswith("https://")
            or not isinstance(self.terms_content_sha256, str)
            or len(self.terms_content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.terms_content_sha256)
            or self.qualification_status != "candidate_terms_not_archived"
            or not self.members
            or len({member.dataset_id for member in self.members}) != len(self.members)
        ):
            raise ValueError("us_zero_fee_source_basis_invalid")

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
        }


@dataclass(frozen=True)
class UnitedStatesStockPoolManifest:
    manifest_id: str
    selection_evidence_version: str
    selection_as_of: date
    taiwan_target: int
    united_states_target: int
    market_calendar_cases: frozenset[Literal["half_day_session"]]
    market_calendar_evidence: USMarketCalendarEvidence
    source_basis: ZeroFeeAuthenticatedSourceBasis
    formal_qualification_artifact_id: str | None
    historical_availability_claim_id: str | None
    listings: tuple[USStockPoolListing, ...]

    def __post_init__(self) -> None:
        if (
            len(self.listings) != self.united_states_target
            or len({listing.listing_id for listing in self.listings}) != len(self.listings)
            or len({listing.security_id for listing in self.listings}) != len(self.listings)
            or self.market_calendar_cases != frozenset({"half_day_session"})
        ):
            raise ValueError("us_stock_pool_manifest_invalid")

    @property
    def market_targets(self) -> dict[str, int]:
        return {"XTAI": self.taiwan_target, "US": self.united_states_target}

    @property
    def formally_qualified(self) -> bool:
        return (
            self.formal_qualification_artifact_id is not None
            and self.historical_availability_claim_id is not None
            and self.source_basis.terms_content_sha256 is not None
        )


def load_us_stock_pool_manifest() -> UnitedStatesStockPoolManifest:
    manifest_path = files("stock_forecasting").joinpath(
        "manifests/p2_us_stock_pool_contract_v1.json"
    )
    payload = cast(dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8")))
    targets = cast(dict[str, int], payload["market_targets"])
    calendar = cast(dict[str, object], payload["market_calendar_evidence"])
    source_basis = cast(dict[str, object], payload["source_basis"])
    return UnitedStatesStockPoolManifest(
        manifest_id=str(payload["manifest_id"]),
        selection_evidence_version=str(payload["selection_evidence_version"]),
        selection_as_of=date.fromisoformat(str(payload["selection_as_of"])),
        taiwan_target=targets["XTAI"],
        united_states_target=targets["US"],
        market_calendar_cases=frozenset({"half_day_session"}),
        market_calendar_evidence=USMarketCalendarEvidence(
            session_date=date.fromisoformat(str(calendar["session_date"])),
            open_time=str(calendar["open_time"]),
            close_time=str(calendar["close_time"]),
            source_url=str(calendar["source_url"]),
        ),
        source_basis=ZeroFeeAuthenticatedSourceBasis(
            source_basis_id=str(source_basis["source_basis_id"]),
            basis_type="zero_fee_plan",
            provider_id=str(source_basis["provider_id"]),
            plan_id=str(source_basis["plan_id"]),
            principal_classification=str(source_basis["principal_classification"]),
            credential_kind="api_key_pair",
            account_required=True,
            fee_required=False,
            terms_url=str(source_basis["terms_url"]),
            terms_content_sha256=cast(str | None, source_basis["terms_content_sha256"]),
            qualification_status="candidate_terms_not_archived",
            members=tuple(
                ZeroFeeSourceBundleMember(
                    provider_id=str(member["provider_id"]),
                    dataset_id=str(member["dataset_id"]),
                    distribution_url=str(member["distribution_url"]),
                    qualification_scope=str(member["qualification_scope"]),
                    schema_version=str(member["schema_version"]),
                    price_semantics=cast(
                        Literal["unadjusted"] | None,
                        member["price_semantics"],
                    ),
                    qualification_status=cast(
                        Literal[
                            "candidate_terms_not_archived",
                            "candidate_scope_limited",
                        ],
                        member["qualification_status"],
                    ),
                    materialization_role=cast(
                        Literal[
                            "required_observation",
                            "supplemental_qualification_reference",
                        ],
                        member["materialization_role"],
                    ),
                    known_gaps=tuple(cast(list[str], member["known_gaps"])),
                )
                for member in cast(list[dict[str, object]], source_basis["members"])
            ),
        ),
        formal_qualification_artifact_id=cast(
            str | None,
            payload["formal_qualification_artifact_id"],
        ),
        historical_availability_claim_id=cast(
            str | None,
            payload["historical_availability_claim_id"],
        ),
        listings=tuple(
            USStockPoolListing(
                listing_id=str(listing["listing_id"]),
                issuer_id=str(listing["issuer_id"]),
                security_id=str(listing["security_id"]),
                market=cast(Literal["XNAS", "XNYS"], listing["market"]),
                security_kind=cast(
                    Literal["ordinary_share", "adr"],
                    listing["security_kind"],
                ),
                external_security_code=str(listing["external_security_code"]),
                external_aliases=tuple(
                    ExternalSecurityAlias(
                        security_code=str(alias["security_code"]),
                        security_name=str(alias["security_name"]),
                        valid_from=(
                            date.fromisoformat(str(alias["valid_from"]))
                            if alias["valid_from"] is not None
                            else None
                        ),
                        valid_to=(
                            date.fromisoformat(str(alias["valid_to"]))
                            if alias["valid_to"] is not None
                            else None
                        ),
                    )
                    for alias in cast(list[dict[str, object]], listing["external_aliases"])
                ),
                coverage_cases=frozenset(cast(list[USCoverageCase], listing["coverage_cases"])),
                selection_evidence_urls=tuple(cast(list[str], listing["selection_evidence_urls"])),
            )
            for listing in cast(list[dict[str, object]], payload["listings"])
        ),
    )
