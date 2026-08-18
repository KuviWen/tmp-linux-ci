from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from stock_forecasting.forecast_lab import (
    CandidateEvidenceBundle,
    ForecastLab,
    TrainingIntentRef,
)
from stock_forecasting.model_governance import (
    EvaluateBootstrapCandidate,
    GateDecision,
    HardGateEvidence,
    ModelLifecycle,
    RecordCandidate,
    RecordDevelopmentGateFailure,
)


@dataclass(frozen=True)
class BootstrapGovernanceCommand:
    command_id_prefix: str
    intent: TrainingIntentRef
    policy_version_id: str
    hard_gates: HardGateEvidence
    expected_version: int
    occurred_at: datetime


@dataclass(frozen=True)
class BootstrapGovernanceOutcome:
    status: Literal["awaiting_approval", "blocked"]
    candidate_bundle: CandidateEvidenceBundle | None
    gate_decision: GateDecision | None
    version: int


class BootstrapGovernanceWorkflow:
    def __init__(self, forecast_lab: ForecastLab, lifecycle: ModelLifecycle) -> None:
        self._forecast_lab = forecast_lab
        self._lifecycle = lifecycle

    def execute(self, command: BootstrapGovernanceCommand) -> BootstrapGovernanceOutcome:
        development = self._forecast_lab.develop(command.intent)
        if development.candidate_bundle is None:
            failure = self._lifecycle.execute(
                RecordDevelopmentGateFailure(
                    command_id=f"{command.command_id_prefix}:development-blocked",
                    model_family_id=command.intent.model_family_id,
                    candidate_id=f"blocked:{command.intent.training_intent_id}",
                    policy_version_id=command.policy_version_id,
                    failed_gates=development.blocked_reasons,
                    expected_version=command.expected_version,
                    occurred_at=command.occurred_at,
                )
            )
            return BootstrapGovernanceOutcome(
                status="blocked",
                candidate_bundle=None,
                gate_decision=failure.gate_decision,
                version=failure.version,
            )
        bundle = development.candidate_bundle
        recorded = self._lifecycle.execute(
            RecordCandidate(
                command_id=f"{command.command_id_prefix}:record-candidate",
                candidate_bundle=bundle,
                expected_version=command.expected_version,
                occurred_at=command.occurred_at,
            )
        )
        gate = self._lifecycle.execute(
            EvaluateBootstrapCandidate(
                command_id=f"{command.command_id_prefix}:evaluate-gate",
                model_family_id=bundle.model_family_id,
                candidate_id=bundle.candidate_id,
                policy_version_id=command.policy_version_id,
                hard_gates=command.hard_gates,
                expected_version=recorded.version,
                occurred_at=command.occurred_at,
            )
        )
        return BootstrapGovernanceOutcome(
            status="awaiting_approval" if gate.status == "gate_passed" else "blocked",
            candidate_bundle=bundle,
            gate_decision=gate.gate_decision,
            version=gate.version,
        )
