from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast

from stock_forecasting.data_supply import HistoricalAvailabilityClaim
from stock_forecasting.platform.object_repository import FilesystemObjectRepository
from stock_forecasting.platform.state_store import StateStore

HistoricalEvidenceAction = Literal["qualify", "supersede", "revoke", "expire"]
HistoricalEvidenceLevel = Literal[
    "platform_observed",
    "archive_attested",
    "published_current_only",
    "unknown",
    "self_asserted",
]


@dataclass(frozen=True)
class HistoricalEvidenceCommand:
    action: HistoricalEvidenceAction
    listing_id: str
    market: str
    source_id: str
    evidence_level: HistoricalEvidenceLevel
    evidence_object_id: str
    source_policy_id: str
    public_terms_url: str
    trace_id: str
    prior_claim_id: str | None = None


@dataclass(frozen=True)
class HistoricalEvidenceOutcome:
    status: Literal["qualified", "quarantined", "revoked", "expired"]
    reason_code: str
    claim_id: str | None
    use_scope: tuple[str, ...] = ()
    artifact_ids: dict[str, str] = field(default_factory=dict)


class HistoricalEvidenceWorkflow:
    def __init__(
        self,
        state_store: StateStore,
        *,
        object_repository: FilesystemObjectRepository,
        observed_at: datetime,
    ) -> None:
        self._state_store = state_store
        self._object_repository = object_repository
        self._observed_at = observed_at

    def execute(self, command: HistoricalEvidenceCommand) -> HistoricalEvidenceOutcome:
        rejected_levels = {
            "self_asserted": "historical_evidence_self_asserted",
            "published_current_only": "historical_evidence_current_only",
            "unknown": "historical_evidence_unknown",
        }
        if reason_code := rejected_levels.get(command.evidence_level):
            return self._quarantine(command, reason_code=reason_code)
        if command.action in {"qualify", "supersede"} and command.evidence_level in {
            "platform_observed",
            "archive_attested",
        }:
            return self._qualify(command)
        if command.action in {"revoke", "expire"}:
            return self._record_claim_status(command)
        raise NotImplementedError(command.action)

    def _qualify(self, command: HistoricalEvidenceCommand) -> HistoricalEvidenceOutcome:
        prior_claim: dict[str, object] | None = None
        if command.action == "supersede":
            if command.prior_claim_id is None:
                raise ValueError("prior_claim_required")
            prior_claim = self._state_store.get_verified_governance_artifact(
                artifact_id=command.prior_claim_id,
                artifact_kind="historical_availability_claim",
            )
            if (
                prior_claim.get("listing_id") != command.listing_id
                or prior_claim.get("source_id") != command.source_id
            ):
                raise ValueError("prior_claim_scope_mismatch")
        evidence_bytes = self._object_repository.open_by_id(command.evidence_object_id).read()
        evidence = cast(dict[str, object], json.loads(evidence_bytes))
        listings = cast(list[dict[str, object]], evidence["listings"])
        listing = next(
            (
                item
                for item in listings
                if item.get("listing_id") == command.listing_id
                and item.get("market") == command.market
            ),
            None,
        )
        expected_observation_kind = {
            "platform_observed": "platform_observation",
            "archive_attested": "official_archive",
        }[command.evidence_level]
        if (
            evidence.get("schema_version") != "historical-reconstruction-evidence/v1"
            or evidence.get("price_schema_version")
            not in {"taiwan-unadjusted-eod-v1", "us-unadjusted-eod-v1"}
            or evidence.get("observation_kind") != expected_observation_kind
            or evidence.get("public_terms_url") != command.public_terms_url
            or listing is None
            or not listing.get("security_id")
            or not listing.get("sessions")
            or not listing.get("unadjusted_prices")
            or not listing.get("lifecycle")
            or "company_actions" not in listing
        ):
            return self._quarantine(
                command,
                reason_code="historical_evidence_invalid",
            )
        if listing.get("company_actions_status") != "complete":
            return self._quarantine(
                command,
                reason_code="historical_evidence_company_actions_incomplete",
            )
        coverage = cast(dict[str, object], evidence["coverage"])
        validity = cast(dict[str, object], evidence["validity"])
        sessions = cast(list[str], listing["sessions"])
        price_rows = cast(list[dict[str, object]], listing["unadjusted_prices"])
        price_sessions = [str(row.get("session_date")) for row in price_rows]
        price_session_set = set(price_sessions)
        if (
            len(price_sessions) != len(price_session_set)
            or price_sessions != [session for session in sessions if session in price_session_set]
            or coverage.get("start") != sessions[0]
            or coverage.get("end") != sessions[-1]
        ):
            return self._quarantine(
                command,
                reason_code="historical_evidence_session_mismatch",
            )
        use_scope = (
            ("production", "historical_reconstruction")
            if command.evidence_level == "platform_observed"
            else ("historical_reconstruction",)
        )
        verification_payload: dict[str, object] = {
            "verification_schema_version": "historical-evidence-verification/v1",
            "listing_id": command.listing_id,
            "market": command.market,
            "source_id": command.source_id,
            "evidence_level": command.evidence_level,
            "evidence_object_id": command.evidence_object_id,
            "evidence_checksum": hashlib.sha256(evidence_bytes).hexdigest(),
            "evidence_version": evidence["evidence_version"],
            "evidence_revision": evidence["revision"],
            "observation_kind": evidence["observation_kind"],
            "observation_reference": evidence["observation_reference"],
            "evidence_observed_at": evidence["observed_at"],
            "observed_start": coverage["start"],
            "observed_end": coverage["end"],
            "source_policy_id": command.source_policy_id,
            "public_terms_url": command.public_terms_url,
            "valid_from": validity["valid_from"],
            "valid_until": validity["valid_until"],
            "verified_at": self._observed_at.isoformat(),
            "checks": {
                "exact_sessions": "passed",
                "integrity": "passed",
                "company_actions": "passed",
                "listing_lifecycle": "passed",
            },
            "use_scope": list(use_scope),
        }
        verification_id = self._publish(
            artifact_kind="historical_evidence_verification",
            payload=verification_payload,
            trace_id=command.trace_id,
        )
        claim_payload: dict[str, object] = {
            "claim_schema_version": "historical-availability-claim/v1",
            "schema_version": evidence["price_schema_version"],
            "listing_id": command.listing_id,
            "market": command.market,
            "source_id": command.source_id,
            "evidence_level": command.evidence_level,
            "evidence_status": "qualified",
            "evidence_object_id": command.evidence_object_id,
            "evidence_checksum": hashlib.sha256(evidence_bytes).hexdigest(),
            "evidence_version": evidence["evidence_version"],
            "evidence_revision": evidence["revision"],
            "observation_kind": evidence["observation_kind"],
            "observation_reference": evidence["observation_reference"],
            "evidence_observed_at": evidence["observed_at"],
            "observed_start": coverage["start"],
            "observed_end": coverage["end"],
            "source_policy_id": command.source_policy_id,
            "public_terms_url": command.public_terms_url,
            "valid_from": validity["valid_from"],
            "valid_until": validity["valid_until"],
            "qualified_at": self._observed_at.isoformat(),
            "status": "qualified",
            "exact_sessions_verified": True,
            "integrity_verified": True,
            "company_actions_verified": True,
            "listing_lifecycle_verified": True,
            "qualification_artifact_id": verification_id,
            "supersedes_claim_id": command.prior_claim_id,
        }
        claim_id = self._publish(
            artifact_kind="historical_availability_claim",
            payload=claim_payload,
            trace_id=command.trace_id,
        )
        artifact_ids = {"claim": claim_id, "verification": verification_id}
        if self._can_build_reconstruction(evidence, listing):
            artifact_ids.update(
                self._build_reconstruction(
                    command=command,
                    evidence=evidence,
                    listing=listing,
                    claim_id=claim_id,
                )
            )
        if prior_claim is not None:
            impact_id = self._publish(
                artifact_kind="historical_claim_impact",
                payload={
                    "impact_schema_version": "historical-claim-impact/v1",
                    "event": "superseded",
                    "prior_claim_id": command.prior_claim_id,
                    "replacement_claim_id": claim_id,
                    "listing_id": command.listing_id,
                    "source_id": command.source_id,
                    "recorded_at": self._observed_at.isoformat(),
                    "affected_artifact_ids": self._affected_artifact_ids(
                        cast(str, command.prior_claim_id)
                    ),
                },
                trace_id=command.trace_id,
            )
            artifact_ids["impact"] = impact_id
        return HistoricalEvidenceOutcome(
            status="qualified",
            reason_code="historical_evidence_qualified",
            claim_id=claim_id,
            use_scope=use_scope,
            artifact_ids=artifact_ids,
        )

    def _record_claim_status(self, command: HistoricalEvidenceCommand) -> HistoricalEvidenceOutcome:
        if command.prior_claim_id is None:
            raise ValueError("prior_claim_required")
        prior_claim = self._state_store.get_verified_governance_artifact(
            artifact_id=command.prior_claim_id,
            artifact_kind="historical_availability_claim",
        )
        if (
            prior_claim.get("listing_id") != command.listing_id
            or prior_claim.get("source_id") != command.source_id
        ):
            raise ValueError("prior_claim_scope_mismatch")
        event = "revoked" if command.action == "revoke" else "expired"
        impact_id = self._publish(
            artifact_kind="historical_claim_impact",
            payload={
                "impact_schema_version": "historical-claim-impact/v1",
                "event": event,
                "prior_claim_id": command.prior_claim_id,
                "replacement_claim_id": None,
                "listing_id": command.listing_id,
                "source_id": command.source_id,
                "recorded_at": self._observed_at.isoformat(),
                "affected_artifact_ids": self._affected_artifact_ids(command.prior_claim_id),
            },
            trace_id=command.trace_id,
        )
        return HistoricalEvidenceOutcome(
            status=cast(Literal["revoked", "expired"], event),
            reason_code=f"historical_evidence_{event}",
            claim_id=command.prior_claim_id,
            artifact_ids={"claim": command.prior_claim_id, "impact": impact_id},
        )

    def _affected_artifact_ids(self, claim_id: str) -> list[str]:
        for report in self._state_store.list_historical_qualification_reports():
            if report.get("historical_availability_claim_id") != claim_id:
                continue
            dataset_ids = report.get("dataset_version_ids")
            affected = [str(item) for item in dataset_ids] if isinstance(dataset_ids, list) else []
            for field_name in (
                "qualification_report_id",
                "adjustment_version_id",
                "mature_labels_id",
                "feature_snapshot_id",
                "fold_manifest_id",
            ):
                artifact_id = report.get(field_name)
                if isinstance(artifact_id, str):
                    affected.append(artifact_id)
            return sorted(set(affected))
        return []

    def _quarantine(
        self,
        command: HistoricalEvidenceCommand,
        *,
        reason_code: str,
    ) -> HistoricalEvidenceOutcome:
        report_id = self._publish(
            artifact_kind="historical_qualification_report",
            payload={
                "qualification_report_schema_version": "historical-qualification-report/v1",
                "listing_id": command.listing_id,
                "market": command.market,
                "source_id": command.source_id,
                "status": "quarantined",
                "reason_code": reason_code,
                "historical_availability_claim_id": None,
                "evidence_level": command.evidence_level,
                "source_policy_id": command.source_policy_id,
                "display_mode": "historical_reconstruction",
                "production_prediction": False,
                "exclusion_reasons": [reason_code],
                "created_at": self._observed_at.isoformat(),
            },
            trace_id=command.trace_id,
        )
        return HistoricalEvidenceOutcome(
            status="quarantined",
            reason_code=reason_code,
            claim_id=None,
            artifact_ids={"qualification_report": report_id},
        )

    @staticmethod
    def _can_build_reconstruction(evidence: dict[str, object], listing: dict[str, object]) -> bool:
        return listing.get("company_actions_status") == "complete" and all(
            isinstance(evidence.get(field_name), str)
            for field_name in (
                "calendar_version",
                "adjustment_rule_version",
                "label_rule_version",
                "code_provenance",
            )
        )

    def _build_reconstruction(
        self,
        *,
        command: HistoricalEvidenceCommand,
        evidence: dict[str, object],
        listing: dict[str, object],
        claim_id: str,
    ) -> dict[str, str]:
        common_lineage: dict[str, object] = {
            "listing_id": command.listing_id,
            "security_id": listing["security_id"],
            "market": command.market,
            "source_id": command.source_id,
            "historical_availability_claim_id": claim_id,
            "evidence_object_id": command.evidence_object_id,
            "calendar_version": evidence["calendar_version"],
            "label_rule_version": evidence["label_rule_version"],
            "source_policy_id": command.source_policy_id,
            "code_provenance": evidence["code_provenance"],
            "created_at": self._observed_at.isoformat(),
        }
        dataset_payload = {
            **common_lineage,
            "dataset_schema_version": "historical-reconstruction-dataset/v1",
            "evidence_version": evidence["evidence_version"],
            "evidence_revision": evidence["revision"],
            "sessions": listing["sessions"],
            "symbols": listing["symbols"],
            "lifecycle": listing["lifecycle"],
            "unadjusted_prices": listing["unadjusted_prices"],
            "company_actions": listing["company_actions"],
        }
        dataset_id = self._publish(
            artifact_kind="historical_reconstruction_dataset",
            payload=dataset_payload,
            trace_id=command.trace_id,
        )
        adjustment_payload = {
            **common_lineage,
            "adjustment_schema_version": "historical-adjustment-version/v1",
            "dataset_version_id": dataset_id,
            "adjustment_rule_version": evidence["adjustment_rule_version"],
            "adjusted_prices": self._adjusted_prices(listing),
            "company_action_ids": [
                action.get("source_action_id")
                for action in cast(list[dict[str, object]], listing["company_actions"])
            ],
        }
        adjustment_version_id = self._publish(
            artifact_kind="historical_adjustment_version",
            payload=adjustment_payload,
            trace_id=command.trace_id,
        )
        labels_payload = self._mature_labels(
            command=command,
            evidence=evidence,
            listing=listing,
            claim_id=claim_id,
            dataset_id=dataset_id,
            adjustment_version_id=adjustment_version_id,
            adjusted_prices=cast(list[dict[str, str]], adjustment_payload["adjusted_prices"]),
        )
        labels_id = self._publish(
            artifact_kind="historical_mature_labels",
            payload=labels_payload,
            trace_id=command.trace_id,
        )
        labels = cast(list[dict[str, object]], labels_payload["labels"])
        exclusions = [
            f"{label['status']}:horizon_{label['horizon_sessions']}"
            for label in labels
            if label["status"] != "mature"
        ]
        derived_lineage = {
            **common_lineage,
            "dataset_version_ids": [dataset_id],
            "adjustment_version_id": adjustment_version_id,
            "mature_labels_id": labels_id,
        }
        feature_snapshot_id = self._publish(
            artifact_kind="historical_feature_snapshot",
            payload={
                **derived_lineage,
                "feature_snapshot_schema_version": "historical-feature-snapshot/v1",
                "information_cutoff": cast(list[str], listing["sessions"])[
                    min(20, len(cast(list[object], listing["sessions"])) - 1)
                ],
                "execution_purpose": "historical_reconstruction",
            },
            trace_id=command.trace_id,
        )
        fold_manifest_id = self._publish(
            artifact_kind="historical_fold_manifest",
            payload={
                **derived_lineage,
                "fold_manifest_schema_version": "historical-fold-manifest/v1",
                "feature_snapshot_id": feature_snapshot_id,
                "execution_purpose": "historical_reconstruction",
            },
            trace_id=command.trace_id,
        )
        report_id = self._publish(
            artifact_kind="historical_qualification_report",
            payload={
                **derived_lineage,
                "qualification_report_schema_version": "historical-qualification-report/v1",
                "status": "qualified",
                "display_mode": "historical_reconstruction",
                "production_prediction": False,
                "evidence_level": command.evidence_level,
                "evidence_revision": evidence["revision"],
                "exact_session_count": len(cast(list[object], listing["sessions"])),
                "unadjusted_prices_verified": True,
                "company_actions_verified": True,
                "listing_lifecycle_verified": True,
                "exact_endpoints_verified": not exclusions,
                "exclusion_reasons": exclusions,
                "feature_snapshot_id": feature_snapshot_id,
                "fold_manifest_id": fold_manifest_id,
            },
            trace_id=command.trace_id,
        )
        return {
            "qualification_report": report_id,
            "dataset": dataset_id,
            "adjustment_version": adjustment_version_id,
            "mature_labels": labels_id,
            "feature_snapshot": feature_snapshot_id,
            "fold_manifest": fold_manifest_id,
        }

    def _mature_labels(
        self,
        *,
        command: HistoricalEvidenceCommand,
        evidence: dict[str, object],
        listing: dict[str, object],
        claim_id: str,
        dataset_id: str,
        adjustment_version_id: str,
        adjusted_prices: list[dict[str, str]],
    ) -> dict[str, object]:
        sessions = cast(list[str], listing["sessions"])
        prices = {row["session_date"]: Decimal(row["adjusted_close"]) for row in adjusted_prices}
        anchor_index = min(20, len(sessions) - 1)
        anchor_session = sessions[anchor_index]
        if anchor_index < 20 or any(
            session not in prices for session in sessions[: anchor_index + 1]
        ):
            return {
                "mature_labels_schema_version": "historical-mature-labels/v1",
                "listing_id": command.listing_id,
                "anchor_session_id": anchor_session,
                "labels": [
                    {
                        "horizon_sessions": horizon,
                        "target_session_id": None,
                        "status": "invalid_history",
                        "reason_code": "insufficient_20_session_history",
                        "future_return": None,
                        "sigma20": None,
                        "threshold": None,
                        "label": None,
                    }
                    for horizon in (1, 5, 20)
                ],
                "label_rule_version": evidence["label_rule_version"],
                "realized_calendar_version": evidence["calendar_version"],
                "adjustment_version_id": adjustment_version_id,
                "dataset_version_id": dataset_id,
                "historical_availability_claim_id": claim_id,
                "source_policy_id": command.source_policy_id,
                "code_provenance": evidence["code_provenance"],
                "created_at": self._observed_at.isoformat(),
            }
        returns = [
            (prices[sessions[index]] / prices[sessions[index - 1]]).ln()
            for index in range(1, anchor_index + 1)
        ]
        mean_return = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = sum(
            ((realized_return - mean_return) ** 2 for realized_return in returns),
            Decimal(0),
        ) / Decimal(len(returns) - 1)
        sigma20 = variance.sqrt()
        market_floor = Decimal("0.006") if command.market == "XTAI" else Decimal("0.0025")
        labels: list[dict[str, object]] = []
        for horizon in (1, 5, 20):
            threshold = max(
                market_floor,
                Decimal("0.35") * sigma20 * Decimal(horizon).sqrt(),
            )
            if anchor_index + horizon >= len(sessions):
                labels.append(
                    {
                        "horizon_sessions": horizon,
                        "target_session_id": None,
                        "status": "invalid_endpoint",
                        "reason_code": "exact_target_session_missing",
                        "future_return": None,
                        "sigma20": _decimal_text(sigma20),
                        "threshold": _decimal_text(threshold),
                        "label": None,
                    }
                )
                continue
            target_session = sessions[anchor_index + horizon]
            if target_session not in prices:
                labels.append(
                    {
                        "horizon_sessions": horizon,
                        "target_session_id": target_session,
                        "status": "invalid_endpoint",
                        "reason_code": "exact_target_price_missing",
                        "future_return": None,
                        "sigma20": _decimal_text(sigma20),
                        "threshold": _decimal_text(threshold),
                        "label": None,
                    }
                )
                continue
            future_return = prices[target_session] / prices[anchor_session] - Decimal(1)
            label = (
                "up"
                if future_return > threshold
                else "down"
                if future_return < -threshold
                else "flat"
            )
            labels.append(
                {
                    "horizon_sessions": horizon,
                    "target_session_id": target_session,
                    "status": "mature",
                    "reason_code": None,
                    "future_return": _decimal_text(future_return),
                    "sigma20": _decimal_text(sigma20),
                    "threshold": _decimal_text(threshold),
                    "label": label,
                }
            )
        return {
            "mature_labels_schema_version": "historical-mature-labels/v1",
            "listing_id": command.listing_id,
            "anchor_session_id": anchor_session,
            "labels": labels,
            "label_rule_version": evidence["label_rule_version"],
            "realized_calendar_version": evidence["calendar_version"],
            "adjustment_version_id": adjustment_version_id,
            "dataset_version_id": dataset_id,
            "historical_availability_claim_id": claim_id,
            "source_policy_id": command.source_policy_id,
            "code_provenance": evidence["code_provenance"],
            "created_at": self._observed_at.isoformat(),
        }

    @staticmethod
    def _adjusted_prices(listing: dict[str, object]) -> list[dict[str, str]]:
        actions = cast(list[dict[str, object]], listing["company_actions"])
        adjusted_rows: list[dict[str, str]] = []
        for price in cast(list[dict[str, object]], listing["unadjusted_prices"]):
            session_date = str(price["session_date"])
            adjusted_close = Decimal(str(price["close"]))
            for action in actions:
                if session_date >= str(action["effective_date"]):
                    continue
                value = Decimal(str(action["value"]))
                if action["kind"] == "cash_dividend":
                    adjusted_close -= value
                elif action["kind"] == "split":
                    adjusted_close /= value
            adjusted_rows.append(
                {
                    "session_date": session_date,
                    "adjusted_close": str(adjusted_close),
                }
            )
        return adjusted_rows

    def _publish(
        self,
        *,
        artifact_kind: str,
        payload: dict[str, object],
        trace_id: str,
    ) -> str:
        return self._state_store.publish_historical_evidence_artifact(
            artifact_kind=artifact_kind,
            payload=payload,
            trace_id=trace_id,
        )


