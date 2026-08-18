from decimal import Decimal
from io import BytesIO

from stock_forecasting.formal_cost_scenario import (
    ObjectFormalCostScenarioVerifier,
    load_conservative_cost_scenario,
)


class MutableCostScenarioRepository:
    def __init__(self) -> None:
        self.serialized: bytes | None = None

    def open_by_id(self, object_id: str) -> BytesIO:
        if self.serialized is None:
            raise FileNotFoundError(object_id)
        return BytesIO(self.serialized)


def test_packaged_conservative_scenario_preserves_the_approved_cost_formulas() -> None:
    scenario = load_conservative_cost_scenario()

    assert scenario.schema_version == "formal-cost-scenario/v1"
    assert scenario.scenario_name == "conservative_v1"
    assert scenario.purpose == "research_stress_not_actual_execution_cost"
    assert scenario.approved_by == "owner-local"
    assert scenario.effective_from.isoformat() == "2026-08-19"
    assert scenario.cost_manifest_id.startswith("sha256:")
    assert {evidence.document_sha256 for evidence in scenario.source_evidence} == {
        "d42a786e873c757fc975e22c15aa70fecb6f233cb89abbc37a071d6e930c9420",
        "cfed684b2554e856022bc80c4883260ea1414c4ba79fc65304f7fc08cc780a7e",
        "68f0d2c95a3722a69606aa93a4962aa0e04fbbbf897453163157ceb6d7ae3ed9",
    }

    taiwan = scenario.market("XTAI")
    assert taiwan.commission_rate_each_side == Decimal("0.001425")
    assert taiwan.sell_tax_rate == Decimal("0.003")
    assert taiwan.slippage_rate_each_side == Decimal("0.001")
    assert taiwan.spread_handling == "included_in_slippage"
    assert taiwan.fixed_round_trip_rate == Decimal("0.00785")

    united_states = scenario.market("XNAS")
    assert united_states.commission_rate_each_side == Decimal("0")
    assert united_states.sell_tax_rate == Decimal("0")
    assert united_states.slippage_rate_each_side == Decimal("0.0005")
    assert united_states.spread_handling == "included_in_slippage"
    assert united_states.sec_sell_notional_rate == Decimal("0.0000206")
    assert united_states.finra_taf_per_sell_share == Decimal("0.000195")
    assert united_states.finra_taf_maximum == Decimal("9.79")
    assert united_states.cat_fee_per_equivalent_share_each_side == Decimal("0.000003")
    assert united_states.turnover_formula == (
        "net_return=gross_return-(round_trip_cost*turnover_fraction)"
    )


def test_object_verifier_only_accepts_the_installed_approved_manifest() -> None:
    scenario = load_conservative_cost_scenario()
    repository = MutableCostScenarioRepository()
    verifier = ObjectFormalCostScenarioVerifier(
        repository,
        approved_manifest_ids=frozenset({scenario.cost_manifest_id}),
    )

    assert verifier.verify_cost_scenario(scenario.cost_manifest_id) is False

    repository.serialized = scenario.serialized

    assert verifier.verify_cost_scenario(scenario.cost_manifest_id) is True
    assert verifier.verify_cost_scenario("sha256:" + "0" * 64) is False

    repository.serialized = scenario.serialized + b" "

    assert verifier.verify_cost_scenario(scenario.cost_manifest_id) is False
