from __future__ import annotations

from typing import Any

from stock_forecasting.platform.state_store import StateStore


class OperationsControl:
    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store

    def list_health(self, *, scope: str) -> list[dict[str, object]]:
        return self._state_store.list_health(scope=scope)

    def get_work(self, work_id: str) -> dict[str, Any] | None:
        return self._state_store.get_work(work_id)

    def get_trace_evidence(self, trace_id: str) -> dict[str, Any]:
        return self._state_store.get_trace_evidence(trace_id)

    def get_outbox_event(self, event_id: str) -> dict[str, Any]:
        return self._state_store.get_outbox_event(event_id)

    def list_prediction_records(self, *, trace_id: str) -> list[dict[str, Any]]:
        return self._state_store.list_prediction_records(trace_id=trace_id)

    def list_prediction_record_evidence(self, *, trace_id: str) -> list[dict[str, str]]:
        return self._state_store.list_prediction_record_evidence(trace_id=trace_id)

    def get_outbox_recovery(self, event_id: str) -> dict[str, Any]:
        return self._state_store.get_outbox_recovery(event_id)

    def list_outbox_incidents(self, *, aggregate_id: str) -> list[dict[str, Any]]:
        return self._state_store.list_outbox_incidents(aggregate_id=aggregate_id)
