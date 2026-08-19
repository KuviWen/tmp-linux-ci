from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import (
    ActionGrant,
    AuthorizationAction,
    AuthorizationPolicy,
    AuthorizationPurpose,
    LocalApiKeyIdentity,
    RuntimeEnvironment,
    SourceEntitlement,
    SourcePolicyVersion,
)
from stock_forecasting.model_governance import (
    InMemoryAssignmentPinStore,
    InMemoryCandidateArtifactRepository,
    InMemoryLifecycleStore,
    ServingAssignment,
    ServingAssignmentResolver,
)
from stock_forecasting.operations_control import OperationsControl
from stock_forecasting.outbox import EventCompatibility, NoRelayFault, SystemRelayClock
from stock_forecasting.platform.state_store import ImmutableStateConflict, StateStore
from stock_forecasting.production_eod import (
    ForecastExecution,
    ForecastRunCommand,
    InMemoryProductionPublicationStore,
    ProductionDataSelectionRequest,
    ProductionListingInput,
    ResolvedProductionDataSelection,
    SqlAlchemyProductionPublicationStore,
)
from stock_forecasting.research_query import ResearchQuery
from stock_forecasting.source_credentials import InMemorySecretProvider
from tests.modeling_support import lifecycle_candidate_bundle


class _FixedProductionDataSelectionResolver:
    def __init__(self, selection: ResolvedProductionDataSelection) -> None:
        self._selection = selection

    def resolve(
        self,
        request: ProductionDataSelectionRequest,
    ) -> ResolvedProductionDataSelection:
        assert request.market == self._selection.market
        assert request.information_cutoff == self._selection.information_cutoff
        assert request.stock_pool_version_id == self._selection.stock_pool_version_id
        return self._selection


class _ResearchOnlyApplication:
    def __init__(
        self,
        *,
        state_store: StateStore,
        research_query: ResearchQuery,
        local_identity: LocalApiKeyIdentity,
        authenticated_at: datetime,
    ) -> None:
        self.state_store = state_store
        self.research_query = research_query
        self.local_identity = local_identity
        self.security_context = local_identity.context
        self._authenticated_at = authenticated_at

    def authenticate_local_request(
        self,
        authorization_header: str,
        *,
        client_host: str,
    ) -> Any:
        return self.local_identity.verifier.authenticate(
            authorization_header,
            client_host=client_host,
            environment="development",
            authenticated_at=self._authenticated_at,
        )


class _RejectedNotificationTransport:
    def deliver(self, notification: dict[str, Any]) -> bool:
        assert notification["reason_code"] == "production_t_plus_120_breached"
        return False


def _listing_id(index: int, market: str = "XTAI") -> str:
    return str(uuid5(NAMESPACE_URL, f"ticket-10/{market.lower()}/listing/{index}"))


def test_deployed_production_entrypoint_fails_closed_without_data_selection() -> None:
    cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    application = build_test_application(observed_at=cutoff)

    with pytest.raises(ValueError, match="production_data_selection_unavailable"):
        application.run_production_eod(
            ForecastRunCommand(
                market="XTAI",
                information_cutoff=cutoff,
                stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key="production-provider-unavailable",
                trace_id="trace-production-provider-unavailable",
            )
        )


