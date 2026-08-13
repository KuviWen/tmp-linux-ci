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
    assert unavailable["properties"]["unavailable_reason"]["properties"]["code"]["enum"] == [
        "missing_anchor_price",
        "post_cutoff_evidence",
        "source_withdrawn",
        "missing_company_action",
        "calendar_unresolved",
    ]
    matrix_item = contract["components"]["schemas"]["MatrixItem"]
    assert matrix_item["properties"]["market"] == {
        "type": "string",
        "enum": ["XTAI", "XNAS"],
    }
    assert "projection" in matrix_item["required"]
    assert matrix_item["properties"]["projection"] == {
        "$ref": "#/components/schemas/ProjectionStatus"
    }
    listing_research = contract["components"]["schemas"]["ListingResearch"]
    assert "projection" in listing_research["required"]
    projection = contract["components"]["schemas"]["ProjectionStatus"]
    assert projection == {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "core_projection_version",
            "evidence_projection_version",
            "stale",
        ],
        "properties": {
            "core_projection_version": {"type": "integer", "minimum": 1},
            "evidence_projection_version": {"type": "integer", "minimum": 0},
            "stale": {"type": "boolean"},
        },
    }
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
