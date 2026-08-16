from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDistributionContract:
    policy_dataset_id: str
    distribution_id: str
    distribution_url: str

    def __post_init__(self) -> None:
        if (
            not self.policy_dataset_id
            or not self.distribution_id
            or not self.distribution_url.startswith("https://")
        ):
            raise ValueError("provider_distribution_contract_invalid")


@dataclass(frozen=True)
class ProviderUniverseIdentity:
    manifest_id: str
    reference_graph_version_id: str
    listing_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProviderValidationContract:
    contract_id: str
    required_ticker_count: int
    required_dataset_ids: frozenset[str]
    minimum_pagination_pages: int | None = None
    requires_symbol_lifecycle_probe: bool = False
    universe_identity_loader: Callable[[], ProviderUniverseIdentity] | None = None

    def accepts_passed_evidence(
        self,
        *,
        ticker_count: int | None,
        pagination_pages: int | None,
        dataset_ids: tuple[str, ...],
        symbol_lifecycle_probe: str | None,
        universe_manifest_id: str | None,
        reference_graph_version_id: str | None,
        listing_ids: tuple[str, ...],
    ) -> bool:
        if self.universe_identity_loader is not None:
            universe = self.universe_identity_loader()
            universe_identity_matches = (
                universe_manifest_id == universe.manifest_id
                and reference_graph_version_id == universe.reference_graph_version_id
                and listing_ids == universe.listing_ids
            )
        else:
            universe_identity_matches = (
                universe_manifest_id is None
                and reference_graph_version_id is None
                and not listing_ids
            )
        return (
            ticker_count == self.required_ticker_count
            and len(dataset_ids) == len(self.required_dataset_ids)
            and frozenset(dataset_ids) == self.required_dataset_ids
            and (
                self.minimum_pagination_pages is None
                or (
                    pagination_pages is not None
                    and pagination_pages >= self.minimum_pagination_pages
                )
            )
            and (not self.requires_symbol_lifecycle_probe or symbol_lifecycle_probe == "passed")
            and universe_identity_matches
        )


def provider_validation_contract(contract_id: str) -> ProviderValidationContract | None:
    from stock_forecasting.alpaca_provider_contract import ALPACA_VALIDATION_CONTRACTS
    from stock_forecasting.finmind_provider_contract import FINMIND_VALIDATION_CONTRACTS

    return next(
        (
            contract
            for contract in (*ALPACA_VALIDATION_CONTRACTS, *FINMIND_VALIDATION_CONTRACTS)
            if contract.contract_id == contract_id
        ),
        None,
    )


def provider_validation_dataset_ids() -> frozenset[str]:
    from stock_forecasting.alpaca_provider_contract import ALPACA_VALIDATION_DATASET_IDS
    from stock_forecasting.finmind_provider_contract import FINMIND_VALIDATION_DATASET_IDS

    return ALPACA_VALIDATION_DATASET_IDS | FINMIND_VALIDATION_DATASET_IDS