@pytest.mark.parametrize("market", ["XTAI", "XNAS"])
def test_production_eod_pins_one_selection_and_assignment_for_ten_listings(
    market: Literal["XTAI", "XNAS"],
) -> None:
    bundle = lifecycle_candidate_bundle(
        model_family_id="taiwan-us-price-trend-v1",
        logistic_macro_f1=0.52,
    )
    artifact = bundle.primary_artifact
    artifacts = InMemoryCandidateArtifactRepository()
    artifacts.put(
        artifact.artifact_id,
        artifact.serialized,
        object_kind="bootstrap_model_artifact",
    )
    lifecycle_store = InMemoryLifecycleStore()
    assignment = ServingAssignment.create(
        model_family_id="taiwan-us-price-trend-v1",
        candidate_id=bundle.candidate_id,
        artifact_id=artifact.artifact_id,
        previous_assignment_id=None,
        readiness_evidence_id="sha256:production-readiness",
        effective_from_batch_id="next-unstarted-eod",
        assigned_at=datetime(2026, 8, 18, 5, 0, tzinfo=UTC),
    )
    lifecycle_store.promote(
        command_id="arrange-production-assignment",
        model_family_id=assignment.model_family_id,
        expected_version=0,
        promotion_payload={"promotion_event_id": "sha256:arranged-promotion"},
        assignment_payload=assignment.to_payload(),
        occurred_at=assignment.assigned_at,
    )
    information_cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    listings = tuple(
        ProductionListingInput(
            listing_id=_listing_id(index, market),
            display_ticker=f"{2300 + index}" if market == "XTAI" else f"US{index}",
            market=market,
            dataset_version_id=f"sha256:dataset-{index}",
            calendar_version_id=f"sha256:{market.lower()}-calendar-v1",
            anchor_session_id=f"{market}:2026-08-18",
            target_session_ids=(
                (1, f"{market}:2026-08-19"),
                (5, f"{market}:2026-08-25"),
                (20, f"{market}:2026-09-15"),
            ),
            evidence_level="platform_observed",
            first_observed_at=information_cutoff,
            processed_at=information_cutoff,
            feature_values=(0.5, -0.25),
            support_status="full",
            unavailable_reason=None,
        )
        for index in range(1, 11)
    )
    selection = ResolvedProductionDataSelection.create(
        market=market,
        information_cutoff=information_cutoff,
        stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=listings,
    )
    publication_store = InMemoryProductionPublicationStore()
    execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        publication_store=publication_store,
        clock=lambda: information_cutoff,
    )

    publication = execution.run(
        ForecastRunCommand(
            market=market,
            information_cutoff=information_cutoff,
            stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key=f"{market.lower()}-2026-08-18-production",
            trace_id=f"trace-{market.lower()}-2026-08-18-production",
        )
    )

    assert publication.status == "completed"
    assert publication.execution_purpose == "production"
    assert len(publication.predictions) == 30
    assert {result.listing_id for result in publication.predictions} == {
        item.listing_id for item in listings
    }
    assert {result.horizon_sessions for result in publication.predictions} == {1, 5, 20}
    assert {result.data_selection_id for result in publication.predictions} == {
        selection.data_selection_id
    }
    assert {result.serving_assignment_id for result in publication.predictions} == {
        assignment.assignment_id
    }
    assert {result.feature_snapshot_id for result in publication.predictions} == {
        publication.feature_snapshot_id
    }
    for result in publication.predictions:
        assert result.status == "full"
        assert result.probabilities is not None
        assert (
            abs(
                result.probabilities["up"]
                + result.probabilities["flat"]
                + result.probabilities["down"]
                - 1.0
            )
            <= 1e-12
        )
        assert result.confidence_score is not None
        assert result.model_artifact_id == artifact.artifact_id
        assert result.calibrator_ids == artifact.calibrator_ids
        assert result.execution_purpose == "production"
    assert publication_store.get_batch(publication.forecast_batch_id) == publication
    assert publication.projection.core_projection_version == 1
    assert publication.projection.evidence_projection_version == 0
    assert publication.projection.stale is True
    assert publication.outbox_delivery_status == "pending"

    other_market: Literal["XTAI", "XNAS"] = "XNAS" if market == "XTAI" else "XTAI"
    mixed_market_selection = ResolvedProductionDataSelection.create(
        market=market,
        information_cutoff=information_cutoff,
        stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=(replace(listings[0], market=other_market), *listings[1:]),
    )
    invalid_execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(mixed_market_selection),
        artifact_repository=artifacts,
        publication_store=InMemoryProductionPublicationStore(),
        clock=lambda: information_cutoff,
    )
    with pytest.raises(ValueError, match="production_data_selection_invalid"):
        invalid_execution.run(
            ForecastRunCommand(
                market=market,
                information_cutoff=information_cutoff,
                stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key=f"{market.lower()}-mixed-market",
                trace_id=f"trace-{market.lower()}-mixed-market",
            )
        )

    invalid_store = InMemoryProductionPublicationStore()
    invalid_probability = replace(
        publication.predictions[0],
        probabilities={"up": 1.1, "flat": -0.1, "down": 0.0},
    )
    invalid_publication = replace(
        publication,
        predictions=(invalid_probability, *publication.predictions[1:]),
    )

    with pytest.raises(ValueError, match="production_probability_invalid"):
        invalid_store.publish(
            invalid_publication,
            trace_id="trace-invalid-probability",
            idempotency_key="invalid-probability",
        )

    assert invalid_store.get_batch(publication.forecast_batch_id) is None

    application = build_test_application(
        observed_at=information_cutoff,
        production_data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
    )
    application.model_artifact_repository.put(
        artifact.artifact_id,
        artifact.serialized,
        object_kind="bootstrap_model_artifact",
    )
    application.model_lifecycle_store.promote(
        command_id="arrange-deployed-production-assignment",
        model_family_id=assignment.model_family_id,
        expected_version=0,
        promotion_payload={"promotion_event_id": "sha256:deployed-promotion"},
        assignment_payload=assignment.to_payload(),
        occurred_at=assignment.assigned_at,
    )

    deployed = application.run_production_eod(
        ForecastRunCommand(
            market=market,
            information_cutoff=information_cutoff,
            stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key=f"{market.lower()}-2026-08-18-deployed",
            trace_id=f"P2-TRACE-EOD-01-DEPLOYED-{market}",
        )
    )

    assert deployed.status == "completed"
    assert len(application.state_store.list_research_records(execution_purpose="production")) == 10


