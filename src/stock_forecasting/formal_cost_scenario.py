from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from typing import Any, BinaryIO, Literal, Protocol, cast

from stock_forecasting.content_address import canonical_json_bytes, sha256_id

FORMAL_COST_SCENARIO_SCHEMA = "formal-cost-scenario/v1"

Market = Literal["XTAI", "XNAS"]


class CostScenarioObjectRepository(Protocol):
    def open_by_id(self, object_id: str) -> BinaryIO: ...


@dataclass(frozen=True)
class MarketCostScenario:
    market: Market
    commission_rate_each_side: Decimal
    sell_tax_rate: Decimal
    slippage_rate_each_side: Decimal
    spread_handling: str
    turnover_formula: str
    sec_sell_notional_rate: Decimal
    finra_taf_per_sell_share: Decimal
    finra_taf_maximum: Decimal
    cat_fee_per_equivalent_share_each_side: Decimal

    @property
    def fixed_round_trip_rate(self) -> Decimal:
        return (
            self.commission_rate_each_side * 2
            + self.sell_tax_rate
            + self.slippage_rate_each_side * 2
        )


@dataclass(frozen=True)
class CostSourceEvidence:
    publisher: str
    url: str
    retrieved_on: date
    evidence_file: str
    document_sha256: str
    supports: tuple[str, ...]


@dataclass(frozen=True)
class FormalCostScenario:
    schema_version: str
    scenario_name: str
    purpose: str
    approved_by: str
    approved_at: datetime
    effective_from: date
    markets: tuple[MarketCostScenario, ...]
    source_evidence: tuple[CostSourceEvidence, ...]
    serialized: bytes
    cost_manifest_id: str

    def market(self, market: Market) -> MarketCostScenario:
        try:
            return next(item for item in self.markets if item.market == market)
        except StopIteration as error:
            raise KeyError(market) from error

    @classmethod
    def from_serialized(
        cls,
        cost_manifest_id: str,
        serialized: bytes,
    ) -> FormalCostScenario:
        try:
            payload = json.loads(serialized)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("formal_cost_scenario_invalid") from error
        if not isinstance(payload, dict) or serialized != canonical_json_bytes(payload):
            raise ValueError("formal_cost_scenario_invalid")
        _expect_exact_keys(
            payload,
            {
                "schema_version",
                "scenario_name",
                "purpose",
                "approved_by",
                "approved_at",
                "effective_from",
                "markets",
                "source_evidence",
            },
        )
        if (
            payload["schema_version"] != FORMAL_COST_SCENARIO_SCHEMA
            or payload["scenario_name"] != "conservative_v1"
            or payload["purpose"] != "research_stress_not_actual_execution_cost"
            or payload["approved_by"] != "owner-local"
            or sha256_id(serialized) != cost_manifest_id
        ):
            raise ValueError("formal_cost_scenario_invalid")
        approved_at = _parse_instant(payload["approved_at"])
        effective_from = _parse_date(payload["effective_from"])
        markets_payload = payload["markets"]
        if not isinstance(markets_payload, list) or len(markets_payload) != 2:
            raise ValueError("formal_cost_scenario_invalid")
        markets = tuple(
            _load_market(cast(dict[str, Any], item))
            for item in markets_payload
            if isinstance(item, dict)
        )
        if {item.market for item in markets} != {"XTAI", "XNAS"}:
            raise ValueError("formal_cost_scenario_invalid")
        source_evidence = _load_source_evidence(payload["source_evidence"])
        return cls(
            schema_version=FORMAL_COST_SCENARIO_SCHEMA,
            scenario_name="conservative_v1",
            purpose="research_stress_not_actual_execution_cost",
            approved_by="owner-local",
            approved_at=approved_at,
            effective_from=effective_from,
            markets=markets,
            source_evidence=source_evidence,
            serialized=serialized,
            cost_manifest_id=cost_manifest_id,
        )


class ObjectFormalCostScenarioVerifier:
    def __init__(
        self,
        objects: CostScenarioObjectRepository,
        *,
        approved_manifest_ids: frozenset[str],
    ) -> None:
        self._objects = objects
        self._approved_manifest_ids = approved_manifest_ids

    def verify_cost_scenario(self, cost_manifest_id: str) -> bool:
        if cost_manifest_id not in self._approved_manifest_ids:
            return False
        try:
            serialized = self._objects.open_by_id(cost_manifest_id).read()
            FormalCostScenario.from_serialized(cost_manifest_id, serialized)
        except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            return False
        return True


def load_conservative_cost_scenario() -> FormalCostScenario:
    packaged = (
        files("stock_forecasting")
        .joinpath("manifests/formal_cost_scenario_conservative_v1.json")
        .read_bytes()
    )
    try:
        serialized = canonical_json_bytes(json.loads(packaged))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("formal_cost_scenario_invalid") from error
    return FormalCostScenario.from_serialized(sha256_id(serialized), serialized)


