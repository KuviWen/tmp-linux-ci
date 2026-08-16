from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceDistribution,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.data_supply import HistoricalAvailabilityClaim
from stock_forecasting.historical_evidence import (
    HistoricalEvidenceAttestationCommand,
    HistoricalEvidenceAttestationIssuer,
    HistoricalEvidenceCommand,
    HistoricalEvidenceWorkflow,
    QualifiedHistoricalAvailabilityClaimVerifier,
    SubmittedHistoricalEvidenceLevel,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository, ObjectRef
from stock_forecasting.platform.state_store import StateStore


def _realized_weekdays(*, start: date, count: int) -> list[str]:
    sessions: list[str] = []
    candidate = start
    while len(sessions) < count:
        if candidate.weekday() < 5:
            sessions.append(candidate.isoformat())
        candidate += timedelta(days=1)
    return sessions


def _put_evidence(
    object_repository: FilesystemObjectRepository,
    evidence: dict[str, object],
) -> str:
    encoded = _evidence_content(evidence)
    checksum = hashlib.sha256(encoded).hexdigest()
    return object_repository.put_verified(
        BytesIO(encoded),
        expected_checksum=checksum,
        metadata={"content_type": "application/json"},
    ).object_id


def _evidence_content(evidence: dict[str, object]) -> bytes:
    return json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


_HISTORICAL_REQUIRED_USES: frozenset[SourceUseRight] = frozenset(
    {
        "ingest",
        "retain_observed_history",
        "transform",
        "model",
        "internal_display",
        "backup_restore",
    }
)


def _authorized_workflow(
    state_store: StateStore,
    object_repository: FilesystemObjectRepository,
    *,
    source_id: str,
    now: datetime,
    separate_principals: bool = True,
) -> tuple[HistoricalEvidenceWorkflow, HistoricalEvidenceAttestationIssuer]:
    collector = LocalApiKeyIdentity.issue(
        owner=f"{source_id}-collector",
        environment="development",
        scopes=(
            {"market_data.collect"}
            if separate_principals
            else {"market_data.collect", "price_qualification.govern"}
        ),
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    governor = (
        LocalApiKeyIdentity.issue(
            owner=f"{source_id}-governor",
            environment="development",
            scopes={"price_qualification.govern"},
            issued_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=23),
            data_protection_classes={"licensed"},
        )
        if separate_principals
        else collector
    )
    policy_id = f"ticket-08/{source_id}-policy-v1"
    action_grants = (
        (
            ActionGrant(
                version_id=f"ticket-08/{source_id}-collector-grant-v1",
                principal_id=collector.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(hours=23),
            ),
            ActionGrant(
                version_id=f"ticket-08/{source_id}-governor-grant-v1",
                principal_id=governor.context.principal_id,
                actions=frozenset({"price_qualification.govern"}),
                environment="development",
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(hours=23),
            ),
        )
        if separate_principals
        else (
            ActionGrant(
                version_id=f"ticket-08/{source_id}-shared-grant-v1",
                principal_id=collector.context.principal_id,
                actions=frozenset({"market_data.collect", "price_qualification.govern"}),
                environment="development",
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(hours=23),
            ),
        )
    )
    entitlements = (
        (
            SourceEntitlement(
                version_id=f"ticket-08/{source_id}-collector-entitlement-v1",
                principal_id=collector.context.principal_id,
                dataset_id=source_id,
                status="active",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(hours=23),
                allowed_uses=_HISTORICAL_REQUIRED_USES,
            ),
            SourceEntitlement(
                version_id=f"ticket-08/{source_id}-governor-entitlement-v1",
                principal_id=governor.context.principal_id,
                dataset_id=source_id,
                status="active",
                allowed_actions=frozenset({"price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(hours=23),
                allowed_uses=_HISTORICAL_REQUIRED_USES,
            ),
        )
        if separate_principals
        else (
            SourceEntitlement(
                version_id=f"ticket-08/{source_id}-shared-entitlement-v1",
                principal_id=collector.context.principal_id,
                dataset_id=source_id,
                status="active",
                allowed_actions=frozenset({"market_data.collect", "price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(hours=23),
                allowed_uses=_HISTORICAL_REQUIRED_USES,
            ),
        )
    )
    policy = AuthorizationPolicy(
        action_grants=action_grants,
        source_policies=(
            SourcePolicyVersion(
                version_id=policy_id,
                dataset_id=source_id,
                allowed_actions=frozenset({"market_data.collect", "price_qualification.govern"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(hours=23),
                allowed_uses=_HISTORICAL_REQUIRED_USES,
                access_basis="engineering_contract",
                source_basis_id="ENGINEERING-HISTORICAL-RECONSTRUCTION-01",
                distributions=(
                    SourceDistribution(
                        dataset_id=f"{source_id}-archive",
                        distribution_url=f"https://archive.example.test/{source_id}.json",
                    ),
                ),
            ),
        ),
        source_entitlements=entitlements,
    )
    return (
        HistoricalEvidenceWorkflow(
            state_store,
            object_repository=object_repository,
            observed_at=now,
            authorization_policy=policy,
            security_context=governor.context,
        ),
        HistoricalEvidenceAttestationIssuer(
            state_store,
            object_repository=object_repository,
            authorization_policy=policy,
            security_context=collector.context,
            clock=lambda: now,
        ),
    )


def _attest(
    issuer: HistoricalEvidenceAttestationIssuer,
    object_repository: FilesystemObjectRepository,
    evidence: dict[str, object],
    *,
    source_id: str,
    listing_id: str,
    market: str,
    trace_id: str,
) -> str:
    listing = next(
        item
        for item in cast(list[dict[str, object]], evidence["listings"])
        if item["listing_id"] == listing_id and item["market"] == market
    )
    calendar_content = _evidence_content(
        {
            "schema_version": "historical-realized-calendar/v1",
            "source_reference": f"https://archive.example.test/{source_id}.json",
            "market": market,
            "version": evidence["calendar_version"],
            "sessions": listing["sessions"],
        },
    )
    reference_content = _evidence_content(
        {
            "schema_version": "historical-listing-reference/v1",
            "source_reference": f"https://archive.example.test/{source_id}.json",
            "listing": {
                key: listing[key]
                for key in (
                    "listing_id",
                    "market",
                    "security_id",
                    "symbols",
                    "lifecycle",
                    "company_actions",
                )
            },
        },
    )
    return issuer.issue(
        HistoricalEvidenceAttestationCommand(
            listing_id=listing_id,
            market=market,
            source_id=source_id,
            evidence_content=_evidence_content(evidence),
            calendar_content=calendar_content,
            reference_content=reference_content,
            trace_id=trace_id,
        )
    )


def test_candidate_evidence_cannot_self_approve(tmp_path: Path) -> None:
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    workflow, _ = _authorized_workflow(
        state_store,
        object_repository,
        source_id="manual-upload",
        now=now,
    )

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="manual-upload",
            trace_id="trace-ticket-08-self-asserted",
            submitted_evidence_level="self_asserted",
        )
    )

    assert outcome.status == "quarantined"
    assert outcome.reason_code == "historical_evidence_self_asserted"
    assert outcome.claim_id is None


@pytest.mark.parametrize(
    ("evidence_level", "reason_code"),
    [
        ("published_current_only", "historical_evidence_current_only"),
        ("unknown", "historical_evidence_unknown"),
    ],
)
def test_non_historical_evidence_is_quarantined_before_formal_use(
    tmp_path: Path,
    evidence_level: SubmittedHistoricalEvidenceLevel,
    reason_code: str,
) -> None:
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    workflow, _ = _authorized_workflow(
        state_store,
        object_repository,
        source_id="current-page",
        now=now,
    )

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-tw-2330-xtai",
            market="XTAI",
            source_id="current-page",
            trace_id=f"trace-ticket-08-{evidence_level}",
            submitted_evidence_level=evidence_level,
        )
    )

    assert outcome.status == "quarantined"
    assert outcome.reason_code == reason_code
    assert outcome.claim_id is None


def test_platform_observation_creates_content_addressed_qualified_claim(tmp_path: Path) -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    evidence: dict[str, object] = {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": "us-unadjusted-eod-v1",
        "evidence_version": "platform-observation-2026-08-15",
        "revision": "rev-1",
        "observation_kind": "platform_observation",
        "observation_reference": "platform://raw-price-partition/2026-08-15",
        "evidence_observed_at": "2026-08-15T22:00:00+00:00",
        "observed_at": "2026-08-15T22:00:00+00:00",
        "coverage": {"start": "2026-08-14", "end": "2026-08-15"},
        "validity": {
            "valid_from": "2026-08-15T22:00:00+00:00",
            "valid_until": "2026-09-15T22:00:00+00:00",
        },
        "public_terms_url": "https://example.test/platform-terms",
        "calendar_version": "xnas-calendar-2026-v1",
        "listings": [
            {
                "listing_id": "listing-us-xnas-meta",
                "market": "XNAS",
                "security_id": "security-meta-class-a",
                "symbols": [
                    {
                        "symbol": "META",
                        "valid_from": "2022-06-09",
                        "valid_to": None,
                    }
                ],
                "sessions": ["2026-08-14", "2026-08-15"],
                "unadjusted_prices": [
                    {"session_date": "2026-08-14", "close": "98.00"},
                    {"session_date": "2026-08-15", "close": "100.00"},
                ],
                "company_actions": [],
                "company_actions_status": "complete",
                "lifecycle": [
                    {
                        "status": "active",
                        "effective_date": "2012-05-18",
                        "source_event_id": "nasdaq-meta-listing",
                    }
                ],
            }
        ],
    }
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    checksum = hashlib.sha256(encoded).hexdigest()
    evidence_ref = object_repository.put_verified(
        BytesIO(encoded),
        expected_checksum=checksum,
        metadata={"content_type": "application/json"},
    )
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    workflow, issuer = _authorized_workflow(
        state_store,
        object_repository,
        source_id="platform-us-prices",
        now=now,
    )
    attestation_id = _attest(
        issuer,
        object_repository,
        evidence,
        source_id="platform-us-prices",
        listing_id="listing-us-xnas-meta",
        market="XNAS",
        trace_id="trace-ticket-08-platform-attestation",
    )
    attestation = state_store.get_verified_governance_artifact(
        artifact_id=attestation_id,
        artifact_kind="historical_evidence_attestation",
    )

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            trace_id="trace-ticket-08-platform-observed",
            attestation_id=attestation_id,
        )
    )

    assert outcome.status == "qualified"
    assert outcome.reason_code == "historical_evidence_qualified"
    assert outcome.use_scope == ("production",)
    assert outcome.claim_id is not None
    assert "verification" in outcome.artifact_ids
    claim = state_store.get_verified_governance_artifact(
        artifact_id=outcome.claim_id,
        artifact_kind="historical_availability_claim",
    )
    assert claim == {
        "claim_schema_version": "historical-availability-claim/v1",
        "schema_version": "us-unadjusted-eod-v1",
        "listing_id": "listing-us-xnas-meta",
        "market": "XNAS",
        "source_id": "platform-us-prices",
        "evidence_level": "platform_observed",
        "evidence_status": "qualified",
        "attestation_id": attestation_id,
        "evidence_object_id": evidence_ref.object_id,
        "evidence_checksum": checksum,
        "observation_receipt_id": attestation["observation_receipt_id"],
        "evidence_version": "platform-observation-2026-08-15",
        "evidence_revision": "rev-1",
        "observation_kind": "platform_observation",
        "observation_reference": "platform://raw-price-partition/2026-08-15",
        "evidence_observed_at": "2026-08-15T22:00:00+00:00",
        "first_observed_at": "2026-08-16T02:00:00+00:00",
        "observed_start": "2026-08-14",
        "observed_end": "2026-08-15",
        "source_policy_id": "ticket-08/platform-us-prices-policy-v1",
        "source_basis_id": "ENGINEERING-HISTORICAL-RECONSTRUCTION-01",
        "public_terms_url": "https://example.test/platform-terms",
        "valid_from": "2026-08-15T22:00:00+00:00",
        "valid_until": "2026-09-15T22:00:00+00:00",
        "qualified_at": "2026-08-16T02:00:00+00:00",
        "status": "qualified",
        "exact_sessions_verified": True,
        "integrity_verified": True,
        "company_actions_verified": True,
        "listing_lifecycle_verified": True,
        "qualification_artifact_id": outcome.artifact_ids["verification"],
        "supersedes_claim_id": None,
    }
    parsed_claim = HistoricalAvailabilityClaim.from_payload(claim)
    assert not QualifiedHistoricalAvailabilityClaimVerifier(
        state_store,
        evaluated_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    ).is_usable(claim_id=outcome.claim_id, claim=parsed_claim)
    attestation_trace = state_store.get_trace_evidence("trace-ticket-08-platform-attestation")
    assert attestation_trace["artifact_kinds"] == [
        "source_retrieval_receipt",
        "historical_evidence_attestation",
    ]
    receipt = state_store.get_canonical_artifact(str(attestation["observation_receipt_id"]))
    assert receipt["artifact_kind"] == "source_retrieval_receipt"
    assert receipt["payload"] == {
        "object_id": evidence_ref.object_id,
        "request_id": "trace-ticket-08-platform-attestation:historical-observation",
        "source_id": "platform-us-prices",
        "source_mode": "current",
        "source_revision": "rev-1",
        "distribution_id": "platform-us-prices-archive",
        "distribution_url": "https://archive.example.test/platform-us-prices.json",
        "sanitized_source_uri": "platform://raw-price-partition/2026-08-15",
        "acquired_at": "2026-08-16T02:00:00Z",
        "checkpoint_before": None,
        "checkpoint_after": None,
    }
    assert {event["action"] for event in attestation_trace["audit_events"]} == {
        "market_data.collect"
    }
    qualification_trace = state_store.get_trace_evidence("trace-ticket-08-platform-observed")
    assert {event["action"] for event in qualification_trace["audit_events"]} == {
        "price_qualification.govern"
    }

    missing_attestation = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            trace_id="trace-ticket-08-attestation-required",
        )
    )
    assert missing_attestation.status == "quarantined"
    assert missing_attestation.reason_code == "historical_evidence_attestation_required"
    assert missing_attestation.claim_id is None

    shared_workflow, shared_issuer = _authorized_workflow(
        state_store,
        object_repository,
        source_id="platform-us-shared-principal",
        now=now,
        separate_principals=False,
    )
    shared_principal = shared_workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-shared-principal",
            trace_id="trace-ticket-08-shared-principal-qualification",
            attestation_id=_attest(
                shared_issuer,
                object_repository,
                evidence,
                source_id="platform-us-shared-principal",
                listing_id="listing-us-xnas-meta",
                market="XNAS",
                trace_id="trace-ticket-08-shared-principal-attestation",
            ),
        )
    )
    assert shared_principal.status == "quarantined"
    assert shared_principal.reason_code == "historical_evidence_separation_of_duties_required"

    incomplete_evidence = deepcopy(evidence)
    incomplete_listing = cast(list[dict[str, object]], incomplete_evidence["listings"])[0]
    incomplete_listing["company_actions_status"] = "unknown"
    incomplete_attestation = _attest(
        issuer,
        object_repository,
        incomplete_evidence,
        source_id="platform-us-prices",
        listing_id="listing-us-xnas-meta",
        market="XNAS",
        trace_id="trace-ticket-08-missing-actions-attestation",
    )
    incomplete = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            trace_id="trace-ticket-08-missing-actions",
            attestation_id=incomplete_attestation,
        )
    )
    assert incomplete.status == "quarantined"
    assert incomplete.reason_code == "historical_evidence_company_actions_incomplete"
    assert incomplete.claim_id is None
    report = state_store.get_canonical_artifact(incomplete.artifact_ids["qualification_report"])
    report_payload = cast(dict[str, object], report["payload"])
    assert report_payload["status"] == "quarantined"

    future_observation = deepcopy(evidence)
    future_observation["observed_at"] = "2026-08-17T02:00:00+00:00"
    future_observation_attestation = _attest(
        issuer,
        object_repository,
        future_observation,
        source_id="platform-us-prices",
        listing_id="listing-us-xnas-meta",
        market="XNAS",
        trace_id="trace-ticket-08-future-observation-attestation",
    )
    future = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            trace_id="trace-ticket-08-future-observation",
            attestation_id=future_observation_attestation,
        )
    )
    assert future.status == "quarantined"
    assert future.reason_code == "historical_evidence_observation_chronology_invalid"

    listing = cast(list[dict[str, object]], evidence["listings"])[0]
    mismatched_calendar_attestation = issuer.issue(
        HistoricalEvidenceAttestationCommand(
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            evidence_content=encoded,
            calendar_content=_evidence_content(
                {
                    "schema_version": "historical-realized-calendar/v1",
                    "source_reference": ("https://archive.example.test/platform-us-prices.json"),
                    "market": "XNAS",
                    "version": evidence["calendar_version"],
                    "sessions": ["2026-08-14"],
                },
            ),
            reference_content=_evidence_content(
                {
                    "schema_version": "historical-listing-reference/v1",
                    "source_reference": ("https://archive.example.test/platform-us-prices.json"),
                    "listing": {
                        key: listing[key]
                        for key in (
                            "listing_id",
                            "market",
                            "security_id",
                            "symbols",
                            "lifecycle",
                            "company_actions",
                        )
                    },
                },
            ),
            trace_id="trace-ticket-08-calendar-mismatch-attestation",
        )
    )
    calendar_mismatch = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            trace_id="trace-ticket-08-calendar-mismatch",
            attestation_id=mismatched_calendar_attestation,
        )
    )
    assert calendar_mismatch.status == "quarantined"
    assert calendar_mismatch.reason_code == "historical_evidence_calendar_mismatch"

    malformed_content = b"{not-json"
    malformed_attestation = issuer.issue(
        HistoricalEvidenceAttestationCommand(
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            evidence_content=malformed_content,
            calendar_content=_evidence_content(
                {
                    "schema_version": "historical-realized-calendar/v1",
                    "market": "XNAS",
                    "version": evidence["calendar_version"],
                    "sessions": listing["sessions"],
                },
            ),
            reference_content=_evidence_content(
                {
                    "schema_version": "historical-listing-reference/v1",
                    "listing": {
                        key: listing[key]
                        for key in (
                            "listing_id",
                            "market",
                            "security_id",
                            "symbols",
                            "lifecycle",
                            "company_actions",
                        )
                    },
                },
            ),
            trace_id="trace-ticket-08-malformed-attestation",
        )
    )
    malformed = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            trace_id="trace-ticket-08-malformed-evidence",
            attestation_id=malformed_attestation,
        )
    )
    assert malformed.status == "quarantined"
    assert malformed.reason_code == "historical_evidence_json_invalid"
    assert malformed.claim_id is None

    Path(evidence_ref.uri).write_bytes(b"corrupted-after-attestation")
    corrupted = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            trace_id="trace-ticket-08-corrupted-attested-object",
            attestation_id=attestation_id,
        )
    )
    assert corrupted.status == "quarantined"
    assert corrupted.reason_code == "historical_evidence_object_invalid"
    assert corrupted.claim_id is None


