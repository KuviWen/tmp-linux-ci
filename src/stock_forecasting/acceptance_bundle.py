from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

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
P1_FAILURE_EVIDENCE_CATALOG = {
    "late_data": ("blocked", "post_cutoff_evidence", "data_owner"),
    "necessary_modality_missing": ("blocked", "missing_anchor_price", "data_owner"),
    "optional_modalities_missing": (
        "degraded",
        "phase_1_optional_modality_out_of_scope",
        "research_owner",
    ),
    "missing_calendar": ("blocked", "calendar_unresolved", "data_owner"),
    "missing_company_action": ("blocked", "missing_company_action", "data_owner"),
    "withdrawal": ("blocked", "source_withdrawn", "data_owner"),
    "checksum_failure": ("blocked", "checksum_mismatch", "data_owner"),
    "stale_fencing": (
        "blocked_then_recovered",
        "relay_lease_superseded",
        "operations_owner",
    ),
    "one_market_failure": ("degraded", "market_failure_isolated", "operations_owner"),
    "fixture_promotion_attempt": (
        "policy_blocked",
        "fixture_use_forbidden",
        "model_governor",
    ),
    "source_entitlement": (
        "policy_blocked",
        "source_entitlement_revoked",
        "source_steward",
    ),
    "outbox_restart": (
        "failed_then_recovered",
        "injected_relay_crash",
        "operations_owner",
    ),
}
P1_SCOPE_CLAIMS = {
    "fixture_model_promotable": False,
    "fixture_prediction_record_production_eligible": False,
    "formal_capacity": False,
    "predictive_power": False,
    "scope": "engineering_spine_only",
    "source_rights": False,
}

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


def _is_stable_evidence_id(value: object) -> bool:
    if is_sha256_reference(value):
        return True
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _failure_evidence_is_complete(
    evidence: object,
    *,
    require_observed: bool,
) -> bool:
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
    if set(by_scenario) != set(P1_FAILURE_EVIDENCE_CATALOG):
        return False
    for scenario, result in by_scenario.items():
        evidence_ids = result.get("evidence_ids")
        if (
            result.get("owner") != P1_FAILURE_EVIDENCE_CATALOG[scenario][2]
            or not (
                (result.get("status"), result.get("reason"))
                == P1_FAILURE_EVIDENCE_CATALOG[scenario][:2]
                or (
                    not require_observed
                    and result.get("status") == "failed"
                    and result.get("reason") == "evidence_capture_failed"
                )
            )
            or not isinstance(evidence_ids, (list, tuple))
            or not evidence_ids
            or not all(_is_stable_evidence_id(value) for value in evidence_ids)
        ):
            return False
    return True


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
    failure_evidence_require_observed: bool = True,
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
        and _failure_evidence_is_complete(
            failure_evidence,
            require_observed=failure_evidence_require_observed,
        )
        and is_sha256_reference(rest_golden_digest)
        and is_sha256_reference(ui_golden_digest)
        and (previous_bundle_reference is None or is_sha256_reference(previous_bundle_reference))
        and reproduction_command
        == "docker compose --profile acceptance run --build --rm acceptance"
    )


