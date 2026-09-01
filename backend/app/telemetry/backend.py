from __future__ import annotations

from typing import Protocol

from backend.app.tools.schemas import (
    DeploymentResponse,
    LogResponse,
    MetricResponse,
    ServiceHealthResponse,
)
from backend.app.telemetry.models import TelemetrySourceHealth


class TelemetryBackend(Protocol):
    """Read-only telemetry access for diagnostics."""

    @property
    def mode(self) -> str:
        """'reference' or 'live'."""

    def source_health(self) -> list[TelemetrySourceHealth]:
        """Current health of each telemetry source."""

    def query_metrics(self, service: str) -> MetricResponse:
        ...

    def get_service_logs(self, service: str) -> list[LogResponse]:
        ...

    def get_recent_deployments(self, service: str) -> list[DeploymentResponse]:
        ...

    def get_service_health(self, service: str) -> ServiceHealthResponse:
        ...
