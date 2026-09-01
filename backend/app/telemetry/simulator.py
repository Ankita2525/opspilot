from __future__ import annotations

from simulator.environment import SimulatedEnvironment
from simulator.models import evaluate_service_health

from backend.app.telemetry.backend import TelemetryBackend
from backend.app.telemetry.health import healthy_source, utc_now
from backend.app.telemetry.models import TelemetrySourceKind, TelemetrySourceHealth
from backend.app.tools.schemas import (
    DeploymentResponse,
    LogResponse,
    MetricResponse,
    ServiceHealthResponse,
)


class SimulatorTelemetryBackend:
    """Reference-mode telemetry backed by deterministic scenario fixtures."""

    def __init__(self, environment: SimulatedEnvironment) -> None:
        self._environment = environment

    @property
    def environment(self) -> SimulatedEnvironment:
        return self._environment

    @property
    def mode(self) -> str:
        return "reference"

    def source_health(self) -> list[TelemetrySourceHealth]:
        now = utc_now()
        return [
            healthy_source(TelemetrySourceKind.METRICS, observed_at=now),
            healthy_source(TelemetrySourceKind.LOGS, observed_at=now),
            healthy_source(TelemetrySourceKind.DEPLOYMENTS, observed_at=now),
            healthy_source(TelemetrySourceKind.SERVICE_HEALTH, observed_at=now),
        ]

    def query_metrics(self, service: str) -> MetricResponse:
        snapshot = self._environment.query_metrics(service)
        return MetricResponse.model_validate(snapshot, from_attributes=True)

    def get_service_logs(self, service: str) -> list[LogResponse]:
        events = self._environment.get_logs(service)
        return [
            LogResponse.model_validate(event, from_attributes=True) for event in events
        ]

    def get_recent_deployments(self, service: str) -> list[DeploymentResponse]:
        events = self._environment.get_recent_deployments(service)
        return [
            DeploymentResponse.model_validate(event, from_attributes=True)
            for event in events
        ]

    def get_service_health(self, service: str) -> ServiceHealthResponse:
        health = self._environment.get_service_health(service)
        return ServiceHealthResponse.model_validate(health, from_attributes=True)


def evaluate_health(
    service: str,
    p95_latency_ms: int,
    error_rate_percent: float,
) -> ServiceHealthResponse:
    from simulator.models import ServiceHealthThresholds

    from backend.app.telemetry.models import SANDBOX_SERVICE_THRESHOLDS

    thresholds = SANDBOX_SERVICE_THRESHOLDS.get(service)
    if thresholds is None:
        raise ValueError(f"Unknown service: {service}")
    threshold_values = ServiceHealthThresholds(
        max_p95_latency_ms=thresholds.max_p95_latency_ms,
        max_error_rate_percent=thresholds.max_error_rate_percent,
    )
    healthy = evaluate_service_health(
        p95_latency_ms,
        error_rate_percent,
        threshold_values,
    )
    return ServiceHealthResponse(
        service=service,
        p95_latency_ms=p95_latency_ms,
        error_rate_percent=error_rate_percent,
        max_p95_latency_ms=thresholds.max_p95_latency_ms,
        max_error_rate_percent=thresholds.max_error_rate_percent,
        healthy=healthy,
    )
