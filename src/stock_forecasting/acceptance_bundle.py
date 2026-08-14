from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from io import BytesIO
from pathlib import Path

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

P1_REQUIRED_CONTRACTS = (
    "dagster_wrapper",
    "event",
    "filesystem_object_repository",
    "fixture_market_provider",
    "postgresql",
    "rest",
)
P1_REQUIRED_SCENARIOS = (
    "checksum_failure",
    "correction",
    "duplicate_collection",
    "fixture_promotion_attempt",
    "late_data",
    "missing_calendar",
    "missing_company_action",
    "necessary_modality_missing",
    "one_market_failure",
    "optional_modalities_missing",
    "outbox_redelivery",
    "outbox_restart",
    "stale_fencing",
    "withdrawal",
)
P1_REQUIRED_RESTART_CHECKS = (
    "outbox_recovered",
    "same_event_identity",
    "single_consumer_effect",
)
P1_REQUIRED_RESOURCES = (
    "api_ready",
    "dagster_ready",
    "filesystem_object_round_trip",
    "postgresql_ready",
    "formal_capacity_claim",
)
P1_REQUIRED_FAILURE_SCENARIOS = (
    "late_data",
    "necessary_modality_missing",
    "optional_modalities_missing",
    "missing_calendar",
    "missing_company_action",
    "withdrawal",
    "checksum_failure",
    "stale_fencing",
    "one_market_failure",
    "fixture_promotion_attempt",
    "source_entitlement",
    "outbox_restart",
)

_P1_GATE_STATUSES = {"passed", "failed", "blocked", "policy_blocked"}
_P1_PLATFORMS = {"linux_ci", "windows_docker_desktop"}


