from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from backend.app.telemetry.models import (
    TelemetrySourceHealth,
    TelemetrySourceKind,
    TelemetrySourceStatus,
)

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.05
DEFAULT_MAX_DELAY_SECONDS = 0.5


def utc_now() -> datetime:
    return datetime.now(UTC)


def healthy_source(
    source: TelemetrySourceKind,
    *,
    observed_at: datetime | None = None,
) -> TelemetrySourceHealth:
    now = observed_at or utc_now()
    return TelemetrySourceHealth(
        source=source,
        status=TelemetrySourceStatus.HEALTHY,
        observed_at=now,
        freshness_seconds=0.0,
    )


def unavailable_source(
    source: TelemetrySourceKind,
    *,
    error_category: str,
    observed_at: datetime | None = None,
) -> TelemetrySourceHealth:
    now = observed_at or utc_now()
    return TelemetrySourceHealth(
        source=source,
        status=TelemetrySourceStatus.UNAVAILABLE,
        observed_at=now,
        freshness_seconds=0.0,
        error_category=error_category,
    )


def stale_source(
    source: TelemetrySourceKind,
    *,
    freshness_seconds: float,
    observed_at: datetime,
) -> TelemetrySourceHealth:
    return TelemetrySourceHealth(
        source=source,
        status=TelemetrySourceStatus.STALE,
        observed_at=observed_at,
        freshness_seconds=freshness_seconds,
        error_category="stale_data",
    )


def with_bounded_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
) -> T:
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= max_attempts:
                break
            delay = min(
                max_delay_seconds,
                base_delay_seconds * (2**attempt),
            )
            jitter = random.uniform(0, delay * 0.25)
            time.sleep(delay + jitter)
    assert last_error is not None
    raise last_error
