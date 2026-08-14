from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_forecasting.acceptance_bundle import (
    P1AcceptanceBundlePublisher,
    P1AcceptanceEvaluation,
    P1GateResult,
    digest_required_paths,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository


def test_passing_p1_evaluation_publishes_content_addressed_scope_limited_bundle(
    tmp_path: Path,
) -> None:
    repository = FilesystemObjectRepository(tmp_path)
    publisher = P1AcceptanceBundlePublisher(repository)
    evaluation = P1AcceptanceEvaluation(
        attempt_id="p1-attempt-pass-001",
        created_at=datetime(2026, 8, 15, 1, 2, 3, tzinfo=UTC),
        git_commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        deployment_digest="sha256:" + "c" * 64,
        migration_digest="sha256:" + "d" * 64,
        fixture_digests={"XNAS": "sha256:" + "e" * 64, "XTAI": "sha256:" + "f" * 64},
        source_policy_ids=("fixture-source-policy-xtai-v1", "fixture-source-policy-xnas-v1"),
        manifest_ids=("manifest-xtai-001", "manifest-xnas-001"),
        contract_results={
            "event": "passed",
            "filesystem_object_repository": "passed",
            "postgresql": "passed",
            "rest": "passed",
        },
        end_to_end_ids=("e2e-xtai-001", "e2e-xnas-001"),
        scenario_results={"duplicate_collection": "passed", "late_data": "passed"},
        failure_evidence=(
            {
                "scenario": "policy_blocked",
                "reason": "source_entitlement_inactive",
                "owner": "source_steward",
            },
        ),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results={"outbox_recovered": True, "same_event_identity": True},
        resource_smoke={"api_ready": True, "object_round_trip": True, "postgresql_ready": True},
        gate_results=(
            P1GateResult("GATE-POLICY-01", "passed", "policy_contract_passed", "source_steward"),
            P1GateResult("GATE-PIT-01", "passed", "pit_contract_passed", "data_owner"),
            P1GateResult("GATE-DATA-01", "passed", "data_contract_passed", "data_owner"),
            P1GateResult("GATE-MODEL-01", "passed", "fixture_boundary_passed", "model_governor"),
            P1GateResult("GATE-SEC-01", "passed", "security_contract_passed", "security_owner"),
            P1GateResult("GATE-OPS-01", "passed", "operations_contract_passed", "operations_owner"),
            P1GateResult(
                "GATE-DEPLOY-01", "passed", "deployment_contract_passed", "platform_owner"
            ),
            P1GateResult("GATE-UX-01", "passed", "ux_contract_passed", "research_owner"),
        ),
        previous_bundle_reference=None,
        reproduction_command="docker compose --profile acceptance run --build --rm acceptance",
        platform_results={
            platform: {
                "application_payload_digest": "sha256:" + "b" * 64,
                "container_image_digest": "sha256:" + image_character * 64,
                "contract_results": {
                    "event": "passed",
                    "filesystem_object_repository": "passed",
                    "postgresql": "passed",
                    "rest": "passed",
                },
                "deployment_digest": "sha256:" + "c" * 64,
                "git_commit": "a" * 40,
                "migration_digest": "sha256:" + "d" * 64,
                "reproduction_command": (
                    "docker compose --profile acceptance run --build --rm acceptance"
                ),
                "resource_smoke": {
                    "api_ready": True,
                    "object_round_trip": True,
                    "postgresql_ready": True,
                },
                "restart_results": {"outbox_recovered": True, "same_event_identity": True},
                "scenario_results": {
                    "duplicate_collection": "passed",
                    "late_data": "passed",
                },
                "status": "passed",
            }
            for platform, image_character in (
                ("windows_docker_desktop", "3"),
                ("linux_ci", "4"),
            )
        },
    )

    reference = publisher.publish(evaluation)

    bundle = json.loads(repository.open(reference).read())
    assert reference.object_id == f"sha256:{reference.checksum}"
    assert repository.stat(reference)["metadata"] == {
        "attempt_id": "p1-attempt-pass-001",
        "media_type": "application/vnd.stock-forecasting.p1-acceptance+json",
        "phase": "P1",
        "status": "passed",
    }
    assert bundle == {
        "approval": {"approved": True, "kind": "automated_hard_gate_evaluation"},
        "attempt_id": "p1-attempt-pass-001",
        "claims": {
            "fixture_model_promotable": False,
            "fixture_prediction_record_production_eligible": False,
            "formal_capacity": False,
            "predictive_power": False,
            "scope": "engineering_spine_only",
            "source_rights": False,
        },
        "contracts": evaluation.contract_results,
        "created_at": "2026-08-15T01:02:03Z",
        "end_to_end_ids": list(evaluation.end_to_end_ids),
        "failure_evidence": list(evaluation.failure_evidence),
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
            for result in evaluation.gate_results
        },
        "manifests": list(evaluation.manifest_ids),
        "phase": "P1",
        "platform_runs": {
            platform: {
                "evidence": evaluation.platform_results[platform],
                "evidence_reference": bundle["platform_runs"][platform]["evidence_reference"],
                "status": "passed",
            }
            for platform in ("linux_ci", "windows_docker_desktop")
        },
        "previous_bundle_reference": None,
        "provenance": {
            "application_payload_digest": evaluation.image_digest,
            "deployment_digest": evaluation.deployment_digest,
            "fixture_digests": evaluation.fixture_digests,
            "git_commit": evaluation.git_commit,
            "images": {
                platform: {
                    "digest": evaluation.platform_results[platform]["container_image_digest"],
                    "kind": "oci_image_id",
                    "signed": False,
                }
                for platform in ("linux_ci", "windows_docker_desktop")
            },
            "migration_digest": evaluation.migration_digest,
        },
        "reproduction_command": evaluation.reproduction_command,
        "resource_smoke": evaluation.resource_smoke,
        "restart": evaluation.restart_results,
        "scenario_results": evaluation.scenario_results,
        "schema_version": "p1-acceptance-bundle-v1",
        "source_policy_ids": list(evaluation.source_policy_ids),
        "status": "passed",
        "trace_ids": [
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
        ],
    }


