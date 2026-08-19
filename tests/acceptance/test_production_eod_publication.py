from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import (
    PRODUCTION_RESEARCH_CATALOG_DATASET_ID,
    ActionGrant,
    AuthorizationAction,
    AuthorizationPolicy,
    AuthorizationPurpose,
    LocalApiKeyIdentity,
    PolicyDeniedOutcome,
    RuntimeEnvironment,
    SourceEntitlement,
    SourcePolicyVersion,
    SourceUseRight,
)
from stock_forecasting.content_address import content_id
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
    ForecastPublication,
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


class _MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, elapsed: timedelta) -> None:
        self.current += elapsed


class _AdvancingProductionDataSelectionResolver:
    def __init__(
        self,
        selection: ResolvedProductionDataSelection,
        *,
        clock: _MutableClock,
        elapsed: timedelta,
    ) -> None:
        self._delegate = _FixedProductionDataSelectionResolver(selection)
        self._clock = clock
        self._elapsed = elapsed

    def resolve(
        self,
        request: ProductionDataSelectionRequest,
    ) -> ResolvedProductionDataSelection:
        selection = self._delegate.resolve(request)
        self._clock.advance(self._elapsed)
        return selection


class _DelayedProductionStateStore:
    def __init__(
        self,
        *,
        clock: _MutableClock,
        elapsed: timedelta,
    ) -> None:
        self.delegate = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
        self._clock = clock
        self._elapsed = elapsed

    def publish_production_trace(self, **kwargs: Any) -> int:
        self._clock.advance(self._elapsed)
        return self.delegate.publish_production_trace(**kwargs)

    def record_authorization_decision(
        self,
        *,
        authorization: dict[str, object],
        outcome: Literal["allowed", "denied"],
        trace_id: str,
    ) -> None:
        self.delegate.record_authorization_decision(
            authorization=authorization,
            outcome=outcome,
            trace_id=trace_id,
        )

    def get_production_publication_replay(
        self,
        *,
        idempotency_key: str,
        trace_id: str,
    ) -> dict[str, Any] | None:
        return self.delegate.get_production_publication_replay(
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )


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
    def __init__(self) -> None:
        self.calls = 0

    def deliver(self, notification: dict[str, Any]) -> bool:
        self.calls += 1
        assert notification["reason_code"] == "production_t_plus_120_breached"
        return False


class _AuthorizationFirstResearchStore:
    def __init__(self) -> None:
        self.authorization_recorded = False
        self.protected_read_attempted = False

    def record_authorization_decision(
        self,
        *,
        authorization: dict[str, object],
        outcome: str,
        trace_id: str,
    ) -> None:
        del authorization, outcome, trace_id
        self.authorization_recorded = True

    def list_research_records(self, *, execution_purpose: str) -> list[dict[str, Any]]:
        del execution_purpose
        self.protected_read_attempted = True
        assert self.authorization_recorded
        return []


class _AuthorizationFirstPublicationStore:
    def __init__(self) -> None:
        self.authorization_recorded = False
        self.protected_read_attempted = False

    def record_authorization_decision(
        self,
        *,
        authorization: dict[str, object],
        outcome: Literal["allowed", "denied"],
        trace_id: str,
    ) -> None:
        del authorization, trace_id
        self.authorization_recorded = outcome == "denied"

    def replay(self, *, idempotency_key: str, trace_id: str) -> ForecastPublication | None:
        del idempotency_key, trace_id
        self.protected_read_attempted = True
        assert self.authorization_recorded
        return None


class _MutableAuthorizationPolicyRepository:
    def __init__(self, policy: AuthorizationPolicy) -> None:
        self._policy = policy

    def get(self, policy_set_id: str, *, principal_id: str) -> AuthorizationPolicy:
        del policy_set_id, principal_id
        return self._policy

    def replace_current(self, policy: AuthorizationPolicy) -> None:
        self._policy = policy


class _PolicyWithdrawingArtifactRepository:
    def __init__(
        self,
        delegate: InMemoryCandidateArtifactRepository,
        *,
        policy_repository: _MutableAuthorizationPolicyRepository,
        withdrawn_policy: AuthorizationPolicy,
    ) -> None:
        self._delegate = delegate
        self._policy_repository = policy_repository
        self._withdrawn_policy = withdrawn_policy

    def resolve(self, artifact_id: str) -> bytes | None:
        self._policy_repository.replace_current(self._withdrawn_policy)
        return self._delegate.resolve(artifact_id)


class _PolicyWithdrawingDataSelectionResolver:
    def __init__(
        self,
        selection: ResolvedProductionDataSelection,
        *,
        policy_repository: _MutableAuthorizationPolicyRepository,
        withdrawn_policy: AuthorizationPolicy,
    ) -> None:
        self._delegate = _FixedProductionDataSelectionResolver(selection)
        self._policy_repository = policy_repository
        self._withdrawn_policy = withdrawn_policy

    def resolve(
        self,
        request: ProductionDataSelectionRequest,
    ) -> ResolvedProductionDataSelection:
        selection = self._delegate.resolve(request)
        self._policy_repository.replace_current(self._withdrawn_policy)
        return selection