def p1_acceptance_bundle_envelope_is_valid(payload: object) -> bool:
    return (
        isinstance(payload, Mapping)
        and _common_bundle_envelope_is_valid(payload)
        and (
            _normal_bundle_envelope_is_valid(payload)
            or _fail_closed_bundle_envelope_is_valid(payload)
        )
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


def _contract_evidence_is_valid(result: object) -> bool:
    if not isinstance(result, Mapping) or set(result) != {
        "checks",
        "evidence_digest",
        "status",
    }:
        return False
    checks = result.get("checks")
    if not isinstance(checks, Mapping) or not checks:
        return False
    if not all(isinstance(check, bool) for check in checks.values()):
        return False
    encoded = json.dumps(checks, separators=(",", ":"), sort_keys=True).encode("utf-8")
    expected_status = "passed" if all(checks.values()) else "failed"
    return (
        result.get("status") == expected_status
        and result.get("evidence_digest") == f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    )


def _contract_evidence_passed(result: object) -> bool:
    return (
        isinstance(result, Mapping)
        and _contract_evidence_is_valid(result)
        and result.get("status") == "passed"
    )


def _has_exact_catalog(results: object, required_names: tuple[str, ...]) -> bool:
    return isinstance(results, Mapping) and set(results) == set(required_names)


def _catalog_results_are_structurally_valid(
    results: object,
    required_names: tuple[str, ...],
    *,
    contracts: bool = False,
) -> bool:
    if not isinstance(results, Mapping) or set(results) != set(required_names):
        return False
    if contracts:
        return all(_contract_evidence_is_valid(result) for result in results.values())
    return all(
        _evidence_status(result) in {"passed", "failed", "blocked", "policy_blocked"}
        for result in results.values()
    )


def _restart_results_are_structurally_valid(results: object) -> bool:
    return (
        isinstance(results, Mapping)
        and set(results) == set(P1_REQUIRED_RESTART_CHECKS)
        and all(isinstance(result, bool) for result in results.values())
    )


def _resource_results_are_structurally_valid(results: object) -> bool:
    return (
        isinstance(results, Mapping)
        and set(results) == set(P1_REQUIRED_RESOURCES)
        and all(isinstance(result, bool) for result in results.values())
        and results.get("formal_capacity_claim") is False
    )


def _results_are_passing(
    *,
    contracts: Mapping[str, object],
    scenarios: Mapping[str, object],
    restarts: Mapping[str, object],
    resources: Mapping[str, object],
) -> bool:
    return (
        all(_contract_evidence_passed(result) for result in contracts.values())
        and all(_evidence_status(result) == "passed" for result in scenarios.values())
        and all(result is True for result in restarts.values())
        and all(
            result is True for name, result in resources.items() if name != "formal_capacity_claim"
        )
        and resources.get("formal_capacity_claim") is False
    )


def _common_bundle_envelope_is_valid(payload: Mapping[str, object]) -> bool:
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
    status = payload.get("status")
    approval = payload.get("approval")
    trace_ids = payload.get("trace_ids")
    hard_gates = payload.get("hard_gates")
    if (
        set(payload) != required_fields
        or payload.get("schema_version") != "p1-acceptance-bundle-v1"
        or payload.get("phase") != "P1"
        or status not in {"passed", "failed", "blocked"}
        or not isinstance(payload.get("attempt_id"), str)
        or not payload["attempt_id"]
        or not isinstance(payload.get("created_at"), str)
        or not payload["created_at"]
        or not isinstance(trace_ids, (list, tuple))
        or tuple(trace_ids) != P1_TRACE_IDS
        or approval
        != {
            "approved": status == "passed",
            "kind": "automated_hard_gate_evaluation",
        }
        or payload.get("claims") != P1_SCOPE_CLAIMS
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
    gate_statuses: set[str] = set()
    for trace_id, owner in P1_HARD_GATE_OWNERS.items():
        result = hard_gates[trace_id]
        if (
            not isinstance(result, Mapping)
            or set(result) != {"owner", "reason", "status"}
            or result.get("owner") != owner
            or result.get("status") not in _P1_GATE_STATUSES
            or not isinstance(result.get("reason"), str)
            or not result["reason"]
        ):
            return False
        gate_statuses.add(str(result["status"]))
    expected_status = (
        "passed"
        if gate_statuses == {"passed"}
        else "blocked"
        if gate_statuses & {"blocked", "policy_blocked"}
        else "failed"
    )
    return status == expected_status


def _platform_run_is_valid(
    *,
    platform: str,
    result: object,
    provenance: Mapping[str, object],
) -> bool:
    if not isinstance(result, Mapping) or set(result) != {
        "evidence",
        "evidence_reference",
        "status",
    }:
        return False
    evidence = result.get("evidence")
    if not isinstance(evidence, Mapping):
        return False
    encoded = json.dumps(
        {
            "evidence": evidence,
            "platform": platform,
            "schema_version": "p1-platform-run-v1",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected_reference = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    return (
        result.get("status") == evidence.get("status")
        and result.get("evidence_reference") == expected_reference
        and _platform_evidence_is_valid(
            platform=platform,
            evidence=evidence,
            provenance=provenance,
            require_passing=False,
        )
    )


def _platform_evidence_is_valid(
    *,
    platform: str,
    evidence: Mapping[str, object],
    provenance: Mapping[str, object],
    require_passing: bool,
) -> bool:
    if set(evidence) != {
        "application_payload_digest",
        "container_image_digest",
        "contract_results",
        "deployment_digest",
        "git_commit",
        "migration_digest",
        "reproduction_command",
        "resource_smoke",
        "restart_results",
        "scenario_results",
        "status",
    }:
        return False
    contracts = evidence.get("contract_results")
    scenarios = evidence.get("scenario_results")
    restarts = evidence.get("restart_results")
    resources = evidence.get("resource_smoke")
    if (
        platform not in _P1_PLATFORMS
        or evidence.get("status") not in {"passed", "blocked"}
        or evidence.get("git_commit") != provenance.get("git_commit")
        or evidence.get("application_payload_digest")
        != provenance.get("application_payload_digest")
        or evidence.get("deployment_digest") != provenance.get("deployment_digest")
        or evidence.get("migration_digest") != provenance.get("migration_digest")
        or evidence.get("reproduction_command")
        != "docker compose --profile acceptance run --build --rm acceptance"
        or not is_sha256_reference(evidence.get("container_image_digest"))
        or not isinstance(contracts, Mapping)
        or not isinstance(scenarios, Mapping)
        or not isinstance(restarts, Mapping)
        or not isinstance(resources, Mapping)
        or not _catalog_results_are_structurally_valid(
            contracts, P1_REQUIRED_CONTRACTS, contracts=True
        )
        or not _catalog_results_are_structurally_valid(scenarios, P1_REQUIRED_SCENARIOS)
        or not _restart_results_are_structurally_valid(restarts)
        or not _resource_results_are_structurally_valid(resources)
    ):
        return False
    results_are_passing = _results_are_passing(
        contracts=contracts,
        scenarios=scenarios,
        restarts=restarts,
        resources=resources,
    )
    return (evidence.get("status") != "passed" or results_are_passing) and (
        not require_passing or evidence.get("status") == "passed" and results_are_passing
    )


def _normal_bundle_envelope_is_valid(payload: Mapping[str, object]) -> bool:
    provenance = payload.get("provenance")
    goldens = payload.get("goldens")
    platform_runs = payload.get("platform_runs")
    contracts = payload.get("contracts")
    scenarios = payload.get("scenario_results")
    restarts = payload.get("restart")
    resources = payload.get("resource_smoke")
    if (
        not isinstance(provenance, Mapping)
        or set(provenance)
        != {
            "application_payload_digest",
            "deployment_digest",
            "fixture_digests",
            "git_commit",
            "images",
            "migration_digest",
        }
        or not isinstance(goldens, Mapping)
        or set(goldens) != {"rest", "ui"}
        or not isinstance(platform_runs, Mapping)
        or not set(platform_runs) <= _P1_PLATFORMS
        or not isinstance(contracts, Mapping)
        or not isinstance(scenarios, Mapping)
        or not isinstance(restarts, Mapping)
        or not isinstance(resources, Mapping)
        or not _catalog_results_are_structurally_valid(
            contracts, P1_REQUIRED_CONTRACTS, contracts=True
        )
        or not _catalog_results_are_structurally_valid(scenarios, P1_REQUIRED_SCENARIOS)
        or not _restart_results_are_structurally_valid(restarts)
        or not _resource_results_are_structurally_valid(resources)
        or not _passing_provenance_is_complete(
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
            failure_evidence_require_observed=payload.get("status") == "passed",
        )
    ):
        return False
    if not all(
        _platform_run_is_valid(platform=platform, result=result, provenance=provenance)
        for platform, result in platform_runs.items()
    ):
        return False
    images = provenance.get("images")
    if not isinstance(images, Mapping) or set(images) != set(platform_runs):
        return False
    for platform, run in platform_runs.items():
        if not isinstance(platform, str) or not isinstance(run, Mapping):
            return False
        image = images[platform]
        evidence = run.get("evidence")
        if not isinstance(evidence, Mapping):
            return False
        if image != {
            "digest": evidence["container_image_digest"],
            "kind": "oci_image_id",
            "signed": False,
        }:
            return False
    if payload.get("status") != "passed":
        return True
    return (
        set(platform_runs) == _P1_PLATFORMS
        and all(
            isinstance(run, Mapping) and run.get("status") == "passed"
            for run in platform_runs.values()
        )
        and _results_are_passing(
            contracts=contracts,
            scenarios=scenarios,
            restarts=restarts,
            resources=resources,
        )
    )


def _fail_closed_bundle_envelope_is_valid(payload: Mapping[str, object]) -> bool:
    failure_evidence = payload.get("failure_evidence")
    hard_gates = payload.get("hard_gates")
    if (
        payload.get("status") != "blocked"
        or payload.get("contracts") != {"acceptance_runner": {"status": "blocked"}}
        or payload.get("scenario_results") != {"acceptance_runner": {"status": "blocked"}}
        or payload.get("restart") != {}
        or payload.get("resource_smoke") != {}
        or payload.get("source_policy_ids") not in ([], ())
        or payload.get("manifests") not in ([], ())
        or payload.get("end_to_end_ids") not in ([], ())
        or payload.get("platform_runs") != {}
        or payload.get("goldens")
        != {
            "rest": "unavailable:evidence_capture_failed",
            "ui": "unavailable:evidence_capture_failed",
        }
        or payload.get("provenance")
        != {
            "application_payload_digest": "unavailable:evidence_capture_failed",
            "deployment_digest": "unavailable:evidence_capture_failed",
            "fixture_digests": {},
            "git_commit": "unavailable:evidence_capture_failed",
            "images": {},
            "migration_digest": "unavailable:evidence_capture_failed",
        }
        or not isinstance(failure_evidence, (list, tuple))
        or len(failure_evidence) != 1
        or not isinstance(failure_evidence[0], Mapping)
        or not isinstance(hard_gates, Mapping)
    ):
        return False
    result = failure_evidence[0]
    reason = result.get("reason")
    expected_stage = {
        "evidence_capture_failed": "orchestration",
        "previous_acceptance_bundle_invalid": "previous_bundle_validation",
    }.get(reason if isinstance(reason, str) else "")
    if (
        expected_stage is None
        or result.get("owner") != "platform_owner"
        or result.get("scenario") != "acceptance_runner"
        or result.get("stage") != expected_stage
        or result.get("status") != "exception"
        or any(
            gate["status"] != "blocked" or gate["reason"] != reason for gate in hard_gates.values()
        )
    ):
        return False
    evidence_ids = result.get("evidence_ids")
    return evidence_ids is None or (
        isinstance(evidence_ids, (list, tuple))
        and len(evidence_ids) == 1
        and is_sha256_reference(evidence_ids[0])
        and evidence_ids[0] == payload.get("previous_bundle_reference")
    )


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


def _fail_closed_payload(evaluation: P1AcceptanceEvaluation) -> dict[str, object]:
    reason = "evidence_capture_failed"
    return {
        "approval": {
            "approved": False,
            "kind": "automated_hard_gate_evaluation",
        },
        "attempt_id": evaluation.attempt_id,
        "claims": P1_SCOPE_CLAIMS,
        "contracts": {"acceptance_runner": {"status": "blocked"}},
        "created_at": evaluation.created_at.isoformat().replace("+00:00", "Z"),
        "end_to_end_ids": (),
        "failure_evidence": (
            {
                "owner": "platform_owner",
                "reason": reason,
                "scenario": "acceptance_runner",
                "stage": "orchestration",
                "status": "exception",
            },
        ),
        "goldens": {
            "rest": "unavailable:evidence_capture_failed",
            "ui": "unavailable:evidence_capture_failed",
        },
        "hard_gates": {
            trace_id: {
                "owner": owner,
                "reason": reason,
                "status": "blocked",
            }
            for trace_id, owner in P1_HARD_GATE_OWNERS.items()
        },
        "manifests": (),
        "phase": "P1",
        "platform_runs": {},
        "previous_bundle_reference": (
            evaluation.previous_bundle_reference
            if is_sha256_reference(evaluation.previous_bundle_reference)
            else None
        ),
        "provenance": {
            "application_payload_digest": "unavailable:evidence_capture_failed",
            "deployment_digest": "unavailable:evidence_capture_failed",
            "fixture_digests": {},
            "git_commit": "unavailable:evidence_capture_failed",
            "images": {},
            "migration_digest": "unavailable:evidence_capture_failed",
        },
        "reproduction_command": "docker compose --profile acceptance run --build --rm acceptance",
        "resource_smoke": {},
        "restart": {},
        "scenario_results": {"acceptance_runner": {"status": "blocked"}},
        "schema_version": "p1-acceptance-bundle-v1",
        "source_policy_ids": (),
        "status": "blocked",
        "trace_ids": P1_TRACE_IDS,
    }


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
        evaluation_provenance = {
            "application_payload_digest": evaluation.image_digest,
            "deployment_digest": evaluation.deployment_digest,
            "git_commit": evaluation.git_commit,
            "migration_digest": evaluation.migration_digest,
        }
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
            _platform_evidence_is_valid(
                platform=platform,
                evidence=evidence,
                provenance=evaluation_provenance,
                require_passing=True,
            )
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
        payload: dict[str, object] = {
            "approval": {
                "approved": status == "passed",
                "kind": "automated_hard_gate_evaluation",
            },
            "attempt_id": evaluation.attempt_id,
            "claims": P1_SCOPE_CLAIMS,
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
        if not p1_acceptance_bundle_envelope_is_valid(payload):
            payload = _fail_closed_payload(evaluation)
        if not p1_acceptance_bundle_envelope_is_valid(payload):
            raise ValueError("acceptance_bundle_envelope_invalid")
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
                "status": str(payload["status"]),
            },
        )
