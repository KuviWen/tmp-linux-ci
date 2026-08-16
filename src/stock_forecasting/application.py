from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Literal
from uuid import uuid4

from stock_forecasting.alpaca_market_data import (
    AlpacaPriceSourceAdapter,
    AlpacaSourceCollector,
    AlpacaSourceDecoder,
    ProviderHttpTransport,
    UrllibProviderHttpTransport,
    load_candidate_alpaca_market_calendar_evidence,
    load_candidate_alpaca_reference_graph,
)
from stock_forecasting.alpaca_provider_contract import (
    ALPACA_BARS_DISTRIBUTION,
    ALPACA_PROVIDER_ID,
)
from stock_forecasting.authorization import (
    AuthorizationPolicy,
    CurrentSourcePrincipalAttributes,
    EntitlementStatus,
    LocalApiKeyIdentity,
    PolicyDeniedOutcome,
    SecurityContext,
    SourceAccessMode,
    build_fixture_authorization_policy,
)
from stock_forecasting.authorization_repository import AuthorizationPolicyRepository
from stock_forecasting.finmind_market_data import (
    FinMindPriceSourceAdapter,
    FinMindSourceCollector,
    FinMindSourceDecoder,
    load_candidate_finmind_reference_graph,
)
from stock_forecasting.finmind_provider_contract import (
    FINMIND_PRICE_DISTRIBUTION,
    FINMIND_PROVIDER_ID,
)
from stock_forecasting.operations_control import OperationsControl
from stock_forecasting.outbox import (
    EventCompatibility,
    NoRelayFault,
    RelayClock,
    RelayFault,
    RelayOutcome,
    SystemRelayClock,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.price_eligibility_query import PriceEligibilityQuery
from stock_forecasting.provider_http import (
    UrllibProviderHttpTransport as ProviderUrllibHttpTransport,
)
from stock_forecasting.research_query import ResearchQuery
from stock_forecasting.security_audit import SecurityAudit
from stock_forecasting.source_credentials import (
    InMemorySecretProvider,
    ManagedSourceCredentialResolver,
    SecretProvider,
    SourceCredentialValidator,
)
from stock_forecasting.workflows.fixture_eod import (
    FixtureEodCommand,
    FixtureEodOutcome,
    FixtureEodWorkflow,
)
from stock_forecasting.workflows.fixture_use import FixtureUseCommand, FixtureUseWorkflow


class Application:
    def __init__(
        self,
        *,
        observed_at: datetime | None,
        object_root: Path,
        database_url: str,
        create_schema: bool,
        relay_fault: RelayFault | None = None,
        event_compatibility: EventCompatibility | None = None,
        relay_clock: RelayClock | None = None,
        relay_worker_id: str | None = None,
        local_identity: LocalApiKeyIdentity,
        authorization_policy_set_id: str,
        authorization_policy_bootstrap: AuthorizationPolicy | None,
        fixed_security_time: datetime | None,
        source_adapter_security_context: SecurityContext | None,
        secret_provider: SecretProvider | None = None,
        source_credential_validators: Mapping[str, SourceCredentialValidator] | None = None,
    ) -> None:
        self.state_store = StateStore(database_url, create_schema=create_schema)
        self.local_identity = local_identity
        self.security_context: SecurityContext = local_identity.context
        if source_adapter_security_context is not None:
            if source_adapter_security_context.principal_id == self.security_context.principal_id:
                raise ValueError("source_adapter_identity_must_be_distinct")
            if source_adapter_security_context.environment != self.security_context.environment:
                raise ValueError("source_adapter_identity_environment_mismatch")
            if source_adapter_security_context.scopes != frozenset({"market_data.collect"}):
                raise ValueError("source_adapter_identity_scope_invalid")
        self.source_adapter_security_context = source_adapter_security_context
        self.authorization_policy_repository = AuthorizationPolicyRepository(self.state_store)
        if authorization_policy_bootstrap is not None:
            self.authorization_policy_repository.install(
                authorization_policy_set_id,
                authorization_policy_bootstrap,
            )
        self.authorization_policy = self.authorization_policy_repository.get(
            authorization_policy_set_id,
            principal_id=self.security_context.principal_id,
        )
        self._fixed_security_time = fixed_security_time
        self.object_repository = FilesystemObjectRepository(object_root)
        self.research_query = ResearchQuery(
            self.state_store,
            security_context=self.security_context,
            authorization_policy=self.authorization_policy,
            authorization_time=fixed_security_time,
        )
        self.price_eligibility_query = PriceEligibilityQuery(
            self.state_store,
            authorization_policy=self.authorization_policy,
            authorization_time=fixed_security_time,
            source_authorization_policy=lambda principal_id: (
                self.authorization_policy_repository.get(
                    authorization_policy_set_id,
                    principal_id=principal_id,
                )
            ),
            source_principal_attributes=self._source_principal_attributes,
            object_repository=self.object_repository,
        )
        self.security_audit = SecurityAudit(self.state_store)
        self.secret_provider = secret_provider or InMemorySecretProvider(
            clock=lambda: self._fixed_security_time or datetime.now(UTC)
        )
        source_adapter_authorization_policy: Callable[[], AuthorizationPolicy] | None = None
        if self.source_adapter_security_context is not None:
            source_adapter_principal_id = self.source_adapter_security_context.principal_id

            def load_source_adapter_authorization_policy() -> AuthorizationPolicy:
                return self.authorization_policy_repository.get(
                    authorization_policy_set_id,
                    principal_id=source_adapter_principal_id,
                )

            source_adapter_authorization_policy = load_source_adapter_authorization_policy
        self.operations_control = OperationsControl(
            self.state_store,
            authorization_policy=self.authorization_policy,
            secret_provider=self.secret_provider,
            source_credential_validators=source_credential_validators or {},
            clock=lambda: self._fixed_security_time or datetime.now(UTC),
            source_adapter_security_context=self.source_adapter_security_context,
            source_adapter_authorization_policy=source_adapter_authorization_policy,
        )
        self.alpaca_price_adapter = (
            self.build_alpaca_price_adapter(
                transport=UrllibProviderHttpTransport(),
                source_access_mode="live_provider",
            )
            if self.source_adapter_security_context is not None
            else None
        )
        self.finmind_current_price_adapter = (
            self.build_finmind_price_adapter(
                transport=ProviderUrllibHttpTransport(
                    allowed_hosts=frozenset({"api.finmindtrade.com"})
                ),
                source_access_mode="live_provider",
                mode="current",
            )
            if self.source_adapter_security_context is not None
            else None
        )
        self.finmind_historical_price_adapter = (
            self.build_finmind_price_adapter(
                transport=ProviderUrllibHttpTransport(
                    allowed_hosts=frozenset({"api.finmindtrade.com"})
                ),
                source_access_mode="live_provider",
                mode="historical",
            )
            if self.source_adapter_security_context is not None
            else None
        )
        self.finmind_price_adapter = self.finmind_historical_price_adapter
        self._relay_fault = relay_fault or NoRelayFault()
        self._event_compatibility = event_compatibility or EventCompatibility.current()
        self._relay_clock = relay_clock or SystemRelayClock()
        self._relay_worker_id = relay_worker_id or str(uuid4())
        self._fixture_eod = FixtureEodWorkflow(
            self.state_store,
            observed_at=observed_at,
            object_repository=self.object_repository,
            security_context=self.security_context,
            authorization_policy=self.authorization_policy,
            authorization_time=fixed_security_time,
            authorization_uses_system_clock=fixed_security_time is None,
        )
        self._fixture_use = FixtureUseWorkflow(
            state_store=self.state_store,
        )

    def run_fixture_eod(
        self, command: FixtureEodCommand
    ) -> FixtureEodOutcome | PolicyDeniedOutcome:
        return self._fixture_eod.execute(command)

    def build_alpaca_price_adapter(
        self,
        *,
        transport: ProviderHttpTransport,
        source_access_mode: SourceAccessMode,
    ) -> AlpacaPriceSourceAdapter:
        if self.source_adapter_security_context is None:
            raise ValueError("source_adapter_identity_unavailable")
        reference_graph = load_candidate_alpaca_reference_graph()
        market_calendar_evidence = load_candidate_alpaca_market_calendar_evidence()
        return AlpacaPriceSourceAdapter(
            source_id=ALPACA_BARS_DISTRIBUTION.policy_dataset_id,
            mode="historical",
            adapter_version=f"{ALPACA_PROVIDER_ID}-v1",
            rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
            source_access_mode=source_access_mode,
            collector=AlpacaSourceCollector(
                source_id=ALPACA_BARS_DISTRIBUTION.policy_dataset_id,
                provider_id=ALPACA_PROVIDER_ID,
                reference_graph=reference_graph,
                market_calendar_evidence=market_calendar_evidence,
                credential_resolver=ManagedSourceCredentialResolver(
                    self.state_store,
                    self.secret_provider,
                    workload_principal_id=self.source_adapter_security_context.principal_id,
                    environment=self.source_adapter_security_context.environment,
                    clock=lambda: self._fixed_security_time or datetime.now(UTC),
                ),
                transport=transport,
                clock=lambda: self._fixed_security_time or datetime.now(UTC),
                rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
            ),
            decoder=AlpacaSourceDecoder(
                source_id=ALPACA_BARS_DISTRIBUTION.policy_dataset_id,
                reference_graph=reference_graph,
            ),
        )

    def build_finmind_price_adapter(
        self,
        *,
        transport: ProviderHttpTransport,
        source_access_mode: SourceAccessMode,
        mode: Literal["current", "historical"] = "historical",
    ) -> FinMindPriceSourceAdapter:
        if self.source_adapter_security_context is None:
            raise ValueError("source_adapter_identity_unavailable")
        reference_graph = load_candidate_finmind_reference_graph()
        source_id = FINMIND_PRICE_DISTRIBUTION.policy_dataset_id
        rate_limit_policy_id = "finmind-free-600-requests-per-hour-v1"
        return FinMindPriceSourceAdapter(
            source_id=source_id,
            mode=mode,
            adapter_version=f"{FINMIND_PROVIDER_ID}-v1",
            rate_limit_policy_id=rate_limit_policy_id,
            source_access_mode=source_access_mode,
            collector=FinMindSourceCollector(
                source_id=source_id,
                provider_id=FINMIND_PROVIDER_ID,
                reference_graph=reference_graph,
                credential_resolver=ManagedSourceCredentialResolver(
                    self.state_store,
                    self.secret_provider,
                    workload_principal_id=self.source_adapter_security_context.principal_id,
                    environment=self.source_adapter_security_context.environment,
                    clock=lambda: self._fixed_security_time or datetime.now(UTC),
                ),
                transport=transport,
                clock=lambda: self._fixed_security_time or datetime.now(UTC),
                rate_limit_policy_id=rate_limit_policy_id,
            ),
            decoder=FinMindSourceDecoder(
                source_id=source_id,
                reference_graph=reference_graph,
            ),
        )

    def _source_principal_attributes(
        self,
        principal_id: str,
    ) -> CurrentSourcePrincipalAttributes:
        contexts = {self.security_context.principal_id: self.security_context}
        if self.source_adapter_security_context is not None:
            contexts[self.source_adapter_security_context.principal_id] = (
                self.source_adapter_security_context
            )
        context = contexts.get(principal_id)
        if context is None:
            raise KeyError("source_principal_attributes_unavailable")
        return CurrentSourcePrincipalAttributes.from_verified_security_context(context)

    def authenticate_local_request(
        self,
        authorization_header: str,
        *,
        client_host: str,
    ) -> SecurityContext:
        return self.local_identity.verifier.authenticate(
            authorization_header,
            client_host=client_host,
            environment=self.security_context.environment,
            authenticated_at=self._fixed_security_time or datetime.now(UTC),
        )

    def relay_outbox(self, *, event_id: str | None = None) -> RelayOutcome:
        return self.state_store.relay_outbox(
            event_id=event_id,
            fault=self._relay_fault,
            compatibility=self._event_compatibility,
            clock=self._relay_clock,
            worker_id=self._relay_worker_id,
        )

    def attempt_fixture_use(self, command: FixtureUseCommand) -> dict[str, str]:
        return self._fixture_use.execute(command)


def build_test_application(
    *,
    observed_at: datetime | None = None,
    object_root: Path | None = None,
    database_url: str | None = None,
    relay_fault: RelayFault | None = None,
    event_compatibility: EventCompatibility | None = None,
    relay_clock: RelayClock | None = None,
    relay_worker_id: str | None = None,
    local_identity: LocalApiKeyIdentity | None = None,
    entitlement_states: Mapping[str, EntitlementStatus] | None = None,
    entitlement_purposes: Mapping[str, frozenset[str]] | None = None,
    grant_actions: frozenset[str] | None = None,
    policy_markets: frozenset[str] | None = None,
    authorization_time: datetime | None = None,
    authorization_policy_set_id: str | None = None,
    authorization_policy_override: AuthorizationPolicy | None = None,
    source_adapter_security_context: SecurityContext | None = None,
    secret_provider: SecretProvider | None = None,
    source_credential_validators: Mapping[str, SourceCredentialValidator] | None = None,
) -> Application:
    root = object_root or Path(mkdtemp(prefix="stock-forecasting-objects-"))
    resolved_database_url = database_url or "sqlite+pysqlite:///:memory:"
    identity_time = authorization_time or observed_at or datetime.now(UTC)
    resolved_identity = local_identity or LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=identity_time - timedelta(minutes=1),
        expires_at=identity_time + timedelta(hours=24),
    )
    authorization_policy_bootstrap = (
        authorization_policy_override
        or build_fixture_authorization_policy(
            resolved_identity.context,
            entitlement_states=entitlement_states,
            entitlement_purposes=entitlement_purposes,
            grant_actions=grant_actions,
            policy_markets=policy_markets,
        )
    )
    return Application(
        observed_at=observed_at,
        object_root=root,
        database_url=resolved_database_url,
        create_schema=True,
        relay_fault=relay_fault,
        event_compatibility=event_compatibility,
        relay_clock=relay_clock,
        relay_worker_id=relay_worker_id,
        local_identity=resolved_identity,
        authorization_policy_set_id=authorization_policy_set_id or f"test-policy-{uuid4()}",
        authorization_policy_bootstrap=authorization_policy_bootstrap,
        fixed_security_time=authorization_time or observed_at,
        source_adapter_security_context=source_adapter_security_context,
        secret_provider=secret_provider,
        source_credential_validators=source_credential_validators,
    )


def build_application(
    *,
    database_url: str,
    object_root: Path,
    observed_at: datetime,
    relay_fault: RelayFault | None = None,
    event_compatibility: EventCompatibility | None = None,
    relay_clock: RelayClock | None = None,
    relay_worker_id: str | None = None,
    local_identity: LocalApiKeyIdentity,
    authorization_policy_set_id: str,
    source_adapter_security_context: SecurityContext | None = None,
    secret_provider: SecretProvider | None = None,
    source_credential_validators: Mapping[str, SourceCredentialValidator] | None = None,
) -> Application:
    return Application(
        observed_at=observed_at,
        object_root=object_root,
        database_url=database_url,
        create_schema=False,
        relay_fault=relay_fault,
        event_compatibility=event_compatibility,
        relay_clock=relay_clock,
        relay_worker_id=relay_worker_id,
        local_identity=local_identity,
        authorization_policy_set_id=authorization_policy_set_id,
        authorization_policy_bootstrap=None,
        fixed_security_time=None,
        source_adapter_security_context=source_adapter_security_context,
        secret_provider=secret_provider,
        source_credential_validators=source_credential_validators,
    )
