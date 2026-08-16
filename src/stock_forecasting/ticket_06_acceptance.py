from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from stock_forecasting.authorization import LocalApiKeyIdentity
from stock_forecasting.authorization_repository import (
    TICKET_06_POLICY_BLOCKED_SET,
    AuthorizationPolicyRepository,
)
from stock_forecasting.data_supply import (
    DataSupply,
    LoadedSourcePartition,
    SourcePartitionRequest,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore


class _PolicyBlockedAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def load(self, request: SourcePartitionRequest) -> LoadedSourcePartition:
        self.calls += 1
        raise RuntimeError("policy_blocked_adapter_called")


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


def run_ticket_06_acceptance(
    *,
    database_url: str,
    object_root: Path,
    base_url: str,
    key_file: Path,
) -> dict[str, object]:
    identity = LocalApiKeyIdentity.load(key_file)
    state_store = StateStore(database_url, create_schema=False)
    policy = AuthorizationPolicyRepository(state_store).get(
        TICKET_06_POLICY_BLOCKED_SET,
        principal_id=identity.context.principal_id,
    )
    current_adapter = _PolicyBlockedAdapter()
    historical_adapter = _PolicyBlockedAdapter()
    data_supply = DataSupply(
        authorization_policy=policy,
        security_context=identity.context,
        adapters={
            "twse-open-data-current": current_adapter,
            "twse-open-data-observed-history": historical_adapter,
        },
        object_repository=FilesystemObjectRepository(object_root),
        state_store=state_store,
        clock=lambda: datetime.now(UTC),
    )
    listing_id = load_taiwan_stock_pool_manifest().listings[0].listing_id
    outcomes = []
    for source_id, mode in (
        ("twse-open-data-current", "current"),
        ("twse-open-data-observed-history", "historical"),
    ):
        outcomes.append(
            data_supply.materialize(
                SourcePartitionRequest(
                    request_id=f"ticket-06-deployed-{mode}",
                    trace_id=f"p2-trace-tw-01-deployed-{mode}",
                    source_id=source_id,
                    mode=mode,  # type: ignore[arg-type]
                    listing_ids=(listing_id,),
                    start_date=date(2019, 8, 14),
                    end_date=date(2026, 8, 14),
                    expected_checkpoint=None,
                )
            )
        )
    research_status, research_text = _get(
        base_url=base_url,
        path=f"/api/v1/research/listings/{listing_id}/price-eligibility",
        identity=identity,
    )
    operations_status, operations_text = _get(
        base_url=base_url,
        path="/api/v1/operations/sources",
        identity=identity,
    )
    ui_status, ui_text = _get(
        base_url=base_url,
        path=f"/research/listings/{listing_id}/price-eligibility",
        identity=identity,
    )
    credential_status, credential_text = _get(
        base_url=base_url,
        path="/api/v1/operations/source-credentials",
        identity=identity,
    )
    credential_ui_status, credential_ui_text = _get(
        base_url=base_url,
        path="/operations/source-credentials",
        identity=identity,
    )
    research = json.loads(research_text)
    operations = json.loads(operations_text)
    credentials = json.loads(credential_text)
    finmind = next(
        (
            item
            for item in credentials.get("items", [])
            if item.get("provider_id") == "finmind-free-api"
        ),
        None,
    )
    checks = {
        "materialization_policy_blocked": all(
            outcome.status == "policy_blocked" for outcome in outcomes
        ),
        "provider_not_contacted": current_adapter.calls == historical_adapter.calls == 0,
        "research_rest": research_status == 200
        and research.get("status") == "policy_blocked"
        and research.get("source_basis_id") == "TWSE-OGDL-OPEN-DATA-01",
        "operations_rest": operations_status == 200 and len(operations.get("items", [])) == 2,
        "traditional_chinese_ui": ui_status == 200
        and "台股行情研究資格" in ui_text
        and "政策阻擋" in ui_text,
        "finmind_formal_candidate": credential_status == 200
        and finmind is not None
        and finmind.get("credential_kind") == "bearer_token"
        and finmind.get("readiness") == "missing"
        and finmind.get("reason_code") == "source_credential_missing"
        and finmind.get("source_basis", {}).get("source_basis_id")
        == "FINMIND-FREE-TAIWAN-MARKET-DATA-01"
        and finmind.get("source_basis", {}).get("qualification_status")
        == "candidate_terms_not_archived"
        and "credential_fields" not in finmind,
        "finmind_write_only_ui": credential_ui_status == 200
        and "FinMind Free API" in credential_ui_text
        and 'data-provider-id="finmind-free-api"' in credential_ui_text
        and 'name="token"' in credential_ui_text,
        "no_false_lineage": all(
            source.get("dataset_version_id") is None and source.get("adjustment_version_id") is None
            for source in research.get("sources", [])
        ),
    }
    return {
        "ticket": "06",
        "status": "passed" if all(checks.values()) else "failed",
        "formal_qualification": False,
        "source_basis_id": "TWSE-OGDL-OPEN-DATA-01",
        "listing_id": listing_id,
        "checks": checks,
        "trace_ids": [outcome.trace_id for outcome in outcomes],
    }
