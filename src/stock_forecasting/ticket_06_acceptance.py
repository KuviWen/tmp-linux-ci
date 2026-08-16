from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, NoReturn
from urllib.request import Request, urlopen

from stock_forecasting.authorization import LocalApiKeyIdentity
from stock_forecasting.authorization_repository import (
    TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
    AuthorizationPolicyRepository,
)
from stock_forecasting.data_supply import (
    DataSupply,
    SourceBundleMemberRequest,
    SourcePartitionRequest,
    load_taiwan_stock_pool_manifest,
)
from stock_forecasting.finmind_market_data import (
    FinMindPriceSourceAdapter,
    FinMindSourceCollector,
    FinMindSourceDecoder,
    load_candidate_finmind_reference_graph,
)
from stock_forecasting.finmind_provider_contract import (
    FINMIND_PRICE_DISTRIBUTION,
    FINMIND_PROVIDER_ID,
    FINMIND_REQUIRED_BUNDLE_DISTRIBUTIONS,
)
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.provider_http import ProviderHttpRequest
from stock_forecasting.source_credentials import (
    EncryptedFilesystemSecretProvider,
    ManagedSourceCredentialResolver,
)


class _ProviderMustNotBeContacted:
    def __init__(self) -> None:
        self.requests: list[ProviderHttpRequest] = []

    def send(self, request: ProviderHttpRequest) -> NoReturn:
        self.requests.append(request)
        raise RuntimeError("missing_credential_provider_contacted")


def _request(
    *,
    base_url: str,
    path: str,
    identity: LocalApiKeyIdentity,
    method: Literal["GET", "PUT", "POST", "DELETE"] = "GET",
    body: dict[str, object] | None = None,
) -> tuple[int, str]:
    headers = {"Authorization": identity.credential.authorization_header()}
    encoded_body = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded_body = json.dumps(body).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=encoded_body,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed deployed base URL
        return response.status, response.read().decode("utf-8")


