from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from stock_forecasting.platform.object_repository import (
    FilesystemObjectRepository,
    ObjectRef,
)

P1_TRACE_IDS = (
    "P1-ENTRY-01",
    "P1-TRACE-TW-01",
    "P1-TRACE-US-01",
    "P1-TRACE-OUTBOX-01",
    "P1-TRACE-AUTH-01",
    "P1-EXIT-01",
    "GATE-POLICY-01",
    "GATE-PIT-01",
    "GATE-DATA-01",
    "GATE-MODEL-01",
    "GATE-SEC-01",
    "GATE-OPS-01",
    "GATE-DEPLOY-01",
    "GATE-UX-01",
)

P1_HARD_GATE_OWNERS = {
    "GATE-POLICY-01": "source_steward",
    "GATE-PIT-01": "data_owner",
    "GATE-DATA-01": "data_owner",
    "GATE-MODEL-01": "model_governor",
    "GATE-SEC-01": "security_owner",
    "GATE-OPS-01": "operations_owner",
    "GATE-DEPLOY-01": "platform_owner",
    "GATE-UX-01": "research_owner",
}


@dataclass(frozen=True)
class P1GateResult:
    trace_id: str
    status: str
    reason: str
    owner: str


@dataclass(frozen=True)
class P1AcceptanceEvaluation:
    attempt_id: str
    created_at: datetime
    git_commit: str
    image_digest: str
    deployment_digest: str
    migration_digest: str
    fixture_digests: Mapping[str, str]
    source_policy_ids: tuple[str, ...]
    manifest_ids: tuple[str, ...]
    contract_results: Mapping[str, object]
    end_to_end_ids: tuple[str, ...]
    scenario_results: Mapping[str, object]
    failure_evidence: tuple[Mapping[str, object], ...]
    rest_golden_digest: str
    ui_golden_digest: str
    restart_results: Mapping[str, object]
    resource_smoke: Mapping[str, object]
    gate_results: tuple[P1GateResult, ...]
    previous_bundle_reference: str | None
    reproduction_command: str


class P1AcceptanceBundlePublisher:
    def __init__(self, object_repository: FilesystemObjectRepository) -> None:
        self._object_repository = object_repository

    def publish(self, evaluation: P1AcceptanceEvaluation) -> ObjectRef:
        supplied_gate_results = {result.trace_id: result for result in evaluation.gate_results}
        gate_results = tuple(
            supplied_gate_results.get(trace_id)
            or P1GateResult(
                trace_id=trace_id,
                status="blocked",
                reason="gate_result_missing",
                owner=owner,
            )
            for trace_id, owner in P1_HARD_GATE_OWNERS.items()
        )
        gate_statuses = {result.status for result in gate_results}
        if gate_statuses == {"passed"}:
            status = "passed"
        elif gate_statuses & {"blocked", "policy_blocked"}:
            status = "blocked"
        else:
            status = "failed"
        payload = {
            "approval": {
                "approved": status == "passed",
                "kind": "automated_hard_gate_evaluation",
            },
            "attempt_id": evaluation.attempt_id,
            "claims": {
                "fixture_model_promotable": False,
                "fixture_prediction_record_production_eligible": False,
                "formal_capacity": False,
                "predictive_power": False,
                "scope": "engineering_spine_only",
                "source_rights": False,
            },
            "contracts": evaluation.contract_results,
            "created_at": evaluation.created_at.isoformat().replace("+00:00", "Z"),
            "end_to_end_ids": evaluation.end_to_end_ids,
            "failure_evidence": evaluation.failure_evidence,
            "goldens": {
                "rest": evaluation.rest_golden_digest,
                "ui": evaluation.ui_golden_digest,
            },
            "hard_gates": {
                result.trace_id: {
                    "owner": result.owner,
                    "reason": result.reason,
                    "status": result.status,
                }
                for result in gate_results
            },
            "manifests": evaluation.manifest_ids,
            "phase": "P1",
            "previous_bundle_reference": evaluation.previous_bundle_reference,
            "provenance": {
                "deployment_digest": evaluation.deployment_digest,
                "fixture_digests": evaluation.fixture_digests,
                "git_commit": evaluation.git_commit,
                "image": {
                    "digest": evaluation.image_digest,
                    "kind": "application_payload_sha256",
                    "signed": False,
                },
                "migration_digest": evaluation.migration_digest,
            },
            "reproduction_command": evaluation.reproduction_command,
            "resource_smoke": evaluation.resource_smoke,
            "restart": evaluation.restart_results,
            "scenario_results": evaluation.scenario_results,
            "schema_version": "p1-acceptance-bundle-v1",
            "source_policy_ids": evaluation.source_policy_ids,
            "status": status,
            "trace_ids": P1_TRACE_IDS,
        }
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        checksum = hashlib.sha256(content).hexdigest()
        return self._object_repository.put_verified(
            BytesIO(content),
            expected_checksum=checksum,
            metadata={
                "attempt_id": evaluation.attempt_id,
                "media_type": "application/vnd.stock-forecasting.p1-acceptance+json",
                "phase": "P1",
                "status": status,
            },
        )
