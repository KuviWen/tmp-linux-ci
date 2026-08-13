from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from stock_forecasting.contracts import PublicationDisposition, UnavailableCode

FixtureScenario = Literal[
    "normal",
    "late",
    "duplicate",
    "correction",
    "missing",
    "withdrawal",
]
RawMutation = Literal["unchanged", "correct_anchor", "remove_anchor"]
VersionRelation = Literal["independent", "duplicate", "supersedes", "withdraws"]


@dataclass(frozen=True)
class FixtureScenarioPolicy:
    name: FixtureScenario
    raw_mutation: RawMutation
    version_relation: VersionRelation
    coverage_status: Literal["completed", "incomplete", "withdrawn"]
    missing_partitions: tuple[str, ...]
    intrinsic_unavailable_reason: UnavailableCode | None
    success_health_reason: str

    @property
    def related_base_required(self) -> bool:
        return self.version_relation in ("supersedes", "withdraws")

    def mutate_records(
        self,
        records: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        mutated = [dict(record) for record in records]
        if self.raw_mutation == "correct_anchor":
            mutated[-1]["close"] = "607.50"
        elif self.raw_mutation == "remove_anchor":
            mutated[-1].pop("close")
        return mutated

    def source_record_payload(
        self,
        *,
        current_payload: dict[str, object],
        base_payload: dict[str, object],
        base_version_id: str,
    ) -> dict[str, object]:
        if self.version_relation == "duplicate":
            return base_payload
        payload = dict(current_payload)
        if self.version_relation == "supersedes":
            payload.update({"supersedes": base_version_id, "revision_number": 2})
        elif self.version_relation == "withdraws":
            payload.update({"withdraws": base_version_id, "availability": "withdrawn"})
        return payload

    def source_evidence(self, base_version_id: str) -> dict[str, object]:
        if self.version_relation == "duplicate":
            return {"duplicate_of": base_version_id, "deduplicated": True}
        if self.version_relation == "supersedes":
            return {"supersedes": base_version_id, "revision_number": 2}
        if self.version_relation == "withdraws":
            return {"withdraws": base_version_id}
        return {}

    def unavailable_reason(
        self,
        *,
        observed_at: datetime,
        information_cutoff: datetime,
    ) -> UnavailableCode | None:
        if observed_at > information_cutoff:
            return "post_cutoff_evidence"
        return self.intrinsic_unavailable_reason

    def publication_disposition(
        self,
        unavailable_reason: UnavailableCode | None,
    ) -> PublicationDisposition:
        scope = (
            "xtai_fixture_source" if self.name == "normal" else f"xtai_fixture_source/{self.name}"
        )
        if unavailable_reason is None:
            return PublicationDisposition(
                work_status="succeeded",
                health_scope=scope,
                health_status="ready",
                health_reason_code=self.success_health_reason,
            )
        reason_code = (
            "coverage_incomplete"
            if unavailable_reason == "missing_anchor_price"
            else unavailable_reason
        )
        return PublicationDisposition(
            work_status="blocked",
            health_scope=scope,
            health_status="blocked" if unavailable_reason == "source_withdrawn" else "degraded",
            health_reason_code=reason_code,
        )


POLICIES: dict[FixtureScenario, FixtureScenarioPolicy] = {
    "normal": FixtureScenarioPolicy(
        "normal", "unchanged", "independent", "completed", (), None, "coverage_complete"
    ),
    "late": FixtureScenarioPolicy(
        "late", "unchanged", "independent", "completed", (), None, "coverage_complete"
    ),
    "duplicate": FixtureScenarioPolicy(
        "duplicate",
        "unchanged",
        "duplicate",
        "completed",
        (),
        None,
        "duplicate_deduplicated",
    ),
    "correction": FixtureScenarioPolicy(
        "correction",
        "correct_anchor",
        "supersedes",
        "completed",
        (),
        None,
        "correction_applied",
    ),
    "missing": FixtureScenarioPolicy(
        "missing",
        "remove_anchor",
        "independent",
        "incomplete",
        ("anchor_close",),
        "missing_anchor_price",
        "coverage_incomplete",
    ),
    "withdrawal": FixtureScenarioPolicy(
        "withdrawal",
        "unchanged",
        "withdraws",
        "withdrawn",
        (),
        "source_withdrawn",
        "source_withdrawn",
    ),
}


def scenario_policy(name: FixtureScenario) -> FixtureScenarioPolicy:
    return POLICIES[name]