def run_ticket_06_acceptance(
    *,
    base_url: str,
    key_file: Path,
) -> dict[str, object]:
    identity = LocalApiKeyIdentity.load(key_file)
    manifest = load_taiwan_stock_pool_manifest()
    listing_id = manifest.listings[0].listing_id
    research_status, research_text = _request(
        base_url=base_url,
        path=f"/api/v1/research/listings/{listing_id}/price-eligibility",
        identity=identity,
    )
    operations_status, operations_text = _request(
        base_url=base_url,
        path="/api/v1/operations/sources",
        identity=identity,
    )
    ui_status, ui_text = _request(
        base_url=base_url,
        path=f"/research/listings/{listing_id}/price-eligibility",
        identity=identity,
    )
    credential_status, credential_text = _request(
        base_url=base_url,
        path="/api/v1/operations/source-credentials",
        identity=identity,
    )
    credential_ui_status, credential_ui_text = _request(
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
    first_token = "ticket-06-finmind-expired-token-v1"
    second_token = "ticket-06-finmind-expired-token-v2"
    credential_path = "/api/v1/operations/source-credentials/finmind-free-api"
    set_status, set_text = _request(
        base_url=base_url,
        path=credential_path,
        identity=identity,
        method="PUT",
        body={
            "credential_fields": {"token": first_token},
            "expires_at": "2000-01-01T00:00:00Z",
        },
    )
    validation_status, validation_text = _request(
        base_url=base_url,
        path=f"{credential_path}/validations",
        identity=identity,
        method="POST",
    )
    expired_research_status, expired_research_text = _request(
        base_url=base_url,
        path=f"/api/v1/research/listings/{listing_id}/price-eligibility",
        identity=identity,
    )
    rotation_status, rotation_text = _request(
        base_url=base_url,
        path=f"{credential_path}/rotations",
        identity=identity,
        method="POST",
        body={
            "credential_fields": {"token": second_token},
            "expires_at": "2000-01-01T00:00:00Z",
        },
    )
    revoke_status, revoke_text = _request(
        base_url=base_url,
        path=credential_path,
        identity=identity,
        method="DELETE",
    )
    final_credential_status, final_credential_text = _request(
        base_url=base_url,
        path="/api/v1/operations/source-credentials",
        identity=identity,
    )
    revoked_research_status, revoked_research_text = _request(
        base_url=base_url,
        path=f"/api/v1/research/listings/{listing_id}/price-eligibility",
        identity=identity,
    )
    set_payload = json.loads(set_text)
    validation_payload = json.loads(validation_text)
    rotation_payload = json.loads(rotation_text)
    revoke_payload = json.loads(revoke_text)
    final_credentials = json.loads(final_credential_text)
    expired_research = json.loads(expired_research_text)
    revoked_research = json.loads(revoked_research_text)
    final_finmind = next(
        item
        for item in final_credentials.get("items", [])
        if item.get("provider_id") == "finmind-free-api"
    )
    public_text = "".join(
        (
            research_text,
            operations_text,
            ui_text,
            credential_text,
            credential_ui_text,
            set_text,
            validation_text,
            expired_research_text,
            rotation_text,
            revoke_text,
            final_credential_text,
            revoked_research_text,
        )
    )
    checks = {
        "research_rest": research_status == 200
        and research.get("status") == "credential_required"
        and research.get("source_basis_id") == "FINMIND-FREE-TAIWAN-MARKET-DATA-01"
        and research.get("formally_qualified") is False
        and set(research.get("downstream_readiness", {}).values()) == {"credential_required"},
        "operations_rest": operations_status == 200 and len(operations.get("items", [])) == 2,
        "traditional_chinese_ui": ui_status == 200
        and "台股行情研究資格" in ui_text
        and "憑證待設定" in ui_text,
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
        "finmind_credential_set": set_status == 200
        and set_payload.get("readiness") == "configured"
        and set_payload.get("version") == 1,
        "expired_validation_fail_closed": validation_status == 200
        and validation_payload.get("credential", {}).get("readiness") == "expired"
        and validation_payload.get("credential", {}).get("reason_code")
        == "source_credential_expired"
        and validation_payload.get("credential", {})
        .get("validation_evidence", {})
        .get("authentication_status")
        == "not_run"
        and expired_research_status == 200
        and expired_research.get("status") == "credential_required"
        and expired_research.get("formally_qualified") is False,
        "finmind_credential_rotated": rotation_status == 200
        and rotation_payload.get("readiness") == "configured"
        and rotation_payload.get("version") == 3,
        "finmind_credential_revoked": revoke_status == 200
        and final_credential_status == 200
        and revoke_payload.get("readiness") == "revoked"
        and final_finmind.get("readiness") == "revoked"
        and final_finmind.get("reason_code") == "source_credential_revoked"
        and revoked_research_status == 200
        and revoked_research.get("status") == "credential_required"
        and revoked_research.get("formally_qualified") is False,
        "credential_plaintext_absent": first_token not in public_text
        and second_token not in public_text,
        "no_false_lineage": all(
            source.get("dataset_version_id") is None and source.get("adjustment_version_id") is None
            for source in research.get("sources", [])
        ),
    }
    return {
        "ticket": "06",
        "status": "passed" if all(checks.values()) else "failed",
        "formal_qualification": research.get("formally_qualified"),
        "source_basis_id": "FINMIND-FREE-TAIWAN-MARKET-DATA-01",
        "listing_id": listing_id,
        "checks": checks,
        "trace_ids": [],
    }


def run_ticket_06_source_probe(
    *,
    database_url: str,
    object_root: Path,
    source_adapter_key_file: Path,
    source_secret_root: Path,
) -> dict[str, object]:
    identity = LocalApiKeyIdentity.load(source_adapter_key_file)
    state_store = StateStore(database_url, create_schema=False)
    policy = AuthorizationPolicyRepository(state_store).get(
        TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
        principal_id=identity.context.principal_id,
    )
    manifest = load_taiwan_stock_pool_manifest()
    listing_id = manifest.listings[0].listing_id
    transport = _ProviderMustNotBeContacted()
    reference_graph = load_candidate_finmind_reference_graph()
    credential_resolver = ManagedSourceCredentialResolver(
        state_store,
        EncryptedFilesystemSecretProvider(source_secret_root),
        workload_principal_id=identity.context.principal_id,
        environment=identity.context.environment,
        clock=lambda: datetime.now(UTC),
    )
    outcomes = []
    for mode in ("current", "historical"):
        adapter = FinMindPriceSourceAdapter(
            source_id=FINMIND_PRICE_DISTRIBUTION.policy_dataset_id,
            mode=mode,
            adapter_version=f"finmind-ticket-06-{mode}-v1",
            rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
            source_access_mode="engineering_double",
            collector=FinMindSourceCollector(
                source_id=FINMIND_PRICE_DISTRIBUTION.policy_dataset_id,
                provider_id=FINMIND_PROVIDER_ID,
                reference_graph=reference_graph,
                credential_resolver=credential_resolver,
                transport=transport,
                clock=lambda: datetime.now(UTC),
                rate_limit_policy_id="finmind-free-600-requests-per-hour-v1",
            ),
            decoder=FinMindSourceDecoder(
                source_id=FINMIND_PRICE_DISTRIBUTION.policy_dataset_id,
                reference_graph=reference_graph,
            ),
        )
        outcome = DataSupply(
            authorization_policy=policy,
            security_context=identity.context,
            adapters={FINMIND_PRICE_DISTRIBUTION.policy_dataset_id: adapter},
            object_repository=FilesystemObjectRepository(object_root),
            state_store=state_store,
            clock=lambda: datetime.now(UTC),
        ).materialize(
            SourcePartitionRequest(
                request_id=f"ticket-06-deployed-{mode}",
                trace_id=f"p2-trace-tw-01-deployed-{mode}",
                source_id=FINMIND_PRICE_DISTRIBUTION.policy_dataset_id,
                mode=mode,
                listing_ids=(listing_id,),
                start_date=date(2024, 1, 3),
                end_date=date(2024, 1, 3),
                expected_checkpoint=None,
                distribution_id=FINMIND_PRICE_DISTRIBUTION.distribution_id,
                distribution_url=FINMIND_PRICE_DISTRIBUTION.distribution_url,
                source_basis_id="ENGINEERING-FINMIND-CONTRACT-01",
                bundle_members=tuple(
                    SourceBundleMemberRequest(
                        dataset_id=distribution.policy_dataset_id,
                        distribution_id=distribution.distribution_id,
                        distribution_url=distribution.distribution_url,
                        schema_version=f"finmind-{distribution.distribution_id}-v1",
                    )
                    for distribution in FINMIND_REQUIRED_BUNDLE_DISTRIBUTIONS
                ),
            )
        )
        outcomes.append(outcome)
    checks = {
        "materialization_credential_required": all(
            outcome.status == "credential_required"
            and outcome.reason_code == "source_credential_missing"
            for outcome in outcomes
        ),
        "provider_not_contacted": transport.requests == [],
        "source_identity_least_privilege": policy.action_grants[0].actions
        == frozenset({"market_data.collect"}),
        "no_false_lineage": all(
            outcome.raw_object_id is None
            and outcome.dataset_version_id is None
            and outcome.adjustment_version_id is None
            for outcome in outcomes
        ),
    }
    return {
        "ticket": "06-source-probe",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "trace_ids": [outcome.trace_id for outcome in outcomes],
    }