def test_late_and_non_observed_inputs_are_unavailable_without_losing_successes() -> None:
    bundle = lifecycle_candidate_bundle(
        model_family_id="taiwan-us-price-trend-v1",
        logistic_macro_f1=0.52,
    )
    artifact = bundle.primary_artifact
    artifacts = InMemoryCandidateArtifactRepository()
    artifacts.put(
        artifact.artifact_id,
        artifact.serialized,
        object_kind="bootstrap_model_artifact",
    )
    lifecycle_store = InMemoryLifecycleStore()
    assignment = ServingAssignment.create(
        model_family_id="taiwan-us-price-trend-v1",
        candidate_id=bundle.candidate_id,
        artifact_id=artifact.artifact_id,
        previous_assignment_id=None,
        readiness_evidence_id="sha256:production-readiness",
        effective_from_batch_id="next-unstarted-eod",
        assigned_at=datetime(2026, 8, 18, 5, 0, tzinfo=UTC),
    )
    lifecycle_store.promote(
        command_id="arrange-production-assignment-partial",
        model_family_id=assignment.model_family_id,
        expected_version=0,
        promotion_payload={"promotion_event_id": "sha256:arranged-promotion-partial"},
        assignment_payload=assignment.to_payload(),
        occurred_at=assignment.assigned_at,
    )
    cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    baseline = tuple(
        ProductionListingInput(
            listing_id=_listing_id(index),
            display_ticker=f"{2300 + index}",
            market="XTAI",
            dataset_version_id=f"sha256:dataset-{index}",
            calendar_version_id="sha256:xtai-calendar-v1",
            anchor_session_id="XTAI:2026-08-18",
            target_session_ids=(
                (1, "XTAI:2026-08-19"),
                (5, "XTAI:2026-08-25"),
                (20, "XTAI:2026-09-15"),
            ),
            evidence_level="platform_observed",
            first_observed_at=cutoff,
            processed_at=cutoff,
            feature_values=(0.5, -0.25),
            support_status="full",
            unavailable_reason=None,
        )
        for index in range(1, 11)
    )
    listings = (
        replace(baseline[0], processed_at=cutoff + timedelta(minutes=16)),
        replace(baseline[1], evidence_level="provider_claimed"),
        *baseline[2:],
    )
    selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=listings,
    )
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    observed_times = iter(
        (
            cutoff,
            cutoff + timedelta(minutes=10),
            cutoff + timedelta(minutes=20),
            cutoff + timedelta(minutes=31),
        )
    )
    execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        publication_store=SqlAlchemyProductionPublicationStore(state_store),
        clock=lambda: next(observed_times),
    )

    publication = execution.run(
        ForecastRunCommand(
            market="XTAI",
            information_cutoff=cutoff,
            stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key="xtai-2026-08-18-partial",
            trace_id="trace-xtai-2026-08-18-partial",
        )
    )

    assert publication.status == "completed"
    assert len(publication.predictions) == 30
    assert len([item for item in publication.predictions if item.status == "full"]) == 24
    unavailable = [item for item in publication.predictions if item.status == "unavailable"]
    assert len(unavailable) == 6
    assert {item.unavailable_reason for item in unavailable} == {
        "late_after_feature_freeze",
        "evidence_not_platform_observed",
    }
    assert all(item.probabilities is None for item in unavailable)
    assert all(item.confidence_score is None for item in unavailable)
    assert set(publication.feature_snapshot_listing_ids) == {
        item.listing_id for item in baseline[2:]
    }
    operations_control = OperationsControl(
        state_store,
        authorization_policy=AuthorizationPolicy((), (), ()),
        secret_provider=InMemorySecretProvider(clock=lambda: cutoff),
        source_credential_validators={},
        clock=lambda: cutoff + timedelta(minutes=32),
        source_adapter_security_context=None,
        source_adapter_authorization_policy=None,
    )
    operations = operations_control.get_production_forecast(publication.forecast_batch_id)
    assert publication.slo_breached is True
    assert operations["milestones"][-1]["status"] == "missed"
    assert operations["source_health"]["status"] == "degraded"
    assert operations["incidents"] == [
        {
            "event_kind": "incident",
            "status": "open",
            "severity": "SEV3",
            "reason_code": "production_t_plus_120_breached",
        }
    ]
    assert operations["notifications"][0]["delivery_status"] == "pending"

    delivery = operations_control.deliver_production_notification(
        publication.forecast_batch_id,
        transport=_RejectedNotificationTransport(),
    )
    after_delivery = operations_control.get_production_forecast(publication.forecast_batch_id)

    assert delivery["delivery_status"] == "dead_letter"
    assert [item["delivery_status"] for item in after_delivery["notifications"]] == [
        "pending",
        "dead_letter",
    ]

    skewed_times = iter((cutoff, cutoff - timedelta(seconds=1)))
    with pytest.raises(ValueError, match="production_clock_skew_detected"):
        ForecastExecution(
            assignment_resolver=ServingAssignmentResolver(
                lifecycle_store,
                InMemoryAssignmentPinStore(),
            ),
            data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
            artifact_repository=artifacts,
            clock=lambda: next(skewed_times),
        ).run(
            ForecastRunCommand(
                market="XTAI",
                information_cutoff=cutoff,
                stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key="xtai-2026-08-18-clock-skew",
                trace_id="trace-xtai-2026-08-18-clock-skew",
            )
        )

    schema_listings = (replace(baseline[0], feature_values=(0.5,)), *baseline[1:])
    schema_selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=schema_listings,
    )
    schema_publication = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(schema_selection),
        artifact_repository=artifacts,
        clock=lambda: cutoff,
    ).run(
        ForecastRunCommand(
            market="XTAI",
            information_cutoff=cutoff,
            stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key="xtai-2026-08-18-schema-drift",
            trace_id="trace-xtai-2026-08-18-schema-drift",
        )
    )

    assert len([item for item in schema_publication.predictions if item.status == "full"]) == 27
    schema_unavailable = [
        item for item in schema_publication.predictions if item.status == "unavailable"
    ]
    assert len(schema_unavailable) == 3
    assert {item.unavailable_reason for item in schema_unavailable} == {"feature_schema_mismatch"}
    assert baseline[0].listing_id not in schema_publication.feature_snapshot_listing_ids