class QualifiedHistoricalAvailabilityClaimVerifier:
    def __init__(self, state_store: StateStore, *, evaluated_at: datetime) -> None:
        self._state_store = state_store
        self._evaluated_at = evaluated_at

    def is_usable(
        self,
        *,
        claim_id: str,
        claim: HistoricalAvailabilityClaim,
    ) -> bool:
        if claim.evidence_status != "qualified" or claim.qualification_artifact_id is None:
            return False
        try:
            stored_payload = self._state_store.get_verified_governance_artifact(
                artifact_id=claim_id,
                artifact_kind="historical_availability_claim",
            )
            if HistoricalAvailabilityClaim.from_payload(stored_payload) != claim:
                return False
            verification = self._state_store.get_verified_governance_artifact(
                artifact_id=claim.qualification_artifact_id,
                artifact_kind="historical_evidence_verification",
            )
            valid_from = datetime.fromisoformat(str(verification["valid_from"]))
            valid_until = datetime.fromisoformat(str(verification["valid_until"]))
        except (KeyError, ValueError):
            return False
        checks = verification.get("checks")
        use_scope = verification.get("use_scope")
        if (
            not isinstance(checks, dict)
            or set(checks.values()) != {"passed"}
            or not isinstance(use_scope, list)
            or "historical_reconstruction" not in use_scope
            or verification.get("source_id") != claim.source_id
            or verification.get("evidence_level") != claim.evidence_level
            or not (valid_from <= self._evaluated_at < valid_until)
        ):
            return False
        impacts = self._state_store.list_historical_claim_impacts(claim_id=claim_id)
        return not any(
            impact.get("event") in {"superseded", "revoked", "expired"} for impact in impacts
        )