def _listing_id(index: int, market: str = "XTAI") -> str:
    return str(uuid5(NAMESPACE_URL, f"ticket-10/{market.lower()}/listing/{index}"))


def _production_identity_and_policy(
    source_policy_manifest_id: str,
    at: datetime,
    *,
    actions: frozenset[AuthorizationAction] = frozenset(
        {
            "production_forecast.publish",
            "production_notification.deliver",
            "production_operations.read",
        }
    ),
    allowed_uses: frozenset[SourceUseRight] = frozenset(
        {
            "ingest",
            "retain_observed_history",
            "transform",
            "model",
            "internal_display",
            "backup_restore",
        }
    ),
    catalog_publish: bool = True,
) -> tuple[LocalApiKeyIdentity, AuthorizationPolicy]:
    source_actions = actions - {"production_operations.read"}
    catalog_actions = actions & {
        *({"production_forecast.publish"} if catalog_publish else set()),
        "production_notification.deliver",
        "production_operations.read",
    }
    identity = LocalApiKeyIdentity.issue(
        owner="production-workload",
        environment="development",
        scopes=set(actions),
        issued_at=at - timedelta(hours=1),
        expires_at=at + timedelta(hours=2),
        data_protection_classes={"licensed"},
    )
    policy = AuthorizationPolicy(
        action_grants=(
            ActionGrant(
                version_id="production-action-grant-v1",
                principal_id=identity.context.principal_id,
                actions=actions,
                environment="development",
                valid_from=at - timedelta(hours=1),
                valid_to=at + timedelta(hours=2),
            ),
        ),
        source_policies=(
            SourcePolicyVersion(
                version_id="production-source-policy-v1",
                dataset_id=source_policy_manifest_id,
                allowed_actions=source_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                valid_from=at - timedelta(hours=1),
                valid_to=at + timedelta(hours=2),
                allowed_uses=allowed_uses,
            ),
            SourcePolicyVersion(
                version_id="production-catalog-policy-v1",
                dataset_id=PRODUCTION_RESEARCH_CATALOG_DATASET_ID,
                allowed_actions=catalog_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                data_protection_class="licensed",
                resource_states=frozenset({"active"}),
                valid_from=at - timedelta(hours=1),
                valid_to=at + timedelta(hours=2),
                allowed_uses=allowed_uses,
            ),
        ),
        source_entitlements=(
            SourceEntitlement(
                version_id="production-source-entitlement-v1",
                principal_id=identity.context.principal_id,
                dataset_id=source_policy_manifest_id,
                status="active",
                allowed_actions=source_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=at - timedelta(hours=1),
                valid_to=at + timedelta(hours=2),
                allowed_uses=allowed_uses,
            ),
            SourceEntitlement(
                version_id="production-catalog-entitlement-v1",
                principal_id=identity.context.principal_id,
                dataset_id=PRODUCTION_RESEARCH_CATALOG_DATASET_ID,
                status="active",
                allowed_actions=catalog_actions,
                purposes=frozenset({"price_research"}),
                environments=frozenset({"development"}),
                valid_from=at - timedelta(hours=1),
                valid_to=at + timedelta(hours=2),
                allowed_uses=allowed_uses,
            ),
        ),
    )
    return identity, policy


def _without_dataset_policy(
    policy: AuthorizationPolicy,
    dataset_id: str,
) -> AuthorizationPolicy:
    return replace(
        policy,
        source_policies=tuple(
            item for item in policy.source_policies if item.dataset_id != dataset_id
        ),
        source_entitlements=tuple(
            item for item in policy.source_entitlements if item.dataset_id != dataset_id
        ),
    )


def test_deployed_production_entrypoint_fails_closed_without_data_selection() -> None:
    cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    identity, policy = _production_identity_and_policy("sha256:source-policy", cutoff)
    application = build_test_application(
        observed_at=cutoff,
        local_identity=identity,
        authorization_policy_override=policy,
    )

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


def test_production_forecast_authorizes_catalog_before_protected_replay_lookup() -> None:
    cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    identity, policy = _production_identity_and_policy(
        "sha256:source-policy",
        cutoff,
        catalog_publish=False,
    )
    publication_store = _AuthorizationFirstPublicationStore()

    result = ForecastExecution(
        assignment_resolver=cast(Any, object()),
        data_selection_resolver=cast(Any, object()),
        artifact_repository=cast(Any, object()),
        publication_store=cast(Any, publication_store),
        security_context=identity.context,
        authorization_policy=policy,
        clock=lambda: cutoff,
    ).run(
        ForecastRunCommand(
            market="XTAI",
            information_cutoff=cutoff,
            stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key="unauthorized-production-replay",
            trace_id="trace-unauthorized-production-replay",
        )
    )

    assert isinstance(result, PolicyDeniedOutcome)
    assert publication_store.authorization_recorded is True
    assert publication_store.protected_read_attempted is False


