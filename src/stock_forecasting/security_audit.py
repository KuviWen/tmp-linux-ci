from __future__ import annotations

from stock_forecasting.platform.state_store import StateStore


class SecurityAudit:
    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store

    def list_events(self, *, trace_id: str) -> list[dict[str, str]]:
        return self._state_store.list_audit_events(trace_id=trace_id)