def test_withdrawn_source_policy_publishes_only_stable_unavailable_reasons() -> None:
    bundle = lifecycle_candidate_bundle(
        model_family_id="taiwan-us-price-trend-v1",
        logistic_macro_f1=0.52,
    )
    artifact = bundle.primary_artifact
    artifacts = InMemoryCandidateArtifactRepository()
    artifacts.put(
        artifact.artifact_id,
        artifact.serialized,
        object_kind="bootstrap_model_artifact",
    )
    lifecycle_store = InMemoryLifecycleStore()
    assignment = ServingAssignment.create(
        model_family_id="taiwan-us-price-trend-v1",
        candidate_id=bundle.candidate_id,
        artifact_id=artifact.artifact_id,
        previous_assignment_id=None,
        readiness_evidence_id="sha256:production-readiness",
        effective_from_batch_id="next-unstarted-eod",
        assigned_at=datetime(2026, 8, 18, 5, 0, tzinfo=UTC),
    )
    lifecycle_store.promote(
        command_id="arrange-production-assignment-withdrawn",
        model_family_id=assignment.model_family_id,
        expected_version=0,
        promotion_payload={"promotion_event_id": "sha256:arranged-promotion-withdrawn"},
        assignment_payload=assignment.to_payload(),
        occurred_at=assignment.assigned_at,
    )
    cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    listings = tuple(
        ProductionListingInput(
            listing_id=_listing_id(index),
            display_ticker=f"{2300 + index}",
            market="XTAI",
            dataset_version_id=f"sha256:dataset-{index}",
            calendar_version_id="sha256:xtai-calendar-v1",
            anchor_session_id="XTAI:2026-08-18",
            target_session_ids=(
                (1, "XTAI:2026-08-19"),
                (5, "XTAI:2026-08-25"),
                (20, "XTAI:2026-09-15"),
            ),
            evidence_level="platform_observed",
            first_observed_at=cutoff,
            processed_at=cutoff,
            feature_values=(0.5, -0.25),
            support_status="full",
            unavailable_reason=None,
        )
        for index in range(1, 11)
    )
    selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="withdrawn",
        listings=listings,
    )
    execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        clock=lambda: cutoff,
    )

    publication = execution.run(
        ForecastRunCommand(
            market="XTAI",
            information_cutoff=cutoff,
            stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key="xtai-2026-08-18-withdrawn",
            trace_id="trace-xtai-2026-08-18-withdrawn",
        )
    )

    assert publication.status == "completed"
    assert publication.feature_snapshot_listing_ids == ()
    assert {item.status for item in publication.predictions} == {"unavailable"}
    assert {item.unavailable_reason for item in publication.predictions} == {
        "source_policy_withdrawn"
    }
    assert all(item.probabilities is None for item in publication.predictions)

    replacement_selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id="sha256:replacement-source-policy",
        source_policy_status="active",
        listings=listings,
    )
    replacement = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(replacement_selection),
        artifact_repository=artifacts,
        clock=lambda: cutoff,
    ).run(
        ForecastRunCommand(
            market="XTAI",
            information_cutoff=cutoff,
            stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key="xtai-2026-08-18-replacement-policy",
            trace_id="trace-xtai-2026-08-18-replacement-policy",
        )
    )

    assert {item.unavailable_reason for item in replacement.predictions} == {
        "source_policy_assignment_mismatch"
    }
    assert all(item.probabilities is None for item in replacement.predictions)


