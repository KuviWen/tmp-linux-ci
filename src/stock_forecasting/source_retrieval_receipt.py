from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

SourceRetrievalMode = Literal["current", "historical"]

_REQUIRED_FIELDS = {
    "object_id",
    "request_id",
    "source_id",
    "source_mode",
    "source_revision",
    "distribution_id",
    "distribution_url",
    "sanitized_source_uri",
    "acquired_at",
    "checkpoint_before",
    "checkpoint_after",
}
_OPTIONAL_FIELDS = {
    "reference_graph",
    "market_calendar_evidence_version_id",
}


@dataclass(frozen=True)
class SourceRetrievalReceipt:
    """Canonical receipt for one immutable source-object retrieval."""

    object_id: str
    request_id: str
    source_id: str
    source_mode: SourceRetrievalMode
    source_revision: str
    distribution_id: str | None
    distribution_url: str | None
    sanitized_source_uri: str
    acquired_at: datetime
    checkpoint_before: str | None
    checkpoint_after: str | None
    reference_graph: Mapping[str, object] | None = None
    market_calendar_evidence_version_id: str | None = None

    def __post_init__(self) -> None:
        required_strings = (
            self.object_id,
            self.request_id,
            self.source_id,
            self.source_revision,
            self.sanitized_source_uri,
        )
        if (
            any(not isinstance(value, str) or not value for value in required_strings)
            or self.source_mode not in {"current", "historical"}
            or not isinstance(self.acquired_at, datetime)
            or self.acquired_at.tzinfo is None
            or not _optional_string(self.distribution_id)
            or not _optional_string(self.distribution_url)
            or not _optional_string(self.checkpoint_before)
            or not _optional_string(self.checkpoint_after)
            or not _optional_string(self.market_calendar_evidence_version_id)
            or not _valid_reference_graph(self.reference_graph)
        ):
            raise ValueError("source_retrieval_receipt_invalid")

    @property
    def acquired_at_text(self) -> str:
        return self.acquired_at.isoformat().replace("+00:00", "Z")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "object_id": self.object_id,
            "request_id": self.request_id,
            "source_id": self.source_id,
            "source_mode": self.source_mode,
            "source_revision": self.source_revision,
            "distribution_id": self.distribution_id,
            "distribution_url": self.distribution_url,
            "sanitized_source_uri": self.sanitized_source_uri,
            "acquired_at": self.acquired_at_text,
            "checkpoint_before": self.checkpoint_before,
            "checkpoint_after": self.checkpoint_after,
        }
        if self.reference_graph is not None:
            payload["reference_graph"] = dict(self.reference_graph)
        if self.market_calendar_evidence_version_id is not None:
            payload["market_calendar_evidence_version_id"] = (
                self.market_calendar_evidence_version_id
            )
        return payload

    def to_artifact(self) -> dict[str, Any]:
        payload = self.to_payload()
        return {
            "artifact_id": _artifact_id("source_retrieval_receipt", payload),
            "artifact_kind": "source_retrieval_receipt",
            "payload": payload,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> SourceRetrievalReceipt:
        fields = set(payload)
        if not _REQUIRED_FIELDS.issubset(fields) or not fields <= (
            _REQUIRED_FIELDS | _OPTIONAL_FIELDS
        ):
            raise ValueError("source_retrieval_receipt_invalid")
        required_string_fields = (
            "object_id",
            "request_id",
            "source_id",
            "source_revision",
            "sanitized_source_uri",
            "acquired_at",
        )
        if any(not isinstance(payload.get(field), str) for field in required_string_fields):
            raise ValueError("source_retrieval_receipt_invalid")
        source_mode = payload.get("source_mode")
        if source_mode not in {"current", "historical"}:
            raise ValueError("source_retrieval_receipt_invalid")
        try:
            acquired_at = datetime.fromisoformat(str(payload["acquired_at"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("source_retrieval_receipt_invalid") from error
        reference_graph = payload.get("reference_graph")
        if reference_graph is not None and not isinstance(reference_graph, dict):
            raise ValueError("source_retrieval_receipt_invalid")
        market_calendar_version = payload.get("market_calendar_evidence_version_id")
        if market_calendar_version is not None and not isinstance(market_calendar_version, str):
            raise ValueError("source_retrieval_receipt_invalid")
        checkpoint_before = payload.get("checkpoint_before")
        checkpoint_after = payload.get("checkpoint_after")
        distribution_id = payload.get("distribution_id")
        distribution_url = payload.get("distribution_url")
        if any(
            not _optional_string(value)
            for value in (
                distribution_id,
                distribution_url,
                checkpoint_before,
                checkpoint_after,
            )
        ):
            raise ValueError("source_retrieval_receipt_invalid")
        return cls(
            object_id=str(payload["object_id"]),
            request_id=str(payload["request_id"]),
            source_id=str(payload["source_id"]),
            source_mode=cast(SourceRetrievalMode, source_mode),
            source_revision=str(payload["source_revision"]),
            distribution_id=cast(str | None, distribution_id),
            distribution_url=cast(str | None, distribution_url),
            sanitized_source_uri=str(payload["sanitized_source_uri"]),
            acquired_at=acquired_at,
            checkpoint_before=cast(str | None, checkpoint_before),
            checkpoint_after=cast(str | None, checkpoint_after),
            reference_graph=reference_graph,
            market_calendar_evidence_version_id=market_calendar_version,
        )


def _optional_string(value: object) -> bool:
    return value is None or isinstance(value, str)


def _valid_reference_graph(value: Mapping[str, object] | None) -> bool:
    return value is None or (
        set(value) == {"version_id", "lifecycle_complete", "company_actions_complete"}
        and isinstance(value.get("version_id"), str)
        and isinstance(value.get("lifecycle_complete"), bool)
        and isinstance(value.get("company_actions_complete"), bool)
    )


def _artifact_id(kind: str, payload: object) -> str:
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(kind.encode() + canonical_payload).hexdigest()}"