def historical_qualification_projections(
    state_store: StateStore,
    *,
    evaluated_at: datetime,
    listing_id: str | None = None,
) -> list[dict[str, object]]:
    reports = state_store.list_historical_qualification_reports(listing_id=listing_id)
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for report in reports:
        key = (str(report["listing_id"]), str(report["source_id"]))
        if key in latest:
            continue
        if report.get("status") == "quarantined":
            latest[key] = {
                "listing_id": report["listing_id"],
                "market": report["market"],
                "source_id": report["source_id"],
                "source_mode": "historical_reconstruction",
                "status": "quarantined",
                "reason_code": report["reason_code"],
                "historical_availability_claim_id": None,
                "claim_id": None,
                "evidence_level": report["evidence_level"],
                "source_policy_id": report["source_policy_id"],
                "qualification_report_id": report["qualification_report_id"],
                "exclusion_reasons": report["exclusion_reasons"],
                "display_mode": "historical_reconstruction",
                "production_prediction": False,
            }
            continue
        claim_id = str(report["historical_availability_claim_id"])
        claim = state_store.get_verified_governance_artifact(
            artifact_id=claim_id,
            artifact_kind="historical_availability_claim",
        )
        status = "qualified"
        reason_code = "historical_reconstruction_qualified"
        exclusions = report.get("exclusion_reasons")
        if isinstance(exclusions, list) and exclusions:
            status = (
                "invalid_endpoint"
                if any(str(reason).startswith("invalid_endpoint:") for reason in exclusions)
                else "quarantined"
            )
            reason_code = str(exclusions[0])
        impacts = state_store.list_historical_claim_impacts(claim_id=claim_id)
        if impacts:
            event = str(impacts[-1]["event"])
            status = "expired" if event == "superseded" else event
            reason_code = f"historical_claim_{event}"
        elif evaluated_at >= datetime.fromisoformat(str(claim["valid_until"])):
            status = "expired"
            reason_code = "historical_claim_validity_expired"
        latest[key] = {
            "listing_id": report["listing_id"],
            "market": report["market"],
            "source_id": report["source_id"],
            "source_mode": "historical_reconstruction",
            "status": status,
            "reason_code": reason_code,
            "historical_availability_claim_id": claim_id,
            "claim_id": claim_id,
            "evidence_level": report["evidence_level"],
            "evidence_revision": report["evidence_revision"],
            "source_policy_id": report["source_policy_id"],
            "qualification_report_id": report["qualification_report_id"],
            "dataset_version_ids": report["dataset_version_ids"],
            "adjustment_version_id": report["adjustment_version_id"],
            "mature_labels_id": report["mature_labels_id"],
            "feature_snapshot_id": report["feature_snapshot_id"],
            "fold_manifest_id": report["fold_manifest_id"],
            "exclusion_reasons": report["exclusion_reasons"],
            "display_mode": "historical_reconstruction",
            "production_prediction": False,
        }
    return list(latest.values())


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0.0"
    return format(value.normalize(), "f")
