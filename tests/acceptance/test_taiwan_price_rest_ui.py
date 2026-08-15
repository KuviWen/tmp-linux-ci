from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import Application, build_test_application
from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationPolicy,
    LocalApiKeyIdentity,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.data_supply import (
    CanonicalPriceRow,
    CollectedSourcePartition,
    DataSupply,
    DecodedSourcePartition,
    ListingLifecycleRecord,
    LoadedSourcePartition,
    SourceCollectionCoverage,
    SourcePartitionRequest,
)


class NeverCalledAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def load(self, request: SourcePartitionRequest) -> NoReturn:
        self.calls += 1
        raise AssertionError("unverified Taiwan price sources must not be contacted")


class LiteralQuarantineAdapter:
    def __init__(self, loaded: LoadedSourcePartition) -> None:
        self.loaded = loaded

    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition:
        return self.loaded


def _blocked_price_application(
    tmp_path: Path, now: datetime
) -> tuple[Application, LocalApiKeyIdentity]:
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-researcher",
        environment="development",
        scopes={"market_data.collect", "price_research_eligibility.read"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"internal", "licensed"},
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-ticket-06-blocked-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"market_data.collect", "price_research_eligibility.read"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-price-eligibility-metadata-v1",
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
                version_id="entitlement-price-eligibility-metadata-v1",
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
    application = build_test_application(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'rest-ui.db'}",
        object_root=tmp_path / "objects",
        observed_at=now,
        authorization_time=now,
        local_identity=identity,
        authorization_policy_override=policy,
    )
    return application, identity


