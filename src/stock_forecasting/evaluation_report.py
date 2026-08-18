from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from math import isfinite

from stock_forecasting.content_address import canonical_json


@dataclass(frozen=True)
class EvaluationReport:
    evaluation_report_id: str
    class_prior_equal_cell_macro_f1: float
    logistic_equal_cell_macro_f1: float
    improvement_percentage_points: float
    seed_macro_f1: tuple[float, ...]
    cost_manifest_id: str
    fold_manifest_id: str
    serialized: bytes

    @classmethod
    def create(
        cls,
        *,
        class_prior_equal_cell_macro_f1: float,
        logistic_equal_cell_macro_f1: float,
        seed_macro_f1: tuple[float, ...],
        cost_manifest_id: str,
        fold_manifest_id: str,
    ) -> EvaluationReport:
        improvement = (logistic_equal_cell_macro_f1 - class_prior_equal_cell_macro_f1) * 100
        values = (
            class_prior_equal_cell_macro_f1,
            logistic_equal_cell_macro_f1,
            improvement,
            *seed_macro_f1,
        )
        if (
            not seed_macro_f1
            or not cost_manifest_id
            or not fold_manifest_id
            or any(not isfinite(value) for value in values)
            or any(not 0.0 <= value <= 1.0 for value in values[:2])
            or any(not 0.0 <= value <= 1.0 for value in seed_macro_f1)
        ):
            raise ValueError("evaluation_report_invalid")
        payload = {
            "artifact_kind": "bootstrap_evaluation_report",
            "schema_version": "bootstrap-evaluation-report/v1",
            "class_prior_equal_cell_macro_f1": class_prior_equal_cell_macro_f1,
            "logistic_equal_cell_macro_f1": logistic_equal_cell_macro_f1,
            "improvement_percentage_points": improvement,
            "seed_macro_f1": seed_macro_f1,
            "cost_manifest_id": cost_manifest_id,
            "fold_manifest_id": fold_manifest_id,
        }
        serialized = canonical_json(payload).encode("utf-8")
        return cls(
            evaluation_report_id=f"sha256:{hashlib.sha256(serialized).hexdigest()}",
            class_prior_equal_cell_macro_f1=class_prior_equal_cell_macro_f1,
            logistic_equal_cell_macro_f1=logistic_equal_cell_macro_f1,
            improvement_percentage_points=improvement,
            seed_macro_f1=seed_macro_f1,
            cost_manifest_id=cost_manifest_id,
            fold_manifest_id=fold_manifest_id,
            serialized=serialized,
        )

    @classmethod
    def from_serialized(cls, evaluation_report_id: str, serialized: bytes) -> EvaluationReport:
        try:
            payload = json.loads(serialized)
        except (TypeError, ValueError) as error:
            raise ValueError("evaluation_report_schema_invalid") from error
        expected_fields = {
            "artifact_kind",
            "schema_version",
            "class_prior_equal_cell_macro_f1",
            "logistic_equal_cell_macro_f1",
            "improvement_percentage_points",
            "seed_macro_f1",
            "cost_manifest_id",
            "fold_manifest_id",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_fields
            or payload.get("artifact_kind") != "bootstrap_evaluation_report"
            or payload.get("schema_version") != "bootstrap-evaluation-report/v1"
            or not isinstance(payload.get("seed_macro_f1"), list)
            or not isinstance(payload.get("cost_manifest_id"), str)
            or not isinstance(payload.get("fold_manifest_id"), str)
        ):
            raise ValueError("evaluation_report_schema_invalid")
        numeric = (
            payload.get("class_prior_equal_cell_macro_f1"),
            payload.get("logistic_equal_cell_macro_f1"),
            payload.get("improvement_percentage_points"),
            *payload["seed_macro_f1"],
        )
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numeric):
            raise ValueError("evaluation_report_schema_invalid")
        report = cls.create(
            class_prior_equal_cell_macro_f1=float(payload["class_prior_equal_cell_macro_f1"]),
            logistic_equal_cell_macro_f1=float(payload["logistic_equal_cell_macro_f1"]),
            seed_macro_f1=tuple(float(value) for value in payload["seed_macro_f1"]),
            cost_manifest_id=payload["cost_manifest_id"],
            fold_manifest_id=payload["fold_manifest_id"],
        )
        if (
            report.evaluation_report_id != evaluation_report_id
            or report.serialized != serialized
            or abs(
                report.improvement_percentage_points
                - float(payload["improvement_percentage_points"])
            )
            > 1e-12
        ):
            raise ValueError("evaluation_report_checksum_mismatch")
        return report
