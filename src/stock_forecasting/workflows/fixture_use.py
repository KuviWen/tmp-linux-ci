from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from stock_forecasting.platform.state_store import StateStore

FixtureUseTarget = Literal[
    "production_route",
    "production_prediction_record",
    "formal_export",
    "model_promotion",
]


@dataclass(frozen=True)
class FixtureUseCommand:
    model_artifact_id: str
    target: FixtureUseTarget
    trace_id: str


class FixtureUseWorkflow:
    def __init__(
        self,
        *,
        state_store: StateStore,
    ) -> None:
        self._state_store = state_store

    def execute(self, command: FixtureUseCommand) -> dict[str, str]:
        decision = {
            "status": "blocked",
            "code": "fixture_use_forbidden",
            "target": command.target,
        }
        identity = f"{command.trace_id}/{command.target}/{command.model_artifact_id}"
        self._state_store.record_fixture_use_denial(
            event_id=str(uuid5(NAMESPACE_URL, f"fixture-denial-event/{identity}")),
            assessment_id=str(uuid5(NAMESPACE_URL, f"fixture-denial-health/{identity}")),
            action=command.target,
            trace_id=command.trace_id,
        )
        return decision
