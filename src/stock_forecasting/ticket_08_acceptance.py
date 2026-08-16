from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.request import Request, urlopen

from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationAction,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceDistribution,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.data_supply import load_taiwan_stock_pool_manifest
from stock_forecasting.historical_evidence import (
    HistoricalEvidenceAttestationCommand,
    HistoricalEvidenceAttestationIssuer,
    HistoricalEvidenceCommand,
    HistoricalEvidenceWorkflow,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest


def _realized_weekdays(*, start: date, count: int) -> list[str]:
    sessions: list[str] = []
    candidate = start
    while len(sessions) < count:
        if candidate.weekday() < 5:
            sessions.append(candidate.isoformat())
        candidate += timedelta(days=1)
    return sessions


def _engineering_archive(
    *,
    source_id: str,
    listing_id: str,
    market: str,
    security_id: str,
    symbol: str,
    observed_at: datetime,
) -> dict[str, object]:
    sessions = _realized_weekdays(start=date(2026, 5, 4), count=41)
    closes = ["100.00"] * 41
    closes[21] = "101.00"
    return {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": (
            "taiwan-unadjusted-eod-v1" if market == "XTAI" else "us-unadjusted-eod-v1"
        ),
        "evidence_version": f"ticket-08-{market.lower()}-engineering-archive-v1",
        "revision": "rev-1",
        "observation_kind": "official_archive",
        "observation_reference": f"https://archive.example.test/{source_id}.json",
        "observed_at": observed_at.isoformat(),
        "coverage": {"start": sessions[0], "end": sessions[-1]},
        "validity": {
            "valid_from": (observed_at - timedelta(minutes=1)).isoformat(),
            "valid_until": (observed_at + timedelta(days=365)).isoformat(),
        },
        "public_terms_url": "https://archive.example.test/terms",
        "calendar_version": f"{market.lower()}-realized-calendar-2026-v1",
        "adjustment_rule_version": "internal-price-adjustment/v1",
        "label_rule_version": "trend-label-rule/v1",
        "code_provenance": "ticket-08-deployed-engineering-acceptance-v1",
        "listings": [
            {
                "listing_id": listing_id,
                "market": market,
                "security_id": security_id,
                "symbols": [{"symbol": symbol, "valid_from": "2012-01-01", "valid_to": None}],
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
                        "effective_date": "2012-01-01",
                        "source_event_id": f"ticket-08-{market.lower()}-{symbol}-listing",
                    }
                ],
            }
        ],
    }


def _put_json(
    repository: FilesystemObjectRepository,
    payload: dict[str, object],
) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    checksum = hashlib.sha256(content).hexdigest()
    return repository.put_verified(
        BytesIO(content),
        expected_checksum=checksum,
        metadata={
            "content_type": "application/json",
            "evidence_kind": "engineering_acceptance",
        },
    ).object_id


_REQUIRED_USES: frozenset[SourceUseRight] = frozenset(
    {
        "ingest",
        "retain_observed_history",
        "transform",
        "model",
        "internal_display",
        "backup_restore",
    }
)


