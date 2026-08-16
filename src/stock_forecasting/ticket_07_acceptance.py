from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NoReturn
from urllib.request import Request, urlopen

from stock_forecasting.alpaca_market_data import (
    AlpacaPriceSourceAdapter,
    AlpacaSourceCollector,
    AlpacaSourceDecoder,
    ProviderHttpRequest,
    load_candidate_alpaca_reference_graph,
)
from stock_forecasting.authorization import LocalApiKeyIdentity
from stock_forecasting.authorization_repository import (
    TICKET_07_ENGINEERING_POLICY_SET,
    AuthorizationPolicyRepository,
)
from stock_forecasting.data_supply import (
    DataSupply,
    SourceBundleMemberRequest,
    SourcePartitionRequest,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.source_credentials import (
    EncryptedFilesystemSecretProvider,
    ManagedSourceCredentialResolver,
)
from stock_forecasting.us_stock_pool import load_us_stock_pool_manifest


class _ProviderMustNotBeContacted:
    def __init__(self) -> None:
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> NoReturn:
        self.requests.append(request)
        raise RuntimeError("missing_credential_provider_contacted")


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


def run_ticket_07_acceptance(
    *,
    database_url: str,
    object_root: Path,
    base_url: str,
    key_file: Path,
    source_adapter_key_file: Path,
    source_secret_root: Path,
) -> dict[str, object]:
    identity = LocalApiKeyIdentity.load(key_file)
    source_adapter_identity = LocalApiKeyIdentity.load(source_adapter_key_file)
    state_store = StateStore(database_url, create_schema=False)
    policy_repository = AuthorizationPolicyRepository(state_store)
    source_adapter_policy = policy_repository.get(
        TICKET_07_ENGINEERING_POLICY_SET,
        principal_id=source_adapter_identity.context.principal_id,
    )
    manifest = load_us_stock_pool_manifest()
    listing = manifest.listings[0]
    reference_graph = load_candidate_alpaca_reference_graph()
    secret_provider = EncryptedFilesystemSecretProvider(source_secret_root)
    transport = _ProviderMustNotBeContacted()
    adapter = AlpacaPriceSourceAdapter(
        source_id="alpaca-us-stock-bars",
        mode="historical",
        adapter_version="alpaca-market-data-basic-v1",
        rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        source_access_mode="engineering_double",
        collector=AlpacaSourceCollector(
            source_id="alpaca-us-stock-bars",
            provider_id="alpaca-market-data-basic",
            reference_graph=reference_graph,
            credential_resolver=ManagedSourceCredentialResolver(
                state_store,
                secret_provider,
                workload_principal_id=source_adapter_identity.context.principal_id,
                environment=source_adapter_identity.context.environment,
            ),
            transport=transport,
            clock=lambda: datetime.now(UTC),
            rate_limit_policy_id="alpaca-basic-200-requests-per-minute-v1",
        ),
        decoder=AlpacaSourceDecoder(
            source_id="alpaca-us-stock-bars",
            reference_graph=reference_graph,
        ),
    )
    data_supply = DataSupply(
        authorization_policy=source_adapter_policy,
        security_context=source_adapter_identity.context,
        adapters={"alpaca-us-stock-bars": adapter},
        object_repository=FilesystemObjectRepository(object_root),
        state_store=state_store,
        clock=lambda: datetime.now(UTC),
    )
    outcome = data_supply.materialize(
        SourcePartitionRequest(
            request_id="ticket-07-deployed-missing-credential",
            trace_id="p2-trace-us-01-deployed-missing-credential",
            source_id="alpaca-us-stock-bars",
            mode="historical",
            listing_ids=(listing.listing_id,),
            start_date=date(2024, 1, 3),
            end_date=date(2024, 1, 3),
            expected_checkpoint=None,
            distribution_id="alpaca-us-stock-bars-v2",
            distribution_url="https://data.alpaca.markets/v2/stocks/bars",
            source_basis_id="ENGINEERING-ALPACA-CONTRACT-01",
            bundle_members=(
                SourceBundleMemberRequest(
                    dataset_id="alpaca-us-corporate-actions-v1",
                    distribution_id="alpaca-us-corporate-actions-v1",
                    distribution_url="https://data.alpaca.markets/v1/corporate-actions",
                    schema_version="alpaca-corporate-actions-v1",
                ),
                SourceBundleMemberRequest(
                    dataset_id="alpaca-us-trading-calendar-v2",
                    distribution_id="alpaca-us-trading-calendar-v2",
                    distribution_url="https://paper-api.alpaca.markets/v2/calendar",
                    schema_version="alpaca-trading-calendar-v2",
                ),
            ),
        )
    )
    research_status, research_text = _get(
        base_url=base_url,
        path=f"/api/v1/research/listings/{listing.listing_id}/price-eligibility",
        identity=identity,
    )
    operations_status, operations_text = _get(
        base_url=base_url,
        path="/api/v1/operations/sources",
        identity=identity,
    )
    credential_status, credential_text = _get(
        base_url=base_url,
        path="/api/v1/operations/source-credentials",
        identity=identity,
    )
    research_ui_status, research_ui_text = _get(
        base_url=base_url,
        path=f"/research/listings/{listing.listing_id}/price-eligibility",
        identity=identity,
    )
    credential_ui_status, credential_ui_text = _get(
        base_url=base_url,
        path="/operations/source-credentials",
        identity=identity,
    )
    research = json.loads(research_text)
    operations = json.loads(operations_text)
    credential = json.loads(credential_text)["items"][0]
    checks = {
        "credential_required": outcome.status == "credential_required"
        and outcome.reason_code == "source_credential_missing",
        "provider_not_contacted": transport.requests == [],
        "no_false_lineage": outcome.raw_object_id is None
        and outcome.dataset_version_id is None
        and outcome.adjustment_version_id is None,
        "research_rest": research_status == 200
        and research.get("market") == listing.market
        and research.get("status") == "credential_required"
        and research.get("source_basis_id") == manifest.source_basis.source_basis_id,
        "operations_rest": operations_status == 200 and len(operations.get("items", [])) == 1,
        "credential_rest": credential_status == 200
        and credential.get("readiness") == "missing"
        and credential.get("secret_ref_id") is None
        and isinstance(credential.get("source_basis"), dict)
        and credential["source_basis"].get("source_basis_id")
        == manifest.source_basis.source_basis_id,
        "research_ui": research_ui_status == 200
        and "美股行情研究資格" in research_ui_text
        and "憑證待設定" in research_ui_text,
        "credential_ui": credential_ui_status == 200
        and "來源憑證管理" in credential_ui_text
        and "重新申請帳號" in credential_ui_text
        and 'type="password"' in credential_ui_text,
    }
    return {
        "ticket": "07",
        "status": "passed" if all(checks.values()) else "failed",
        "formal_qualification": False,
        "live_validation": "not_run",
        "provider_contract": "engineering",
        "source_basis_id": manifest.source_basis.source_basis_id,
        "listing_id": listing.listing_id,
        "checks": checks,
        "trace_ids": [outcome.trace_id],
    }
