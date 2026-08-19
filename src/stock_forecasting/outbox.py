from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class EventCompatibility:
    accepted_versions: Mapping[str, frozenset[str]]

    @classmethod
    def current(cls) -> EventCompatibility:
        return cls(
            accepted_versions={
                "forecast_publication.completed": frozenset({"1.0.0"}),
                "production_forecast_publication.completed": frozenset({"1.0.0"}),
            }
        )

    def accepts(self, *, event_type: str, schema_version: str) -> bool:
        return schema_version in self.accepted_versions.get(event_type, frozenset())


class RelayFault(Protocol):
    def before_consumers(self, event_id: str) -> None: ...

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None: ...

    def before_ack(self, event_id: str) -> None: ...


class RelayClock(Protocol):
    def now(self) -> datetime: ...


class SystemRelayClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class NoRelayFault:
    def before_consumers(self, event_id: str) -> None:
        pass

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None:
        pass

    def before_ack(self, event_id: str) -> None:
        pass


class OutOfOrderEvent(RuntimeError):
    """Raised when an aggregate event arrives before its predecessor."""


class RelayLeaseLost(RuntimeError):
    """Raised when a relay no longer owns the current fencing token."""


@dataclass(frozen=True)
class RelayOutcome:
    status: Literal[
        "delivered",
        "already_delivered",
        "empty",
        "failed",
        "deferred",
        "isolated",
        "busy",
    ]
    event_id: str | None
    aggregate_version: int | None