def build_ticket_08_engineering_governance(
    *,
    observed_at: datetime,
    source_ids: tuple[str, ...],
) -> tuple[LocalApiKeyIdentity, LocalApiKeyIdentity, AuthorizationPolicy]:
    collector = LocalApiKeyIdentity.issue(
        owner="ticket-08-evidence-collector",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=observed_at - timedelta(hours=1),
        expires_at=observed_at + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    governor = LocalApiKeyIdentity.issue(
        owner="ticket-08-evidence-governor",
        environment="development",
        scopes={"price_qualification.govern"},
        issued_at=observed_at - timedelta(hours=1),
        expires_at=observed_at + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    grants = (
        ActionGrant(
            version_id="ticket-08/collector-grant-v1",
            principal_id=collector.context.principal_id,
            actions=frozenset({"market_data.collect"}),
            environment="development",
            valid_from=observed_at - timedelta(hours=1),
            valid_to=observed_at + timedelta(hours=23),
        ),
        ActionGrant(
            version_id="ticket-08/governor-grant-v1",
            principal_id=governor.context.principal_id,
            actions=frozenset({"price_qualification.govern"}),
            environment="development",
            valid_from=observed_at - timedelta(hours=1),
            valid_to=observed_at + timedelta(hours=23),
        ),
    )
    policies = tuple(
        SourcePolicyVersion(
            version_id=f"ticket-08/{source_id}-engineering-policy-v1",
            dataset_id=source_id,
            allowed_actions=frozenset({"market_data.collect", "price_qualification.govern"}),
            purposes=frozenset({"price_research"}),
            environments=frozenset({"development"}),
            data_protection_class="licensed",
            resource_states=frozenset({"active"}),
            valid_from=observed_at - timedelta(hours=1),
            valid_to=observed_at + timedelta(hours=23),
            allowed_uses=_REQUIRED_USES,
            access_basis="engineering_contract",
            source_basis_id="ENGINEERING-HISTORICAL-RECONSTRUCTION-01",
            distributions=(
                SourceDistribution(
                    dataset_id=f"{source_id}-archive",
                    distribution_url=f"https://archive.example.test/{source_id}.json",
                ),
            ),
        )
        for source_id in source_ids
    )
    entitlements = tuple(
        SourceEntitlement(
            version_id=f"ticket-08/{source_id}-{role}-entitlement-v1",
            principal_id=identity.context.principal_id,
            dataset_id=source_id,
            status="active",
            allowed_actions=frozenset({cast(AuthorizationAction, action)}),
            purposes=frozenset({"price_research"}),
            environments=frozenset({"development"}),
            valid_from=observed_at - timedelta(hours=1),
            valid_to=observed_at + timedelta(hours=23),
            allowed_uses=_REQUIRED_USES,
        )
        for source_id in source_ids
        for role, identity, action in (
            ("collector", collector, "market_data.collect"),
            ("governor", governor, "price_qualification.govern"),
        )
    )
    return (
        collector,
        governor,
        AuthorizationPolicy(
            action_grants=grants,
            source_policies=policies,
            source_entitlements=entitlements,
        ),
    )


def _get(
    *,
    base_url: str,
    path: str,
    identity: LocalApiKeyIdentity,
) -> tuple[int, str]:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Authorization": identity.credential.authorization_header()},
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed deployed base URL
        return response.status, response.read().decode("utf-8")


def run_ticket_08_acceptance(
    *,
    database_url: str,
    object_root: Path,
    observed_at: datetime,
    base_url: str,
    key_file: Path,
) -> dict[str, object]:
    identity = LocalApiKeyIdentity.load(key_file)
    state_store = StateStore(database_url, create_schema=False)
    repository = FilesystemObjectRepository(object_root)
    taiwan_listing = load_taiwan_stock_pool_manifest().listings[0]
    us_listing = load_us_stock_pool_manifest().listings[0]
    inputs = (
        (
            taiwan_listing.listing_id,
            "XTAI",
            taiwan_listing.security_id,
            taiwan_listing.external_security_code,
        ),
        (
            us_listing.listing_id,
            us_listing.market,
            us_listing.security_id,
            us_listing.external_security_code,
        ),
    )
    source_ids = tuple(
        f"ticket-08-{market.lower()}-engineering-archive" for _, market, _, _ in inputs
    )
    collector, governor, governance_policy = build_ticket_08_engineering_governance(
        observed_at=observed_at,
        source_ids=source_ids,
    )
    issuer = HistoricalEvidenceAttestationIssuer(
        state_store,
        object_repository=repository,
        authorization_policy=governance_policy,
        security_context=collector.context,
        clock=lambda: observed_at,
    )
    workflow = HistoricalEvidenceWorkflow(
        state_store,
        object_repository=repository,
        observed_at=observed_at,
        authorization_policy=governance_policy,
        security_context=governor.context,
    )
    outcomes = []
    rest_checks: list[bool] = []
    ui_checks: list[bool] = []
    for listing_id, market, security_id, symbol in inputs:
        source_id = f"ticket-08-{market.lower()}-engineering-archive"
        evidence = _engineering_archive(
            source_id=source_id,
            listing_id=listing_id,
            market=market,
            security_id=security_id,
            symbol=symbol,
            observed_at=observed_at,
        )
        evidence_object_id = _put_json(
            repository,
            evidence,
        )
        listing = cast(list[dict[str, object]], evidence["listings"])[0]
        calendar_object_id = _put_json(
            repository,
            {
                "schema_version": "historical-realized-calendar/v1",
                "source_reference": f"https://archive.example.test/{source_id}.json",
                "market": market,
                "version": evidence["calendar_version"],
                "sessions": listing["sessions"],
            },
        )
        reference_object_id = _put_json(
            repository,
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
        attestation_id = issuer.issue(
            HistoricalEvidenceAttestationCommand(
                listing_id=listing_id,
                market=market,
                source_id=source_id,
                evidence_content=repository.open_by_id(evidence_object_id).read(),
                calendar_content=repository.open_by_id(calendar_object_id).read(),
                reference_content=repository.open_by_id(reference_object_id).read(),
                trace_id=f"trace-ticket-08-{market.lower()}-attestation",
            )
        )
        outcome = workflow.execute(
            HistoricalEvidenceCommand(
                action="qualify",
                listing_id=listing_id,
                market=market,
                source_id=source_id,
                trace_id=f"trace-ticket-08-{market.lower()}-deployed",
                attestation_id=attestation_id,
            )
        )
        outcomes.append(outcome)
        rest_status, rest_text = _get(
            base_url=base_url,
            path=f"/api/v1/research/listings/{listing_id}/price-eligibility",
            identity=identity,
        )
        ui_status, ui_text = _get(
            base_url=base_url,
            path=f"/research/listings/{listing_id}/price-eligibility",
            identity=identity,
        )
        rest = json.loads(rest_text)
        historical = rest.get("historical_reconstruction", {})
        rest_checks.append(
            rest_status == 200
            and rest.get("formally_qualified") is False
            and historical.get("status") == "qualified"
            and historical.get("claim_id") == outcome.claim_id
            and historical.get("production_prediction") is False
        )
        ui_checks.append(
            ui_status == 200
            and "歷史重建" in ui_text
            and "不得視為 production prediction" in ui_text
            and str(outcome.claim_id) in ui_text
        )
    operations_status, operations_text = _get(
        base_url=base_url,
        path="/api/v1/operations/sources",
        identity=identity,
    )
    operation_items = json.loads(operations_text).get("items", [])
    claim_ids = {outcome.claim_id for outcome in outcomes}
    operation_claim_ids = {
        item.get("historical_availability_claim_id")
        for item in operation_items
        if item.get("source_mode") == "historical_reconstruction"
    }
    checks = {
        "dual_market_claims": all(outcome.status == "qualified" for outcome in outcomes),
        "reconstruction_artifacts": all(
            {
                "dataset",
                "adjustment_version",
                "mature_labels",
                "feature_snapshot",
                "fold_manifest",
                "qualification_report",
            }
            <= set(outcome.artifact_ids)
            for outcome in outcomes
        ),
        "research_rest": all(rest_checks),
        "research_ui": all(ui_checks),
        "operations_control": operations_status == 200 and claim_ids <= operation_claim_ids,
        "not_production_prediction": all(
            outcome.use_scope == ("historical_reconstruction",) for outcome in outcomes
        ),
    }
    return {
        "ticket": "08",
        "status": "passed" if all(checks.values()) else "failed",
        "evidence_kind": "engineering_acceptance",
        "formal_source_qualification": "not_claimed",
        "checks": checks,
        "claim_ids": sorted(str(claim_id) for claim_id in claim_ids),
        "trace_ids": [f"trace-ticket-08-{market.lower()}-deployed" for _, market, _, _ in inputs],
    }
