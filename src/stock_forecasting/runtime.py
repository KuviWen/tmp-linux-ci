from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stock_forecasting.application import Application, build_application
from stock_forecasting.outbox import RelayFault


@dataclass(frozen=True)
class RuntimeSettings:
    database_url: str
    object_root: Path
    fixture_information_cutoff: datetime
    fixture_collection_observed_at: datetime

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        database_url = os.environ.get("DATABASE_URL")
        object_root = os.environ.get("OBJECT_ROOT")
        cutoff_text = os.environ.get("FIXTURE_INFORMATION_CUTOFF")
        observed_at_text = os.environ.get("FIXTURE_COLLECTION_OBSERVED_AT")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        if not object_root:
            raise RuntimeError("OBJECT_ROOT is required")
        if not cutoff_text:
            raise RuntimeError("FIXTURE_INFORMATION_CUTOFF is required")
        if not observed_at_text:
            raise RuntimeError("FIXTURE_COLLECTION_OBSERVED_AT is required")
        cutoff = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
        observed_at = datetime.fromisoformat(observed_at_text.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise RuntimeError("FIXTURE_INFORMATION_CUTOFF must include a timezone")
        if observed_at.tzinfo is None:
            raise RuntimeError("FIXTURE_COLLECTION_OBSERVED_AT must include a timezone")
        return cls(
            database_url=database_url,
            object_root=Path(object_root),
            fixture_information_cutoff=cutoff.astimezone(UTC),
            fixture_collection_observed_at=observed_at.astimezone(UTC),
        )

    def build_application(self, *, relay_fault: RelayFault | None = None) -> Application:
        return build_application(
            database_url=self.database_url,
            object_root=self.object_root,
            observed_at=self.fixture_collection_observed_at,
            relay_fault=relay_fault,
        )
