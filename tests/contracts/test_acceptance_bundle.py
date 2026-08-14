from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from stock_forecasting.acceptance_bundle import (
    P1_FAILURE_EVIDENCE_CATALOG,
    P1_REQUIRED_CONTRACTS,
    P1_REQUIRED_FAILURE_SCENARIOS,
    P1_REQUIRED_RESOURCES,
    P1_REQUIRED_RESTART_CHECKS,
    P1_REQUIRED_SCENARIOS,
    P1_SCENARIO_OWNERS,
    P1AcceptanceBundlePublisher,
    P1AcceptanceEvaluation,
    P1GateResult,
    digest_required_paths,
    p1_acceptance_bundle_envelope_is_valid,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository

EXPECTED_P1_CONTRACTS = (
    "dagster_wrapper",
    "event",
    "filesystem_object_repository",
    "fixture_market_provider",
    "postgresql",
    "rest",
)
EXPECTED_P1_SCENARIOS = (
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
EXPECTED_P1_RESTART_CHECKS = (
    "outbox_recovered",
    "same_event_identity",
    "single_consumer_effect",
)
EXPECTED_P1_FAILURE_SCENARIOS = (
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
EXPECTED_P1_FAILURE_EVIDENCE_CATALOG = {
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
EXPECTED_P1_SCENARIO_OWNERS = {
    "checksum_failure": "data_owner",
    "correction": "data_owner",
    "duplicate_collection": "data_owner",
    "fixture_promotion_attempt": "model_governor",
    "late_data": "data_owner",
    "missing_calendar": "data_owner",
    "missing_company_action": "data_owner",
    "necessary_modality_missing": "data_owner",
    "one_market_failure": "operations_owner",
    "optional_modalities_missing": "research_owner",
    "outbox_redelivery": "operations_owner",
    "outbox_restart": "operations_owner",
    "stale_fencing": "operations_owner",
    "withdrawal": "data_owner",
}
EXPECTED_P1_RESOURCES = (
    "api_ready",
    "dagster_ready",
    "filesystem_object_round_trip",
    "postgresql_ready",
    "formal_capacity_claim",
)
TEST_SOURCE_POLICY_IDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
)
TEST_MANIFEST_IDS = (
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
)
TEST_END_TO_END_IDS = (
    "trace-p1-trace-tw-01",
    "trace-p1-trace-us-01",
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
    "77777777-7777-4777-8777-777777777777",
)


def _passing_contracts() -> dict[str, dict[str, object]]:
    return {
        contract: {
            "checks": {"verified": True},
            "evidence_digest": (
                "sha256:348f299cf43d57826c76c5ef7c8ccc37668b45161b857d4ef09f7125f3381be9"
            ),
            "status": "passed",
        }
        for contract in EXPECTED_P1_CONTRACTS
    }


def _passing_scenarios() -> dict[str, dict[str, str]]:
    return {
        scenario: {
            "owner": EXPECTED_P1_SCENARIO_OWNERS[scenario],
            "reason": f"{scenario}_verified",
            "status": "passed",
        }
        for scenario in EXPECTED_P1_SCENARIOS
    }


def _passing_restart_checks() -> dict[str, bool]:
    return dict.fromkeys(EXPECTED_P1_RESTART_CHECKS, True)


def _passing_resources() -> dict[str, bool]:
    return {resource: resource != "formal_capacity_claim" for resource in EXPECTED_P1_RESOURCES}


def _failure_evidence() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "evidence_ids": ["sha256:" + "9" * 64],
            "owner": EXPECTED_P1_FAILURE_EVIDENCE_CATALOG[scenario][2],
            "reason": EXPECTED_P1_FAILURE_EVIDENCE_CATALOG[scenario][1],
            "scenario": scenario,
            "status": EXPECTED_P1_FAILURE_EVIDENCE_CATALOG[scenario][0],
        }
        for scenario in EXPECTED_P1_FAILURE_SCENARIOS
    )


def test_required_p1_evidence_catalogs_match_the_ticket_contract() -> None:
    assert P1_REQUIRED_CONTRACTS == EXPECTED_P1_CONTRACTS
    assert P1_REQUIRED_SCENARIOS == EXPECTED_P1_SCENARIOS
    assert P1_REQUIRED_RESTART_CHECKS == EXPECTED_P1_RESTART_CHECKS
    assert P1_REQUIRED_RESOURCES == EXPECTED_P1_RESOURCES
    assert P1_REQUIRED_FAILURE_SCENARIOS == EXPECTED_P1_FAILURE_SCENARIOS
    assert P1_FAILURE_EVIDENCE_CATALOG == EXPECTED_P1_FAILURE_EVIDENCE_CATALOG
    assert P1_SCENARIO_OWNERS == EXPECTED_P1_SCENARIO_OWNERS


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
        source_policy_ids=TEST_SOURCE_POLICY_IDS,
        manifest_ids=TEST_MANIFEST_IDS,
        contract_results=_passing_contracts(),
        end_to_end_ids=TEST_END_TO_END_IDS,
        scenario_results=_passing_scenarios(),
        failure_evidence=_failure_evidence(),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results=_passing_restart_checks(),
        resource_smoke=_passing_resources(),
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
                "contract_results": _passing_contracts(),
                "deployment_digest": "sha256:" + "c" * 64,
                "git_commit": "a" * 40,
                "migration_digest": "sha256:" + "d" * 64,
                "reproduction_command": (
                    "docker compose --profile acceptance run --build --rm acceptance"
                ),
                "resource_smoke": _passing_resources(),
                "restart_results": _passing_restart_checks(),
                "scenario_results": _passing_scenarios(),
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
    assert p1_acceptance_bundle_envelope_is_valid(bundle)
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

    for incomplete_evaluation in (
        replace(evaluation, contract_results={"x": "passed"}),
        replace(
            evaluation,
            contract_results=dict.fromkeys(EXPECTED_P1_CONTRACTS, "passed"),
        ),
        replace(evaluation, scenario_results={"x": "passed"}),
        replace(
            evaluation,
            scenario_results=dict.fromkeys(EXPECTED_P1_SCENARIOS, "passed"),
        ),
        replace(evaluation, restart_results={"x": True}),
        replace(
            evaluation,
            resource_smoke={"x": True, "formal_capacity_claim": False},
        ),
        replace(evaluation, git_commit=""),
        replace(evaluation, image_digest=""),
        replace(evaluation, deployment_digest=""),
        replace(evaluation, migration_digest=""),
        replace(evaluation, fixture_digests={}),
        replace(evaluation, source_policy_ids=()),
        replace(evaluation, source_policy_ids=("not-a-uuid", "also-not-a-uuid")),
        replace(evaluation, manifest_ids=()),
        replace(evaluation, manifest_ids=("not-a-uuid", "also-not-a-uuid")),
        replace(evaluation, end_to_end_ids=()),
        replace(
            evaluation,
            end_to_end_ids=(
                "trace-p1-trace-tw-01",
                "missing-us-tracer",
                *TEST_END_TO_END_IDS[2:],
            ),
        ),
        replace(evaluation, failure_evidence=()),
        replace(evaluation, rest_golden_digest=""),
        replace(evaluation, ui_golden_digest=""),
    ):
        with pytest.raises(ValueError, match="passing_gate_evidence_inconsistent"):
            publisher.publish(incomplete_evaluation)

    for field_name, invalid_value in (
        ("status", "passed"),
        ("reason", "arbitrary_reason"),
        ("owner", "arbitrary_owner"),
        ("evidence_ids", ["arbitrary-evidence"]),
    ):
        invalid_failure_evidence = [dict(result) for result in _failure_evidence()]
        invalid_failure_evidence[0][field_name] = invalid_value
        with pytest.raises(ValueError, match="passing_gate_evidence_inconsistent"):
            publisher.publish(replace(evaluation, failure_evidence=tuple(invalid_failure_evidence)))

    incomplete_platforms = {
        platform: dict(platform_evidence)
        for platform, platform_evidence in evaluation.platform_results.items()
    }
    incomplete_platforms["linux_ci"]["scenario_results"] = {"x": "passed"}
    incomplete_reference = publisher.publish(
        replace(evaluation, platform_results=incomplete_platforms)
    )
    incomplete_bundle = json.loads(repository.open(incomplete_reference).read())
    assert incomplete_bundle["status"] == "blocked"
    assert p1_acceptance_bundle_envelope_is_valid(incomplete_bundle)
    assert incomplete_bundle["contracts"] == {"acceptance_runner": {"status": "blocked"}}
    assert incomplete_bundle["failure_evidence"] == [
        {
            "owner": "platform_owner",
            "reason": "evidence_capture_failed",
            "scenario": "acceptance_runner",
            "stage": "orchestration",
            "status": "exception",
        }
    ]

    def copy_bundle() -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(json.dumps(bundle)))

    contradictory_bundles = []

    candidate = copy_bundle()
    candidate["platform_runs"] = {}
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["hard_gates"]["GATE-DATA-01"]["status"] = "failed"
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["contracts"].pop("event")
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["contracts"]["event"] = "passed"
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["scenario_results"]["late_data"] = "passed"
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["platform_runs"]["linux_ci"]["evidence_reference"] = "sha256:" + "0" * 64
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["claims"] = {}
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["provenance"]["images"] = {}
    contradictory_bundles.append(candidate)

    candidate = copy_bundle()
    candidate["status"] = "blocked"
    candidate["approval"]["approved"] = False
    candidate["hard_gates"]["GATE-DEPLOY-01"] = {
        "owner": "platform_owner",
        "reason": "dual_platform_evidence_required",
        "status": "blocked",
    }
    candidate["contracts"] = {}
    contradictory_bundles.append(candidate)
    assert not any(
        p1_acceptance_bundle_envelope_is_valid(candidate) for candidate in contradictory_bundles
    )


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
        source_policy_ids=TEST_SOURCE_POLICY_IDS,
        manifest_ids=TEST_MANIFEST_IDS,
        contract_results=_passing_contracts(),
        end_to_end_ids=TEST_END_TO_END_IDS,
        scenario_results=_passing_scenarios(),
        failure_evidence=_failure_evidence(),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results=_passing_restart_checks(),
        resource_smoke=_passing_resources(),
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
    assert p1_acceptance_bundle_envelope_is_valid(json.loads(first_content))
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
    assert p1_acceptance_bundle_envelope_is_valid(second_bundle)
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
        fixture_digests={"XNAS": "sha256:" + "e" * 64, "XTAI": "sha256:" + "f" * 64},
        source_policy_ids=TEST_SOURCE_POLICY_IDS,
        manifest_ids=TEST_MANIFEST_IDS,
        contract_results=_passing_contracts(),
        end_to_end_ids=TEST_END_TO_END_IDS,
        scenario_results=_passing_scenarios(),
        failure_evidence=_failure_evidence(),
        rest_golden_digest="sha256:" + "1" * 64,
        ui_golden_digest="sha256:" + "2" * 64,
        restart_results=_passing_restart_checks(),
        resource_smoke=_passing_resources(),
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
                "contract_results": _passing_contracts(),
                "deployment_digest": "sha256:" + "c" * 64,
                "git_commit": "a" * 40,
                "migration_digest": "sha256:" + "d" * 64,
                "reproduction_command": (
                    "docker compose --profile acceptance run --build --rm acceptance"
                ),
                "resource_smoke": _passing_resources(),
                "restart_results": _passing_restart_checks(),
                "scenario_results": _passing_scenarios(),
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
    assert p1_acceptance_bundle_envelope_is_valid(bundle)
    assert bundle["contracts"] == {"acceptance_runner": {"status": "blocked"}}
    assert bundle["failure_evidence"][0]["reason"] == "evidence_capture_failed"
    assert all(
        result["reason"] == "evidence_capture_failed" for result in bundle["hard_gates"].values()
    )


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


def test_required_tree_digest_is_stable_across_checkout_line_endings(tmp_path: Path) -> None:
    linux_checkout = tmp_path / "linux"
    windows_checkout = tmp_path / "windows"
    linux_checkout.mkdir()
    windows_checkout.mkdir()
    (linux_checkout / "module.py").write_bytes(b"first\nsecond\n")
    (windows_checkout / "module.py").write_bytes(b"first\r\nsecond\r\n")

    assert digest_required_paths(linux_checkout, ("module.py",)) == digest_required_paths(
        windows_checkout,
        ("module.py",),
    )


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
