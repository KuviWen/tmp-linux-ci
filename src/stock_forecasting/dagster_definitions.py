from dagster import Definitions, ResourceDefinition

from stock_forecasting.adapters.dagster import FixtureRunner, xtai_fixture_eod_asset
from stock_forecasting.runtime import RuntimeSettings
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand

settings = RuntimeSettings.from_environment()
application = settings.build_application()
fixture_command = FixtureEodCommand(
    information_cutoff=settings.fixture_information_cutoff,
    trace_id="trace-p1-trace-tw-01",
    idempotency_key="p1-trace-tw-01",
)

defs = Definitions(
    assets=[xtai_fixture_eod_asset],
    resources={
        "fixture_runner": ResourceDefinition.hardcoded_resource(
            FixtureRunner(application, fixture_command)
        )
    },
)
