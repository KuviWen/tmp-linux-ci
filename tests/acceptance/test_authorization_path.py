from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.authorization import (
    EntitlementStatus,
    LocalApiKeyIdentity,
    PolicyDeniedOutcome,
)
from stock_forecasting.fixture_market import FixtureMarket
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand


def test_active_entitlements_allow_both_fixture_sources_through_the_public_workflow() -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    application = build_test_application(observed_at=cutoff)

    markets: tuple[FixtureMarket, ...] = ("XTAI", "XNAS")
    outcomes = {
        market: application.require_fixture_eod_success(
            FixtureEodCommand(
                information_cutoff=cutoff,
                trace_id=f"trace-ticket-04-active-{market.lower()}",
                idempotency_key=f"ticket-04-active-{market.lower()}",
                market=market,
            )
        )
        for market in markets
    }

    for market, outcome in outcomes.items():
        assert outcome.status == "succeeded"
        research = application.research_query.require_listing_research(
            listing_id=outcome.listing_id,
            information_cutoff=cutoff,
        )
        authorization = research["source_evidence"]["authorization"]
        assert authorization["decision"] == "allow"
        assert authorization["reason_code"] == "authorized"
        assert authorization["grant_version_id"]
        assert authorization["source_policy_version_id"] == outcome.source_policy_version_id
        assert authorization["source_entitlement_version_id"]
        assert authorization["data_protection_class"] == "internal"
        audit = application.security_audit.list_events(
            trace_id=f"trace-ticket-04-active-{market.lower()}"
        )
        evaluation_id = audit[0]["evaluation_id"]
        assert audit == [
            {
                "action": "fixture_pipeline.execute",
                "outcome": "allowed",
                "reason_code": "authorized",
                "trace_id": f"trace-ticket-04-active-{market.lower()}",
                "evaluation_id": evaluation_id,
                "decision_id": authorization["decision_id"],
                "correlation_id": f"trace-ticket-04-active-{market.lower()}",
                "principal_id": application.security_context.principal_id,
                "credential_id": application.security_context.credential_id,
                "authentication_method": "local_api_key",
                "dataset_id": f"{market.lower()}-fixture-eod",
                "purpose": "fixture_research",
                "environment": "development",
                "grant_version_id": authorization["grant_version_id"],
                "source_policy_version_id": authorization["source_policy_version_id"],
                "source_entitlement_version_id": authorization["source_entitlement_version_id"],
                "data_protection_class": "internal",
                "evaluated_at": "2026-08-12T22:00:00Z",
                "valid_until": "2026-08-13T22:00:00Z",
            }
        ]


def test_authorization_uses_execution_clock_not_historical_fixture_observation() -> None:
    observed_at = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    authorization_time = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="local-researcher",
        environment="development",
        scopes={"fixture_pipeline.execute", "research_prediction.read"},
        issued_at=authorization_time - timedelta(minutes=1),
        expires_at=authorization_time + timedelta(hours=24),
    )
    application = build_test_application(
        observed_at=observed_at,
        authorization_time=authorization_time,
        local_identity=identity,
    )

    outcome = application.require_fixture_eod_success(
        FixtureEodCommand(
            information_cutoff=observed_at,
            trace_id="trace-ticket-04-security-clock",
            idempotency_key="ticket-04-security-clock",
        )
    )

    assert outcome.status == "succeeded"


@pytest.mark.parametrize(
    ("entitlement_status", "entitlement_purposes", "audit_reason"),
    [
        ("suspended", frozenset({"fixture_research"}), "source_entitlement_suspended"),
        ("expired", frozenset({"fixture_research"}), "source_entitlement_expired"),
        ("revoked", frozenset({"fixture_research"}), "source_entitlement_revoked"),
        ("active", frozenset(), "source_entitlement_purpose_denied"),
    ],
)
def test_inactive_entitlement_denies_before_raw_persistence_and_audits_true_reason(
    tmp_path: Path,
    entitlement_status: EntitlementStatus,
    entitlement_purposes: frozenset[str],
    audit_reason: str,
) -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    object_root = tmp_path / "objects"
    application = build_test_application(
        observed_at=cutoff,
        object_root=object_root,
        entitlement_states={"XTAI": entitlement_status},
        entitlement_purposes={"XTAI": entitlement_purposes},
    )
    trace_id = f"trace-ticket-04-denied-{audit_reason}"

    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id=trace_id,
            idempotency_key=trace_id,
            market="XTAI",
        )
    )

    assert isinstance(outcome, PolicyDeniedOutcome)
    assert outcome.code == "authorization_denied"
    assert outcome.correlation_id == trace_id
    assert list(object_root.rglob("*")) == []
    assert application.state_store.list_prediction_records(trace_id=trace_id) == []
    with pytest.raises(KeyError):
        application.operations_control.get_trace_evidence(trace_id)
    audit = application.security_audit.list_events(trace_id=trace_id)[0]
    assert application.security_audit.list_events(trace_id=trace_id) == [
        {
            "action": "fixture_pipeline.execute",
            "outcome": "denied",
            "reason_code": audit_reason,
            "trace_id": trace_id,
            "evaluation_id": audit["evaluation_id"],
            "decision_id": outcome.decision_id,
            "correlation_id": trace_id,
            "principal_id": application.security_context.principal_id,
            "credential_id": application.security_context.credential_id,
            "authentication_method": "local_api_key",
            "dataset_id": "xtai-fixture-eod",
            "purpose": "fixture_research",
            "environment": "development",
            "grant_version_id": application.authorization_policy.action_grants[0].version_id,
            "source_policy_version_id": application.authorization_policy.source_policies[
                0
            ].version_id,
            "source_entitlement_version_id": application.authorization_policy.source_entitlements[
                0
            ].version_id,
            "data_protection_class": "internal",
            "evaluated_at": "2026-08-12T22:00:00Z",
            "valid_until": "2026-08-12T22:00:00Z",
        }
    ]


