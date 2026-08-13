from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


class RelayFault(Protocol):
    def before_consumers(self, event_id: str) -> None: ...

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None: ...

    def before_ack(self, event_id: str) -> None: ...


class NoRelayFault:
    def before_consumers(self, event_id: str) -> None:
        pass

    def before_consumer_commit(self, consumer_name: str, event_id: str) -> None:
        pass

    def before_ack(self, event_id: str) -> None:
        pass


class OutOfOrderEvent(RuntimeError):
    """Raised when an aggregate event arrives before its predecessor."""


@dataclass(frozen=True)
class RelayOutcome:
    status: Literal["delivered", "already_delivered", "empty", "failed", "deferred"]
    event_id: str | None
    aggregate_version: int | None
