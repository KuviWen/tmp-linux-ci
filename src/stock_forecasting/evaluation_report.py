from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from typing import cast

from stock_forecasting.content_address import canonical_json_bytes, sha256_id


@dataclass(frozen=True)
class SeedArtifactEvaluation:
    seed: int
    logistic_artifact_id: str
    class_prior_artifact_id: str
    logistic_macro_f1: float

    def payload(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "logistic_artifact_id": self.logistic_artifact_id,
            "class_prior_artifact_id": self.class_prior_artifact_id,
            "logistic_macro_f1": self.logistic_macro_f1,
        }


@dataclass(frozen=True)
class EvaluationReport:
    evaluation_report_id: str
    class_prior_equal_cell_macro_f1: float
    logistic_equal_cell_macro_f1: float
    improvement_percentage_points: float
    seed_results: tuple[SeedArtifactEvaluation, SeedArtifactEvaluation, SeedArtifactEvaluation]
    feature_batch_id: str
    source_policy_manifest_id: str
    label_manifest_id: str
    cost_manifest_id: str
    fold_manifest_id: str
    serialized: bytes

    @property
    def seed_macro_f1(self) -> tuple[float, float, float]:
        return cast(
            tuple[float, float, float],
            tuple(result.logistic_macro_f1 for result in self.seed_results),
        )

    @property
    def logistic_artifact_ids(self) -> tuple[str, str, str]:
        return cast(
            tuple[str, str, str],
            tuple(result.logistic_artifact_id for result in self.seed_results),
        )

    @property
    def class_prior_artifact_ids(self) -> tuple[str, str, str]:
        return cast(
            tuple[str, str, str],
            tuple(result.class_prior_artifact_id for result in self.seed_results),
        )

    @classmethod
    def create(
        cls,
        *,
        class_prior_equal_cell_macro_f1: float,
        logistic_equal_cell_macro_f1: float,
        seed_results: tuple[SeedArtifactEvaluation, ...],
        feature_batch_id: str,
        source_policy_manifest_id: str,
        label_manifest_id: str,
        cost_manifest_id: str,
        fold_manifest_id: str,
    ) -> EvaluationReport:
        improvement = (logistic_equal_cell_macro_f1 - class_prior_equal_cell_macro_f1) * 100
        values = (
            class_prior_equal_cell_macro_f1,
            logistic_equal_cell_macro_f1,
            improvement,
            *(result.logistic_macro_f1 for result in seed_results),
        )
        seeds = tuple(result.seed for result in seed_results)
        artifact_ids = tuple(
            artifact_id
            for result in seed_results
            for artifact_id in (
                result.logistic_artifact_id,
                result.class_prior_artifact_id,
            )
        )
        manifests = (
            feature_batch_id,
            source_policy_manifest_id,
            label_manifest_id,
            cost_manifest_id,
            fold_manifest_id,
        )
        if (
            len(seed_results) != 3
            or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
            or len(set(seeds)) != 3
            or any(
                not isinstance(artifact_id, str) or not artifact_id for artifact_id in artifact_ids
            )
            or any(not isinstance(manifest, str) or not manifest for manifest in manifests)
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) for value in values
            )
            or any(not isfinite(float(value)) for value in values)
            or any(not 0.0 <= float(value) <= 1.0 for value in values[:2])
            or any(not 0.0 <= result.logistic_macro_f1 <= 1.0 for result in seed_results)
            or abs(
                logistic_equal_cell_macro_f1
                - sum(result.logistic_macro_f1 for result in seed_results) / 3
            )
            > 1e-12
        ):
            raise ValueError("evaluation_report_invalid")
        payload = {
            "artifact_kind": "bootstrap_evaluation_report",
            "schema_version": "bootstrap-evaluation-report/v2",
            "class_prior_equal_cell_macro_f1": class_prior_equal_cell_macro_f1,
            "logistic_equal_cell_macro_f1": logistic_equal_cell_macro_f1,
            "improvement_percentage_points": improvement,
            "seed_results": [result.payload() for result in seed_results],
            "feature_batch_id": feature_batch_id,
            "source_policy_manifest_id": source_policy_manifest_id,
            "label_manifest_id": label_manifest_id,
            "cost_manifest_id": cost_manifest_id,
            "fold_manifest_id": fold_manifest_id,
        }
        serialized = canonical_json_bytes(payload)
        return cls(
            evaluation_report_id=sha256_id(serialized),
            class_prior_equal_cell_macro_f1=class_prior_equal_cell_macro_f1,
            logistic_equal_cell_macro_f1=logistic_equal_cell_macro_f1,
            improvement_percentage_points=improvement,
            seed_results=seed_results,
            feature_batch_id=feature_batch_id,
            source_policy_manifest_id=source_policy_manifest_id,
            label_manifest_id=label_manifest_id,
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
            "seed_results",
            "feature_batch_id",
            "source_policy_manifest_id",
            "label_manifest_id",
            "cost_manifest_id",
            "fold_manifest_id",
        }
        manifest_fields = (
            "feature_batch_id",
            "source_policy_manifest_id",
            "label_manifest_id",
            "cost_manifest_id",
            "fold_manifest_id",
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_fields
            or payload.get("artifact_kind") != "bootstrap_evaluation_report"
            or payload.get("schema_version") != "bootstrap-evaluation-report/v2"
            or not isinstance(payload.get("seed_results"), list)
            or any(not isinstance(payload.get(field), str) for field in manifest_fields)
        ):
            raise ValueError("evaluation_report_schema_invalid")
        results: list[SeedArtifactEvaluation] = []
        for raw_result in payload["seed_results"]:
            if (
                not isinstance(raw_result, dict)
                or set(raw_result)
                != {
                    "seed",
                    "logistic_artifact_id",
                    "class_prior_artifact_id",
                    "logistic_macro_f1",
                }
                or isinstance(raw_result["seed"], bool)
                or not isinstance(raw_result["seed"], int)
                or not isinstance(raw_result["logistic_artifact_id"], str)
                or not isinstance(raw_result["class_prior_artifact_id"], str)
                or isinstance(raw_result["logistic_macro_f1"], bool)
                or not isinstance(raw_result["logistic_macro_f1"], (int, float))
            ):
                raise ValueError("evaluation_report_schema_invalid")
            results.append(
                SeedArtifactEvaluation(
                    seed=raw_result["seed"],
                    logistic_artifact_id=raw_result["logistic_artifact_id"],
                    class_prior_artifact_id=raw_result["class_prior_artifact_id"],
                    logistic_macro_f1=float(raw_result["logistic_macro_f1"]),
                )
            )
        numeric = (
            payload.get("class_prior_equal_cell_macro_f1"),
            payload.get("logistic_equal_cell_macro_f1"),
            payload.get("improvement_percentage_points"),
        )
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numeric):
            raise ValueError("evaluation_report_schema_invalid")
        report = cls.create(
            class_prior_equal_cell_macro_f1=float(payload["class_prior_equal_cell_macro_f1"]),
            logistic_equal_cell_macro_f1=float(payload["logistic_equal_cell_macro_f1"]),
            seed_results=tuple(results),
            feature_batch_id=payload["feature_batch_id"],
            source_policy_manifest_id=payload["source_policy_manifest_id"],
            label_manifest_id=payload["label_manifest_id"],
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