def test_blocked_rerun_publishes_new_evidence_without_changing_previous_bundle(
    tmp_path: Path,
) -> None:
    repository = FilesystemObjectRepository(tmp_path)
    publisher = P1AcceptanceBundlePublisher(repository)
    first_evaluation = P1AcceptanceEvaluation(
        attempt_id="p1-attempt-blocked-001",
        created_at=datetime(2026, 8, 15, 2, 0, tzinfo=UTC),
        git_commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        deployment_digest="sha256:" + "c" * 64,
        migration_digest="sha256:" + "d" * 64,
        fixture_digests={"XNAS": "sha256:" + "e" * 64, "XTAI": "sha256:" + "f" * 64},
        source_policy_ids=("fixture-source-policy-xtai-v1", "fixture-source-policy-xnas-v1"),
        manifest_ids=("manifest-xtai-001", "manifest-xnas-001"),
        contract_results={"postgresql": "passed"},
        end_to_end_ids=("e2e-xtai-001", "e2e-xnas-001"),
        scenario_results={"linux_ci": "blocked"},
        failure_evidence=(
            {
                "scenario": "linux_ci",
                "reason": "hosted_linux_run_unavailable",
                "owner": "platform_owner",
                "status": "blocked",
            },
        ),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results={"outbox_recovered": True},
        resource_smoke={"api_ready": True},
        gate_results=(
            P1GateResult("GATE-POLICY-01", "passed", "policy_contract_passed", "source_steward"),
            P1GateResult("GATE-PIT-01", "passed", "pit_contract_passed", "data_owner"),
            P1GateResult("GATE-DATA-01", "passed", "data_contract_passed", "data_owner"),
            P1GateResult("GATE-MODEL-01", "passed", "fixture_boundary_passed", "model_governor"),
            P1GateResult("GATE-SEC-01", "passed", "security_contract_passed", "security_owner"),
            P1GateResult("GATE-OPS-01", "passed", "operations_contract_passed", "operations_owner"),
            P1GateResult(
                "GATE-DEPLOY-01",
                "policy_blocked",
                "hosted_linux_run_unavailable",
                "platform_owner",
            ),
            P1GateResult("GATE-UX-01", "passed", "ux_contract_passed", "research_owner"),
        ),
        previous_bundle_reference=None,
        reproduction_command="docker compose --profile acceptance run --build --rm acceptance",
    )

    first_reference = publisher.publish(first_evaluation)
    first_content = repository.open(first_reference).read()
    second_evaluation = replace(
        first_evaluation,
        attempt_id="p1-attempt-blocked-002",
        created_at=datetime(2026, 8, 15, 2, 5, tzinfo=UTC),
        previous_bundle_reference=first_reference.object_id,
    )

    second_reference = publisher.publish(second_evaluation)

    assert second_reference != first_reference
    assert repository.open(first_reference).read() == first_content
    second_bundle = json.loads(repository.open(second_reference).read())
    assert second_bundle["status"] == "blocked"
    assert second_bundle["approval"] == {
        "approved": False,
        "kind": "automated_hard_gate_evaluation",
    }
    assert second_bundle["previous_bundle_reference"] == first_reference.object_id
    assert second_bundle["hard_gates"]["GATE-DEPLOY-01"] == {
        "owner": "platform_owner",
        "reason": "hosted_linux_run_unavailable",
        "status": "policy_blocked",
    }


