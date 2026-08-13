import os

from dagster import Definitions, ResourceDefinition

from stock_forecasting.adapters.dagster import (
    FixtureRunner,
    xnas_fixture_eod_asset,
    xtai_fixture_eod_asset,
)
from stock_forecasting.runtime import RuntimeSettings
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand

settings = RuntimeSettings.from_environment()
application = settings.build_application()
authorization_acceptance_mode = os.environ.get("AUTHORIZATION_ACCEPTANCE_MODE")
xtai_trace_id = (
    "trace-p1-trace-auth-01-deployed-dagster-denied"
    if authorization_acceptance_mode == "denied"
    else "trace-p1-trace-tw-01"
)
xtai_idempotency_key = (
    "p1-trace-auth-01-deployed-dagster-denied"
    if authorization_acceptance_mode == "denied"
    else "p1-trace-tw-01"
)
fixture_command = FixtureEodCommand(
    information_cutoff=settings.fixture_information_cutoff,
    trace_id=xtai_trace_id,
    idempotency_key=xtai_idempotency_key,
)
xnas_fixture_command = FixtureEodCommand(
    information_cutoff=settings.fixture_information_cutoff,
    trace_id="trace-p1-trace-us-01",
    idempotency_key="p1-trace-us-01",
    market="XNAS",
)

defs = Definitions(
    assets=[xtai_fixture_eod_asset, xnas_fixture_eod_asset],
    resources={
        "fixture_runner": ResourceDefinition.hardcoded_resource(
            FixtureRunner(application, fixture_command)
        ),
        "xnas_fixture_runner": ResourceDefinition.hardcoded_resource(
            FixtureRunner(application, xnas_fixture_command)
        ),
    },
)
