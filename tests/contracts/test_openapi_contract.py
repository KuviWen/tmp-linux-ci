from __future__ import annotations

from pathlib import Path

import yaml


def test_openapi_contract_covers_research_health_and_unavailable_results() -> None:
    contract = yaml.safe_load(Path("openapi/openapi.yaml").read_text(encoding="utf-8"))

    assert contract["openapi"] == "3.2.0"
    assert set(contract["paths"]) == {
        "/api/v1/research/predictions",
        "/api/v1/research/listings/{listing_id}",
        "/api/v1/operations/health",
    }
    prediction = contract["components"]["schemas"]["PredictionResult"]
    assert prediction["oneOf"] == [
        {"$ref": "#/components/schemas/AvailablePrediction"},
        {"$ref": "#/components/schemas/UnavailablePrediction"},
    ]
    unavailable = contract["components"]["schemas"]["UnavailablePrediction"]
    assert "probabilities" not in unavailable["properties"]
    assert "confidence_score" not in unavailable["properties"]
    problem = contract["components"]["schemas"]["ProblemDetails"]
    assert set(problem["required"]) == {
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "trace_id",
        "code",
    }
