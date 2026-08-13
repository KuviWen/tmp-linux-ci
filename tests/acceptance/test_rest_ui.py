from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.application import build_test_application
from stock_forecasting.workflows.fixture_eod import FixtureEodCommand


def _client_with_fixture() -> tuple[TestClient, str, str]:
    cutoff = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    application = build_test_application(observed_at=cutoff)
    outcome = application.run_fixture_eod(
        FixtureEodCommand(
            information_cutoff=cutoff,
            trace_id="trace-ticket-01-rest",
            idempotency_key="ticket-01-rest",
        )
    )
    return TestClient(create_web_app(application)), outcome.listing_id, "2026-08-12T07:00:00Z"


def test_rest_matrix_and_listing_detail_expose_fixture_lineage() -> None:
    client, listing_id, cutoff = _client_with_fixture()

    matrix_response = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff},
    )
    assert matrix_response.status_code == 200
    assert matrix_response.headers["content-type"].startswith("application/json")
    assert matrix_response.headers["etag"].startswith('"')
    matrix = matrix_response.json()
    assert matrix["information_cutoff"] == cutoff
    assert matrix["items"][0]["listing_id"] == listing_id
    assert matrix["items"][0]["fixture_badge"] == "Fixture／非正式預測"
    assert [row["horizon_sessions"] for row in matrix["items"][0]["predictions"]] == [
        1,
        5,
        20,
    ]

    detail_response = client.get(
        f"/api/v1/research/listings/{listing_id}",
        params={"information_cutoff": cutoff},
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["identity"]["listing_id"] == listing_id
    assert detail["execution_purpose"] == "fixture"
    assert set(detail["lineage"]) == {
        "data_selection_id",
        "dataset_version_id",
        "feature_snapshot_id",
        "model_artifact_id",
        "serving_assignment_id",
        "raw_artifact_id",
    }


def test_traditional_chinese_matrix_and_detail_preserve_url_state_on_reload() -> None:
    client, listing_id, cutoff = _client_with_fixture()
    matrix_url = (
        "/research?information_cutoff=2026-08-12T07%3A00%3A00Z"
        "&horizon=5&market=XTAI&support=full&sort=confidence_desc"
    )

    matrix_response = client.get(matrix_url)
    assert matrix_response.status_code == 200
    assert matrix_response.headers["content-type"].startswith("text/html")
    assert '<html lang="zh-Hant">' in matrix_response.text
    assert "比較矩陣" in matrix_response.text
    assert "Fixture／非正式預測" in matrix_response.text
    assert "上漲 55.0%" in matrix_response.text
    assert "盤整 28.0%" in matrix_response.text
    assert "下跌 17.0%" in matrix_response.text
    assert "完整" in matrix_response.text
    assert f"/research/listings/{listing_id}?" in matrix_response.text

    detail_url = (
        f"/research/listings/{listing_id}"
        "?information_cutoff=2026-08-12T07%3A00%3A00Z"
        "&horizon=5&market=XTAI&support=full&sort=confidence_desc&tab=lineage"
    )
    first_load = client.get(detail_url)
    second_load = client.get(detail_url)

    assert first_load.status_code == 200
    assert first_load.text == second_load.text
    assert "標的研究頁" in first_load.text
    assert 'aria-current="page">版本追溯</a>' in first_load.text
    assert "FeatureSnapshot" in first_load.text
    assert "ModelArtifact" in first_load.text
    assert "服務指派" in first_load.text
    assert "資料集版本" in first_load.text
    assert "原始資料物件" in first_load.text
    assert "資訊截止點 2026-08-12T07:00:00Z" in first_load.text


def test_rest_matrix_etag_is_bound_to_the_same_snapshot() -> None:
    client, _, cutoff = _client_with_fixture()
    first = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff},
    )

    unchanged = client.get(
        "/api/v1/research/predictions",
        params={"information_cutoff": cutoff},
        headers={"If-None-Match": first.headers["etag"]},
    )

    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == first.headers["etag"]


def test_rest_not_found_uses_stable_problem_details() -> None:
    client, _, cutoff = _client_with_fixture()

    response = client.get(
        "/api/v1/research/listings/00000000-0000-0000-0000-000000000000",
        params={"information_cutoff": cutoff},
        headers={"X-Trace-Id": "trace-http-not-found"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://example.invalid/problems/listing-not-found",
        "title": "找不到掛牌研究資源",
        "status": 404,
        "detail": "指定的掛牌在此資訊截止點不存在或不可見。",
        "instance": "/api/v1/research/listings/00000000-0000-0000-0000-000000000000",
        "trace_id": "trace-http-not-found",
        "code": "listing_not_found",
    }


def test_process_probes_and_canonical_source_health_are_public() -> None:
    client, _, _ = _client_with_fixture()

    assert client.get("/livez").json() == {"status": "live"}
    assert client.get("/startupz").json() == {"status": "started"}
    assert client.get("/readyz").json() == {"status": "ready"}

    health_response = client.get(
        "/api/v1/operations/health",
        params={"scope": "xtai_fixture_source"},
    )
    assert health_response.status_code == 200
    assert health_response.json() == {
        "items": [
            {
                "scope": "xtai_fixture_source",
                "status": "ready",
                "reason_code": "coverage_complete",
                "affected_attempts": 1,
            }
        ]
    }


def test_versioned_openapi_source_is_served_without_a_generated_shadow_contract() -> None:
    client, _, _ = _client_with_fixture()

    source = client.get("/openapi/openapi.yaml")

    assert source.status_code == 200
    assert source.headers["content-type"].startswith("application/yaml")
    assert source.text.startswith("openapi: 3.2.0\n")
    assert client.get("/openapi.json").status_code == 404
