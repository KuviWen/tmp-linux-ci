from __future__ import annotations

from dataclasses import replace
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
    SourceRateLimited,
)
from stock_forecasting.price_eligibility_query import PriceEligibilityQuery


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


class RateLimitedAdapter:
    def load(self, request: SourcePartitionRequest) -> NoReturn:
        raise SourceRateLimited(
            retry_after_seconds=45,
            rate_limit_policy_id="provider-rate-limit-v1",
        )


def _blocked_price_application(
    tmp_path: Path, now: datetime
) -> tuple[Application, LocalApiKeyIdentity]:
    identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-researcher",
        environment="development",
        scopes={"price_research_eligibility.read"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"internal", "licensed"},
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-ticket-06-blocked-v1",
                principal_id=identity.context.principal_id,
                actions=frozenset({"price_research_eligibility.read"}),
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
    workload_identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-blocked-source-workload",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    blocked_collect_policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-blocked-source-workload-v1",
                principal_id=workload_identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(),
        source_entitlements=(),
    )
    current_adapter = NeverCalledAdapter()
    historical_adapter = NeverCalledAdapter()
    listing_id = "10000000-0000-4000-8000-000000000001"
    data_supply = DataSupply(
        authorization_policy=blocked_collect_policy,
        security_context=workload_identity.context,
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
    workload_identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-quarantine-workload",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
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
        action_grants=(
            ActionGrant(
                version_id="grant-quarantine-workload-v1",
                principal_id=workload_identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
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
                principal_id=workload_identity.context.principal_id,
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
        security_context=workload_identity.context,
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
    application.price_eligibility_query = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=application.authorization_policy,
        authorization_time=now,
        source_authorization_policy=lambda _principal_id: collect_policy,
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

    revoked_policy = AuthorizationPolicy(
        action_grants=collect_policy.action_grants,
        source_policies=collect_policy.source_policies,
        source_entitlements=(
            replace(
                collect_policy.source_entitlements[0],
                version_id="entitlement-quarantine-revoked-v2",
                status="revoked",
            ),
        ),
    )
    application.price_eligibility_query = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=application.authorization_policy,
        authorization_time=now,
        source_authorization_policy=lambda _principal_id: revoked_policy,
    )
    operations = client.get(
        "/api/v1/operations/sources",
        headers={"Authorization": identity.credential.authorization_header()},
    ).json()
    assert {item["status"] for item in operations["items"]} == {"policy_blocked"}
    assert all(
        item["current_policy_decision"]["reason_code"] == "source_entitlement_revoked"
        for item in operations["items"]
    )
    assert all(item["policy_reason_code"] == "authorized" for item in operations["items"])


def test_rate_limited_listing_is_deferred_without_claiming_saved_candidate_data(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    application, identity = _blocked_price_application(tmp_path, now)
    workload_identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-rate-limited-workload",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    listing_id = "10000000-0000-4000-8000-000000000001"
    source_id = "twse-rate-limited-contract"
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
    collect_policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-rate-limited-workload-v1",
                principal_id=workload_identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="policy-rate-limited-v1",
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
                version_id="entitlement-rate-limited-v1",
                principal_id=workload_identity.context.principal_id,
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
    outcome = DataSupply(
        authorization_policy=collect_policy,
        security_context=workload_identity.context,
        adapters={source_id: RateLimitedAdapter()},
        object_repository=application.object_repository,
        state_store=application.state_store,
        clock=lambda: now,
    ).materialize(
        SourcePartitionRequest(
            request_id="request-rate-limited-ui",
            trace_id="trace-rate-limited-ui",
            source_id=source_id,
            mode="current",
            listing_ids=(listing_id,),
            start_date=date(2026, 8, 14),
            end_date=date(2026, 8, 14),
            expected_checkpoint=None,
        )
    )
    assert outcome.status == "deferred"
    application.price_eligibility_query = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=application.authorization_policy,
        authorization_time=now,
        source_authorization_policy=lambda _principal_id: collect_policy,
    )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {"Authorization": identity.credential.authorization_header()}

    rest_response = client.get(
        f"/api/v1/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )
    ui_response = client.get(
        f"/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )

    assert rest_response.status_code == 200
    rest = rest_response.json()
    assert rest["status"] == "deferred"
    assert rest["reason_code"] == "source_collection_deferred"
    assert rest["checks"]["policy"] == "passed"
    assert rest["sources"][0]["raw_object_id"] is None
    assert ui_response.status_code == 200
    assert "來源限流，尚未取得資料" in ui_response.text
    assert "checkpoint 未前進" in ui_response.text
    assert "來源候選資料已保存" not in ui_response.text
    assert "已具研究資格" not in ui_response.text

    revoked_policy = AuthorizationPolicy(
        action_grants=collect_policy.action_grants,
        source_policies=collect_policy.source_policies,
        source_entitlements=(
            replace(
                collect_policy.source_entitlements[0],
                version_id="entitlement-rate-limited-revoked-v2",
                status="revoked",
            ),
        ),
    )
    application.price_eligibility_query = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=application.authorization_policy,
        authorization_time=now,
        source_authorization_policy=lambda _principal_id: revoked_policy,
    )
    revoked = client.get(
        "/api/v1/operations/sources",
        headers=headers,
    ).json()["items"][0]
    assert revoked["status"] == "deferred"
    assert revoked["reason_code"] == "source_rate_limited"
    assert revoked["checks"]["policy"] == "blocked"
    assert revoked["current_policy_decision"]["reason_code"] == "source_entitlement_revoked"
    revoked_listing = client.get(
        f"/api/v1/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    ).json()
    revoked_ui = client.get(
        f"/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )
    assert revoked_listing["status"] == "policy_blocked"
    assert revoked_listing["reason_code"] == "source_rights_not_effective"
    assert revoked_listing["sources"][0]["status"] == "deferred"
    assert "來源權利已撤銷" in revoked_ui.text
    assert "先前來源限流" in revoked_ui.text


def test_current_source_use_revocation_blocks_rest_and_ui_before_policy_expiry(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    application, identity = _blocked_price_application(tmp_path, now)
    workload_identity = LocalApiKeyIdentity.issue(
        owner="ticket-06-source-workload",
        environment="development",
        scopes={"market_data.collect"},
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"licensed"},
    )
    listing_id = "10000000-0000-4000-8000-000000000001"
    source_id = "twse-current-rights-contract"
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
    source_policy = SourcePolicyVersion(
        version_id="policy-current-rights-v1",
        dataset_id=source_id,
        allowed_actions=frozenset({"market_data.collect"}),
        purposes=frozenset({"price_research"}),
        environments=frozenset({"development"}),
        data_protection_class="licensed",
        resource_states=frozenset({"active"}),
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
        allowed_uses=required_uses,
    )
    source_entitlement = SourceEntitlement(
        version_id="entitlement-current-rights-v1",
        principal_id=workload_identity.context.principal_id,
        dataset_id=source_id,
        status="active",
        allowed_actions=frozenset({"market_data.collect"}),
        purposes=frozenset({"price_research"}),
        environments=frozenset({"development"}),
        valid_from=now - timedelta(days=1),
        valid_to=now + timedelta(days=1),
        allowed_uses=required_uses,
    )
    collect_policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="grant-current-rights-workload-v1",
                principal_id=workload_identity.context.principal_id,
                actions=frozenset({"market_data.collect"}),
                environment="development",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
        ),
        source_policies=(source_policy,),
        source_entitlements=(source_entitlement,),
    )
    coverage = SourceCollectionCoverage(
        requested_start=date(2026, 8, 14),
        requested_end=date(2026, 8, 14),
        observed_start=date(2026, 8, 14),
        observed_end=date(2026, 8, 14),
        complete=True,
    )
    loaded = LoadedSourcePartition(
        collection=CollectedSourcePartition(
            request_id="request-current-rights",
            source_id=source_id,
            acquired_at=now,
            sanitized_source_uri="provider://current-rights-contract",
            media_type="application/json",
            raw_payload=b'{"close":"100"}',
            checkpoint_before=None,
            checkpoint_after="page:1",
            coverage=coverage,
            source_revision="revision-current-rights-v1",
        ),
        decoded=DecodedSourcePartition(
            source_id=source_id,
            schema_version="taiwan-unadjusted-eod-v1",
            source_revision="revision-current-rights-v1",
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
                    effective_date=date(2026, 8, 14),
                    status="active",
                    source_event_id="lifecycle-current-rights",
                ),
            ),
            adjusted_close_cross_checks=(Decimal("100"),),
            identity_assertion_ids=("identity-current-rights",),
            parent_object_ids=(),
        ),
    )
    DataSupply(
        authorization_policy=collect_policy,
        security_context=workload_identity.context,
        adapters={source_id: LiteralQuarantineAdapter(loaded)},
        object_repository=application.object_repository,
        state_store=application.state_store,
        clock=lambda: now,
    ).materialize(
        SourcePartitionRequest(
            request_id=loaded.collection.request_id,
            trace_id="trace-current-rights-materialization",
            source_id=source_id,
            mode="current",
            listing_ids=(listing_id,),
            start_date=coverage.requested_start,
            end_date=coverage.requested_end,
            expected_checkpoint=None,
        )
    )
    application.price_eligibility_query = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=application.authorization_policy,
        authorization_time=now,
        source_authorization_policy=lambda _principal_id: collect_policy,
    )
    client = TestClient(create_web_app(application), client=("127.0.0.1", 50000))
    headers = {"Authorization": identity.credential.authorization_header()}

    allowed_rest = client.get(
        f"/api/v1/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    ).json()
    allowed_operations = client.get("/api/v1/operations/sources", headers=headers).json()

    assert allowed_rest["reason_code"] == "dependency_evidence_unverified"
    assert allowed_rest["sources"][0]["status"] == "published"
    assert allowed_rest["sources"][0]["current_policy_decision"]["reason_code"] == "authorized"
    assert allowed_operations["items"][0]["status"] == "published"
    assert allowed_operations["items"][0]["current_policy_decision"]["reason_code"] == "authorized"

    def unavailable_source_policy(_principal_id: str) -> AuthorizationPolicy:
        raise KeyError("current source policy unavailable")

    application.price_eligibility_query = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=application.authorization_policy,
        authorization_time=now,
        source_authorization_policy=unavailable_source_policy,
    )
    unavailable_trace_id = "trace-current-rights-policy-unavailable"
    unavailable_response = client.get(
        "/api/v1/operations/sources",
        headers={**headers, "X-Trace-Id": unavailable_trace_id},
    )

    assert unavailable_response.status_code == 200
    unavailable = unavailable_response.json()["items"][0]
    assert unavailable["status"] == "policy_blocked"
    assert unavailable["reason_code"] == "source_rights_not_effective"
    assert (
        unavailable["current_policy_decision"]["reason_code"] == "source_rights_policy_unavailable"
    )
    unavailable_trace = application.state_store.get_trace_evidence(unavailable_trace_id)
    assert "current_source_rights_resolution" in unavailable_trace["artifact_kinds"]
    assert (
        unavailable["current_policy_decision"]["evidence_artifact_id"]
        in unavailable_trace["artifact_ids"]
    )
    unavailable_audit = application.state_store.list_audit_events(trace_id=unavailable_trace_id)
    assert {event["action"] for event in unavailable_audit} == {"price_research_eligibility.read"}

    revoked_policy = AuthorizationPolicy(
        action_grants=collect_policy.action_grants,
        source_policies=(
            replace(
                source_policy,
                version_id="policy-current-rights-use-removed-v2",
                allowed_uses=required_uses - {"retain_7_years"},
            ),
        ),
        source_entitlements=(source_entitlement,),
    )
    application.price_eligibility_query = PriceEligibilityQuery(
        application.state_store,
        authorization_policy=application.authorization_policy,
        authorization_time=now,
        source_authorization_policy=lambda _principal_id: revoked_policy,
    )
    rest_response = client.get(
        f"/api/v1/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )
    operations_trace_id = "trace-current-rights-operations"
    operations_response = client.get(
        "/api/v1/operations/sources",
        headers={**headers, "X-Trace-Id": operations_trace_id},
    )
    ui_response = client.get(
        f"/research/listings/{listing_id}/price-eligibility",
        headers=headers,
    )

    assert rest_response.status_code == 200
    rest = rest_response.json()
    assert rest["status"] == "policy_blocked"
    assert rest["reason_code"] == "source_rights_not_effective"
    assert rest["checks"]["policy"] == "blocked"
    assert operations_response.status_code == 200
    operations = operations_response.json()
    assert len(operations["items"]) == 1
    assert operations["items"][0]["status"] == "policy_blocked"
    assert operations["items"][0]["reason_code"] == "source_rights_not_effective"
    assert operations["items"][0]["checks"]["policy"] == "blocked"
    assert (
        operations["items"][0]["current_policy_decision"]["reason_code"]
        == "source_policy_use_denied"
    )
    current_decision = operations["items"][0]["current_policy_decision"]
    assert current_decision["dataset_id"] == source_id
    operations_audit = application.state_store.list_audit_events(trace_id=operations_trace_id)
    assert {event["action"] for event in operations_audit} == {"price_research_eligibility.read"}
    resolution_trace = application.state_store.get_trace_evidence(operations_trace_id)
    assert "current_source_rights_resolution" in resolution_trace["artifact_kinds"]
    assert current_decision["evidence_artifact_id"] in resolution_trace["artifact_ids"]
    assert ui_response.status_code == 200
    assert "資格阻擋" in ui_response.text
    assert "已具研究資格" not in ui_response.text