def test_archive_attestation_builds_reproducible_reconstruction_artifacts(
    tmp_path: Path,
) -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    sessions = _realized_weekdays(start=date(2026, 5, 4), count=41)
    closes = ["100.00"] * 41
    closes[21] = "100.30"
    closes[25] = "99.70"
    closes[40] = "100.20"
    evidence: dict[str, object] = {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": "us-unadjusted-eod-v1",
        "evidence_version": "archive-bundle-2026-08-15",
        "revision": "archive-rev-4",
        "observation_kind": "official_archive",
        "observation_reference": "https://archive.example.test/official-us-archive.json",
        "observed_at": "2026-08-15T22:00:00+00:00",
        "coverage": {"start": sessions[0], "end": sessions[-1]},
        "validity": {
            "valid_from": "2026-08-15T22:00:00+00:00",
            "valid_until": "2026-09-15T22:00:00+00:00",
        },
        "public_terms_url": "https://archive.example.test/terms",
        "calendar_version": "xnas-realized-calendar-2026-v1",
        "adjustment_rule_version": "internal-price-adjustment/v1",
        "label_rule_version": "trend-label-rule/v1",
        "code_provenance": "git:91867a06-test-fixture",
        "listings": [
            {
                "listing_id": "listing-us-xnas-meta",
                "market": "XNAS",
                "security_id": "security-meta-class-a",
                "symbols": [
                    {
                        "symbol": "META",
                        "valid_from": "2022-06-09",
                        "valid_to": None,
                    }
                ],
                "sessions": sessions,
                "unadjusted_prices": [
                    {"session_date": session, "close": close}
                    for session, close in zip(sessions, closes, strict=True)
                ],
                "company_actions": [],
                "company_actions_status": "complete",
                "lifecycle": [
                    {
                        "status": "active",
                        "effective_date": "2012-05-18",
                        "source_event_id": "nasdaq-meta-listing",
                    }
                ],
            }
        ],
    }
    evidence_object_id = _put_evidence(object_repository, evidence)
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    workflow, issuer = _authorized_workflow(
        state_store,
        object_repository,
        source_id="official-us-archive",
        now=now,
    )
    attestation_id = _attest(
        issuer,
        object_repository,
        evidence,
        source_id="official-us-archive",
        listing_id="listing-us-xnas-meta",
        market="XNAS",
        trace_id="trace-ticket-08-archive-attestation",
    )
    attestation = state_store.get_verified_governance_artifact(
        artifact_id=attestation_id,
        artifact_kind="historical_evidence_attestation",
    )
    assert attestation["distribution_bindings"] == [
        {
            "distribution_id": "official-us-archive-archive",
            "distribution_url": "https://archive.example.test/official-us-archive.json",
        }
    ]

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            trace_id="trace-ticket-08-archive-reconstruction",
            attestation_id=attestation_id,
        )
    )

    assert outcome.status == "qualified"
    assert outcome.use_scope == ("historical_reconstruction",)

    unbound_evidence = deepcopy(evidence)
    unbound_evidence["observation_reference"] = "https://untrusted.example.test/archive.json"
    unbound = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            trace_id="trace-ticket-08-unbound-archive-reference",
            attestation_id=_attest(
                issuer,
                object_repository,
                unbound_evidence,
                source_id="official-us-archive",
                listing_id="listing-us-xnas-meta",
                market="XNAS",
                trace_id="trace-ticket-08-unbound-archive-reference-attestation",
            ),
        )
    )
    assert unbound.status == "quarantined"
    assert unbound.reason_code == "historical_evidence_distribution_mismatch"
    assert set(outcome.artifact_ids) == {
        "claim",
        "verification",
        "qualification_report",
        "dataset",
        "adjustment_version",
        "mature_labels",
        "feature_snapshot",
        "fold_manifest",
    }
    labels_artifact = state_store.get_canonical_artifact(outcome.artifact_ids["mature_labels"])
    labels_reference = cast(dict[str, object], labels_artifact["payload"])
    labels = json.loads(
        object_repository.open_by_id(str(labels_reference["labels_object_id"])).read()
    )
    assert isinstance(labels, dict)
    assert labels["anchor_session_id"] == sessions[20]
    assert labels["labels"] == [
        {
            "horizon_sessions": 1,
            "target_session_id": sessions[21],
            "status": "mature",
            "reason_code": None,
            "future_return": "0.003",
            "sigma20": "0.0",
            "threshold": "0.0025",
            "label": "up",
        },
        {
            "horizon_sessions": 5,
            "target_session_id": sessions[25],
            "status": "mature",
            "reason_code": None,
            "future_return": "-0.003",
            "sigma20": "0.0",
            "threshold": "0.0025",
            "label": "down",
        },
        {
            "horizon_sessions": 20,
            "target_session_id": sessions[40],
            "status": "mature",
            "reason_code": None,
            "future_return": "0.002",
            "sigma20": "0.0",
            "threshold": "0.0025",
            "label": "flat",
        },
    ]
    for artifact_name in ("feature_snapshot", "fold_manifest"):
        artifact = state_store.get_canonical_artifact(outcome.artifact_ids[artifact_name])
        lineage = artifact["payload"]
        assert isinstance(lineage, dict)
        assert lineage["historical_availability_claim_id"] == outcome.claim_id
        assert lineage["evidence_object_id"] == evidence_object_id
        assert lineage["evidence_level"] == "archive_attested"
        assert lineage["calendar_version"] == "xnas-realized-calendar-2026-v1"
        assert lineage["adjustment_version_id"] == outcome.artifact_ids["adjustment_version"]
        assert lineage["label_rule_version"] == "trend-label-rule/v1"
        assert lineage["source_policy_id"] == "ticket-08/official-us-archive-policy-v1"
        assert lineage["code_provenance"] == "git:91867a06-test-fixture"
    report_artifact = state_store.get_canonical_artifact(
        outcome.artifact_ids["qualification_report"]
    )
    report = report_artifact["payload"]
    assert isinstance(report, dict)
    assert report["display_mode"] == "historical_reconstruction"
    assert report["production_prediction"] is False
    assert report["exclusion_reasons"] == []
    dataset_lineage = state_store.get_canonical_artifact(outcome.artifact_ids["dataset"])["payload"]
    assert isinstance(dataset_lineage, dict)
    dataset_object_id = str(dataset_lineage["dataset_object_id"])
    dataset_checksum = dataset_object_id.removeprefix("sha256:")
    dataset_stat = object_repository.stat(
        ObjectRef(
            object_id=dataset_object_id,
            checksum=dataset_checksum,
            uri=str(tmp_path / "objects" / "sha256" / dataset_checksum[:2] / dataset_checksum),
        )
    )
    assert dataset_stat["metadata"] == {
        "content_type": "application/json",
        "object_kind": "historical_reconstruction_dataset",
    }
    for artifact_name, forbidden_fields in (
        ("dataset", {"sessions", "symbols", "lifecycle", "unadjusted_prices"}),
        ("adjustment_version", {"adjusted_prices", "company_action_ids"}),
        ("mature_labels", {"labels"}),
    ):
        artifact_payload = state_store.get_canonical_artifact(outcome.artifact_ids[artifact_name])[
            "payload"
        ]
        assert isinstance(artifact_payload, dict)
        assert not forbidden_fields.intersection(artifact_payload)

    old_snapshot_id = outcome.artifact_ids["feature_snapshot"]
    old_snapshot = state_store.get_canonical_artifact(old_snapshot_id)
    revised_evidence = deepcopy(evidence)
    revised_evidence["revision"] = "archive-rev-5-correction"
    revised_prices = cast(
        list[dict[str, object]],
        cast(list[dict[str, object]], revised_evidence["listings"])[0]["unadjusted_prices"],
    )
    revised_prices[21]["close"] = "100.40"
    revised_attestation_id = _attest(
        issuer,
        object_repository,
        revised_evidence,
        source_id="official-us-archive",
        listing_id="listing-us-xnas-meta",
        market="XNAS",
        trace_id="trace-ticket-08-archive-correction-attestation",
    )
    revised = workflow.execute(
        HistoricalEvidenceCommand(
            action="supersede",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            trace_id="trace-ticket-08-archive-correction",
            attestation_id=revised_attestation_id,
            prior_claim_id=outcome.claim_id,
        )
    )

    assert revised.claim_id != outcome.claim_id
    assert revised.artifact_ids["feature_snapshot"] != old_snapshot_id
    impact = state_store.get_verified_governance_artifact(
        artifact_id=revised.artifact_ids["impact"],
        artifact_kind="historical_claim_impact",
    )
    assert set(cast(list[str], impact["affected_artifact_ids"])) == {
        outcome.artifact_ids["qualification_report"],
        outcome.artifact_ids["dataset"],
        outcome.artifact_ids["adjustment_version"],
        outcome.artifact_ids["mature_labels"],
        outcome.artifact_ids["feature_snapshot"],
        outcome.artifact_ids["fold_manifest"],
    }
    assert state_store.get_canonical_artifact(old_snapshot_id) == old_snapshot


