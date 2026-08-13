from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stock_forecasting.application import Application, build_application


@dataclass(frozen=True)
class RuntimeSettings:
    database_url: str
    object_root: Path
    fixture_information_cutoff: datetime

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        database_url = os.environ.get("DATABASE_URL")
        object_root = os.environ.get("OBJECT_ROOT")
        cutoff_text = os.environ.get("FIXTURE_INFORMATION_CUTOFF")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        if not object_root:
            raise RuntimeError("OBJECT_ROOT is required")
        if not cutoff_text:
            raise RuntimeError("FIXTURE_INFORMATION_CUTOFF is required")
        cutoff = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise RuntimeError("FIXTURE_INFORMATION_CUTOFF must include a timezone")
        return cls(
            database_url=database_url,
            object_root=Path(object_root),
            fixture_information_cutoff=cutoff.astimezone(UTC),
        )

    def build_application(self) -> Application:
        return build_application(
            database_url=self.database_url,
            object_root=self.object_root,
        )
