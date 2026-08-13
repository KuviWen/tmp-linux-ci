from dataclasses import dataclass
from typing import cast

from dagster import AssetExecutionContext, asset

from stock_forecasting.application import Application
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand, FixtureEodOutcome


@dataclass(frozen=True)
class FixtureRunner:
    application: Application
    command: FixtureEodCommand

    def run(self) -> FixtureEodOutcome:
        return self.application.run_fixture_eod(self.command)


def _run_fixture(context: AssetExecutionContext, resource_key: str) -> dict[str, str]:
    runner = cast(FixtureRunner, getattr(context.resources, resource_key))
    outcome = runner.run()
    result = {
        "status": outcome.status,
        "execution_purpose": outcome.execution_purpose,
        "market": outcome.market,
        "listing_id": outcome.listing_id,
        "dataset_version_id": outcome.dataset_version_id,
        "feature_snapshot_id": outcome.feature_snapshot_id,
        "model_artifact_id": outcome.model_artifact_id,
        "serving_assignment_id": outcome.serving_assignment_id,
    }
    context.add_output_metadata(result)
    return result


@asset(name="xtai_fixture_eod", required_resource_keys={"fixture_runner"})
def xtai_fixture_eod_asset(context: AssetExecutionContext) -> dict[str, str]:
    return _run_fixture(context, "fixture_runner")


@asset(name="xnas_fixture_eod", required_resource_keys={"xnas_fixture_runner"})
def xnas_fixture_eod_asset(context: AssetExecutionContext) -> dict[str, str]:
    return _run_fixture(context, "xnas_fixture_runner")
