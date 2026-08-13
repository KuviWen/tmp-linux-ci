from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

from stock_forecasting.operations_control import OperationsControl
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.research_query import ResearchQuery
from stock_forecasting.security_audit import SecurityAudit
from stock_forecasting.workflows.fixture_eod import (
    FixtureEodCommand,
    FixtureEodOutcome,
    FixtureEodWorkflow,
)
from stock_forecasting.workflows.fixture_use import FixtureUseCommand, FixtureUseWorkflow


class Application:
    def __init__(
        self,
        *,
        observed_at: datetime | None,
        object_root: Path,
        database_url: str,
        create_schema: bool,
    ) -> None:
        self.state_store = StateStore(database_url, create_schema=create_schema)
        self.research_query = ResearchQuery(self.state_store)
        self.security_audit = SecurityAudit(self.state_store)
        self.operations_control = OperationsControl(self.state_store)
        self.object_repository = FilesystemObjectRepository(object_root)
        self._fixture_eod = FixtureEodWorkflow(
            self.state_store,
            observed_at=observed_at,
            object_repository=self.object_repository,
        )
        self._fixture_use = FixtureUseWorkflow(
            state_store=self.state_store,
        )

    def run_fixture_eod(self, command: FixtureEodCommand) -> FixtureEodOutcome:
        return self._fixture_eod.execute(command)

    def attempt_fixture_use(self, command: FixtureUseCommand) -> dict[str, str]:
        return self._fixture_use.execute(command)


def build_test_application(
    *,
    observed_at: datetime | None = None,
    object_root: Path | None = None,
    database_url: str | None = None,
) -> Application:
    root = object_root or Path(mkdtemp(prefix="stock-forecasting-objects-"))
    resolved_database_url = database_url or "sqlite+pysqlite:///:memory:"
    return Application(
        observed_at=observed_at,
        object_root=root,
        database_url=resolved_database_url,
        create_schema=True,
    )


def build_application(
    *,
    database_url: str,
    object_root: Path,
    observed_at: datetime | None = None,
) -> Application:
    return Application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        create_schema=False,
    )
