from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.telemetry.clients import PrometheusClient
from backend.app.telemetry.health import with_bounded_retry
from sandbox.scenarios import LiveScenarioMapping
from sandbox.traffic.workload import WorkloadDriver, WorkloadSample

PROMETHEUS_SCRAPE_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_WAIT_SECONDS = 60.0
REQUIRED_CONSECUTIVE_HEALTHY_OBSERVATIONS = 2


@dataclass(frozen=True)
class FreshObservation:
    observed_at: datetime
    p95_latency_ms: int
    error_rate_percent: float
    source: str


def filter_samples_after(
    samples: list[WorkloadSample],
    after: datetime,
) -> list[WorkloadSample]:
    return [sample for sample in samples if sample.timestamp > after]


def summarize_samples(samples: list[WorkloadSample]) -> dict[str, Any]:
    if not samples:
        return {
            "request_count": 0,
            "p95_latency_ms": 0,
            "error_rate_percent": 0.0,
            "newest_sample_at": None,
        }
    latencies = sorted(sample.latency_ms for sample in samples)
    p95_index = max(0, int(len(latencies) * 0.95) - 1)
    failures = sum(1 for sample in samples if not sample.success)
    newest = max(sample.timestamp for sample in samples)
    return {
        "request_count": len(samples),
        "p95_latency_ms": int(latencies[p95_index]),
        "error_rate_percent": round((failures / len(samples)) * 100, 2),
        "newest_sample_at": newest.isoformat(),
    }


def meets_baseline_recovery(
    observed: dict[str, Any],
    baseline: dict[str, Any],
) -> bool:
    baseline_p95 = baseline.get("p95_latency_ms", 0)
    baseline_error = baseline.get("error_rate_percent", 100.0)
    return (
        observed.get("request_count", 0) > 0
        and observed["error_rate_percent"] <= baseline_error + 1.0
        and observed["p95_latency_ms"] <= max(baseline_p95 * 2, baseline_p95 + 50)
    )


class RecoveryVerifier:
    """Wait for telemetry observations that are provably newer than remediation."""

    def __init__(
        self,
        *,
        scrape_interval_seconds: float = PROMETHEUS_SCRAPE_INTERVAL_SECONDS,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        required_consecutive: int = REQUIRED_CONSECUTIVE_HEALTHY_OBSERVATIONS,
    ) -> None:
        self._scrape_interval = scrape_interval_seconds
        self._max_wait_seconds = max_wait_seconds
        self._required_consecutive = required_consecutive

    def verify(
        self,
        *,
        prometheus: PrometheusClient,
        workload: WorkloadDriver,
        mapping: LiveScenarioMapping,
        incident_id: str,
        baseline_summary: dict[str, Any],
        remediation_at: datetime,
        sample_duration_seconds: float = 3.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._max_wait_seconds
        consecutive_healthy = 0
        fresh_observations: list[dict[str, Any]] = []
        last_prometheus: FreshObservation | None = None

        while time.monotonic() < deadline:
            prometheus_obs = self._wait_for_fresh_prometheus(
                prometheus,
                mapping.affected_service,
                remediation_at,
            )
            if prometheus_obs is None:
                time.sleep(self._scrape_interval)
                continue
            last_prometheus = prometheus_obs

            batch = workload.collect_baseline(
                mapping,
                incident_id,
                duration_seconds=sample_duration_seconds,
            )
            fresh_batch = filter_samples_after(batch, remediation_at)
            workload_summary = summarize_samples(fresh_batch)
            if workload_summary["request_count"] == 0:
                time.sleep(self._scrape_interval)
                continue

            observation = {
                "workload": workload_summary,
                "prometheus": {
                    "p95_latency_ms": prometheus_obs.p95_latency_ms,
                    "error_rate_percent": prometheus_obs.error_rate_percent,
                    "observed_at": prometheus_obs.observed_at.isoformat(),
                },
            }
            fresh_observations.append(observation)

            if meets_baseline_recovery(workload_summary, baseline_summary):
                consecutive_healthy += 1
            else:
                consecutive_healthy = 0

            if consecutive_healthy >= self._required_consecutive:
                return {
                    "status": "resolved",
                    "recovered": True,
                    "summary": workload_summary,
                    "prometheus": observation["prometheus"],
                    "observations": fresh_observations,
                    "recovered_p95_latency_ms": workload_summary["p95_latency_ms"],
                    "recovered_error_rate_percent": workload_summary["error_rate_percent"],
                }

            time.sleep(self._scrape_interval)

        if last_prometheus is None:
            return {
                "status": "verification_pending",
                "recovered": False,
                "reason": "fresh_prometheus_metrics_unavailable",
                "observations": fresh_observations,
            }

        latest = fresh_observations[-1] if fresh_observations else None
        return {
            "status": "remediation_failed",
            "recovered": False,
            "summary": latest["workload"] if latest else {},
            "prometheus": latest["prometheus"] if latest else None,
            "observations": fresh_observations,
        }

    def _wait_for_fresh_prometheus(
        self,
        prometheus: PrometheusClient,
        service: str,
        remediation_at: datetime,
    ) -> FreshObservation | None:
        minimum_observation_time = remediation_at + timedelta(
            seconds=self._scrape_interval
        )

        def _fetch() -> FreshObservation:
            p95_obs = prometheus.query_p95_latency_ms_with_timestamp(service)
            error_obs = prometheus.query_error_rate_percent_with_timestamp(service)
            if p95_obs is None or error_obs is None:
                raise RuntimeError("Prometheus returned no fresh samples")
            p95_value, p95_at = p95_obs
            error_value, error_at = error_obs
            observed_at = max(p95_at, error_at)
            if observed_at <= minimum_observation_time:
                raise RuntimeError("Prometheus samples are older than remediation")
            return FreshObservation(
                observed_at=observed_at,
                p95_latency_ms=p95_value,
                error_rate_percent=error_value,
                source="prometheus",
            )

        try:
            return with_bounded_retry(_fetch, max_attempts=2)
        except Exception:
            return None