def test_single_platform_evidence_cannot_approve_p1_exit(tmp_path: Path) -> None:
    repository = FilesystemObjectRepository(tmp_path)
    publisher = P1AcceptanceBundlePublisher(repository)
    passing = P1AcceptanceEvaluation(
        attempt_id="p1-attempt-one-platform-001",
        created_at=datetime(2026, 8, 15, 2, 30, tzinfo=UTC),
        git_commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        deployment_digest="sha256:" + "c" * 64,
        migration_digest="sha256:" + "d" * 64,
        fixture_digests={"XNAS": "sha256:" + "e" * 64},
        source_policy_ids=("fixture-source-policy-xnas-v1",),
        manifest_ids=("manifest-xnas-001",),
        contract_results={"postgresql": "passed"},
        end_to_end_ids=("e2e-xnas-001",),
        scenario_results={"duplicate_collection": "passed"},
        failure_evidence=(),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results={"outbox_recovered": True},
        resource_smoke={"api_ready": True},
        gate_results=tuple(
            P1GateResult(trace_id, "passed", "verified", owner)
            for trace_id, owner in {
                "GATE-POLICY-01": "source_steward",
                "GATE-PIT-01": "data_owner",
                "GATE-DATA-01": "data_owner",
                "GATE-MODEL-01": "model_governor",
                "GATE-SEC-01": "security_owner",
                "GATE-OPS-01": "operations_owner",
                "GATE-DEPLOY-01": "platform_owner",
                "GATE-UX-01": "research_owner",
            }.items()
        ),
        previous_bundle_reference=None,
        reproduction_command="docker compose --profile acceptance run --build --rm acceptance",
        platform_results={
            "windows_docker_desktop": {
                "application_payload_digest": "sha256:" + "b" * 64,
                "container_image_digest": "sha256:" + "3" * 64,
                "contract_results": {"postgresql": "passed"},
                "deployment_digest": "sha256:" + "c" * 64,
                "git_commit": "a" * 40,
                "migration_digest": "sha256:" + "d" * 64,
                "reproduction_command": (
                    "docker compose --profile acceptance run --build --rm acceptance"
                ),
                "resource_smoke": {"api_ready": True},
                "restart_results": {"outbox_recovered": True},
                "scenario_results": {"duplicate_collection": "passed"},
                "status": "passed",
            }
        },
    )

    reference = publisher.publish(passing)

    bundle = json.loads(repository.open(reference).read())
    assert bundle["status"] == "blocked"
    assert bundle["hard_gates"]["GATE-DEPLOY-01"] == {
        "owner": "platform_owner",
        "reason": "dual_platform_evidence_required",
        "status": "blocked",
    }
    assert bundle["platform_runs"]["windows_docker_desktop"]["evidence_reference"].startswith(
        "sha256:"
    )


def test_missing_hard_gate_result_is_recorded_as_blocked_instead_of_passing(
    tmp_path: Path,
) -> None:
    repository = FilesystemObjectRepository(tmp_path)
    evaluation = P1AcceptanceEvaluation(
        attempt_id="p1-attempt-incomplete-001",
        created_at=datetime(2026, 8, 15, 3, 0, tzinfo=UTC),
        git_commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        deployment_digest="sha256:" + "c" * 64,
        migration_digest="sha256:" + "d" * 64,
        fixture_digests={},
        source_policy_ids=(),
        manifest_ids=(),
        contract_results={},
        end_to_end_ids=(),
        scenario_results={},
        failure_evidence=(),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results={},
        resource_smoke={},
        gate_results=(
            P1GateResult("GATE-POLICY-01", "passed", "policy_contract_passed", "source_steward"),
        ),
        previous_bundle_reference=None,
        reproduction_command="docker compose --profile acceptance run --build --rm acceptance",
    )

    reference = P1AcceptanceBundlePublisher(repository).publish(evaluation)

    bundle = json.loads(repository.open(reference).read())
    assert bundle["status"] == "blocked"
    assert bundle["hard_gates"]["GATE-DEPLOY-01"] == {
        "owner": "platform_owner",
        "reason": "gate_result_missing",
        "status": "blocked",
    }


