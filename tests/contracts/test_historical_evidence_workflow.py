from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

from stock_forecasting.data_supply import HistoricalAvailabilityClaim
from stock_forecasting.historical_evidence import (
    HistoricalEvidenceCommand,
    HistoricalEvidenceLevel,
    HistoricalEvidenceWorkflow,
    QualifiedHistoricalAvailabilityClaimVerifier,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
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
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    checksum = hashlib.sha256(encoded).hexdigest()
    return object_repository.put_verified(
        BytesIO(encoded),
        expected_checksum=checksum,
        metadata={"content_type": "application/json"},
    ).object_id


def test_candidate_evidence_cannot_self_approve(tmp_path: Path) -> None:
    workflow = HistoricalEvidenceWorkflow(
        StateStore("sqlite+pysqlite:///:memory:", create_schema=True),
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    )

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="manual-upload",
            evidence_level="self_asserted",
            evidence_object_id="sha256:" + ("0" * 64),
            source_policy_id="policy-manual-v1",
            public_terms_url="https://example.test/terms",
            trace_id="trace-ticket-08-self-asserted",
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
    evidence_level: HistoricalEvidenceLevel,
    reason_code: str,
) -> None:
    workflow = HistoricalEvidenceWorkflow(
        StateStore("sqlite+pysqlite:///:memory:", create_schema=True),
        object_repository=FilesystemObjectRepository(tmp_path / "objects"),
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    )

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-tw-2330-xtai",
            market="XTAI",
            source_id="current-page",
            evidence_level=evidence_level,
            evidence_object_id="sha256:" + ("0" * 64),
            source_policy_id="policy-current-page-v1",
            public_terms_url="https://example.test/current-terms",
            trace_id=f"trace-ticket-08-{evidence_level}",
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
    workflow = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=object_repository,
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    )

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices",
            evidence_level="platform_observed",
            evidence_object_id=evidence_ref.object_id,
            source_policy_id="policy-us-price-v1",
            public_terms_url="https://example.test/platform-terms",
            trace_id="trace-ticket-08-platform-observed",
        )
    )

    assert outcome.status == "qualified"
    assert outcome.reason_code == "historical_evidence_qualified"
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
        "evidence_object_id": evidence_ref.object_id,
        "evidence_checksum": checksum,
        "evidence_version": "platform-observation-2026-08-15",
        "evidence_revision": "rev-1",
        "observation_kind": "platform_observation",
        "observation_reference": "platform://raw-price-partition/2026-08-15",
        "evidence_observed_at": "2026-08-15T22:00:00+00:00",
        "observed_start": "2026-08-14",
        "observed_end": "2026-08-15",
        "source_policy_id": "policy-us-price-v1",
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
    assert QualifiedHistoricalAvailabilityClaimVerifier(
        state_store,
        evaluated_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    ).is_usable(claim_id=outcome.claim_id, claim=parsed_claim)

    incomplete_evidence = deepcopy(evidence)
    incomplete_listing = cast(list[dict[str, object]], incomplete_evidence["listings"])[0]
    incomplete_listing["company_actions_status"] = "unknown"
    incomplete = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="platform-us-prices-incomplete-actions",
            evidence_level="platform_observed",
            evidence_object_id=_put_evidence(object_repository, incomplete_evidence),
            source_policy_id="policy-us-price-v1",
            public_terms_url="https://example.test/platform-terms",
            trace_id="trace-ticket-08-missing-actions",
        )
    )
    assert incomplete.status == "quarantined"
    assert incomplete.reason_code == "historical_evidence_company_actions_incomplete"
    assert incomplete.claim_id is None
    report = state_store.get_canonical_artifact(incomplete.artifact_ids["qualification_report"])
    report_payload = cast(dict[str, object], report["payload"])
    assert report_payload["status"] == "quarantined"


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
        "observation_reference": "https://archive.example.test/us/eod/rev-4.json",
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
    workflow = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=object_repository,
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    )

    outcome = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            evidence_level="archive_attested",
            evidence_object_id=evidence_object_id,
            source_policy_id="policy-us-archive-research-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id="trace-ticket-08-archive-reconstruction",
        )
    )

    assert outcome.status == "qualified"
    assert outcome.use_scope == ("historical_reconstruction",)
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
    labels = labels_artifact["payload"]
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
        assert lineage["calendar_version"] == "xnas-realized-calendar-2026-v1"
        assert lineage["adjustment_version_id"] == outcome.artifact_ids["adjustment_version"]
        assert lineage["label_rule_version"] == "trend-label-rule/v1"
        assert lineage["source_policy_id"] == "policy-us-archive-research-v1"
        assert lineage["code_provenance"] == "git:91867a06-test-fixture"
    report_artifact = state_store.get_canonical_artifact(
        outcome.artifact_ids["qualification_report"]
    )
    report = report_artifact["payload"]
    assert isinstance(report, dict)
    assert report["display_mode"] == "historical_reconstruction"
    assert report["production_prediction"] is False
    assert report["exclusion_reasons"] == []

    old_snapshot_id = outcome.artifact_ids["feature_snapshot"]
    old_snapshot = state_store.get_canonical_artifact(old_snapshot_id)
    revised_evidence = deepcopy(evidence)
    revised_evidence["revision"] = "archive-rev-5-correction"
    revised_prices = cast(
        list[dict[str, object]],
        cast(list[dict[str, object]], revised_evidence["listings"])[0]["unadjusted_prices"],
    )
    revised_prices[21]["close"] = "100.40"
    revised = workflow.execute(
        HistoricalEvidenceCommand(
            action="supersede",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            evidence_level="archive_attested",
            evidence_object_id=_put_evidence(object_repository, revised_evidence),
            source_policy_id="policy-us-archive-research-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id="trace-ticket-08-archive-correction",
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

    workflow = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=object_repository,
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    )
    first = workflow.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-tw-2330-xtai",
            market="XTAI",
            source_id="platform-tw-prices",
            evidence_level="platform_observed",
            evidence_object_id=_put_evidence(
                object_repository,
                platform_evidence(revision="rev-1", observed_at="2026-08-15T22:00:00+00:00"),
            ),
            source_policy_id="policy-tw-platform-v1",
            public_terms_url="https://example.test/platform-terms",
            trace_id="trace-ticket-08-claim-v1",
        )
    )
    assert first.claim_id is not None
    original_view = state_store.get_verified_governance_artifact(
        artifact_id=first.claim_id,
        artifact_kind="historical_availability_claim",
    )

    upgraded = workflow.execute(
        HistoricalEvidenceCommand(
            action="supersede",
            listing_id="listing-tw-2330-xtai",
            market="XTAI",
            source_id="platform-tw-prices",
            evidence_level="platform_observed",
            evidence_object_id=_put_evidence(
                object_repository,
                platform_evidence(revision="rev-2", observed_at="2026-08-16T00:30:00+00:00"),
            ),
            source_policy_id="policy-tw-platform-v1",
            public_terms_url="https://example.test/platform-terms",
            trace_id="trace-ticket-08-claim-v2",
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
            evidence_level="platform_observed",
            evidence_object_id=str(upgraded_claim["evidence_object_id"]),
            source_policy_id="policy-tw-platform-v1",
            public_terms_url="https://example.test/platform-terms",
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
        "observation_reference": "https://archive.example.test/us/missing-endpoint.json",
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
    outcome = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=object_repository,
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    ).execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-archive",
            evidence_level="archive_attested",
            evidence_object_id=_put_evidence(object_repository, evidence),
            source_policy_id="policy-us-archive-research-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id="trace-ticket-08-missing-endpoint",
        )
    )

    assert outcome.status == "qualified"
    labels_artifact = state_store.get_canonical_artifact(outcome.artifact_ids["mature_labels"])
    labels_payload = labels_artifact["payload"]
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
    short = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=object_repository,
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    ).execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-meta",
            market="XNAS",
            source_id="official-us-short-archive",
            evidence_level="archive_attested",
            evidence_object_id=_put_evidence(object_repository, short_evidence),
            source_policy_id="policy-us-archive-research-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id="trace-ticket-08-insufficient-history",
        )
    )
    short_labels_artifact = state_store.get_canonical_artifact(short.artifact_ids["mature_labels"])
    short_labels = short_labels_artifact["payload"]
    assert isinstance(short_labels, dict)
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
        "observation_reference": "https://archive.example.test/us/reused-symbol.json",
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

    outcome = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=object_repository,
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    ).execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-acme-successor",
            market="XNAS",
            source_id="official-us-reused-symbol-archive",
            evidence_level="archive_attested",
            evidence_object_id=_put_evidence(object_repository, evidence),
            source_policy_id="policy-us-archive-research-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id="trace-ticket-08-ticker-reuse-realized-calendar",
        )
    )

    dataset = state_store.get_canonical_artifact(outcome.artifact_ids["dataset"])["payload"]
    assert isinstance(dataset, dict)
    assert dataset["listing_id"] == "listing-us-xnas-acme-successor"
    assert dataset["security_id"] == "security-acme-successor"
    assert dataset["symbols"] == [{"symbol": "ACME", "valid_from": "2021-01-04", "valid_to": None}]
    assert closed_session not in cast(list[str], dataset["sessions"])
    labels = state_store.get_canonical_artifact(outcome.artifact_ids["mature_labels"])["payload"]
    assert isinstance(labels, dict)
    assert [label["target_session_id"] for label in labels["labels"]] == [
        realized_sessions[21],
        realized_sessions[25],
        realized_sessions[40],
    ]


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
        "observation_reference": "https://archive.example.test/us/split.json",
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

    outcome = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=object_repository,
        observed_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    ).execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id="listing-us-xnas-split",
            market="XNAS",
            source_id="official-us-split-archive",
            evidence_level="archive_attested",
            evidence_object_id=_put_evidence(object_repository, evidence),
            source_policy_id="policy-us-archive-research-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id="trace-ticket-08-split-adjustment",
        )
    )

    adjustment = state_store.get_canonical_artifact(outcome.artifact_ids["adjustment_version"])[
        "payload"
    ]
    assert isinstance(adjustment, dict)
    adjusted_prices = cast(list[dict[str, str]], adjustment["adjusted_prices"])
    assert adjusted_prices[0] == {
        "session_date": sessions[0],
        "adjusted_close": "100.00",
    }
    assert adjusted_prices[21] == {
        "session_date": split_session,
        "adjusted_close": "100.00",
    }
    labels = state_store.get_canonical_artifact(outcome.artifact_ids["mature_labels"])["payload"]
    assert isinstance(labels, dict)
    assert cast(list[dict[str, object]], labels["labels"])[0]["label"] == "flat"
