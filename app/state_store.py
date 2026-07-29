"""Expiring local state and privacy-safe operational metrics."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator


class SQLiteStateStore:
    """Small synchronous SQLite store for local-only recoverable state.

    Records are JSON supplied by the application. Callers deliberately exclude
    photographs, addresses, OAuth credentials, and confirmation tokens.
    """

    def __init__(
        self,
        path: Path,
        *,
        record_ttl_seconds: int,
        telemetry_retention_seconds: int,
    ):
        self.path = path
        self.record_ttl_seconds = record_ttl_seconds
        self.telemetry_retention_seconds = telemetry_retention_seconds
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back a short transaction, then always close it."""
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS state_records (
                    kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (kind, record_id)
                );
                CREATE INDEX IF NOT EXISTS state_records_expiry
                    ON state_records (expires_at);

                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT 'ok',
                    duration_ms REAL,
                    item_count INTEGER,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS telemetry_events_created
                    ON telemetry_events (created_at);
                """
            )

    def save(
        self,
        kind: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        now = time.time()
        expires_at = now + (ttl_seconds or self.record_ttl_seconds)
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO state_records
                    (kind, record_id, payload_json, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind, record_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at
                """,
                (kind, record_id, encoded, now, expires_at),
            )

    def load(self, kind: str, record_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json, expires_at
                FROM state_records
                WHERE kind = ? AND record_id = ?
                """,
                (kind, record_id),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now:
                connection.execute(
                    "DELETE FROM state_records WHERE kind = ? AND record_id = ?",
                    (kind, record_id),
                )
                return None
        try:
            decoded = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            self.delete(kind, record_id)
            return None
        return decoded if isinstance(decoded, dict) else None

    def delete(self, kind: str, record_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM state_records WHERE kind = ? AND record_id = ?",
                (kind, record_id),
            )

    def record_metric(
        self,
        event: str,
        *,
        stage: str = "",
        provider: str = "",
        outcome: str = "ok",
        duration_ms: float | None = None,
        item_count: int | None = None,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO telemetry_events
                    (event, stage, provider, outcome, duration_ms, item_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event,
                    stage,
                    provider,
                    outcome,
                    duration_ms,
                    item_count,
                    time.time(),
                ),
            )

    def metric_summary(self) -> dict[str, Any]:
        cutoff = time.time() - self.telemetry_retention_seconds
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT event, stage, provider, outcome, duration_ms, item_count
                FROM telemetry_events
                WHERE created_at >= ?
                ORDER BY id
                """,
                (cutoff,),
            ).fetchall()

        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                row["event"],
                row["stage"],
                row["provider"],
                row["outcome"],
            )
            bucket = grouped.setdefault(
                key,
                {"count": 0, "durations": [], "items": 0},
            )
            bucket["count"] += 1
            if row["duration_ms"] is not None:
                bucket["durations"].append(float(row["duration_ms"]))
            if row["item_count"] is not None:
                bucket["items"] += int(row["item_count"])

        results = []
        for (event, stage, provider, outcome), bucket in sorted(grouped.items()):
            durations = sorted(bucket.pop("durations"))
            average = sum(durations) / len(durations) if durations else None
            p95 = (
                durations[min(len(durations) - 1, int(len(durations) * 0.95))]
                if durations
                else None
            )
            results.append(
                {
                    "event": event,
                    "stage": stage,
                    "provider": provider,
                    "outcome": outcome,
                    "count": bucket["count"],
                    "item_count": bucket["items"],
                    "average_duration_ms": round(average, 1)
                    if average is not None
                    else None,
                    "p95_duration_ms": round(p95, 1) if p95 is not None else None,
                }
            )
        return {
            "retention_seconds": self.telemetry_retention_seconds,
            "groups": results,
        }

    def purge_expired(self) -> dict[str, int]:
        now = time.time()
        telemetry_cutoff = now - self.telemetry_retention_seconds
        with self._lock, self._connection() as connection:
            records = connection.execute(
                "DELETE FROM state_records WHERE expires_at <= ?", (now,)
            ).rowcount
            metrics = connection.execute(
                "DELETE FROM telemetry_events WHERE created_at < ?",
                (telemetry_cutoff,),
            ).rowcount
        return {"state_records": records, "telemetry_events": metrics}
