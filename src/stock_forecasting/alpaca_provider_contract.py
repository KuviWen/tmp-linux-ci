from __future__ import annotations

from dataclasses import dataclass

ALPACA_PROVIDER_ID = "alpaca-market-data-basic"
ALPACA_CREDENTIAL_PROBE_CONTRACT_ID = "alpaca-credential-probe-v1"
ALPACA_LIVE_VALIDATION_CONTRACT_ID = "alpaca-ticket-07-live-v1"


@dataclass(frozen=True)
class AlpacaDistributionContract:
    policy_dataset_id: str
    distribution_id: str
    distribution_url: str


@dataclass(frozen=True)
class AlpacaValidationContract:
    contract_id: str
    required_ticker_count: int
    required_dataset_ids: frozenset[str]
    minimum_pagination_pages: int | None = None
    requires_symbol_lifecycle_probe: bool = False
    requires_versioned_universe: bool = False

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
        if self.requires_versioned_universe:
            from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest

            manifest = load_us_stock_pool_manifest()
            universe_identity_matches = (
                universe_manifest_id == manifest.manifest_id
                and reference_graph_version_id == manifest.selection_evidence_version
                and listing_ids == tuple(listing.listing_id for listing in manifest.listings)
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


ALPACA_BARS_DISTRIBUTION = AlpacaDistributionContract(
    policy_dataset_id="alpaca-us-stock-bars",
    distribution_id="alpaca-us-stock-bars-v2",
    distribution_url="https://data.alpaca.markets/v2/stocks/bars",
)
ALPACA_CORPORATE_ACTIONS_DISTRIBUTION = AlpacaDistributionContract(
    policy_dataset_id="alpaca-us-corporate-actions-v1",
    distribution_id="alpaca-us-corporate-actions-v1",
    distribution_url="https://data.alpaca.markets/v1/corporate-actions",
)
ALPACA_TRADING_CALENDAR_DISTRIBUTION = AlpacaDistributionContract(
    policy_dataset_id="alpaca-us-trading-calendar-v2",
    distribution_id="alpaca-us-trading-calendar-v2",
    distribution_url="https://paper-api.alpaca.markets/v2/calendar",
)

ALPACA_PROVIDER_DISTRIBUTIONS = (
    ALPACA_BARS_DISTRIBUTION,
    ALPACA_CORPORATE_ACTIONS_DISTRIBUTION,
    ALPACA_TRADING_CALENDAR_DISTRIBUTION,
)
ALPACA_REQUIRED_BUNDLE_DISTRIBUTIONS = (
    ALPACA_CORPORATE_ACTIONS_DISTRIBUTION,
    ALPACA_TRADING_CALENDAR_DISTRIBUTION,
)
ALPACA_CREDENTIAL_VALIDATION_URL = (
    f"{ALPACA_BARS_DISTRIBUTION.distribution_url.removesuffix('/bars')}/AAPL/bars"
)
ALPACA_VALIDATION_DATASET_IDS = frozenset(
    distribution.distribution_id for distribution in ALPACA_PROVIDER_DISTRIBUTIONS
)
ALPACA_VALIDATION_CONTRACTS = (
    AlpacaValidationContract(
        contract_id=ALPACA_CREDENTIAL_PROBE_CONTRACT_ID,
        required_ticker_count=1,
        required_dataset_ids=frozenset({ALPACA_BARS_DISTRIBUTION.distribution_id}),
    ),
    AlpacaValidationContract(
        contract_id=ALPACA_LIVE_VALIDATION_CONTRACT_ID,
        required_ticker_count=10,
        required_dataset_ids=ALPACA_VALIDATION_DATASET_IDS,
        minimum_pagination_pages=2,
        requires_symbol_lifecycle_probe=True,
        requires_versioned_universe=True,
    ),
)
ALPACA_VALIDATION_CONTRACT_IDS = frozenset(
    contract.contract_id for contract in ALPACA_VALIDATION_CONTRACTS
)


def alpaca_validation_contract(contract_id: str) -> AlpacaValidationContract | None:
    return next(
        (
            contract
            for contract in ALPACA_VALIDATION_CONTRACTS
            if contract.contract_id == contract_id
        ),
        None,
    )