def test_production_collection_authorizes_before_protected_state_lookup() -> None:
    cutoff = datetime(2026, 8, 18, 6, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="formal-researcher",
        environment="development",
        scopes={"research_prediction.read"},
        issued_at=cutoff - timedelta(minutes=1),
        expires_at=cutoff + timedelta(hours=1),
    )
    store = _AuthorizationFirstResearchStore()
    outcome = ResearchQuery(
        cast(StateStore, store),
        security_context=identity.context,
        authorization_policy=AuthorizationPolicy(
            action_grants=(),
            source_policies=(),
            source_entitlements=(),
        ),
        authorization_time=cutoff,
    ).list_predictions(
        execution_purpose="production",
        trace_id="trace-production-authorization-first",
    )

    assert isinstance(outcome, PolicyDeniedOutcome)
    assert store.authorization_recorded is True
    assert store.protected_read_attempted is False


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
    production_identity, production_policy = _production_identity_and_policy(
        artifact.manifest_ids[1], information_cutoff
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
        security_context=production_identity.context,
        authorization_policy=production_policy,
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

    missing_model_identity, missing_model_policy = _production_identity_and_policy(
        artifact.manifest_ids[1],
        information_cutoff,
        allowed_uses=frozenset({"internal_display"}),
    )
    denied = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        publication_store=InMemoryProductionPublicationStore(),
        security_context=missing_model_identity.context,
        authorization_policy=missing_model_policy,
        clock=lambda: information_cutoff,
    ).run(
        ForecastRunCommand(
            market=market,
            information_cutoff=information_cutoff,
            stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
            model_family_id="taiwan-us-price-trend-v1",
            execution_purpose="production",
            idempotency_key=f"{market.lower()}-missing-model-use",
            trace_id=f"trace-{market.lower()}-missing-model-use",
        )
    )

    assert isinstance(denied, PolicyDeniedOutcome)

    if market == "XTAI":
        readiness_clock = _MutableClock(information_cutoff)
        late_readiness = ForecastExecution(
            assignment_resolver=ServingAssignmentResolver(
                lifecycle_store,
                InMemoryAssignmentPinStore(),
            ),
            data_selection_resolver=_AdvancingProductionDataSelectionResolver(
                selection,
                clock=readiness_clock,
                elapsed=timedelta(minutes=1),
            ),
            artifact_repository=artifacts,
            publication_store=InMemoryProductionPublicationStore(),
            security_context=production_identity.context,
            authorization_policy=production_policy,
            clock=readiness_clock,
        ).run(
            ForecastRunCommand(
                market=market,
                information_cutoff=information_cutoff,
                stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key="xtai-late-readiness",
                trace_id="trace-xtai-late-readiness",
            )
        )

        assert not isinstance(late_readiness, PolicyDeniedOutcome)
        assert late_readiness.milestones[0].status == "missed"
        assert late_readiness.milestones[0].observed_at == information_cutoff + timedelta(minutes=1)

        persistence_clock = _MutableClock(information_cutoff)
        delayed_state_store = _DelayedProductionStateStore(
            clock=persistence_clock,
            elapsed=timedelta(minutes=31),
        )
        late_persistence = ForecastExecution(
            assignment_resolver=ServingAssignmentResolver(
                lifecycle_store,
                InMemoryAssignmentPinStore(),
            ),
            data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
            artifact_repository=artifacts,
            publication_store=SqlAlchemyProductionPublicationStore(delayed_state_store),
            security_context=production_identity.context,
            authorization_policy=production_policy,
            clock=persistence_clock,
        ).run(
            ForecastRunCommand(
                market=market,
                information_cutoff=information_cutoff,
                stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key="xtai-late-persistence",
                trace_id="trace-xtai-late-persistence",
            )
        )

        assert not isinstance(late_persistence, PolicyDeniedOutcome)
        assert late_persistence.slo_breached is True
        assert late_persistence.milestones[-1].status == "missed"
        assert late_persistence.completed_at == information_cutoff + timedelta(minutes=31)
        assert (
            delayed_state_store.delegate.get_outbox_event(late_persistence.outbox_event_id)[
                "occurred_at"
            ]
            == late_persistence.completed_at.isoformat()
        )

        expiry_clock = _MutableClock(information_cutoff)
        expired_state_store = _DelayedProductionStateStore(
            clock=expiry_clock,
            elapsed=timedelta(minutes=121),
        )
        expired_at_commit = ForecastExecution(
            assignment_resolver=ServingAssignmentResolver(
                lifecycle_store,
                InMemoryAssignmentPinStore(),
            ),
            data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
            artifact_repository=artifacts,
            publication_store=SqlAlchemyProductionPublicationStore(expired_state_store),
            security_context=production_identity.context,
            authorization_policy=production_policy,
            clock=expiry_clock,
        ).run(
            ForecastRunCommand(
                market=market,
                information_cutoff=information_cutoff,
                stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key="xtai-expired-at-commit",
                trace_id="trace-xtai-expired-at-commit",
            )
        )

        assert isinstance(expired_at_commit, PolicyDeniedOutcome)
        assert (
            expired_state_store.delegate.list_research_records(execution_purpose="production") == []
        )
        expired_audit = expired_state_store.delegate.list_audit_events(
            trace_id="trace-xtai-expired-at-commit"
        )
        assert expired_audit[-1]["reason_code"] == "identity_expired"

        withdrawn_policy = _without_dataset_policy(
            production_policy,
            selection.source_policy_manifest_id,
        )
        current_policy_repository = _MutableAuthorizationPolicyRepository(production_policy)

        withdrawn_state_store = _DelayedProductionStateStore(
            clock=_MutableClock(information_cutoff),
            elapsed=timedelta(),
        )
        withdrawn_at_commit = ForecastExecution(
            assignment_resolver=ServingAssignmentResolver(
                lifecycle_store,
                InMemoryAssignmentPinStore(),
            ),
            data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
            artifact_repository=_PolicyWithdrawingArtifactRepository(
                artifacts,
                policy_repository=current_policy_repository,
                withdrawn_policy=withdrawn_policy,
            ),
            publication_store=SqlAlchemyProductionPublicationStore(withdrawn_state_store),
            security_context=production_identity.context,
            authorization_policy=lambda: current_policy_repository.get(
                "production-policy",
                principal_id=production_identity.context.principal_id,
            ),
            clock=lambda: information_cutoff,
        ).run(
            ForecastRunCommand(
                market=market,
                information_cutoff=information_cutoff,
                stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key="xtai-withdrawn-at-commit",
                trace_id="trace-xtai-withdrawn-at-commit",
            )
        )

        assert isinstance(withdrawn_at_commit, PolicyDeniedOutcome)
        assert (
            withdrawn_state_store.delegate.list_research_records(execution_purpose="production")
            == []
        )
        withdrawn_audit = withdrawn_state_store.delegate.list_audit_events(
            trace_id="trace-xtai-withdrawn-at-commit"
        )
        assert withdrawn_audit[-1]["reason_code"] == "source_policy_unknown"

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
        security_context=production_identity.context,
        authorization_policy=production_policy,
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

    blank_lineage_selection = ResolvedProductionDataSelection.create(
        market=market,
        information_cutoff=information_cutoff,
        stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=(replace(listings[0], dataset_version_id=""), *listings[1:]),
    )
    unstable_reason_selection = ResolvedProductionDataSelection.create(
        market=market,
        information_cutoff=information_cutoff,
        stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=(
            replace(
                listings[0],
                support_status="unavailable",
                unavailable_reason="provider says nope",
            ),
            *listings[1:],
        ),
    )
    for invalid_selection, suffix in (
        (blank_lineage_selection, "blank-selection-lineage"),
        (unstable_reason_selection, "unstable-unavailable-reason"),
    ):
        with pytest.raises(ValueError, match="production_data_selection_invalid"):
            ForecastExecution(
                assignment_resolver=ServingAssignmentResolver(
                    lifecycle_store,
                    InMemoryAssignmentPinStore(),
                ),
                data_selection_resolver=_FixedProductionDataSelectionResolver(invalid_selection),
                artifact_repository=artifacts,
                publication_store=InMemoryProductionPublicationStore(),
                security_context=production_identity.context,
                authorization_policy=production_policy,
                clock=lambda: information_cutoff,
            ).run(
                ForecastRunCommand(
                    market=market,
                    information_cutoff=information_cutoff,
                    stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                    model_family_id="taiwan-us-price-trend-v1",
                    execution_purpose="production",
                    idempotency_key=f"{market.lower()}-{suffix}",
                    trace_id=f"trace-{market.lower()}-{suffix}",
                )
            )

    stale_selection = replace(selection, data_selection_id="sha256:stale-selection-id")
    stale_selection_execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(stale_selection),
        artifact_repository=artifacts,
        publication_store=InMemoryProductionPublicationStore(),
        security_context=production_identity.context,
        authorization_policy=production_policy,
        clock=lambda: information_cutoff,
    )
    with pytest.raises(ValueError, match="production_data_selection_invalid"):
        stale_selection_execution.run(
            ForecastRunCommand(
                market=market,
                information_cutoff=information_cutoff,
                stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key=f"{market.lower()}-stale-selection",
                trace_id=f"trace-{market.lower()}-stale-selection",
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
            persistence_clock=lambda: invalid_publication.completed_at,
            authorize_at_persistence=lambda _at: {},
        )

    assert invalid_store.get_batch(publication.forecast_batch_id) is None

    invalid_lineage = replace(
        publication.predictions[0],
        dataset_version_id="sha256:wrong-dataset",
        target_session_id=f"{market}:2099-01-01",
    )
    with pytest.raises(ValueError, match="production_prediction_lineage_invalid"):
        invalid_store.publish(
            replace(
                publication,
                predictions=(invalid_lineage, *publication.predictions[1:]),
            ),
            trace_id="trace-invalid-lineage",
            idempotency_key="invalid-lineage",
            persistence_clock=lambda: publication.completed_at,
            authorize_at_persistence=lambda _at: {},
        )

    def assert_cross_artifact_lineage_rejected(corrupted: ForecastPublication, key: str) -> None:
        with pytest.raises(
            ValueError,
            match="production_(publication|prediction)_lineage_invalid",
        ):
            InMemoryProductionPublicationStore().publish(
                corrupted,
                trace_id=f"trace-{key}",
                idempotency_key=key,
                persistence_clock=lambda: corrupted.completed_at,
                authorize_at_persistence=lambda _at: {},
            )

    assert_cross_artifact_lineage_rejected(
        replace(publication, information_cutoff=information_cutoff + timedelta(minutes=1)),
        "cutoff-lineage",
    )
    changed_snapshot_rows = (
        (publication.feature_snapshot_rows[0][0], (99.0, 99.0)),
        *publication.feature_snapshot_rows[1:],
    )
    changed_snapshot_id = content_id(
        "production_feature_snapshot",
        {
            "data_selection_id": publication.data_selection_id,
            "rows": [
                {"listing_id": listing_id, "values": values}
                for listing_id, values in changed_snapshot_rows
            ],
        },
    )
    assert_cross_artifact_lineage_rejected(
        replace(
            publication,
            feature_snapshot_id=changed_snapshot_id,
            feature_snapshot_rows=changed_snapshot_rows,
            predictions=tuple(
                replace(item, feature_snapshot_id=changed_snapshot_id)
                for item in publication.predictions
            ),
        ),
        "snapshot-lineage",
    )
    assert_cross_artifact_lineage_rejected(
        replace(
            publication,
            predictions=(
                replace(
                    publication.predictions[0],
                    model_artifact_id="sha256:other-artifact",
                    calibrator_ids=("sha256:other-calibrator",),
                ),
                *publication.predictions[1:],
            ),
        ),
        "model-lineage",
    )
    assert_cross_artifact_lineage_rejected(
        replace(publication, forecast_batch_id="not-the-business-batch-id"),
        "batch-identity",
    )
    assert_cross_artifact_lineage_rejected(
        replace(
            publication,
            predictions=(
                replace(publication.predictions[0], prediction_id="not-the-prediction-id"),
                *publication.predictions[1:],
            ),
        ),
        "prediction-identity",
    )

    with pytest.raises(ValueError, match="immutable_production_batch_conflict"):
        execution.run(
            ForecastRunCommand(
                market=market,
                information_cutoff=information_cutoff,
                stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
                model_family_id="taiwan-us-price-trend-v1",
                execution_purpose="production",
                idempotency_key=f"{market.lower()}-different-command",
                trace_id=f"trace-{market.lower()}-different-command",
            )
        )

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

    denied_command = ForecastRunCommand(
        market=market,
        information_cutoff=information_cutoff,
        stock_pool_version_id=f"sha256:p2-{market.lower()}-stock-pool-v1",
        model_family_id="taiwan-us-price-trend-v1",
        execution_purpose="production",
        idempotency_key=f"{market.lower()}-2026-08-18-denied",
        trace_id=f"P2-TRACE-EOD-01-DENIED-{market}",
    )
    denied = application.run_production_eod(denied_command)

    assert isinstance(denied, PolicyDeniedOutcome)
    assert application.state_store.list_research_records(execution_purpose="production") == []
    denied_audit = application.state_store.list_audit_events(trace_id=denied_command.trace_id)
    assert denied_audit[0]["decision_id"] == denied.decision_id
    assert denied_audit[0]["reason_code"] == "identity_scope_missing"

    application = build_test_application(
        observed_at=information_cutoff,
        production_data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        local_identity=production_identity,
        authorization_policy_override=production_policy,
    )
    application.model_artifact_repository.put(
        artifact.artifact_id,
        artifact.serialized,
        object_kind="bootstrap_model_artifact",
    )
    application.model_lifecycle_store.promote(
        command_id="arrange-authorized-deployed-production-assignment",
        model_family_id=assignment.model_family_id,
        expected_version=0,
        promotion_payload={"promotion_event_id": "sha256:authorized-deployed-promotion"},
        assignment_payload=assignment.to_payload(),
        occurred_at=assignment.assigned_at,
    )
    deployed = application.run_production_eod(
        replace(
            denied_command,
            idempotency_key=f"{market.lower()}-2026-08-18-deployed",
            trace_id=f"P2-TRACE-EOD-01-DEPLOYED-{market}",
        )
    )

    assert not isinstance(deployed, PolicyDeniedOutcome)
    assert deployed.status == "completed"
    assert len(application.state_store.list_research_records(execution_purpose="production")) == 10

    if market == "XTAI":
        withdrawn_policy = _without_dataset_policy(
            production_policy,
            selection.source_policy_manifest_id,
        )
        current_policy_repository = _MutableAuthorizationPolicyRepository(production_policy)
        withdrawal_application = build_test_application(
            observed_at=information_cutoff,
            production_data_selection_resolver=_PolicyWithdrawingDataSelectionResolver(
                selection,
                policy_repository=current_policy_repository,
                withdrawn_policy=withdrawn_policy,
            ),
            local_identity=production_identity,
            authorization_policy_override=production_policy,
        )
        withdrawal_application.authorization_policy_repository = cast(
            Any,
            current_policy_repository,
        )
        withdrawal_application.model_artifact_repository.put(
            artifact.artifact_id,
            artifact.serialized,
            object_kind="bootstrap_model_artifact",
        )
        withdrawal_application.model_lifecycle_store.promote(
            command_id="arrange-withdrawn-deployed-production-assignment",
            model_family_id=assignment.model_family_id,
            expected_version=0,
            promotion_payload={"promotion_event_id": "sha256:withdrawn-deployed-promotion"},
            assignment_payload=assignment.to_payload(),
            occurred_at=assignment.assigned_at,
        )

        withdrawn = withdrawal_application.run_production_eod(
            replace(
                denied_command,
                idempotency_key="xtai-withdrawn-deployed",
                trace_id="P2-TRACE-EOD-01-WITHDRAWN-DEPLOYED-XTAI",
            )
        )

        assert isinstance(withdrawn, PolicyDeniedOutcome)
        assert (
            withdrawal_application.state_store.list_research_records(execution_purpose="production")
            == []
        )


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
    production_identity, production_policy = _production_identity_and_policy(
        artifact.manifest_ids[1], cutoff
    )
    state_store = StateStore("sqlite+pysqlite:///:memory:", create_schema=True)
    observed_times = iter(
        (
            cutoff,
            cutoff,
            cutoff + timedelta(minutes=5),
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
        security_context=production_identity.context,
        authorization_policy=production_policy,
        clock=lambda: next(observed_times),
    )

    command = ForecastRunCommand(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        model_family_id="taiwan-us-price-trend-v1",
        execution_purpose="production",
        idempotency_key="xtai-2026-08-18-partial",
        trace_id="trace-xtai-2026-08-18-partial",
    )
    publication = execution.run(command)

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
    restarted_execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        publication_store=SqlAlchemyProductionPublicationStore(state_store),
        security_context=production_identity.context,
        authorization_policy=production_policy,
        clock=lambda: cutoff + timedelta(minutes=32),
    )

    assert restarted_execution.run(command) == publication
    replay_identity, replay_policy = _production_identity_and_policy(
        selection.source_policy_manifest_id,
        cutoff,
        allowed_uses=frozenset({"internal_display"}),
    )
    denied_replay = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        publication_store=SqlAlchemyProductionPublicationStore(state_store),
        security_context=replay_identity.context,
        authorization_policy=replay_policy,
        clock=lambda: cutoff + timedelta(minutes=33),
    ).run(command)

    assert isinstance(denied_replay, PolicyDeniedOutcome)
    replay_audit = state_store.list_audit_events(trace_id=command.trace_id)
    assert [item["outcome"] for item in replay_audit[-2:]] == ["allowed", "denied"]
    operations_control = OperationsControl(
        state_store,
        authorization_policy=production_policy,
        secret_provider=InMemorySecretProvider(clock=lambda: cutoff),
        source_credential_validators={},
        clock=lambda: cutoff + timedelta(minutes=32),
        source_adapter_security_context=None,
        source_adapter_authorization_policy=None,
    )
    operations = operations_control.get_production_forecast(
        publication.forecast_batch_id,
        trace_id="trace-production-operations-read",
        security_context=production_identity.context,
    )
    assert isinstance(operations, dict)
    assert publication.slo_breached is True
    assert state_store.get_outbox_event(publication.outbox_event_id)["occurred_at"] == (
        publication.completed_at.isoformat()
    )
    assert publication.completed_at > publication.information_cutoff
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

    missing_display_identity, missing_display_policy = _production_identity_and_policy(
        selection.source_policy_manifest_id,
        cutoff,
        allowed_uses=frozenset({"model"}),
    )
    denied_transport = _RejectedNotificationTransport()
    denied_operations_control = OperationsControl(
        state_store,
        authorization_policy=missing_display_policy,
        secret_provider=InMemorySecretProvider(clock=lambda: cutoff),
        source_credential_validators={},
        clock=lambda: cutoff + timedelta(minutes=32),
        source_adapter_security_context=None,
        source_adapter_authorization_policy=None,
    )
    denied_operations = denied_operations_control.get_production_forecast(
        publication.forecast_batch_id,
        trace_id="trace-production-operations-missing-display-use",
        security_context=missing_display_identity.context,
    )
    denied_delivery = denied_operations_control.deliver_production_notification(
        publication.forecast_batch_id,
        transport=denied_transport,
        trace_id="trace-production-notification-missing-display-use",
        security_context=missing_display_identity.context,
    )

    assert isinstance(denied_operations, PolicyDeniedOutcome)
    assert isinstance(denied_delivery, PolicyDeniedOutcome)
    assert denied_transport.calls == 0

    notification_transport = _RejectedNotificationTransport()
    notification_trace_id = "trace-production-notification-delivery"
    delivery = operations_control.deliver_production_notification(
        publication.forecast_batch_id,
        transport=notification_transport,
        trace_id=notification_trace_id,
        security_context=production_identity.context,
    )
    assert isinstance(delivery, dict)
    after_delivery = operations_control.get_production_forecast(
        publication.forecast_batch_id,
        trace_id="trace-production-operations-after-delivery",
        security_context=production_identity.context,
    )
    assert isinstance(after_delivery, dict)

    assert delivery["delivery_status"] == "dead_letter"
    assert [item["delivery_status"] for item in after_delivery["notifications"]] == [
        "dead_letter",
    ]
    with pytest.raises(ValueError, match="production_notification_not_pending"):
        operations_control.deliver_production_notification(
            publication.forecast_batch_id,
            transport=notification_transport,
            trace_id="trace-production-notification-repeat",
            security_context=production_identity.context,
        )
    assert notification_transport.calls == 1
    notification_audit = state_store.list_audit_events(trace_id=notification_trace_id)
    assert notification_audit[0]["reason_code"] == "authorized"

    skewed_times = iter((cutoff, cutoff - timedelta(seconds=1)))
    with pytest.raises(ValueError, match="production_clock_skew_detected"):
        ForecastExecution(
            assignment_resolver=ServingAssignmentResolver(
                lifecycle_store,
                InMemoryAssignmentPinStore(),
            ),
            data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
            artifact_repository=artifacts,
            security_context=production_identity.context,
            authorization_policy=production_policy,
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
        security_context=production_identity.context,
        authorization_policy=production_policy,
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

    assert not isinstance(schema_publication, PolicyDeniedOutcome)
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
    production_identity, production_policy = _production_identity_and_policy(
        selection.source_policy_manifest_id, cutoff
    )
    execution = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(selection),
        artifact_repository=artifacts,
        security_context=production_identity.context,
        authorization_policy=production_policy,
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

    unstable_reason = replace(
        publication.predictions[0],
        unavailable_reason="provider says nope",
    )
    with pytest.raises(ValueError, match="production_unavailable_contract_invalid"):
        InMemoryProductionPublicationStore().publish(
            replace(publication, predictions=(unstable_reason, *publication.predictions[1:])),
            trace_id="trace-unstable-published-reason",
            idempotency_key="unstable-published-reason",
            persistence_clock=lambda: publication.completed_at,
            authorize_at_persistence=lambda _at: {},
        )

    replacement_selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id="sha256:replacement-source-policy",
        source_policy_status="active",
        listings=listings,
    )
    replacement_identity, replacement_policy = _production_identity_and_policy(
        replacement_selection.source_policy_manifest_id, cutoff
    )
    replacement = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(replacement_selection),
        artifact_repository=artifacts,
        security_context=replacement_identity.context,
        authorization_policy=replacement_policy,
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

    assert not isinstance(replacement, PolicyDeniedOutcome)
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
    listings = (
        listings[0],
        replace(listings[1], support_status="degraded"),
        replace(
            listings[2],
            support_status="unavailable",
            unavailable_reason="data_support_unavailable",
        ),
        *listings[3:],
    )
    selection = ResolvedProductionDataSelection.create(
        market="XTAI",
        information_cutoff=cutoff,
        stock_pool_version_id="sha256:p2-taiwan-stock-pool-v1",
        source_policy_manifest_id=artifact.manifest_ids[1],
        source_policy_status="active",
        listings=listings,
    )
    production_identity, production_policy = _production_identity_and_policy(
        selection.source_policy_manifest_id, cutoff
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
        security_context=production_identity.context,
        authorization_policy=production_policy,
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
    assert trace["health"]["status"] == "degraded"
    data_selection_payload = trace["artifact_payloads"][publication.data_selection_id]
    feature_snapshot_payload = trace["artifact_payloads"][publication.feature_snapshot_id]
    assert len(data_selection_payload["listings"]) == 10
    assert {item["listing_id"] for item in data_selection_payload["listings"]} == {
        item.listing_id for item in listings
    }
    assert len(feature_snapshot_payload["rows"]) == 9
    assert {item["listing_id"] for item in feature_snapshot_payload["rows"]} == set(
        publication.feature_snapshot_listing_ids
    )
    operations = state_store.list_production_operations(publication.forecast_batch_id)
    assert [item["event_kind"] for item in operations["milestones"]] == [
        "t_plus_90_readiness",
        "t_plus_105_feature_freeze",
        "t_plus_115_forecast_validation",
        "t_plus_120_publication",
    ]
    assert {item["status"] for item in operations["milestones"]} == {"met"}
    assert operations["source_health"]["status"] == "degraded"
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
            persistence_clock=lambda: conflicting_replay.completed_at,
            authorize_at_persistence=lambda _at: {},
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
        source_policies=tuple(
            SourcePolicyVersion(
                version_id=f"formal-research-source-policy-{index}",
                dataset_id=dataset_id,
                allowed_actions=action,
                purposes=purposes,
                environments=environments,
                data_protection_class="internal",
                resource_states=frozenset({"active"}),
                allowed_uses=frozenset({"internal_display"}),
            )
            for index, dataset_id in enumerate(
                (
                    PRODUCTION_RESEARCH_CATALOG_DATASET_ID,
                    artifact.manifest_ids[1],
                ),
                start=1,
            )
        ),
        source_entitlements=tuple(
            SourceEntitlement(
                version_id=f"formal-research-entitlement-{index}",
                principal_id=identity.context.principal_id,
                dataset_id=dataset_id,
                status="active",
                allowed_actions=action,
                purposes=purposes,
                environments=environments,
                valid_from=cutoff - timedelta(minutes=1),
                valid_to=cutoff + timedelta(hours=1),
                allowed_uses=frozenset({"internal_display"}),
            )
            for index, dataset_id in enumerate(
                (
                    PRODUCTION_RESEARCH_CATALOG_DATASET_ID,
                    artifact.manifest_ids[1],
                ),
                start=1,
            )
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
    missing_display_policy = AuthorizationPolicy(
        action_grants=policy.action_grants,
        source_policies=tuple(
            replace(item, allowed_uses=frozenset()) for item in policy.source_policies
        ),
        source_entitlements=tuple(
            replace(item, allowed_uses=frozenset()) for item in policy.source_entitlements
        ),
    )
    denied_formal_records = ResearchQuery(
        state_store,
        security_context=identity.context,
        authorization_policy=missing_display_policy,
        authorization_time=cutoff,
    ).list_predictions(
        execution_purpose="production",
        trace_id="trace-production-research-missing-display-use",
    )

    assert isinstance(denied_formal_records, PolicyDeniedOutcome)
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
    assert detail.json()["allowed_evidence"] == [
        {
            "dataset_version_id": listings[0].dataset_version_id,
            "evidence_level": "platform_observed",
            "source_policy_manifest_id": selection.source_policy_manifest_id,
        }
    ]
    assert page.status_code == 200
    assert "正式預測比較矩陣" in page.text
    assert "正式資訊截止點" in page.text
    assert 'name="support"' in page.text
    assert "資料支援：降級" in page.text
    assert "不可預測：data_support_unavailable" in page.text
    assert "fixture" not in page.text.lower()
    assert detail_page.status_code == 200
    assert "正式標的研究頁" in detail_page.text
    assert "校準版本" in detail_page.text
    assert artifact.calibrator_ids[0] in detail_page.text
    assert "資料支援" in detail_page.text
    assert "政策允許證據" in detail_page.text
    assert listings[0].dataset_version_id in detail_page.text
    assert "platform_observed" in detail_page.text
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
        effective_from_batch_id="next-unstarted-eod",
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
    second_identity, second_policy = _production_identity_and_policy(
        second_selection.source_policy_manifest_id, second_cutoff
    )
    second_publication = ForecastExecution(
        assignment_resolver=ServingAssignmentResolver(
            lifecycle_store,
            InMemoryAssignmentPinStore(),
        ),
        data_selection_resolver=_FixedProductionDataSelectionResolver(second_selection),
        artifact_repository=artifacts,
        publication_store=SqlAlchemyProductionPublicationStore(state_store),
        security_context=second_identity.context,
        authorization_policy=second_policy,
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
    assert not isinstance(second_publication, PolicyDeniedOutcome)

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
    fixture_history_response = client.get(
        f"/api/v1/research/listings/{listings[0].listing_id}/prediction-history",
        params={"execution_purpose": "fixture"},
    )
    assert fixture_history_response.status_code == 422