def test_claim_upgrade_and_revocation_append_impact_without_rewriting_prior_view(
    tmp_path: Path,
) -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")

    def platform_evidence(*, revision: str, observed_at: str) -> dict[str, object]:
        return {
            "schema_version": "historical-reconstruction-evidence/v1",
            "price_schema_version": "taiwan-unadjusted-eod-v1",
            "evidence_version": f"platform-evidence-{revision}",
            "revision": revision,
            "observation_kind": "platform_observation",
            "observation_reference": f"platform://prices/{revision}",
            "observed_at": observed_at,
            "coverage": {"start": "2026-08-14", "end": "2026-08-15"},
            "validity": {
                "valid_from": observed_at,
                "valid_until": "2026-10-01T00:00:00+00:00",
            },
            "public_terms_url": "https://example.test/platform-terms",
            "calendar_version": "xtai-calendar-2026-v1",
            "listings": [
                {
                    "listing_id": "listing-tw-2330-xtai",
                    "market": "XTAI",
                    "security_id": "security-tw-2330",
                    "symbols": [{"symbol": "2330", "valid_from": "1994-09-05", "valid_to": None}],
                    "sessions": ["2026-08-14", "2026-08-15"],
                    "unadjusted_prices": [
                        {"session_date": "2026-08-14", "close": "1000.00"},
                        {"session_date": "2026-08-15", "close": "1005.00"},
                    ],
                    "company_actions": [],
                    "company_actions_status": "complete",
                    "lifecycle": [
                        {
                            "status": "active",
                            "effective_date": "1994-09-05",
                            "source_event_id": "twse-2330-listing",
                        }
                    ],
                }
            ],
        }

    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    workflow, issuer = _authorized_workflow(
        state_store,
        object_repository,
        source_id="platform-tw-prices",
        now=now,
    )
    first_evidence = platform_evidence(revision="rev-1", observed_at="2026-08-15T22:00:00+00:00")
    first = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-tw-2330-xtai",
            market="XTAI",
            source_id="platform-tw-prices",
            trace_id="trace-ticket-08-claim-v1",
            attestation_id=_attest(
                issuer,
                object_repository,
                first_evidence,
                source_id="platform-tw-prices",
                listing_id="listing-tw-2330-xtai",
                market="XTAI",
                trace_id="trace-ticket-08-claim-v1-attestation",
            ),
        )
    )
    assert first.claim_id is not None
    original_view = state_store.get_verified_governance_artifact(
        artifact_id=first.claim_id,
        artifact_kind="historical_availability_claim",
    )

    upgraded_evidence = platform_evidence(revision="rev-2", observed_at="2026-08-16T00:30:00+00:00")
    upgraded = workflow.execute(
        HistoricalEvidenceCommand(
            action="supersede",
            listing_id="listing-tw-2330-xtai",
            market="XTAI",
            source_id="platform-tw-prices",
            trace_id="trace-ticket-08-claim-v2",
            attestation_id=_attest(
                issuer,
                object_repository,
                upgraded_evidence,
                source_id="platform-tw-prices",
                listing_id="listing-tw-2330-xtai",
                market="XTAI",
                trace_id="trace-ticket-08-claim-v2-attestation",
            ),
            prior_claim_id=first.claim_id,
        )
    )

    assert upgraded.status == "qualified"
    assert upgraded.claim_id is not None
    assert upgraded.claim_id != first.claim_id
    assert "impact" in upgraded.artifact_ids
    upgraded_claim = state_store.get_verified_governance_artifact(
        artifact_id=upgraded.claim_id,
        artifact_kind="historical_availability_claim",
    )
    assert upgraded_claim["supersedes_claim_id"] == first.claim_id
    assert (
        state_store.get_verified_governance_artifact(
            artifact_id=first.claim_id,
            artifact_kind="historical_availability_claim",
        )
        == original_view
    )
    impact = state_store.get_verified_governance_artifact(
        artifact_id=upgraded.artifact_ids["impact"],
        artifact_kind="historical_claim_impact",
    )
    assert impact["event"] == "superseded"
    assert impact["prior_claim_id"] == first.claim_id
    assert impact["replacement_claim_id"] == upgraded.claim_id

    revoked = workflow.execute(
        HistoricalEvidenceCommand(
            action="revoke",
            listing_id="listing-tw-2330-xtai",
            market="XTAI",
            source_id="platform-tw-prices",
            trace_id="trace-ticket-08-claim-revoked",
            prior_claim_id=upgraded.claim_id,
        )
    )

    assert revoked.status == "revoked"
    assert revoked.claim_id == upgraded.claim_id
    assert (
        state_store.get_verified_governance_artifact(
            artifact_id=upgraded.claim_id,
            artifact_kind="historical_availability_claim",
        )
        == upgraded_claim
    )
    revocation = state_store.get_verified_governance_artifact(
        artifact_id=revoked.artifact_ids["impact"],
        artifact_kind="historical_claim_impact",
    )
    assert revocation["event"] == "revoked"
    assert revocation["prior_claim_id"] == upgraded.claim_id
    parsed_upgraded_claim = HistoricalAvailabilityClaim.from_payload(upgraded_claim)
    assert not QualifiedHistoricalAvailabilityClaimVerifier(
        state_store,
        evaluated_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    ).is_usable(claim_id=upgraded.claim_id, claim=parsed_upgraded_claim)


