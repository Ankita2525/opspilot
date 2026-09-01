from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.app.telemetry.clients import LokiClient, PrometheusClient
from backend.app.telemetry.evidence import is_stale
from backend.app.telemetry.health import (
    healthy_source,
    stale_source,
    unavailable_source,
    utc_now,
    with_bounded_retry,
)
from backend.app.telemetry.models import (
    TelemetrySourceKind,
    TelemetrySourceStatus,
)
from backend.app.telemetry.pipeline_health import (
    check_metrics_pipeline,
    wait_for_loki_ready,
)
from backend.app.telemetry.simulator import evaluate_health
from backend.app.tools.schemas import (
    DeploymentResponse,
    LogResponse,
    MetricResponse,
    ServiceHealthResponse,
)
from sandbox.control import SandboxControlClient


class LiveTelemetryBackend:
    """Live observability queries — never uses simulator fixtures."""

    def __init__(
        self,
        *,
        service: str,
        prometheus: PrometheusClient,
        loki: LokiClient,
        control: SandboxControlClient,
        metrics_unavailable: bool = False,
        logs_unavailable: bool = False,
    ) -> None:
        self._service = service
        self._prometheus = prometheus
        self._loki = loki
        self._control = control
        self._metrics_unavailable = metrics_unavailable
        self._logs_unavailable = logs_unavailable
        self._health_cache: list = []

    @property
    def prometheus_client(self) -> PrometheusClient:
        return self._prometheus

    @property
    def loki_client(self) -> LokiClient:
        return self._loki

    @property
    def mode(self) -> str:
        return "live"

    def source_health(self) -> list:
        return list(self._health_cache)

    def refresh_pipeline_health(self) -> dict[str, str]:
        states: dict[str, str] = {}
        if check_metrics_pipeline(self._prometheus, self._service):
            self._record_health(healthy_source(TelemetrySourceKind.METRICS))
            states["metrics"] = TelemetrySourceStatus.HEALTHY.value
        else:
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.METRICS,
                    error_category="metrics_pipeline_unavailable",
                )
            )
            states["metrics"] = TelemetrySourceStatus.UNAVAILABLE.value

        if wait_for_loki_ready(self._loki, max_attempts=3, base_delay_seconds=1.0):
            states["loki_api"] = TelemetrySourceStatus.HEALTHY.value
        else:
            states["loki_api"] = TelemetrySourceStatus.UNAVAILABLE.value

        try:
            logs = self.get_service_logs(self._service)
            if logs:
                states["logs"] = TelemetrySourceStatus.HEALTHY.value
            else:
                states["logs"] = TelemetrySourceStatus.UNAVAILABLE.value
        except Exception:
            states["logs"] = TelemetrySourceStatus.UNAVAILABLE.value

        states["deployments"] = TelemetrySourceStatus.HEALTHY.value
        return states

    def _record_health(self, health) -> None:
        existing = {item.source: item for item in self._health_cache}
        existing[health.source] = health
        self._health_cache = list(existing.values())

    def query_metrics(self, service: str) -> MetricResponse:
        if service != self._service:
            raise ValueError(f"Unknown service: {service}")
        if self._metrics_unavailable:
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.METRICS,
                    error_category="metrics_backend_unavailable",
                )
            )
            raise RuntimeError("Live metrics source is unavailable")
        if not check_metrics_pipeline(self._prometheus, service):
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.METRICS,
                    error_category="metrics_pipeline_unavailable",
                )
            )
            raise RuntimeError("Metrics pipeline is not producing fresh observations")

        def _fetch() -> MetricResponse:
            p95_obs = self._prometheus.query_p95_latency_ms_with_timestamp(service)
            error_obs = self._prometheus.query_error_rate_percent_with_timestamp(service)
            if p95_obs is None or error_obs is None:
                raise RuntimeError("Metrics query returned no data")
            p95, observed_at = p95_obs
            error_rate, _ = error_obs
            return MetricResponse(
                service=service,
                p95_latency_ms=p95,
                error_rate_percent=error_rate,
                timestamp=observed_at,
                telemetry_status=TelemetrySourceStatus.HEALTHY,
                observed_at=observed_at,
            )

        try:
            metrics = with_bounded_retry(_fetch)
            self._record_health(healthy_source(TelemetrySourceKind.METRICS))
            return metrics
        except Exception:
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.METRICS,
                    error_category="metrics_query_failed",
                )
            )
            raise

    def get_service_logs(self, service: str) -> list[LogResponse]:
        if service != self._service:
            raise ValueError(f"Unknown service: {service}")
        if self._logs_unavailable:
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.LOGS,
                    error_category="logs_backend_unavailable",
                )
            )
            raise RuntimeError("Live logs source is unavailable")
        if not wait_for_loki_ready(self._loki, max_attempts=6, base_delay_seconds=1.0):
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.LOGS,
                    error_category="loki_not_ready",
                )
            )
            raise RuntimeError("Loki is not ready for log queries")

        def _fetch() -> list[LogResponse]:
            entries = self._loki.query_logs(service)
            if not entries:
                raise RuntimeError("Logs query returned no data")
            logs: list[LogResponse] = []
            for entry in entries:
                observed_at = entry["timestamp"]
                status = (
                    TelemetrySourceStatus.STALE
                    if is_stale(observed_at)
                    else TelemetrySourceStatus.HEALTHY
                )
                logs.append(
                    LogResponse(
                        service=entry["service"],
                        timestamp=observed_at,
                        level=str(entry["level"]),
                        message=str(entry["message"]),
                        telemetry_status=status,
                        observed_at=observed_at,
                    )
                )
            return logs

        try:
            logs = with_bounded_retry(_fetch)
            newest = max(log.observed_at for log in logs)
            if is_stale(newest):
                self._record_health(
                    stale_source(
                        TelemetrySourceKind.LOGS,
                        freshness_seconds=(utc_now() - newest).total_seconds(),
                        observed_at=newest,
                    )
                )
            else:
                self._record_health(healthy_source(TelemetrySourceKind.LOGS))
            return logs
        except Exception:
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.LOGS,
                    error_category="logs_query_failed",
                )
            )
            raise

    def get_recent_deployments(self, service: str) -> list[DeploymentResponse]:
        if service != self._service:
            raise ValueError(f"Unknown service: {service}")

        def _fetch() -> list[DeploymentResponse]:
            events = []
            for version, timestamp in self._control.deployment_events():
                events.append(
                    DeploymentResponse(
                        service=service,
                        version=version,
                        timestamp=timestamp,
                        telemetry_status=TelemetrySourceStatus.HEALTHY,
                        observed_at=utc_now(),
                    )
                )
            if not events:
                raise RuntimeError("No deployment metadata available")
            return events

        try:
            deployments = with_bounded_retry(_fetch)
            self._record_health(healthy_source(TelemetrySourceKind.DEPLOYMENTS))
            return deployments
        except Exception:
            self._record_health(
                unavailable_source(
                    TelemetrySourceKind.DEPLOYMENTS,
                    error_category="deployments_unavailable",
                )
            )
            raise

    def get_service_health(self, service: str) -> ServiceHealthResponse:
        metrics = self.query_metrics(service)
        health = evaluate_health(
            service,
            metrics.p95_latency_ms,
            metrics.error_rate_percent,
        )
        self._record_health(healthy_source(TelemetrySourceKind.SERVICE_HEALTH))
        return ServiceHealthResponse(
            service=health.service,
            p95_latency_ms=health.p95_latency_ms,
            error_rate_percent=health.error_rate_percent,
            max_p95_latency_ms=health.max_p95_latency_ms,
            max_error_rate_percent=health.max_error_rate_percent,
            healthy=health.healthy,
            telemetry_status=TelemetrySourceStatus.HEALTHY,
            observed_at=metrics.observed_at,
        )
