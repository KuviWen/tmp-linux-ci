from __future__ import annotations

from pathlib import Path

import yaml


def test_openapi_contract_covers_research_health_and_unavailable_results() -> None:
    contract = yaml.safe_load(Path("openapi/openapi.yaml").read_text(encoding="utf-8"))

    assert contract["openapi"] == "3.2.0"
    assert set(contract["paths"]) == {
        "/api/v1/research/predictions",
        "/api/v1/research/listings/{listing_id}",
        "/api/v1/research/listings/{listing_id}/price-eligibility",
        "/api/v1/operations/health",
        "/api/v1/operations/sources",
        "/api/v1/operations/source-credentials",
        "/api/v1/operations/source-credentials/{provider_id}",
        "/api/v1/operations/source-credentials/{provider_id}/rotations",
        "/api/v1/operations/source-credentials/{provider_id}/validations",
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
            "core_projection_version": {"type": "integer", "minimum": 0},
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
    eligibility = contract["components"]["schemas"]["PriceResearchEligibility"]
    assert eligibility["additionalProperties"] is False
    assert set(eligibility["required"]) == {
        "checks",
        "source_basis_id",
        "source_basis",
        "formally_qualified",
        "downstream_readiness",
        "listing_id",
        "market",
        "reason_code",
        "sources",
        "status",
    }
    assert eligibility["properties"]["status"]["enum"] == [
        "qualified",
        "credential_required",
        "policy_blocked",
        "quarantined",
        "deferred",
    ]
    assert eligibility["properties"]["market"] == {
        "type": "string",
        "enum": ["XTAI", "XNAS", "XNYS"],
    }
    downstream = contract["components"]["schemas"]["PriceDownstreamReadiness"]
    assert set(downstream["required"]) == {
        "new_collection",
        "feature_materialization",
        "training",
        "research_display",
    }
    assert eligibility["properties"]["source_basis"] == {
        "oneOf": [
            {"$ref": "#/components/schemas/OpenDataSourceBasis"},
            {"$ref": "#/components/schemas/ZeroFeeAuthenticatedSourceBasis"},
        ]
    }
    zero_fee_basis = contract["components"]["schemas"]["ZeroFeeAuthenticatedSourceBasis"]
    assert set(zero_fee_basis["required"]) == {
        "account_required",
        "basis_type",
        "credential_kind",
        "fee_required",
        "members",
        "plan_id",
        "principal_classification",
        "provider_id",
        "qualification_status",
        "source_basis_id",
        "supplemental_references",
        "terms_content_sha256",
        "terms_url",
    }
    assert zero_fee_basis["properties"]["terms_content_sha256"]["type"] == [
        "string",
        "null",
    ]
    source = contract["components"]["schemas"]["PriceSourceEligibility"]
    assert source["properties"]["status"]["enum"] == [
        "published",
        "credential_required",
        "policy_blocked",
        "quarantined",
        "deferred",
    ]
    assert source["properties"]["source_mode"]["enum"] == ["current", "historical"]
    assert source["properties"]["dataset_version_id"]["type"] == ["string", "null"]
    assert source["properties"]["adjustment_version_id"]["type"] == ["string", "null"]
    assert source["properties"]["raw_object_id"]["type"] == ["string", "null"]
    assert source["properties"]["retrieval_receipt_id"]["type"] == ["string", "null"]
    assert source["properties"]["source_revision"]["type"] == ["string", "null"]
    assert source["properties"]["checkpoint"]["type"] == ["string", "null"]
    assert source["properties"]["rate_limit_policy_id"]["type"] == ["string", "null"]
    assert source["properties"]["retry_after_seconds"] == {
        "type": ["integer", "null"],
        "minimum": 0,
    }
    assert source["properties"]["evaluated_at"] == {"type": "string", "format": "date-time"}
    assert source["properties"]["policy_evaluation_id"] == {
        "type": "string",
        "format": "uuid",
    }
    assert source["properties"]["policy_correlation_id"] == {"type": "string"}
    assert "current_policy_decision" in source["required"]
    assert source["properties"]["current_policy_decision"] == {
        "oneOf": [
            {"$ref": "#/components/schemas/CurrentSourcePolicyDecision"},
            {"type": "null"},
        ]
    }
    current_decision = contract["components"]["schemas"]["CurrentSourcePolicyDecision"]
    assert current_decision["additionalProperties"] is False
    assert set(current_decision["required"]) == {
        "evaluation_id",
        "decision_id",
        "outcome",
        "reason_code",
        "subject_principal_id",
        "runtime_environment",
        "subject_attributes_evidence_id",
        "subject_attributes_valid_until",
        "subject_data_protection_classes",
        "dataset_id",
        "prior_evaluation_id",
        "prior_decision_id",
        "prior_trace_id",
        "prior_correlation_id",
        "evaluated_at",
        "valid_until",
        "grant_version_id",
        "source_policy_version_id",
        "source_entitlement_version_id",
        "evidence_artifact_id",
    }
    credential = contract["components"]["schemas"]["SourceCredentialMetadata"]
    assert credential["additionalProperties"] is False
    assert set(credential["properties"]) == {
        "provider_id",
        "display_name",
        "credential_kind",
        "required_fields",
        "readiness",
        "reason_code",
        "secret_ref_id",
        "version",
        "configured_at",
        "last_validated_at",
        "expires_at",
        "validation_evidence",
        "secret_cleanup_pending",
        "revoked_at",
        "registration_url",
        "key_management_url",
        "required_uses",
        "source_basis",
    }
    assert "credential_fields" not in credential["properties"]
    assert "expired" in credential["properties"]["readiness"]["enum"]
    write_request = contract["components"]["schemas"]["SourceCredentialWriteRequest"]
    assert write_request["properties"]["credential_fields"]["additionalProperties"] == {
        "type": "string",
        "writeOnly": True,
    }
    assert write_request["properties"]["expires_at"] == {
        "type": ["string", "null"],
        "format": "date-time",
    }