def test_missing_exact_label_endpoint_is_not_shifted_to_next_available_price(
    tmp_path: Path,
) -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    sessions = _realized_weekdays(start=date(2026, 5, 4), count=41)
    prices = [
        {"session_date": session, "close": "100.00"}
        for session in sessions
        if session != sessions[25]
    ]
    evidence: dict[str, object] = {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": "us-unadjusted-eod-v1",
        "evidence_version": "archive-missing-endpoint-v1",
        "revision": "rev-1",
        "observation_kind": "official_archive",
        "observation_reference": "https://archive.example.test/official-us-archive.json",
        "observed_at": "2026-08-15T22:00:00+00:00",
        "coverage": {"start": sessions[0], "end": sessions[-1]},
        "validity": {
            "valid_from": "2026-08-15T22:00:00+00:00",
            "valid_until": "2026-09-15T22:00:00+00:00",
        },
        "public_terms_url": "https://archive.example.test/terms",
        "calendar_version": "xnas-realized-calendar-2026-v1",
        "adjustment_rule_version": "internal-price-adjustment/v1",
        "label_rule_version": "trend-label-rule/v1",
        "code_provenance": "git:91867a06-test-fixture",
        "listings": [
            {
                "listing_id": "listing-us-xnas-meta",
                "market": "XNAS",
                "security_id": "security-meta-class-a",
                "symbols": [{"symbol": "META", "valid_from": "2022-06-09", "valid_to": None}],
                "sessions": sessions,
                "unadjusted_prices": prices,
                "company_actions": [],
                "company_actions_status": "complete",
                "lifecycle": [
                    {
                        "status": "active",
                        "effective_date": "2012-05-18",
                        "source_event_id": "nasdaq-meta-listing",
                    }
                ],
            }
        ],
    }
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    workflow, issuer = _authorized_workflow(
        state_store,
        object_repository,
        source_id="official-us-archive",
        now=now,
    )
    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            trace_id="trace-ticket-08-missing-endpoint",
            attestation_id=_attest(
                issuer,
                object_repository,
                evidence,
                source_id="official-us-archive",
                listing_id="listing-us-xnas-meta",
                market="XNAS",
                trace_id="trace-ticket-08-missing-endpoint-attestation",
            ),
        )
    )

    assert outcome.status == "qualified"
    labels_artifact = state_store.get_canonical_artifact(outcome.artifact_ids["mature_labels"])
    labels_reference = cast(dict[str, object], labels_artifact["payload"])
    labels_payload = json.loads(
        object_repository.open_by_id(str(labels_reference["labels_object_id"])).read()
    )
    assert isinstance(labels_payload, dict)
    labels = cast(list[dict[str, object]], labels_payload["labels"])
    horizon_5 = labels[1]
    assert horizon_5 == {
        "horizon_sessions": 5,
        "target_session_id": sessions[25],
        "status": "invalid_endpoint",
        "reason_code": "exact_target_price_missing",
        "future_return": None,
        "sigma20": "0.0",
        "threshold": "0.0025",
        "label": None,
    }
    assert sessions[26] not in str(horizon_5)
    report = state_store.get_canonical_artifact(outcome.artifact_ids["qualification_report"])[
        "payload"
    ]
    assert isinstance(report, dict)
    assert report["exact_endpoints_verified"] is False
    assert report["exclusion_reasons"] == ["invalid_endpoint:horizon_5"]

    short_evidence = deepcopy(evidence)
    short_evidence["revision"] = "rev-insufficient-history"
    short_listing = cast(list[dict[str, object]], short_evidence["listings"])[0]
    short_listing["sessions"] = sessions[:20]
    short_listing["unadjusted_prices"] = [
        {"session_date": session, "close": "100.00"} for session in sessions[:20]
    ]
    short_evidence["coverage"] = {"start": sessions[0], "end": sessions[19]}
    short = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            trace_id="trace-ticket-08-insufficient-history",
            attestation_id=_attest(
                issuer,
                object_repository,
                short_evidence,
                source_id="official-us-archive",
                listing_id="listing-us-xnas-meta",
                market="XNAS",
                trace_id="trace-ticket-08-insufficient-history-attestation",
            ),
        )
    )
    short_labels_reference = cast(
        dict[str, object],
        state_store.get_canonical_artifact(short.artifact_ids["mature_labels"])["payload"],
    )
    short_labels = json.loads(
        object_repository.open_by_id(str(short_labels_reference["labels_object_id"])).read()
    )
    assert short_labels["anchor_session_id"] == sessions[19]
    assert short_labels["labels"] == [
        {
            "horizon_sessions": horizon,
            "target_session_id": None,
            "status": "invalid_history",
            "reason_code": "insufficient_20_session_history",
            "future_return": None,
            "sigma20": None,
            "threshold": None,
            "label": None,
        }
        for horizon in (1, 5, 20)
    ]


