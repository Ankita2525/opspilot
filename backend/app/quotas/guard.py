from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol, runtime_checkable

import psycopg
from psycopg.rows import dict_row


class RateLimitExceeded(Exception):
    def __init__(self, *, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Rate limit exceeded")


class QuotaExceeded(Exception):
    def __init__(self, *, reason: str, retry_after_seconds: float | None = None) -> None:
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


@dataclass(frozen=True)
class RateLimitConfig:
    burst_per_ip: int = 10
    window_seconds: float = 60.0


@dataclass(frozen=True)
class QuotaConfig:
    max_live_incidents_per_session: int = 3
    max_model_calls_per_incident: int = 5
    max_model_calls_per_session_per_day: int = 20
    global_daily_model_call_cap: int = 500


class InMemoryRateLimiter:
    """Simple in-process sliding-window rate limiter for burst protection."""

    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        window_start = now - self._config.window_seconds
        with self._lock:
            hits = [t for t in self._hits[key] if t > window_start]
            if len(hits) >= self._config.burst_per_ip:
                oldest = hits[0]
                retry = self._config.window_seconds - (now - oldest)
                raise RateLimitExceeded(retry_after_seconds=max(retry, 1.0))
            hits.append(now)
            self._hits[key] = hits


@runtime_checkable
class QuotaCounterStore(Protocol):
    def get_counter(self, counter_key: str, counter_date: date) -> int: ...

    def increment_counter(self, counter_key: str, counter_date: date) -> int: ...


class PostgresQuotaCounterStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get_counter(self, counter_key: str, counter_date: date) -> int:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT counter_value FROM quota_counters
                WHERE counter_key = %s AND counter_date = %s
                """,
                (counter_key, counter_date),
            ).fetchone()
            if row is None:
                return 0
            return int(row["counter_value"])

    def increment_counter(self, counter_key: str, counter_date: date) -> int:
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                INSERT INTO quota_counters (counter_key, counter_date, counter_value)
                VALUES (%s, %s, 1)
                ON CONFLICT (counter_key, counter_date)
                DO UPDATE SET counter_value = quota_counters.counter_value + 1
                RETURNING counter_value
                """,
                (counter_key, counter_date),
            ).fetchone()
            if row is None:
                return 0
            return int(row["counter_value"])


class InMemoryQuotaCounterStore:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, date], int] = {}

    def get_counter(self, counter_key: str, counter_date: date) -> int:
        return self._counters.get((counter_key, counter_date), 0)

    def increment_counter(self, counter_key: str, counter_date: date) -> int:
        key = (counter_key, counter_date)
        value = self._counters.get(key, 0) + 1
        self._counters[key] = value
        return value


class QuotaGuard:
    def __init__(
        self,
        *,
        store: QuotaCounterStore,
        config: QuotaConfig,
        rate_limiter: InMemoryRateLimiter | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._rate_limiter = rate_limiter
        self._incident_model_calls: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def config(self) -> QuotaConfig:
        return self._config

    def check_ip_burst(self, client_ip: str) -> None:
        if self._rate_limiter is None:
            return
        self._rate_limiter.check(client_ip)

    def check_session_live_incident_limit(self, session_id: str, current_count: int) -> None:
        if current_count >= self._config.max_live_incidents_per_session:
            raise QuotaExceeded(
                reason="session_live_incident_limit",
                retry_after_seconds=3600.0,
            )

    def reserve_model_call(self, *, session_id: str, incident_id: str) -> None:
        today = datetime.now(UTC).date()
        global_key = "global_model_calls"
        session_key = f"session:{session_id}:model_calls"
        with self._lock:
            incident_calls = self._incident_model_calls.get(incident_id, 0)
            if incident_calls >= self._config.max_model_calls_per_incident:
                raise QuotaExceeded(reason="ai_capacity_exhausted")
            global_count = self._store.get_counter(global_key, today)
            if global_count >= self._config.global_daily_model_call_cap:
                raise QuotaExceeded(reason="ai_provider_capacity")
            session_count = self._store.get_counter(session_key, today)
            if session_count >= self._config.max_model_calls_per_session_per_day:
                raise QuotaExceeded(reason="ai_capacity_exhausted")
            self._incident_model_calls[incident_id] = incident_calls + 1
            self._store.increment_counter(global_key, today)
            self._store.increment_counter(session_key, today)

    def is_global_budget_exhausted(self) -> bool:
        today = datetime.now(UTC).date()
        return (
            self._store.get_counter("global_model_calls", today)
            >= self._config.global_daily_model_call_cap
        )

    def reset_incident_calls(self, incident_id: str) -> None:
        with self._lock:
            self._incident_model_calls.pop(incident_id, None)
