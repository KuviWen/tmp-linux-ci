from __future__ import annotations

from stock_forecasting.market_data_provider_contract import (
    ProviderDistributionContract,
    ProviderUniverseIdentity,
    ProviderValidationContract,
)

FINMIND_PROVIDER_ID = "finmind-free-api"
FINMIND_CREDENTIAL_PROBE_CONTRACT_ID = "finmind-credential-probe-v1"
FINMIND_LIVE_VALIDATION_CONTRACT_ID = "finmind-ticket-06-live-v1"
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"


def _distribution(dataset_name: str, policy_dataset_id: str) -> ProviderDistributionContract:
    return ProviderDistributionContract(
        policy_dataset_id=policy_dataset_id,
        distribution_id=dataset_name,
        distribution_url=FINMIND_DATA_URL,
    )


FINMIND_PRICE_DISTRIBUTION = _distribution("TaiwanStockPrice", "finmind-taiwan-stock-price")
FINMIND_TRADING_DATE_DISTRIBUTION = _distribution(
    "TaiwanStockTradingDate", "finmind-taiwan-trading-date"
)
FINMIND_DIVIDEND_RESULT_DISTRIBUTION = _distribution(
    "TaiwanStockDividendResult", "finmind-taiwan-dividend-result"
)
FINMIND_DELISTING_DISTRIBUTION = _distribution("TaiwanStockDelisting", "finmind-taiwan-delisting")
FINMIND_SPLIT_PRICE_DISTRIBUTION = _distribution(
    "TaiwanStockSplitPrice", "finmind-taiwan-split-price"
)
FINMIND_PROVIDER_DISTRIBUTIONS = (
    FINMIND_PRICE_DISTRIBUTION,
    FINMIND_TRADING_DATE_DISTRIBUTION,
    FINMIND_DIVIDEND_RESULT_DISTRIBUTION,
    FINMIND_DELISTING_DISTRIBUTION,
    FINMIND_SPLIT_PRICE_DISTRIBUTION,
)
FINMIND_REQUIRED_BUNDLE_DISTRIBUTIONS = FINMIND_PROVIDER_DISTRIBUTIONS[1:]
FINMIND_VALIDATION_DATASET_IDS = frozenset(
    distribution.distribution_id for distribution in FINMIND_PROVIDER_DISTRIBUTIONS
)


def _finmind_universe_identity() -> ProviderUniverseIdentity:
    from stock_forecasting.data_supply import load_taiwan_stock_pool_manifest

    manifest = load_taiwan_stock_pool_manifest()
    return ProviderUniverseIdentity(
        manifest_id=manifest.manifest_id,
        reference_graph_version_id=manifest.selection_evidence_version,
        listing_ids=tuple(listing.listing_id for listing in manifest.listings),
    )


FINMIND_VALIDATION_CONTRACTS = (
    ProviderValidationContract(
        contract_id=FINMIND_CREDENTIAL_PROBE_CONTRACT_ID,
        required_ticker_count=1,
        required_dataset_ids=frozenset({FINMIND_PRICE_DISTRIBUTION.distribution_id}),
    ),
    ProviderValidationContract(
        contract_id=FINMIND_LIVE_VALIDATION_CONTRACT_ID,
        required_ticker_count=10,
        required_dataset_ids=FINMIND_VALIDATION_DATASET_IDS,
        requires_symbol_lifecycle_probe=True,
        universe_identity_loader=_finmind_universe_identity,
    ),
)
FINMIND_VALIDATION_CONTRACT_IDS = frozenset(
    contract.contract_id for contract in FINMIND_VALIDATION_CONTRACTS
)