def test_reconstruction_preserves_security_identity_and_realized_session_gaps(
    tmp_path: Path,
) -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    scheduled_sessions = _realized_weekdays(start=date(2026, 5, 4), count=42)
    closed_session = scheduled_sessions[10]
    realized_sessions = [session for session in scheduled_sessions if session != closed_session]
    evidence: dict[str, object] = {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": "us-unadjusted-eod-v1",
        "evidence_version": "ticker-reuse-realized-calendar-v1",
        "revision": "rev-1",
        "observation_kind": "official_archive",
        "observation_reference": (
            "https://archive.example.test/official-us-reused-symbol-archive.json"
        ),
        "observed_at": "2026-08-15T22:00:00+00:00",
        "coverage": {"start": realized_sessions[0], "end": realized_sessions[-1]},
        "validity": {
            "valid_from": "2026-08-15T22:00:00+00:00",
            "valid_until": "2026-09-15T22:00:00+00:00",
        },
        "public_terms_url": "https://archive.example.test/terms",
        "calendar_version": "xnas-realized-calendar-with-closure-v1",
        "adjustment_rule_version": "internal-price-adjustment/v1",
        "label_rule_version": "trend-label-rule/v1",
        "code_provenance": "git:91867a06-test-fixture",
        "listings": [
            {
                "listing_id": "listing-us-xnas-acme-predecessor",
                "market": "XNAS",
                "security_id": "security-acme-predecessor",
                "symbols": [
                    {
                        "symbol": "ACME",
                        "valid_from": "2010-01-04",
                        "valid_to": "2020-12-31",
                    }
                ],
                "sessions": ["2020-12-30", "2020-12-31"],
                "unadjusted_prices": [
                    {"session_date": "2020-12-30", "close": "50.00"},
                    {"session_date": "2020-12-31", "close": "50.00"},
                ],
                "company_actions": [],
                "company_actions_status": "complete",
                "lifecycle": [
                    {
                        "status": "delisted",
                        "effective_date": "2020-12-31",
                        "source_event_id": "nasdaq-acme-predecessor-delisting",
                    }
                ],
            },
            {
                "listing_id": "listing-us-xnas-acme-successor",
                "market": "XNAS",
                "security_id": "security-acme-successor",
                "symbols": [{"symbol": "ACME", "valid_from": "2021-01-04", "valid_to": None}],
                "sessions": realized_sessions,
                "unadjusted_prices": [
                    {"session_date": session, "close": "100.00"} for session in realized_sessions
                ],
                "company_actions": [],
                "company_actions_status": "complete",
                "lifecycle": [
                    {
                        "status": "active",
                        "effective_date": "2021-01-04",
                        "source_event_id": "nasdaq-acme-successor-listing",
                    }
                ],
            },
        ],
    }

    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    workflow, issuer = _authorized_workflow(
        state_store,
        object_repository,
        source_id="official-us-reused-symbol-archive",
        now=now,
    )
    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-acme-successor",
            market="XNAS",
            source_id="official-us-reused-symbol-archive",
            trace_id="trace-ticket-08-ticker-reuse-realized-calendar",
            attestation_id=_attest(
                issuer,
                object_repository,
                evidence,
                source_id="official-us-reused-symbol-archive",
                listing_id="listing-us-xnas-acme-successor",
                market="XNAS",
                trace_id="trace-ticket-08-ticker-reuse-attestation",
            ),
        )
    )

    dataset_reference = cast(
        dict[str, object],
        state_store.get_canonical_artifact(outcome.artifact_ids["dataset"])["payload"],
    )
    dataset = json.loads(
        object_repository.open_by_id(str(dataset_reference["dataset_object_id"])).read()
    )
    assert dataset["listing_id"] == "listing-us-xnas-acme-successor"
    assert dataset["security_id"] == "security-acme-successor"
    assert dataset["symbols"] == [{"symbol": "ACME", "valid_from": "2021-01-04", "valid_to": None}]
    assert closed_session not in cast(list[str], dataset["sessions"])
    labels_reference = cast(
        dict[str, object],
        state_store.get_canonical_artifact(outcome.artifact_ids["mature_labels"])["payload"],
    )
    labels = json.loads(
        object_repository.open_by_id(str(labels_reference["labels_object_id"])).read()
    )
    assert [label["target_session_id"] for label in labels["labels"]] == [
        realized_sessions[21],
        realized_sessions[25],
        realized_sessions[40],
    ]

    invalid_symbol_evidence = deepcopy(evidence)
    invalid_symbol_listing = cast(list[dict[str, object]], invalid_symbol_evidence["listings"])[1]
    invalid_symbol_listing["symbols"] = [
        {"symbol": "ACME", "valid_from": realized_sessions[1], "valid_to": None}
    ]
    invalid_symbol = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-acme-successor",
            market="XNAS",
            source_id="official-us-reused-symbol-archive",
            trace_id="trace-ticket-08-symbol-validity-gap",
            attestation_id=_attest(
                issuer,
                object_repository,
                invalid_symbol_evidence,
                source_id="official-us-reused-symbol-archive",
                listing_id="listing-us-xnas-acme-successor",
                market="XNAS",
                trace_id="trace-ticket-08-symbol-validity-gap-attestation",
            ),
        )
    )
    assert invalid_symbol.status == "quarantined"
    assert invalid_symbol.reason_code == "historical_evidence_symbol_mismatch"


