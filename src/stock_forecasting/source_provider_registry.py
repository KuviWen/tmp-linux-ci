from __future__ import annotations

from dataclasses import dataclass

from stock_forecasting.alpaca_provider_contract import (
    ALPACA_PROVIDER_DISTRIBUTIONS,
    ALPACA_PROVIDER_ID,
)
from stock_forecasting.data_supply import (
    PRICE_RESEARCH_REQUIRED_USES,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.finmind_provider_contract import (
    FINMIND_PROVIDER_DISTRIBUTIONS,
    FINMIND_PROVIDER_ID,
)
from stock_forecasting.market_data_provider_contract import ProviderDistributionContract
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest
from stock_forecasting.zero_fee_source import CredentialKind, ZeroFeeAuthenticatedSourceBasis


@dataclass(frozen=True)
class SourceCredentialProviderContract:
    provider_id: str
    display_name: str
    credential_kind: CredentialKind
    required_fields: tuple[str, ...]
    registration_url: str
    key_management_url: str
    source_basis: ZeroFeeAuthenticatedSourceBasis
    distributions: tuple[ProviderDistributionContract, ...]

    def __post_init__(self) -> None:
        if (
            self.provider_id != self.source_basis.provider_id
            or self.credential_kind != self.source_basis.credential_kind
            or not self.display_name
            or not self.required_fields
            or len(set(self.required_fields)) != len(self.required_fields)
            or not self.registration_url.startswith("https://")
            or not self.key_management_url.startswith("https://")
            or [member.dataset_id for member in self.source_basis.members]
            != [distribution.distribution_id for distribution in self.distributions]
        ):
            raise ValueError("source_credential_provider_contract_invalid")

    def as_payload(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "credential_kind": self.credential_kind,
            "source_basis": self.source_basis.as_payload(),
            "required_uses": sorted(PRICE_RESEARCH_REQUIRED_USES),
            "required_fields": list(self.required_fields),
            "registration_url": self.registration_url,
            "key_management_url": self.key_management_url,
        }


def source_credential_provider_contracts() -> tuple[SourceCredentialProviderContract, ...]:
    return (
        SourceCredentialProviderContract(
            provider_id=ALPACA_PROVIDER_ID,
            display_name="Alpaca Market Data Basic",
            credential_kind="api_key_pair",
            required_fields=("api_key_id", "api_secret_key"),
            registration_url="https://app.alpaca.markets/signup",
            key_management_url="https://app.alpaca.markets/paper/dashboard/overview",
            source_basis=load_us_stock_pool_manifest().source_basis,
            distributions=ALPACA_PROVIDER_DISTRIBUTIONS,
        ),
        SourceCredentialProviderContract(
            provider_id=FINMIND_PROVIDER_ID,
            display_name="FinMind Free API",
            credential_kind="bearer_token",
            required_fields=("token",),
            registration_url="https://finmindtrade.com/analysis/#/register",
            key_management_url="https://finmindtrade.com/analysis/#/user",
            source_basis=load_taiwan_stock_pool_manifest().authenticated_source_basis,
            distributions=FINMIND_PROVIDER_DISTRIBUTIONS,
        ),
    )


def source_credential_provider_contract(
    provider_id: str,
) -> SourceCredentialProviderContract | None:
    return next(
        (
            contract
            for contract in source_credential_provider_contracts()
            if contract.provider_id == provider_id
        ),
        None,
    )