def test_blocked_taiwan_listing_is_traceable_in_research_operations_and_ui(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    application, identity = _blocked_price_application(tmp_path, now)
    current_adapter = NeverCalledAdapter()
    historical_adapter = NeverCalledAdapter()
    listing_id = "10000000-0000-4000-8000-000000000001"
    data_supply = DataSupply(
        authorization_policy=application.authorization_policy,
        security_context=application.security_context,
        adapters={
            "twse-open-data-current": current_adapter,
            "twse-contracted-history": historical_adapter,
        },
        object_repository=application.object_repository,
        state_store=application.state_store,
        clock=lambda: now,
    )
    for source_id, mode in (
        ("twse-open-data-current", "current"),
        ("twse-contracted-history", "historical"),
    ):
        data_supply.materialize(
            SourcePartitionRequest(
                request_id=f"request-{mode}-blocked",
                trace_id=f"trace-p2-trace-tw-01-{mode}-blocked",
                source_id=source_id,
                mode=mode,  # type: ignore[arg-type]
                listing_ids=(listing_id,),
                start_date=date(2019, 8, 14),
                end_date=date(2026, 8, 14),
                expected_checkpoint=None,
            )
        )
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
    assert research["listing_id"] == listing_id
    assert research["market"] == "XTAI"
    assert research["status"] == "policy_blocked"
    assert research["reason_code"] == "dependency_evidence_unverified"
    assert research["dependency_id"] == "DEP-MKT-TW-01"
    assert research["formally_qualified"] is False
    assert research["checks"] == {
        "coverage": "not_evaluated",
        "depth": "not_evaluated",
        "integrity": "not_evaluated",
        "policy": "blocked",
        "schema": "not_evaluated",
    }
    assert {source["source_mode"] for source in research["sources"]} == {
        "current",
        "historical",
    }
    assert all(source["dataset_version_id"] is None for source in research["sources"])
    assert all(source["adjustment_version_id"] is None for source in research["sources"])
    assert all(source["raw_object_id"] is None for source in research["sources"])
    assert {source["evaluated_at"] for source in research["sources"]} == {"2026-08-15T01:00:00Z"}

    assert operations_response.status_code == 200
    operations = operations_response.json()
    assert {item["source_id"] for item in operations["items"]} == {
        "twse-open-data-current",
        "twse-contracted-history",
    }
    assert {item["status"] for item in operations["items"]} == {"policy_blocked"}
    assert all(
        item["reason_code"] == "dependency_evidence_unverified" for item in operations["items"]
    )

    assert ui_response.status_code == 200
    assert "台股行情研究資格" in ui_response.text
    assert "政策阻擋" in ui_response.text
    assert "DEP-MKT-TW-01" in ui_response.text
    assert "未接觸來源" in ui_response.text
    assert "資料集版本：尚未建立" in ui_response.text
    assert "調整版本：尚未建立" in ui_response.text
    assert current_adapter.calls == 0
    assert historical_adapter.calls == 0


def test_quarantined_listing_ui_never_claims_research_eligibility(tmp_path: Path) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    application, identity = _blocked_price_application(tmp_path, now)
    listing_id = "10000000-0000-4000-8000-000000000001"
    required_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_7_years",
            "transform",
            "model",
            "internal_display",
            "backup_restore",
        }
    )
    source_id = "twse-quarantine-contract"
    collect_policy = AuthorizationPolicy(
        action_grants=application.authorization_policy.action_grants,
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-quarantine-v1",
                dataset_id=source_id,
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                allowed_uses=required_uses,
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="entitlement-quarantine-v1",
                principal_id=identity.context.principal_id,
                dataset_id=source_id,
                status="active",
                allowed_actions=frozenset({"market_data.collect"}),
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
                allowed_uses=required_uses,
            ),
        ),
    )
    coverage = SourceCollectionCoverage(
        requested_start=date(2019, 8, 14),
        requested_end=date(2026, 8, 14),
        observed_start=date(2019, 8, 14),
        observed_end=date(2026, 8, 13),
        complete=False,
    )

    def loaded_for(mode: str) -> LoadedSourcePartition:
        request_id = f"request-quarantine-{mode}"
        revision = f"revision-quarantine-{mode}"
        return LoadedSourcePartition(
            collection=CollectedSourcePartition(
                request_id=request_id,
                source_id=source_id,
                acquired_at=now,
                sanitized_source_uri="provider://quarantine-contract",
                media_type="application/json",
                raw_payload=f'{{"mode":"{mode}"}}'.encode(),
                checkpoint_before=None,
                checkpoint_after=f"checkpoint-{mode}",
                coverage=coverage,
                source_revision=revision,
            ),
            decoded=DecodedSourcePartition(
                source_id=source_id,
                schema_version="taiwan-unadjusted-eod-v1",
                source_revision=revision,
                prices=(
                    CanonicalPriceRow(
                        listing_id=listing_id,
                        session_date=date(2026, 8, 14),
                        open=Decimal("100"),
                        high=Decimal("101"),
                        low=Decimal("99"),
                        close=Decimal("100"),
                        volume=1,
                    ),
                ),
                company_actions=(),
                listing_lifecycle=(
                    ListingLifecycleRecord(
                        listing_id=listing_id,
                        effective_date=date(2019, 8, 14),
                        status="active",
                        source_event_id="lifecycle-quarantine",
                    ),
                ),
                adjusted_close_cross_checks=(Decimal("100"),),
                identity_assertion_ids=("identity-quarantine",),
                parent_object_ids=(),
            ),
        )

    adapter = LiteralQuarantineAdapter(loaded_for("current"))
    data_supply = DataSupply(
        authorization_policy=collect_policy,
        security_context=identity.context,
        adapters={source_id: adapter},
        object_repository=application.object_repository,
        state_store=application.state_store,
        clock=lambda: now,
    )
    for mode in ("current", "historical"):
        adapter.loaded = loaded_for(mode)
        data_supply.materialize(
            SourcePartitionRequest(
                request_id=adapter.loaded.collection.request_id,
                trace_id=f"trace-quarantine-{mode}",
                source_id=source_id,
                mode=mode,
                listing_ids=(listing_id,),
                start_date=coverage.requested_start,
                end_date=coverage.requested_end,
                expected_checkpoint=None,
            )
        )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))

    response = client.get(
        f"/research/listings/{listing_id}/price-eligibility",
        headers={"Authorization": identity.credential.authorization_header()},
    )

    assert response.status_code == 200
    assert "資料隔離" in response.text
    assert "不具研究資格" in response.text
    assert "incomplete_coverage" in response.text
    assert "已具研究資格" not in response.text