def test_reconstruction_applies_company_actions_before_maturing_labels(
    tmp_path: Path,
) -> None:
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    object_repository = FilesystemObjectRepository(tmp_path / "objects")
    sessions = _realized_weekdays(start=date(2026, 5, 4), count=41)
    split_session = sessions[21]
    evidence: dict[str, object] = {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": "us-unadjusted-eod-v1",
        "evidence_version": "split-adjustment-v1",
        "revision": "rev-1",
        "observation_kind": "official_archive",
        "observation_reference": ("https://archive.example.test/official-us-split-archive.json"),
        "observed_at": "2026-08-15T22:00:00+00:00",
        "coverage": {"start": sessions[0], "end": sessions[-1]},
        "validity": {
            "valid_from": "2026-08-15T22:00:00+00:00",
            "valid_until": "2026-09-15T22:00:00+00:00",
        },
        "public_terms_url": "https://archive.example.test/terms",
        "calendar_version": "xnas-realized-calendar-2026-v1",
        "adjustment_rule_version": "internal-price-adjustment/v1",
        "label_rule_version": "trend-label-rule/v1",
        "code_provenance": "git:91867a06-test-fixture",
        "listings": [
            {
                "listing_id": "listing-us-xnas-split",
                "market": "XNAS",
                "security_id": "security-us-split",
                "symbols": [{"symbol": "SPLT", "valid_from": "2020-01-02", "valid_to": None}],
                "sessions": sessions,
                "unadjusted_prices": [
                    {
                        "session_date": session,
                        "close": "200.00" if session < split_session else "100.00",
                    }
                    for session in sessions
                ],
                "company_actions": [
                    {
                        "effective_date": split_session,
                        "kind": "split",
                        "value": "2",
                        "source_action_id": "split-2-for-1",
                    }
                ],
                "company_actions_status": "complete",
                "lifecycle": [
                    {
                        "status": "active",
                        "effective_date": "2020-01-02",
                        "source_event_id": "nasdaq-split-listing",
                    }
                ],
            }
        ],
    }

    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    workflow, issuer = _authorized_workflow(
        state_store,
        object_repository,
        source_id="official-us-split-archive",
        now=now,
    )
    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-split",
            market="XNAS",
            source_id="official-us-split-archive",
            trace_id="trace-ticket-08-split-adjustment",
            attestation_id=_attest(
                issuer,
                object_repository,
                evidence,
                source_id="official-us-split-archive",
                listing_id="listing-us-xnas-split",
                market="XNAS",
                trace_id="trace-ticket-08-split-attestation",
            ),
        )
    )

    adjustment_reference = cast(
        dict[str, object],
        state_store.get_canonical_artifact(outcome.artifact_ids["adjustment_version"])["payload"],
    )
    adjustment = json.loads(
        object_repository.open_by_id(str(adjustment_reference["adjustment_object_id"])).read()
    )
    adjusted_prices = cast(list[dict[str, str]], adjustment["adjusted_prices"])
    assert adjusted_prices[0] == {
        "session_date": sessions[0],
        "adjusted_close": "100.00",
    }
    assert adjusted_prices[21] == {
        "session_date": split_session,
        "adjusted_close": "100.00",
    }
    labels_reference = cast(
        dict[str, object],
        state_store.get_canonical_artifact(outcome.artifact_ids["mature_labels"])["payload"],
    )
    labels = json.loads(
        object_repository.open_by_id(str(labels_reference["labels_object_id"])).read()
    )
    assert cast(list[dict[str, object]], labels["labels"])[0]["label"] == "flat"
