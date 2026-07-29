"""Privacy-filtered operational telemetry.

Only enumerated event metadata is stored. Free-form queries, product names,
cart contents, photos, addresses, credentials, and exception messages are not.
"""

from __future__ import annotations

from .state_store import SQLiteStateStore


class Telemetry:
    def __init__(self):
        self._store: SQLiteStateStore | None = None

    def configure(self, store: SQLiteStateStore) -> None:
        self._store = store

    def record(
        self,
        event: str,
        *,
        stage: str = "",
        provider: str = "",
        outcome: str = "ok",
        duration_ms: float | None = None,
        item_count: int | None = None,
    ) -> None:
        if self._store is None:
            return
        self._store.record_metric(
            event,
            stage=stage,
            provider=provider,
            outcome=outcome,
            duration_ms=duration_ms,
            item_count=item_count,
        )

    def snapshot(self) -> dict:
        if self._store is None:
            return {"retention_seconds": 0, "groups": []}
        return self._store.metric_summary()


TELEMETRY = Telemetry()
