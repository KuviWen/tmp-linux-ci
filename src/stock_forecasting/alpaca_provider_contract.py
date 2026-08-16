from __future__ import annotations

from stock_forecasting.market_data_provider_contract import (
    ProviderDistributionContract,
    ProviderUniverseIdentity,
    ProviderValidationContract,
)

ALPACA_PROVIDER_ID = "alpaca-market-data-basic"
ALPACA_CREDENTIAL_PROBE_CONTRACT_ID = "alpaca-credential-probe-v1"
ALPACA_LIVE_VALIDATION_CONTRACT_ID = "alpaca-ticket-07-live-v1"


AlpacaDistributionContract = ProviderDistributionContract
AlpacaValidationContract = ProviderValidationContract


def _alpaca_universe_identity() -> ProviderUniverseIdentity:
    from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest

    manifest = load_us_stock_pool_manifest()
    return ProviderUniverseIdentity(
        manifest_id=manifest.manifest_id,
        reference_graph_version_id=manifest.selection_evidence_version,
        listing_ids=tuple(listing.listing_id for listing in manifest.listings),
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
        universe_identity_loader=_alpaca_universe_identity,
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