def test_publisher_rejects_passing_gates_when_contract_evidence_failed(
    tmp_path: Path,
) -> None:
    repository = FilesystemObjectRepository(tmp_path)
    evaluation = P1AcceptanceEvaluation(
        attempt_id="p1-attempt-contradictory-001",
        created_at=datetime(2026, 8, 15, 4, 0, tzinfo=UTC),
        git_commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        deployment_digest="sha256:" + "c" * 64,
        migration_digest="sha256:" + "d" * 64,
        fixture_digests={"XNAS": "sha256:" + "e" * 64},
        source_policy_ids=("fixture-source-policy-xnas-v1",),
        manifest_ids=("manifest-xnas-001",),
        contract_results={
            "postgresql": {
                "status": "passed",
                "checks": {"transaction_rollback": False},
                "evidence_digest": "sha256:" + "0" * 64,
            }
        },
        end_to_end_ids=("e2e-xnas-001",),
        scenario_results={"duplicate_collection": {"status": "passed"}},
        failure_evidence=(),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results={"outbox_recovered": True},
        resource_smoke={"api_ready": True},
        gate_results=tuple(
            P1GateResult(trace_id, "passed", "verified", owner)
            for trace_id, owner in {
                "GATE-POLICY-01": "source_steward",
                "GATE-PIT-01": "data_owner",
                "GATE-DATA-01": "data_owner",
                "GATE-MODEL-01": "model_governor",
                "GATE-SEC-01": "security_owner",
                "GATE-OPS-01": "operations_owner",
                "GATE-DEPLOY-01": "platform_owner",
                "GATE-UX-01": "research_owner",
            }.items()
        ),
        previous_bundle_reference=None,
        reproduction_command="docker compose --profile acceptance run --build --rm acceptance",
    )

    with pytest.raises(ValueError, match="passing_gate_evidence_inconsistent"):
        P1AcceptanceBundlePublisher(repository).publish(evaluation)


def test_required_tree_digest_rejects_a_missing_or_empty_required_path(tmp_path: Path) -> None:
    (tmp_path / "present.txt").write_text("present", encoding="utf-8")
    (tmp_path / "empty").mkdir()

    with pytest.raises(FileNotFoundError, match="required_digest_path_missing:missing.txt"):
        digest_required_paths(tmp_path, ("present.txt", "missing.txt"))
    with pytest.raises(ValueError, match="required_digest_path_empty:empty"):
        digest_required_paths(tmp_path, ("present.txt", "empty"))


@pytest.mark.parametrize(
    ("gate_results", "expected_reason"),
    [
        (
            (
                P1GateResult("GATE-POLICY-01", "passed", "verified", "source_steward"),
                P1GateResult("GATE-POLICY-01", "passed", "verified", "source_steward"),
            ),
            "duplicate_hard_gate_result",
        ),
        (
            (P1GateResult("GATE-UNKNOWN-01", "passed", "verified", "data_owner"),),
            "unknown_hard_gate_result",
        ),
        (
            (P1GateResult("GATE-POLICY-01", "passed", "verified", "data_owner"),),
            "hard_gate_owner_mismatch",
        ),
        (
            (P1GateResult("GATE-POLICY-01", "degraded", "verified", "source_steward"),),
            "invalid_hard_gate_status",
        ),
    ],
)
def test_publisher_rejects_invalid_hard_gate_metadata(
    tmp_path: Path,
    gate_results: tuple[P1GateResult, ...],
    expected_reason: str,
) -> None:
    evaluation = P1AcceptanceEvaluation(
        attempt_id="p1-attempt-invalid-gate-001",
        created_at=datetime(2026, 8, 15, 5, 0, tzinfo=UTC),
        git_commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        deployment_digest="sha256:" + "c" * 64,
        migration_digest="sha256:" + "d" * 64,
        fixture_digests={},
        source_policy_ids=(),
        manifest_ids=(),
        contract_results={},
        end_to_end_ids=(),
        scenario_results={},
        failure_evidence=(),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results={},
        resource_smoke={},
        gate_results=gate_results,
        previous_bundle_reference=None,
        reproduction_command="docker compose --profile acceptance run --build --rm acceptance",
    )

    with pytest.raises(ValueError, match=expected_reason):
        P1AcceptanceBundlePublisher(FilesystemObjectRepository(tmp_path)).publish(evaluation)
