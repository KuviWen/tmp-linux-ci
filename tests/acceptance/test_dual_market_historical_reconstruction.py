from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceEntitlement,
    SourcePolicyVersion,
)
from stock_forecasting.historical_evidence import HistoricalEvidenceCommand
from stock_forecasting.platform.object_repository import FilesystemObjectRepository


def _realized_weekdays(*, start: date, count: int) -> list[str]:
    sessions: list[str] = []
    candidate = start
    while len(sessions) < count:
        if candidate.weekday() < 5:
            sessions.append(candidate.isoformat())
        candidate += timedelta(days=1)
    return sessions


def _price_read_policy(identity: LocalApiKeyIdentity, now: datetime) -> AuthorizationPolicy:
    return AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="ticket-08-price-read-grant-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"price_research_eligibility.read"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="ticket-08-price-read-policy-v1",
                dataset_id="price-research-eligibility",
                allowed_actions=frozenset({"price_research_eligibility.read"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="internal",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="ticket-08-price-read-entitlement-v1",
                principal_id=identity.context.principal_id,
                dataset_id="price-research-eligibility",
                status="active",
                allowed_actions=frozenset({"price_research_eligibility.read"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
    )


def _archive_evidence(
    *,
    listing_id: str,
    market: str,
    security_id: str,
    symbol: str,
) -> dict[str, object]:
    sessions = _realized_weekdays(start=date(2026, 5, 4), count=41)
    closes = ["100.00"] * 41
    closes[21] = "101.00"
    return {
        "schema_version": "historical-reconstruction-evidence/v1",
        "price_schema_version": (
            "taiwan-unadjusted-eod-v1" if market == "XTAI" else "us-unadjusted-eod-v1"
        ),
        "evidence_version": f"engineering-{market.lower()}-archive-v1",
        "revision": "rev-1",
        "observation_kind": "official_archive",
        "observation_reference": f"https://archive.example.test/{market}/engineering.json",
        "observed_at": "2026-08-15T22:00:00+00:00",
        "coverage": {"start": sessions[0], "end": sessions[-1]},
        "validity": {
            "valid_from": "2026-08-15T22:00:00+00:00",
            "valid_until": "2026-09-15T22:00:00+00:00",
        },
        "public_terms_url": "https://archive.example.test/terms",
        "calendar_version": f"{market.lower()}-realized-calendar-2026-v1",
        "adjustment_rule_version": "internal-price-adjustment/v1",
        "label_rule_version": "trend-label-rule/v1",
        "code_provenance": "git:91867a06-engineering-acceptance",
        "listings": [
            {
                "listing_id": listing_id,
                "market": market,
                "security_id": security_id,
                "symbols": [{"symbol": symbol, "valid_from": "2022-06-09", "valid_to": None}],
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
                        "source_event_id": f"{market.lower()}-{symbol}-listing",
                    }
                ],
            }
        ],
    }


def _put_evidence(
    object_repository: FilesystemObjectRepository,
    payload: dict[str, object],
) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    checksum = hashlib.sha256(content).hexdigest()
    return object_repository.put_verified(
        BytesIO(content),
        expected_checksum=checksum,
        metadata={"content_type": "application/json", "evidence_kind": "engineering"},
    ).object_id


@pytest.mark.parametrize(
    ("listing_id", "market", "security_id", "symbol"),
    [
        ("10000000-0000-4000-8000-000000000001", "XTAI", "security-tw-2330", "2330"),
        ("20000000-0000-4000-8000-000000000004", "XNAS", "security-us-meta", "META"),
    ],
)
def test_historical_reconstruction_is_visible_without_becoming_production_prediction(
    tmp_path: Path,
    listing_id: str,
    market: str,
    security_id: str,
    symbol: str,
) -> None:
    now = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-08-researcher",
        environment="development",
        scopes={"price_research_eligibility.read"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"internal"},
    )
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / f'{market}.db'}",
        object_root=tmp_path / f"{market}-objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_override=_price_read_policy(identity, now),
    )
    evidence_object_id = _put_evidence(
        application.object_repository,
        _archive_evidence(
            listing_id=listing_id,
            market=market,
            security_id=security_id,
            symbol=symbol,
        ),
    )
    outcome = application.historical_evidence.execute(
        HistoricalEvidenceCommand(
            action="qualify",
            listing_id=listing_id,
            market=market,
            source_id=f"engineering-{market.lower()}-archive",
            evidence_level="archive_attested",
            evidence_object_id=evidence_object_id,
            source_policy_id=f"engineering-{market.lower()}-archive-policy-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id=f"trace-ticket-08-{market.lower()}-reconstruction",
        )
    )
    assert outcome.claim_id is not None
    control_items = application.operations_control.list_historical_qualifications(
        trace_id=f"trace-ticket-08-{market.lower()}-operations-control",
        security_context=identity.context,
    )
    assert isinstance(control_items, list)
    assert control_items[0]["historical_availability_claim_id"] == outcome.claim_id
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {"Authorization": identity.credential.authorization_header()}

    research_response = client.get(
        f"/api/v1/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )
    operations_response = client.get("/api/v1/operations/sources", headers=headers)
    ui_response = client.get(
        f"/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )

    assert research_response.status_code == 200
    research = research_response.json()
    assert research["formally_qualified"] is False
    assert research["historical_reconstruction"]["status"] == "qualified"
    assert research["historical_reconstruction"]["claim_id"] == outcome.claim_id
    assert research["historical_reconstruction"]["production_prediction"] is False
    assert research["historical_reconstruction"]["display_mode"] == ("historical_reconstruction")
    assert operations_response.status_code == 200
    operations_item = next(
        item
        for item in operations_response.json()["items"]
        if item.get("historical_availability_claim_id") == outcome.claim_id
    )
    assert operations_item["status"] == "qualified"
    assert operations_item["source_mode"] == "historical_reconstruction"
    assert ui_response.status_code == 200
    assert "歷史重建" in ui_response.text
    assert "不得視為 production prediction" in ui_response.text
    assert outcome.claim_id in ui_response.text

    application.historical_evidence.execute(
        HistoricalEvidenceCommand(
            action="expire",
            listing_id=listing_id,
            market=market,
            source_id=f"engineering-{market.lower()}-archive",
            evidence_level="archive_attested",
            evidence_object_id=evidence_object_id,
            source_policy_id=f"engineering-{market.lower()}-archive-policy-v1",
            public_terms_url="https://archive.example.test/terms",
            trace_id=f"trace-ticket-08-{market.lower()}-expired",
            prior_claim_id=outcome.claim_id,
        )
    )
    expired_research = client.get(
        f"/api/v1/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    ).json()["historical_reconstruction"]
    expired_operations = client.get("/api/v1/operations/sources", headers=headers).json()["items"]
    expired_ui = client.get(
        f"/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )
    assert expired_research["status"] == "expired"
    assert (
        next(
            item
            for item in expired_operations
            if item.get("historical_availability_claim_id") == outcome.claim_id
        )["status"]
        == "expired"
    )
    assert "expired" in expired_ui.text