def test_sql_publication_atomically_exposes_core_research_and_operations_state() -> None:
    bundle = lifecycle_candidate_bundle(
        model_family_id="taiwan-us-price-trend-v1",
        logistic_macro_f1=0.52,
    )
    artifact = bundle.primary_artifact
    artifacts = InMemoryCandidateArtifactRepository()
    artifacts.put(
        artifact.artifact_id,
        artifact.serialized,
        object_kind="bootstrap_model_artifact",
    )
    lifecycle_store = InMemoryLifecycleStore()
    assignment = ServingAssignment.create(
        model_family_id="taiwan-us-price-trend-v1",
        candidate_id=bundle.candidate_id,
        artifact_id=artifact.artifact_id,
        previous_assignment_id=None,
        readiness_evidence_id="sha256:production-readiness",
        effective_from_batch_id="next-unstarted-eod",
        assigned_at=datetime(2026, 8, 18, 5, 0, tzinfo=UTC),
    )
    lifecycle_store.promote(
        command_id="arrange-production-assignment-sql",
        model_family_id=assignment.model_family_id,
        expected_version=0,
        promotion_payload={"promotion_event_id": "sha256:arranged-promotion-sql"},
        assignment_payload=assignment.to_payload(),
        occurred_at=assignment.assigned_at,
    )
    cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    listings = tuple(
        ProductionListingInput(
            listing_id=_listing_id(index),
            display_ticker=f"{2300 + index}",
            market="XTAI",
            dataset_version_id=f"sha256:dataset-{index}",
            calendar_version_id="sha256:xtai-calendar-v1",
            anchor_session_id="XTAI:2026-08-18",
            target_session_ids=(
                (1, "XTAI:2026-08-19"),
                (5, "XTAI:2026-08-25"),
                (20, "XTAI:2026-09-15"),
            ),
            evidence_level="platform_observed",
            first_observed_at=cutoff,
            processed_at=cutoff,
            feature_values=(0.5, -0.25),
            support_status="full",
            unavailable_reason=None,
        )
        for index in range(1, 11)
    )
    selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=listings,
    )
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    publication_store = SqlAlchemyProductionPublicationStore(state_store)
    execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        publication_store=publication_store,
        clock=lambda: cutoff,
    )

    publication = execution.run(
        ForecastRunCommand(
            market="XTAI",
            information_cutoff=cutoff,
            stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key="xtai-2026-08-18-sql",
            trace_id="P2-TRACE-EOD-01",
        )
    )

    records = state_store.list_research_records(execution_purpose="production")
    assert publication.status == "completed"
    assert len(records) == 10
    assert all(record["execution_purpose"] == "production" for record in records)
    assert all("fixture_badge" not in record for record in records)
    assert all(len(record["predictions"]) == 3 for record in records)
    assert {record["lineage"]["model_artifact_id"] for record in records} == {artifact.artifact_id}
    assert {record["lineage"]["serving_assignment_id"] for record in records} == {
        assignment.assignment_id
    }
    assert all(record["projection"]["stale"] is True for record in records)
    assert len(state_store.list_prediction_records(trace_id="P2-TRACE-EOD-01")) == 30
    assert state_store.get_outbox_event(publication.outbox_event_id)["delivery_status"] == (
        "pending"
    )
    trace = state_store.get_trace_evidence("P2-TRACE-EOD-01")
    assert trace["work"]["status"] == "succeeded"
    assert trace["health"]["status"] == "healthy"
    operations = state_store.list_production_operations(publication.forecast_batch_id)
    assert [item["event_kind"] for item in operations["milestones"]] == [
        "t_plus_90_readiness",
        "t_plus_105_feature_freeze",
        "t_plus_115_forecast_validation",
        "t_plus_120_publication",
    ]
    assert {item["status"] for item in operations["milestones"]} == {"met"}
    assert operations["source_health"]["status"] == "healthy"
    assert operations["incidents"] == []
    assert operations["notifications"] == []

    changed_prediction = replace(
        publication.predictions[0],
        confidence_score=0.0,
    )
    conflicting_replay = replace(
        publication,
        predictions=(changed_prediction, *publication.predictions[1:]),
    )
    with pytest.raises(
        ImmutableStateConflict,
        match="immutable_production_work_conflict",
    ):
        publication_store.publish(
            conflicting_replay,
            trace_id="P2-TRACE-EOD-01",
            idempotency_key="xtai-2026-08-18-sql",
        )
    assert len(state_store.list_prediction_records(trace_id="P2-TRACE-EOD-01")) == 30

    identity = LocalApiKeyIdentity.issue(
        owner="formal-researcher",
        environment="development",
        scopes={"research_prediction.read"},
        issued_at=cutoff - timedelta(minutes=1),
        expires_at=cutoff + timedelta(hours=1),
    )
    action: frozenset[AuthorizationAction] = frozenset({"research_prediction.read"})
    purposes: frozenset[AuthorizationPurpose] = frozenset({"price_research"})
    environments: frozenset[RuntimeEnvironment] = frozenset({"development"})
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="formal-research-grant-v1",
                principal_id=identity.context.principal_id,
                actions=action,
                environment="development",
                valid_from=cutoff - timedelta(minutes=1),
                valid_to=cutoff + timedelta(hours=1),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="formal-research-source-policy-v1",
                dataset_id=artifact.manifest_ids[1],
                allowed_actions=action,
                purposes=purposes,
                environments=environments,
                data_protection_class="internal",
                resource_states=frozenset({"active"}),
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="formal-research-entitlement-v1",
                principal_id=identity.context.principal_id,
                dataset_id=artifact.manifest_ids[1],
                status="active",
                allowed_actions=action,
                purposes=purposes,
                environments=environments,
                valid_from=cutoff - timedelta(minutes=1),
                valid_to=cutoff + timedelta(hours=1),
            ),
        ),
    )
    query = ResearchQuery(
        state_store,
        security_context=identity.context,
        authorization_policy=policy,
        authorization_time=cutoff,
    )

    formal_records = query.list_predictions(execution_purpose="production")

    assert isinstance(formal_records, list)
    assert len(formal_records) == 10
    assert {item["execution_purpose"] for item in formal_records} == {"production"}
    web_application = _ResearchOnlyApplication(
        state_store=state_store,
        research_query=query,
        local_identity=identity,
        authenticated_at=cutoff,
    )
    client = TestClient(
        create_web_app(cast(Any, web_application)),
        headers={"Authorization": identity.credential.authorization_header()},
        client=("127.0.0.1", 50000),
    )
    cutoff_value = cutoff.isoformat().replace("+00:00", "Z")

    matrix = client.get(
        "/api/v1/research/predictions",
        params={
            "information_cutoff": cutoff_value,
            "execution_purpose": "production",
        },
    )
    detail = client.get(
        f"/api/v1/research/listings/{listings[0].listing_id}",
        params={
            "information_cutoff": cutoff_value,
            "execution_purpose": "production",
        },
    )
    page = client.get(
        "/research",
        params={
            "information_cutoff": cutoff_value,
            "execution_purpose": "production",
        },
    )
    detail_page = client.get(
        f"/research/listings/{listings[0].listing_id}",
        params={
            "information_cutoff": cutoff_value,
            "execution_purpose": "production",
        },
    )

    assert matrix.status_code == 200
    assert matrix.json()["execution_purpose"] == "production"
    assert "fixture_badge" not in matrix.json()["items"][0]
    assert matrix.json()["items"][0]["formal_cutoff"] == cutoff_value
    assert detail.status_code == 200
    assert detail.json()["lineage"]["model_artifact_id"] == artifact.artifact_id
    assert detail.json()["lineage"]["serving_assignment_id"] == assignment.assignment_id
    assert detail.json()["lineage"]["feature_snapshot_id"] == (publication.feature_snapshot_id)
    assert detail.json()["calibration"]["model_artifact_id"] == artifact.artifact_id
    assert detail.json()["calibration"]["calibrator_ids"] == list(artifact.calibrator_ids)
    assert detail.json()["support"]["price_volume"] == "full"
    assert detail.json()["allowed_evidence"] == []
    assert page.status_code == 200
    assert "正式預測比較矩陣" in page.text
    assert "正式資訊截止點" in page.text
    assert "fixture" not in page.text.lower()
    assert detail_page.status_code == 200
    assert "正式標的研究頁" in detail_page.text
    assert "校準版本" in detail_page.text
    assert artifact.calibrator_ids[0] in detail_page.text
    assert "資料支援" in detail_page.text
    assert "政策允許證據" in detail_page.text
    assert "預測歷史" in detail_page.text
    assert assignment.assignment_id in detail_page.text
    assert "fixture" not in detail_page.text.lower()

    authoritative_before_relay = state_store.list_prediction_records(trace_id="P2-TRACE-EOD-01")
    relay = state_store.relay_outbox(
        event_id=publication.outbox_event_id,
        fault=NoRelayFault(),
        compatibility=EventCompatibility.current(),
        clock=SystemRelayClock(),
        worker_id="ticket-10-production-relay",
    )
    projected_after_relay = state_store.list_research_records(execution_purpose="production")

    assert relay.status == "delivered"
    assert all(record["projection"]["stale"] is False for record in projected_after_relay)
    assert all(
        record["projection"]["evidence_projection_version"] == 1 for record in projected_after_relay
    )
    assert state_store.list_prediction_records(trace_id="P2-TRACE-EOD-01") == (
        authoritative_before_relay
    )

    second_cutoff = cutoff + timedelta(days=1)
    second_assignment = ServingAssignment.create(
        model_family_id="taiwan-us-price-trend-v1",
        candidate_id="sha256:replacement-candidate",
        artifact_id=artifact.artifact_id,
        previous_assignment_id=assignment.assignment_id,
        readiness_evidence_id="sha256:replacement-readiness",
        effective_from_batch_id="next-unstarted-eod-2",
        assigned_at=second_cutoff - timedelta(hours=1),
    )
    lifecycle_store.promote(
        command_id="arrange-production-assignment-sql-2",
        model_family_id=second_assignment.model_family_id,
        expected_version=2,
        promotion_payload={"promotion_event_id": "sha256:arranged-promotion-sql-2"},
        assignment_payload=second_assignment.to_payload(),
        occurred_at=second_assignment.assigned_at,
    )
    second_listings = tuple(
        replace(
            listing,
            first_observed_at=second_cutoff,
            processed_at=second_cutoff,
            anchor_session_id="XTAI:2026-08-19",
            target_session_ids=(
                (1, "XTAI:2026-08-20"),
                (5, "XTAI:2026-08-26"),
                (20, "XTAI:2026-09-16"),
            ),
        )
        for listing in listings
    )
    second_selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=second_cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=second_listings,
    )
    second_publication = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(second_selection),
        artifact_repository=artifacts,
        publication_store=SqlAlchemyProductionPublicationStore(state_store),
        clock=lambda: second_cutoff,
    ).run(
        ForecastRunCommand(
            market="XTAI",
            information_cutoff=second_cutoff,
            stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key="xtai-2026-08-19-sql",
            trace_id="P2-TRACE-EOD-01-NEXT",
        )
    )

    history = query.list_prediction_history(
        listing_id=listings[0].listing_id,
        execution_purpose="production",
    )

    assert isinstance(history, list)
    assert [item["formal_cutoff"] for item in history] == [
        second_cutoff.isoformat().replace("+00:00", "Z"),
        cutoff_value,
    ]
    assert [item["lineage"]["serving_assignment_id"] for item in history] == [
        second_assignment.assignment_id,
        assignment.assignment_id,
    ]
    assert history[0]["lineage"]["feature_snapshot_id"] == (second_publication.feature_snapshot_id)
    assert history[1]["lineage"]["feature_snapshot_id"] == publication.feature_snapshot_id

    history_response = client.get(
        f"/api/v1/research/listings/{listings[0].listing_id}/prediction-history",
        params={"execution_purpose": "production"},
    )

    assert history_response.status_code == 200
    assert history_response.json()["execution_purpose"] == "production"
    assert [item["formal_cutoff"] for item in history_response.json()["items"]] == [
        second_cutoff.isoformat().replace("+00:00", "Z"),
        cutoff_value,
    ]