def test_revoked_entitlement_blocks_an_existing_projection_without_deleting_it(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'authorization.db'}"
    active_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "objects",
        database_url=database_url,
    )
    outcome = active_application.require_fixture_eod_success(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-04-existing-active",
            idempotency_key="ticket-04-existing-active",
            market="XTAI",
        )
    )
    denied_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "objects",
        database_url=database_url,
        local_identity=active_application.local_identity,
        entitlement_states={"XTAI": "revoked"},
    )
    trace_id = "trace-ticket-04-existing-denied"

    denial = denied_application.research_query.get_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
        trace_id=trace_id,
    )

    assert isinstance(denial, PolicyDeniedOutcome)
    assert denial.code == "authorization_denied"
    audit = denied_application.security_audit.list_events(trace_id=trace_id)[0]
    assert audit == {
        "action": "research_prediction.read",
        "outcome": "denied",
        "reason_code": "source_entitlement_revoked",
        "trace_id": trace_id,
        "evaluation_id": audit["evaluation_id"],
        "decision_id": denial.decision_id,
        "correlation_id": trace_id,
        "principal_id": denied_application.security_context.principal_id,
        "credential_id": denied_application.security_context.credential_id,
        "authentication_method": "local_api_key",
        "dataset_id": "xtai-fixture-eod",
        "purpose": "fixture_research",
        "environment": "development",
        "grant_version_id": denied_application.authorization_policy.action_grants[0].version_id,
        "source_policy_version_id": denied_application.authorization_policy.source_policies[
            0
        ].version_id,
        "source_entitlement_version_id": (
            denied_application.authorization_policy.source_entitlements[0].version_id
        ),
        "data_protection_class": "internal",
        "evaluated_at": "2026-08-12T22:00:00Z",
        "valid_until": "2026-08-12T22:00:00Z",
    }
    restored = active_application.research_query.require_listing_research(
        listing_id=outcome.listing_id,
        information_cutoff=cutoff,
        trace_id="trace-ticket-04-existing-restored",
    )
    assert restored["identity"]["listing_id"] == outcome.listing_id


def test_repeated_authorization_evaluations_are_append_only_not_deduplicated() -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    application = build_test_application(
        observed_at=cutoff,
        entitlement_states={"XTAI": "revoked"},
    )
    command = FixtureEodCommand(
        information_cutoff=cutoff,
        trace_id="trace-ticket-04-repeated-denial",
        idempotency_key="ticket-04-repeated-denial",
    )

    first = application.run_fixture_eod(command)
    second = application.run_fixture_eod(command)

    assert isinstance(first, PolicyDeniedOutcome)
    assert isinstance(second, PolicyDeniedOutcome)
    assert first.decision_id == second.decision_id
    events = application.security_audit.list_events(trace_id=command.trace_id)
    assert len(events) == 2
    assert events[0]["evaluation_id"] != events[1]["evaluation_id"]
    assert {event["decision_id"] for event in events} == {first.decision_id}


def test_rest_and_ui_return_only_stable_denial_problem_while_audit_keeps_reason(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    cutoff_text = "2026-08-12T22:00:00Z"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'rest-authorization.db'}"
    active_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "objects",
        database_url=database_url,
    )
    outcome = active_application.require_fixture_eod_success(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-04-rest-active",
            idempotency_key="ticket-04-rest-active",
            market="XTAI",
        )
    )
    denied_application = build_test_application(
        observed_at=cutoff,
        object_root=tmp_path / "objects",
        database_url=database_url,
        local_identity=active_application.local_identity,
        entitlement_states={"XTAI": "revoked"},
    )
    client = TestClient(
        create_web_app(denied_application),
        client=("172.18.0.5", 50000),
    )

    missing_key = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff_text},
        headers={"X-Trace-Id": "trace-ticket-04-rest-missing-key"},
    )
    assert missing_key.status_code == 401
    assert missing_key.json()["code"] == "authentication_required"
    assert (
        denied_application.security_audit.list_events(trace_id="trace-ticket-04-rest-missing-key")
        == []
    )

    trace_id = "trace-ticket-04-rest-denied"
    response = client.get(
        "/api/v1/research/listings/" + outcome.listing_id,
        params={"information_cutoff": cutoff_text},
        headers={
            "Authorization": active_application.local_identity.credential.authorization_header(),
            "X-Trace-Id": trace_id,
        },
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://example.invalid/problems/authorization-denied",
        "title": "Authorization denied",
        "status": 403,
        "detail": "The requested operation is not authorized.",
        "instance": f"/api/v1/research/listings/{outcome.listing_id}",
        "trace_id": trace_id,
        "code": "authorization_denied",
    }
    audit = denied_application.security_audit.list_events(trace_id=trace_id)
    assert audit[0]["reason_code"] == "source_entitlement_revoked"
    assert audit[0]["correlation_id"] == trace_id

    ui_response = client.get(
        "/research",
        params={"information_cutoff": cutoff_text},
        headers={
            "Authorization": active_application.local_identity.credential.authorization_header(),
            "X-Trace-Id": "trace-ticket-04-ui-denied",
        },
    )
    assert ui_response.status_code == 403
    assert ui_response.json()["code"] == "authorization_denied"
    assert "source_entitlement_revoked" not in ui_response.text
    assert outcome.listing_id not in ui_response.text