def _load_market(payload: dict[str, Any]) -> MarketCostScenario:
    _expect_exact_keys(
        payload,
        {
            "market",
            "commission_rate_each_side",
            "sell_tax_rate",
            "slippage_rate_each_side",
            "spread_handling",
            "turnover_formula",
            "regulatory_fees",
        },
    )
    market = payload["market"]
    if market not in {"XTAI", "XNAS"}:
        raise ValueError("formal_cost_scenario_invalid")
    if (
        payload["spread_handling"] != "included_in_slippage"
        or payload["turnover_formula"]
        != "net_return=gross_return-(round_trip_cost*turnover_fraction)"
    ):
        raise ValueError("formal_cost_scenario_invalid")
    regulatory = payload["regulatory_fees"]
    if not isinstance(regulatory, dict):
        raise ValueError("formal_cost_scenario_invalid")
    _expect_exact_keys(
        regulatory,
        {
            "sec_sell_notional_rate",
            "finra_taf_per_sell_share",
            "finra_taf_maximum",
            "cat_fee_per_equivalent_share_each_side",
        },
    )
    scenario = MarketCostScenario(
        market=cast(Market, market),
        commission_rate_each_side=_decimal(payload["commission_rate_each_side"]),
        sell_tax_rate=_decimal(payload["sell_tax_rate"]),
        slippage_rate_each_side=_decimal(payload["slippage_rate_each_side"]),
        spread_handling=payload["spread_handling"],
        turnover_formula=payload["turnover_formula"],
        sec_sell_notional_rate=_decimal(regulatory["sec_sell_notional_rate"]),
        finra_taf_per_sell_share=_decimal(regulatory["finra_taf_per_sell_share"]),
        finra_taf_maximum=_decimal(regulatory["finra_taf_maximum"]),
        cat_fee_per_equivalent_share_each_side=_decimal(
            regulatory["cat_fee_per_equivalent_share_each_side"]
        ),
    )
    _validate_market_values(scenario)
    return scenario


def _validate_market_values(scenario: MarketCostScenario) -> None:
    expected = {
        "XTAI": (
            Decimal("0.001425"),
            Decimal("0.003"),
            Decimal("0.001"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        ),
        "XNAS": (
            Decimal("0"),
            Decimal("0"),
            Decimal("0.0005"),
            Decimal("0.0000206"),
            Decimal("0.000195"),
            Decimal("9.79"),
            Decimal("0.000003"),
        ),
    }
    actual = (
        scenario.commission_rate_each_side,
        scenario.sell_tax_rate,
        scenario.slippage_rate_each_side,
        scenario.sec_sell_notional_rate,
        scenario.finra_taf_per_sell_share,
        scenario.finra_taf_maximum,
        scenario.cat_fee_per_equivalent_share_each_side,
    )
    if actual != expected[scenario.market]:
        raise ValueError("formal_cost_scenario_invalid")


def _load_source_evidence(value: object) -> tuple[CostSourceEvidence, ...]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("formal_cost_scenario_invalid")
    required_urls = {
        "https://www.twse.com.tw/en/about/company/guide.html",
        "https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf",
        "https://alpaca.markets/support/commission-clearing-fees",
    }
    urls: set[str] = set()
    evidence: list[CostSourceEvidence] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("formal_cost_scenario_invalid")
        _expect_exact_keys(
            item,
            {
                "publisher",
                "url",
                "retrieved_on",
                "evidence_file",
                "document_sha256",
                "supports",
            },
        )
        if not isinstance(item["publisher"], str) or not item["publisher"]:
            raise ValueError("formal_cost_scenario_invalid")
        retrieved_on = _parse_date(item["retrieved_on"])
        supports = item["supports"]
        if (
            not isinstance(supports, list)
            or not supports
            or not all(isinstance(supported, str) and supported for supported in supports)
        ):
            raise ValueError("formal_cost_scenario_invalid")
        if not isinstance(item["url"], str):
            raise ValueError("formal_cost_scenario_invalid")
        if not isinstance(item["evidence_file"], str) or not item["evidence_file"]:
            raise ValueError("formal_cost_scenario_invalid")
        document_sha256 = item["document_sha256"]
        if (
            not isinstance(document_sha256, str)
            or len(document_sha256) != 64
            or any(character not in "0123456789abcdef" for character in document_sha256)
        ):
            raise ValueError("formal_cost_scenario_invalid")
        urls.add(item["url"])
        evidence.append(
            CostSourceEvidence(
                publisher=item["publisher"],
                url=item["url"],
                retrieved_on=retrieved_on,
                evidence_file=item["evidence_file"],
                document_sha256=document_sha256,
                supports=tuple(cast(list[str], supports)),
            )
        )
    if urls != required_urls:
        raise ValueError("formal_cost_scenario_invalid")
    return tuple(evidence)


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("formal_cost_scenario_invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("formal_cost_scenario_invalid") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("formal_cost_scenario_invalid")
    return parsed


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("formal_cost_scenario_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("formal_cost_scenario_invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("formal_cost_scenario_invalid")
    return parsed


def _parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("formal_cost_scenario_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("formal_cost_scenario_invalid") from error


def _expect_exact_keys(payload: dict[str, Any], expected: set[str]) -> None:
    if set(payload) != expected:
        raise ValueError("formal_cost_scenario_invalid")
