from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from backend.app.telemetry.clients import LokiClient, PrometheusClient
from backend.app.telemetry.health import with_bounded_retry

LOKI_READY_MAX_ATTEMPTS = 12
LOKI_READY_BASE_DELAY_SECONDS = 2.0
LOG_INGESTION_MAX_ATTEMPTS = 10
METRICS_PIPELINE_MAX_ATTEMPTS = 6
PROMETHEUS_SCRAPE_INTERVAL_SECONDS = 5.0


def wait_for_loki_ready(
    loki: LokiClient,
    *,
    max_attempts: int = LOKI_READY_MAX_ATTEMPTS,
    base_delay_seconds: float = LOKI_READY_BASE_DELAY_SECONDS,
) -> bool:
    return loki.wait_until_ready(
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
    )


def check_metrics_pipeline(
    prometheus: PrometheusClient,
    service: str,
    *,
    max_age_seconds: float = PROMETHEUS_SCRAPE_INTERVAL_SECONDS * 3,
) -> bool:
    def _probe() -> bool:
        observation = prometheus.query_request_rate_with_timestamp(service)
        if observation is None:
            raise RuntimeError("No Prometheus samples for service")
        _, observed_at = observation
        age = (datetime.now(UTC) - observed_at).total_seconds()
        if age > max_age_seconds:
            raise RuntimeError("Prometheus samples are stale")
        return True

    try:
        return with_bounded_retry(_probe, max_attempts=METRICS_PIPELINE_MAX_ATTEMPTS)
    except Exception:
        return False


def verify_log_ingestion(
    loki: LokiClient,
    *,
    service: str,
    marker: str,
    since: datetime,
    max_attempts: int = LOG_INGESTION_MAX_ATTEMPTS,
) -> bool:
    def _probe() -> bool:
        if not loki.contains_log_since(service, since=since, search_text=marker):
            raise RuntimeError("Expected log marker not found in Loki")
        return True

    try:
        return with_bounded_retry(_probe, max_attempts=max_attempts)
    except Exception:
        return False


def new_log_probe_marker() -> str:
    return f"log-pipeline-probe-{uuid.uuid4().hex[:12]}"
