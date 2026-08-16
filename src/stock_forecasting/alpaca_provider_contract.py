from __future__ import annotations

from dataclasses import dataclass

ALPACA_PROVIDER_ID = "alpaca-market-data-basic"


@dataclass(frozen=True)
class AlpacaDistributionContract:
    policy_dataset_id: str
    distribution_id: str
    distribution_url: str


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