def is_sha256_reference(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _is_git_commit(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _nonempty_unique_strings(values: object, *, minimum: int) -> bool:
    return (
        isinstance(values, (list, tuple))
        and len(values) >= minimum
        and all(isinstance(value, str) and bool(value) for value in values)
        and len(set(values)) == len(values)
    )


def _failure_evidence_is_complete(evidence: object) -> bool:
    if not isinstance(evidence, (list, tuple)):
        return False
    by_scenario: dict[str, Mapping[str, object]] = {}
    for result in evidence:
        if not isinstance(result, Mapping):
            return False
        scenario = result.get("scenario")
        if not isinstance(scenario, str) or scenario in by_scenario:
            return False
        by_scenario[scenario] = result
    return set(by_scenario) == set(P1_REQUIRED_FAILURE_SCENARIOS) and all(
        isinstance(result.get("status"), str)
        and bool(result["status"])
        and isinstance(result.get("reason"), str)
        and bool(result["reason"])
        and isinstance(result.get("owner"), str)
        and bool(result["owner"])
        and _nonempty_unique_strings(result.get("evidence_ids"), minimum=1)
        for result in by_scenario.values()
    )


def _passing_provenance_is_complete(
    *,
    git_commit: object,
    application_digest: object,
    deployment_digest: object,
    migration_digest: object,
    fixture_digests: object,
    source_policy_ids: object,
    manifest_ids: object,
    end_to_end_ids: object,
    failure_evidence: object,
    rest_golden_digest: object,
    ui_golden_digest: object,
    previous_bundle_reference: object,
    reproduction_command: object,
) -> bool:
    return (
        _is_git_commit(git_commit)
        and is_sha256_reference(application_digest)
        and is_sha256_reference(deployment_digest)
        and is_sha256_reference(migration_digest)
        and isinstance(fixture_digests, Mapping)
        and set(fixture_digests) == {"XTAI", "XNAS"}
        and all(is_sha256_reference(value) for value in fixture_digests.values())
        and _nonempty_unique_strings(source_policy_ids, minimum=2)
        and _nonempty_unique_strings(manifest_ids, minimum=2)
        and _nonempty_unique_strings(end_to_end_ids, minimum=2)
        and _failure_evidence_is_complete(failure_evidence)
        and is_sha256_reference(rest_golden_digest)
        and is_sha256_reference(ui_golden_digest)
        and (previous_bundle_reference is None or is_sha256_reference(previous_bundle_reference))
        and reproduction_command
        == "docker compose --profile acceptance run --build --rm acceptance"
    )


def p1_acceptance_bundle_envelope_is_valid(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    required_fields = {
        "approval",
        "attempt_id",
        "claims",
        "contracts",
        "created_at",
        "end_to_end_ids",
        "failure_evidence",
        "goldens",
        "hard_gates",
        "manifests",
        "phase",
        "platform_runs",
        "previous_bundle_reference",
        "provenance",
        "reproduction_command",
        "resource_smoke",
        "restart",
        "scenario_results",
        "schema_version",
        "source_policy_ids",
        "status",
        "trace_ids",
    }
    if not required_fields <= set(payload):
        return False
    status = payload.get("status")
    approval = payload.get("approval")
    hard_gates = payload.get("hard_gates")
    provenance = payload.get("provenance")
    goldens = payload.get("goldens")
    platform_runs = payload.get("platform_runs")
    trace_ids = payload.get("trace_ids")
    if (
        payload.get("schema_version") != "p1-acceptance-bundle-v1"
        or payload.get("phase") != "P1"
        or status not in {"passed", "failed", "blocked"}
        or not isinstance(payload.get("attempt_id"), str)
        or not payload["attempt_id"]
        or not isinstance(payload.get("created_at"), str)
        or not payload["created_at"]
        or not isinstance(trace_ids, (list, tuple))
        or tuple(trace_ids) != P1_TRACE_IDS
        or not isinstance(approval, Mapping)
        or approval.get("kind") != "automated_hard_gate_evaluation"
        or approval.get("approved") is not (status == "passed")
        or not isinstance(payload.get("claims"), Mapping)
        or not isinstance(payload.get("contracts"), Mapping)
        or not isinstance(payload.get("scenario_results"), Mapping)
        or not isinstance(payload.get("restart"), Mapping)
        or not isinstance(payload.get("resource_smoke"), Mapping)
        or not isinstance(payload.get("failure_evidence"), (list, tuple))
        or not isinstance(payload.get("source_policy_ids"), (list, tuple))
        or not isinstance(payload.get("manifests"), (list, tuple))
        or not isinstance(payload.get("end_to_end_ids"), (list, tuple))
        or not isinstance(goldens, Mapping)
        or not isinstance(goldens.get("rest"), str)
        or not isinstance(goldens.get("ui"), str)
        or not isinstance(provenance, Mapping)
        or not {
            "application_payload_digest",
            "deployment_digest",
            "fixture_digests",
            "git_commit",
            "images",
            "migration_digest",
        }
        <= set(provenance)
        or not isinstance(provenance.get("fixture_digests"), Mapping)
        or not isinstance(provenance.get("images"), Mapping)
        or not isinstance(platform_runs, Mapping)
        or payload.get("reproduction_command")
        != "docker compose --profile acceptance run --build --rm acceptance"
        or (
            payload.get("previous_bundle_reference") is not None
            and not is_sha256_reference(payload.get("previous_bundle_reference"))
        )
        or not isinstance(hard_gates, Mapping)
        or set(hard_gates) != set(P1_HARD_GATE_OWNERS)
    ):
        return False
    for trace_id, owner in P1_HARD_GATE_OWNERS.items():
        result = hard_gates[trace_id]
        if (
            not isinstance(result, Mapping)
            or result.get("owner") != owner
            or result.get("status") not in _P1_GATE_STATUSES
            or not isinstance(result.get("reason"), str)
            or not result["reason"]
        ):
            return False
    for platform, result in platform_runs.items():
        if (
            platform not in _P1_PLATFORMS
            or not isinstance(result, Mapping)
            or result.get("status") not in {"passed", "blocked"}
            or not is_sha256_reference(result.get("evidence_reference"))
            or not isinstance(result.get("evidence"), Mapping)
        ):
            return False
    if status != "passed":
        return True
    return _passing_provenance_is_complete(
        git_commit=provenance.get("git_commit"),
        application_digest=provenance.get("application_payload_digest"),
        deployment_digest=provenance.get("deployment_digest"),
        migration_digest=provenance.get("migration_digest"),
        fixture_digests=provenance.get("fixture_digests"),
        source_policy_ids=payload.get("source_policy_ids"),
        manifest_ids=payload.get("manifests"),
        end_to_end_ids=payload.get("end_to_end_ids"),
        failure_evidence=payload.get("failure_evidence"),
        rest_golden_digest=goldens.get("rest"),
        ui_golden_digest=goldens.get("ui"),
        previous_bundle_reference=payload.get("previous_bundle_reference"),
        reproduction_command=payload.get("reproduction_command"),
    )


def digest_required_paths(project_root: Path, relative_paths: tuple[str, ...]) -> str:
    if not relative_paths:
        raise ValueError("required_digest_paths_empty")
    files: list[Path] = []
    for relative_path in relative_paths:
        candidate = project_root / relative_path
        if not candidate.exists():
            raise FileNotFoundError(f"required_digest_path_missing:{relative_path}")
        if candidate.is_file():
            files.append(candidate)
            continue
        candidate_files = [
            path
            for path in candidate.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        ]
        if not candidate_files:
            raise ValueError(f"required_digest_path_empty:{relative_path}")
        files.extend(candidate_files)

    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(project_root).as_posix()):
        digest.update(path.relative_to(project_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _evidence_status(result: object) -> str | None:
    if isinstance(result, str):
        return result
    if isinstance(result, Mapping):
        status = result.get("status")
        return status if isinstance(status, str) else None
    return None


def _contract_evidence_passed(result: object) -> bool:
    if isinstance(result, str):
        return result == "passed"
    if not isinstance(result, Mapping) or result.get("status") != "passed":
        return False
    checks = result.get("checks")
    evidence_digest = result.get("evidence_digest")
    if checks is None and evidence_digest is None:
        return True
    if not isinstance(checks, Mapping) or not checks:
        return False
    if not all(check is True for check in checks.values()):
        return False
    encoded = json.dumps(checks, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return evidence_digest == f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _has_exact_catalog(results: object, required_names: tuple[str, ...]) -> bool:
    return isinstance(results, Mapping) and set(results) == set(required_names)


def _passing_evidence_is_consistent(evaluation: P1AcceptanceEvaluation) -> bool:
    return (
        _passing_provenance_is_complete(
            git_commit=evaluation.git_commit,
            application_digest=evaluation.image_digest,
            deployment_digest=evaluation.deployment_digest,
            migration_digest=evaluation.migration_digest,
            fixture_digests=evaluation.fixture_digests,
            source_policy_ids=evaluation.source_policy_ids,
            manifest_ids=evaluation.manifest_ids,
            end_to_end_ids=evaluation.end_to_end_ids,
            failure_evidence=evaluation.failure_evidence,
            rest_golden_digest=evaluation.rest_golden_digest,
            ui_golden_digest=evaluation.ui_golden_digest,
            previous_bundle_reference=evaluation.previous_bundle_reference,
            reproduction_command=evaluation.reproduction_command,
        )
        and _has_exact_catalog(evaluation.contract_results, P1_REQUIRED_CONTRACTS)
        and all(
            _contract_evidence_passed(result) for result in evaluation.contract_results.values()
        )
        and _has_exact_catalog(evaluation.scenario_results, P1_REQUIRED_SCENARIOS)
        and all(
            _evidence_status(result) == "passed" for result in evaluation.scenario_results.values()
        )
        and _has_exact_catalog(evaluation.restart_results, P1_REQUIRED_RESTART_CHECKS)
        and all(result is True for result in evaluation.restart_results.values())
        and _has_exact_catalog(evaluation.resource_smoke, P1_REQUIRED_RESOURCES)
        and all(
            result is True
            for name, result in evaluation.resource_smoke.items()
            if name != "formal_capacity_claim"
        )
        and evaluation.resource_smoke.get("formal_capacity_claim", False) is False
    )


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
    platform_results: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


def _all_evidence_passed(results: object) -> bool:
    return (
        isinstance(results, Mapping)
        and bool(results)
        and all(_evidence_status(result) == "passed" for result in results.values())
    )


def _all_contract_evidence_passed(results: object) -> bool:
    return (
        isinstance(results, Mapping)
        and bool(results)
        and all(_contract_evidence_passed(result) for result in results.values())
    )


def _all_boolean_evidence_passed(results: object) -> bool:
    return (
        isinstance(results, Mapping)
        and bool(results)
        and all(result is True for result in results.values())
    )


def _platform_evidence_is_passing(
    platform: str,
    evidence: Mapping[str, object],
    evaluation: P1AcceptanceEvaluation,
) -> bool:
    resources = evidence.get("resource_smoke")
    meaningful_resources = (
        {name: result for name, result in resources.items() if name != "formal_capacity_claim"}
        if isinstance(resources, Mapping)
        else {}
    )
    image_digest = evidence.get("container_image_digest")
    return (
        platform in _P1_PLATFORMS
        and evidence.get("status") == "passed"
        and evidence.get("git_commit") == evaluation.git_commit
        and evidence.get("application_payload_digest") == evaluation.image_digest
        and evidence.get("deployment_digest") == evaluation.deployment_digest
        and evidence.get("migration_digest") == evaluation.migration_digest
        and evidence.get("reproduction_command") == evaluation.reproduction_command
        and is_sha256_reference(image_digest)
        and _has_exact_catalog(evidence.get("contract_results"), P1_REQUIRED_CONTRACTS)
        and _all_contract_evidence_passed(evidence.get("contract_results"))
        and _has_exact_catalog(evidence.get("scenario_results"), P1_REQUIRED_SCENARIOS)
        and _all_evidence_passed(evidence.get("scenario_results"))
        and _has_exact_catalog(evidence.get("restart_results"), P1_REQUIRED_RESTART_CHECKS)
        and _all_boolean_evidence_passed(evidence.get("restart_results"))
        and _has_exact_catalog(resources, P1_REQUIRED_RESOURCES)
        and all(result is True for result in meaningful_resources.values())
        and (
            not isinstance(resources, Mapping)
            or resources.get("formal_capacity_claim", False) is False
        )
    )


class P1AcceptanceBundlePublisher:
    def __init__(self, object_repository: FilesystemObjectRepository) -> None:
        self._object_repository = object_repository

    def publish(self, evaluation: P1AcceptanceEvaluation) -> ObjectRef:
        supplied_gate_results: dict[str, P1GateResult] = {}
        for result in evaluation.gate_results:
            if result.trace_id in supplied_gate_results:
                raise ValueError("duplicate_hard_gate_result")
            if result.trace_id not in P1_HARD_GATE_OWNERS:
                raise ValueError("unknown_hard_gate_result")
            if result.owner != P1_HARD_GATE_OWNERS[result.trace_id]:
                raise ValueError("hard_gate_owner_mismatch")
            if result.status not in _P1_GATE_STATUSES:
                raise ValueError("invalid_hard_gate_status")
            supplied_gate_results[result.trace_id] = result
        if (
            set(supplied_gate_results) == set(P1_HARD_GATE_OWNERS)
            and all(result.status == "passed" for result in supplied_gate_results.values())
            and not _passing_evidence_is_consistent(evaluation)
        ):
            raise ValueError("passing_gate_evidence_inconsistent")
        platform_runs: dict[str, dict[str, object]] = {}
        for platform, evidence in evaluation.platform_results.items():
            if platform not in _P1_PLATFORMS:
                raise ValueError("unknown_acceptance_platform")
            evidence_content = json.dumps(
                {
                    "evidence": evidence,
                    "platform": platform,
                    "schema_version": "p1-platform-run-v1",
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            evidence_checksum = hashlib.sha256(evidence_content).hexdigest()
            evidence_reference = self._object_repository.put_verified(
                BytesIO(evidence_content),
                expected_checksum=evidence_checksum,
                metadata={
                    "media_type": "application/vnd.stock-forecasting.p1-platform-run+json",
                    "phase": "P1",
                    "platform": platform,
                    "status": str(evidence.get("status", "blocked")),
                },
            )
            platform_runs[platform] = {
                "evidence": evidence,
                "evidence_reference": evidence_reference.object_id,
                "status": evidence.get("status", "blocked"),
            }

        dual_platform_passed = set(platform_runs) == _P1_PLATFORMS and all(
            _platform_evidence_is_passing(platform, evidence, evaluation)
            for platform, evidence in evaluation.platform_results.items()
        )
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
        if not dual_platform_passed:
            gate_results = tuple(
                replace(
                    result,
                    status="blocked",
                    reason="dual_platform_evidence_required",
                )
                if result.trace_id == "GATE-DEPLOY-01" and result.status == "passed"
                else result
                for result in gate_results
            )
        gate_statuses = {result.status for result in gate_results}
        if gate_statuses == {"passed"}:
            if not _passing_evidence_is_consistent(evaluation):
                raise ValueError("passing_gate_evidence_inconsistent")
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
            "platform_runs": platform_runs,
            "previous_bundle_reference": evaluation.previous_bundle_reference,
            "provenance": {
                "application_payload_digest": evaluation.image_digest,
                "deployment_digest": evaluation.deployment_digest,
                "fixture_digests": evaluation.fixture_digests,
                "git_commit": evaluation.git_commit,
                "images": {
                    platform: {
                        "digest": evidence["container_image_digest"],
                        "kind": "oci_image_id",
                        "signed": False,
                    }
                    for platform, evidence in evaluation.platform_results.items()
                    if isinstance(evidence.get("container_image_digest"), str)
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
